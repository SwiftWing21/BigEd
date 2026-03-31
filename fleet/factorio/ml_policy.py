"""
Factorio RL policy network — actor-critic with hierarchical action heads.

Architecture
------------
Grid (B, C, 64, 64)  →  CNN  →  128-dim spatial embedding
Features (B, 64)      →  MLP  →  64-dim context embedding
Concat (192)          →  shared trunk (256 → 128)
                      →  action_head  : (128 → num_action_types)
                      →  value_head   : (128 → 1)
                      →  per-action parameter heads (see get_action_params)
"""

import logging
import math

import torch
import torch.nn as nn
from torch.distributions import Categorical

from factorio.action_space import ActionType

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CNN spatial feature extractor
# ---------------------------------------------------------------------------

class _GridEncoder(nn.Module):
    """Conv stack: (B, C, 64, 64) → (B, 128).

    When *return_spatial* is True, ``forward`` also returns the 8×8
    feature map produced by the 3rd conv layer (before adaptive pooling).
    This is used by ``_SpatialPlacementHead`` for coordinate-conditioned
    placement decisions.
    """

    def __init__(self, in_channels: int) -> None:
        super().__init__()
        # Build conv layers individually so we can tap intermediate output
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, 16, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
        )
        self.conv3 = nn.Sequential(
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
        )
        self.pool = nn.AdaptiveAvgPool2d(4)
        # After pool: (B, 64, 4, 4) → flatten 1024
        self.fc = nn.Sequential(
            nn.Linear(64 * 4 * 4, 128),
            nn.ReLU(),
        )

    def forward(
        self, x: torch.Tensor, return_spatial: bool = False,
    ) -> torch.Tensor | tuple[torch.Tensor, torch.Tensor]:
        h = self.conv1(x)   # (B, 16, 32, 32)
        h = self.conv2(h)   # (B, 32, 16, 16)
        h = self.conv3(h)   # (B, 64,  8,  8)
        spatial_features = h  # save before pooling
        h = self.pool(h)     # (B, 64,  4,  4)
        h = h.flatten(1)
        embedding = self.fc(h)  # (B, 128)
        if return_spatial:
            return embedding, spatial_features
        return embedding


# ---------------------------------------------------------------------------
# Spatial placement head — coordinate-conditioned via attention on conv features
# ---------------------------------------------------------------------------

class _SpatialPlacementHead(nn.Module):
    """Produce dx/dy logits by attending to spatial conv features.

    Instead of predicting placement coordinates from a 128-dim bottleneck,
    this head operates on the 8×8 feature map from the 3rd conv layer
    (64 channels) concatenated with resource channels pooled to 8×8.

    Architecture:
        conv_features (B, 64, 8, 8)
      + resource_grid (B, 2, 64, 64) → pool → (B, 2, 8, 8)
      → concat → (B, 66, 8, 8)
      → 1×1 conv → (B, 1, 8, 8) placement heatmap
      → flatten → (B, 64)
      + shared trunk (B, 128) → concat → (B, 192)
      → FC → dx_logits (B, 11), dy_logits (B, 11)
    """

    _SPATIAL_SIZE = 8  # matches conv3 output spatial dim

    def __init__(self, dx_dy_bins: int = 11) -> None:
        super().__init__()
        self.resource_pool = nn.AdaptiveAvgPool2d(self._SPATIAL_SIZE)
        # 64 conv channels + 2 resource channels = 66
        self.attn_conv = nn.Sequential(
            nn.Conv2d(66, 32, kernel_size=1),
            nn.ReLU(),
            nn.Conv2d(32, 1, kernel_size=1),
        )
        # Heatmap (64) + shared trunk (128) = 192
        self.dx_head = nn.Linear(192, dx_dy_bins)
        self.dy_head = nn.Linear(192, dx_dy_bins)

    def forward(
        self,
        shared: torch.Tensor,
        spatial_features: torch.Tensor,
        resource_grid: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Parameters
        ----------
        shared           : (B, 128) trunk output
        spatial_features : (B, 64, 8, 8) conv features before pooling
        resource_grid    : (B, 2, 64, 64) resource channels (ch 2-3 of grid)

        Returns
        -------
        dx_logits : (B, dx_dy_bins)
        dy_logits : (B, dx_dy_bins)
        """
        # Pool resource channels to match conv spatial resolution
        res_pooled = self.resource_pool(resource_grid)  # (B, 2, 8, 8)
        combined = torch.cat([spatial_features, res_pooled], dim=1)  # (B, 66, 8, 8)

        # Compute placement heatmap
        heatmap = self.attn_conv(combined)  # (B, 1, 8, 8)
        heatmap_flat = heatmap.flatten(1)   # (B, 64)

        # Combine spatial attention with shared trunk
        fused = torch.cat([heatmap_flat, shared], dim=-1)  # (B, 192)
        return self.dx_head(fused), self.dy_head(fused)


# ---------------------------------------------------------------------------
# Feature MLP context encoder
# ---------------------------------------------------------------------------

class _FeatureEncoder(nn.Module):
    """MLP: (B, feature_dim) → (B, 64)."""

    def __init__(self, feature_dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ---------------------------------------------------------------------------
# Shared trunk
# ---------------------------------------------------------------------------

class _Trunk(nn.Module):
    """(B, input_dim) → (B, 128)."""

    def __init__(self, input_dim: int = 320) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ---------------------------------------------------------------------------
# Main policy network
# ---------------------------------------------------------------------------

class FactorioPolicy(nn.Module):
    """
    Actor-critic policy for Factorio.

    Parameters
    ----------
    grid_channels       : number of input channels in the local spatial grid
    grid_size           : spatial side length (assumed square)
    feature_dim         : length of the non-spatial feature vector
    num_action_types    : number of discrete action types (== len(ActionType))
    num_entities        : vocabulary size for entity parameter head
    num_recipes         : vocabulary size for recipe parameter head
    num_techs           : vocabulary size for technology parameter head
    world_grid_channels : number of input channels in the world minimap grid
    """

    # Grid coordinate range for set_recipe / remove heads
    _GRID_RANGE = 64
    # dx / dy range: [-5, +5] → 11 bins
    _DX_DY_BINS = 11
    # direction bins
    _DIR_BINS = 8
    # craft count bins
    _COUNT_BINS = 10

    def __init__(
        self,
        grid_channels: int,
        grid_size: int,
        feature_dim: int,
        num_action_types: int,
        num_entities: int,
        num_recipes: int,
        num_techs: int,
        world_grid_channels: int = 4,
    ) -> None:
        super().__init__()

        self.num_action_types = num_action_types
        self.num_entities = num_entities
        self.num_recipes = num_recipes
        self.num_techs = num_techs

        # Encoders — local grid + world minimap + features
        self.grid_encoder = _GridEncoder(grid_channels)
        self.world_encoder = _GridEncoder(world_grid_channels)
        self.feature_encoder = _FeatureEncoder(feature_dim)

        # Shared trunk: 128 (local) + 128 (world) + 64 (features) = 320
        self.trunk = _Trunk(input_dim=320)

        # Action-type head + value head
        self.action_head = nn.Linear(128, num_action_types)
        self.value_head = nn.Linear(128, 1)

        # ---- Per-action parameter heads ----
        # PLACE: entity, dx, dy (spatial), direction
        self.place_entity    = nn.Linear(128, num_entities)
        self.place_spatial   = _SpatialPlacementHead(dx_dy_bins=self._DX_DY_BINS)
        # Fallback linear heads used when spatial features are unavailable
        self.place_dx        = nn.Linear(128, self._DX_DY_BINS)
        self.place_dy        = nn.Linear(128, self._DX_DY_BINS)
        self.place_direction = nn.Linear(128, self._DIR_BINS)

        # Side-effect storage for spatial features (set by _shared_forward)
        self._last_spatial_features: torch.Tensor | None = None
        self._last_grid: torch.Tensor | None = None

        # CRAFT: recipe, count
        self.craft_recipe = nn.Linear(128, num_recipes)
        self.craft_count  = nn.Linear(128, self._COUNT_BINS)

        # RESEARCH: tech
        self.research_tech = nn.Linear(128, num_techs)

        # MOVE: dx, dy
        self.move_dx = nn.Linear(128, self._DX_DY_BINS)
        self.move_dy = nn.Linear(128, self._DX_DY_BINS)

        # SET_RECIPE: gx, gy, recipe
        self.set_recipe_gx     = nn.Linear(128, self._GRID_RANGE)
        self.set_recipe_gy     = nn.Linear(128, self._GRID_RANGE)
        self.set_recipe_recipe = nn.Linear(128, num_recipes)

        # REMOVE: gx, gy
        self.remove_gx = nn.Linear(128, self._GRID_RANGE)
        self.remove_gy = nn.Linear(128, self._GRID_RANGE)

        # MINE: dx, dy
        self.mine_dx = nn.Linear(128, self._DX_DY_BINS)
        self.mine_dy = nn.Linear(128, self._DX_DY_BINS)

        # INSERT: dx, dy (target entity position), recipe (item to insert), count
        self.insert_dx = nn.Linear(128, self._DX_DY_BINS)
        self.insert_dy = nn.Linear(128, self._DX_DY_BINS)
        self.insert_item = nn.Linear(128, num_recipes)  # reuse recipe vocab as item selector
        self.insert_count = nn.Linear(128, self._COUNT_BINS)

        # WAIT has no parameters

        # Pack/stamp selection heads
        from factorio.pack_registry import MAX_PACK_SLOTS
        self.pack_head = nn.Linear(128, MAX_PACK_SLOTS)  # 64 slots
        self.pack_offset_dx = nn.Linear(128, self._DX_DY_BINS)  # 11 bins
        self.pack_offset_dy = nn.Linear(128, self._DX_DY_BINS)  # 11 bins

        self._init_weights()

    # ------------------------------------------------------------------
    # Weight initialisation (orthogonal — standard PPO)
    # ------------------------------------------------------------------

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, (nn.Linear, nn.Conv2d)):
                nn.init.orthogonal_(module.weight, gain=math.sqrt(2))
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0.0)

        # Value head uses small gain to start near zero
        nn.init.orthogonal_(self.value_head.weight, gain=0.01)
        nn.init.constant_(self.value_head.bias, 0.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _shared_forward(
        self,
        grid: torch.Tensor,
        features: torch.Tensor,
        world_grid: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Run CNN + MLP + trunk, return (B, 128) shared representation.

        Side effects: stores ``_last_spatial_features`` (8×8 conv map) and
        ``_last_grid`` for use by the spatial placement head.
        """
        spatial, spatial_feat = self.grid_encoder(grid, return_spatial=True)
        self._last_spatial_features = spatial_feat  # (B, 64, 8, 8)
        self._last_grid = grid                       # (B, C, 64, 64)
        if world_grid is not None:
            world = self.world_encoder(world_grid)   # (B, 128)
        else:
            world = torch.zeros_like(spatial)        # (B, 128)
        context = self.feature_encoder(features)     # (B,  64)
        combined = torch.cat([spatial, world, context], dim=-1)  # (B, 320)
        return self.trunk(combined)                  # (B, 128)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def forward(
        self,
        grid: torch.Tensor,
        features: torch.Tensor,
        world_grid: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Main forward pass.

        Returns
        -------
        action_logits : (B, num_action_types)
        value         : (B, 1)
        """
        shared = self._shared_forward(grid, features, world_grid)
        return self.action_head(shared), self.value_head(shared)

    def get_action_params(
        self,
        shared: torch.Tensor,
        action_type: int,
    ) -> dict[str, torch.Tensor]:
        """
        Return parameter logits for the given action type.

        Parameters
        ----------
        shared      : (B, 128) shared trunk output
        action_type : integer value of ActionType

        Returns
        -------
        dict mapping parameter name → logit tensor
        """
        at = action_type

        if at == ActionType.PLACE.value:
            # Use spatial attention head when conv features are available
            if self._last_spatial_features is not None and self._last_grid is not None:
                resource_grid = self._last_grid[:, 2:4, :, :]  # ch 2-3: resource channels
                dx_logits, dy_logits = self.place_spatial(
                    shared, self._last_spatial_features, resource_grid,
                )
            else:
                # Fallback: simple linear heads (e.g. loaded from old checkpoint)
                dx_logits = self.place_dx(shared)
                dy_logits = self.place_dy(shared)
            return {
                "entity_logits":    self.place_entity(shared),
                "dx_logits":        dx_logits,
                "dy_logits":        dy_logits,
                "direction_logits": self.place_direction(shared),
            }
        elif at == ActionType.CRAFT.value:
            return {
                "recipe_logits": self.craft_recipe(shared),
                "count_logits":  self.craft_count(shared),
            }
        elif at == ActionType.RESEARCH.value:
            return {
                "tech_logits": self.research_tech(shared),
            }
        elif at == ActionType.MOVE.value:
            return {
                "dx_logits": self.move_dx(shared),
                "dy_logits": self.move_dy(shared),
            }
        elif at == ActionType.SET_RECIPE.value:
            return {
                "gx_logits":     self.set_recipe_gx(shared),
                "gy_logits":     self.set_recipe_gy(shared),
                "recipe_logits": self.set_recipe_recipe(shared),
            }
        elif at == ActionType.REMOVE.value:
            return {
                "gx_logits": self.remove_gx(shared),
                "gy_logits": self.remove_gy(shared),
            }
        elif at == ActionType.MINE.value:
            return {
                "dx_logits": self.mine_dx(shared),
                "dy_logits": self.mine_dy(shared),
            }
        elif at == ActionType.INSERT.value:
            return {
                "dx_logits": self.insert_dx(shared),
                "dy_logits": self.insert_dy(shared),
                "recipe_logits": self.insert_item(shared),
                "count_logits": self.insert_count(shared),
            }
        elif at == ActionType.PACK.value or at == ActionType.STAMP.value:
            return {
                "pack_logits": self.pack_head(shared),
                "offset_dx_logits": self.pack_offset_dx(shared),
                "offset_dy_logits": self.pack_offset_dy(shared),
            }
        elif at == ActionType.WAIT.value:
            return {}
        else:
            log.warning("get_action_params: unknown action_type %d", action_type)
            return {}

    @torch.no_grad()
    def act(
        self,
        grid: torch.Tensor,
        features: torch.Tensor,
        action_mask: torch.Tensor | None = None,
        world_grid: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
        """
        Sample an action from the policy.

        Parameters
        ----------
        grid         : (B, C, H, W)
        features     : (B, feature_dim)
        action_mask  : optional (B, num_action_types) bool — True = allowed
        world_grid   : optional (B, C, H, W) — zoomed-out minimap

        Returns
        -------
        action_type  : (B,)   sampled action index
        log_prob     : (B,)   log-probability of the sampled action
        value        : (B,)   estimated state value (squeezed)
        params       : dict   parameter logits for the sampled action type
        """
        shared = self._shared_forward(grid, features, world_grid)
        action_logits = self.action_head(shared)  # (B, A)

        if action_mask is not None:
            action_logits = action_logits.masked_fill(~action_mask, -1e8)

        dist = Categorical(logits=action_logits)
        action_type = dist.sample()          # (B,)
        log_prob = dist.log_prob(action_type)  # (B,)
        value = self.value_head(shared).squeeze(-1)  # (B,)

        # Use the first batch element's action type for parameter heads.
        # For batched rollouts with mixed action types callers should call
        # get_action_params per-sample; here we return the majority/first.
        params = self.get_action_params(shared, action_type[0].item())

        return action_type, log_prob, value, params

    def evaluate_action(
        self,
        grid: torch.Tensor,
        features: torch.Tensor,
        action_type: torch.Tensor,
        world_grid: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Evaluate stored actions for PPO update.

        Parameters
        ----------
        grid        : (B, C, H, W)
        features    : (B, feature_dim)
        action_type : (B,) integer action indices
        world_grid  : optional (B, C, H, W) — zoomed-out minimap

        Returns
        -------
        log_prob : (B,)
        value    : (B,)
        entropy  : scalar
        """
        shared = self._shared_forward(grid, features, world_grid)
        action_logits = self.action_head(shared)
        dist = Categorical(logits=action_logits)
        log_prob = dist.log_prob(action_type)
        value = self.value_head(shared).squeeze(-1)
        entropy = dist.entropy().mean()
        return log_prob, value, entropy

    # ------------------------------------------------------------------
    # Checkpoint I/O
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Save model state dict to *path*."""
        try:
            torch.save(self.state_dict(), path)
            log.info("FactorioPolicy saved to %s", path)
        except Exception:
            log.warning("FactorioPolicy.save failed for path=%s", path, exc_info=True)
            raise

    def load(self, path: str) -> None:
        """Load model state dict from *path* (weights_only=True for security).

        Uses ``strict=False`` so that old checkpoints missing the spatial
        placement head still load successfully (the new head starts with
        freshly-initialised weights).
        """
        try:
            state = torch.load(path, weights_only=True)
            missing, unexpected = self.load_state_dict(state, strict=False)
            if missing:
                log.info(
                    "FactorioPolicy loaded from %s (new keys initialised fresh: %s)",
                    path, ", ".join(missing),
                )
            else:
                log.info("FactorioPolicy loaded from %s", path)
            if unexpected:
                log.warning("Unexpected keys in checkpoint: %s", unexpected)
        except Exception:
            log.warning("FactorioPolicy.load failed for path=%s", path, exc_info=True)
            raise
