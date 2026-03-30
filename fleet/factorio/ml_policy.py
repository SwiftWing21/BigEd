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
    """Conv stack: (B, C, 64, 64) → (B, 128)."""

    def __init__(self, in_channels: int) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, 16, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(4),
        )
        # After pool: (B, 64, 4, 4) → flatten 1024
        self.fc = nn.Sequential(
            nn.Linear(64 * 4 * 4, 128),
            nn.ReLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.conv(x)
        h = h.flatten(1)
        return self.fc(h)


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
        # PLACE: entity, dx, dy, direction
        self.place_entity    = nn.Linear(128, num_entities)
        self.place_dx        = nn.Linear(128, self._DX_DY_BINS)
        self.place_dy        = nn.Linear(128, self._DX_DY_BINS)
        self.place_direction = nn.Linear(128, self._DIR_BINS)

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

        self._init_weights()

    # ------------------------------------------------------------------
    # Weight initialisation (orthogonal — standard PPO)
    # ------------------------------------------------------------------

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.orthogonal_(module.weight, gain=math.sqrt(2))
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
        """Run CNN + MLP + trunk, return (B, 128) shared representation."""
        spatial = self.grid_encoder(grid)          # (B, 128)
        if world_grid is not None:
            world = self.world_encoder(world_grid)  # (B, 128)
        else:
            world = torch.zeros_like(spatial)       # (B, 128)
        context = self.feature_encoder(features)    # (B,  64)
        combined = torch.cat([spatial, world, context], dim=-1)  # (B, 320)
        return self.trunk(combined)                 # (B, 128)

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
            return {
                "entity_logits":    self.place_entity(shared),
                "dx_logits":        self.place_dx(shared),
                "dy_logits":        self.place_dy(shared),
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
        """Load model state dict from *path* (weights_only=True for security)."""
        try:
            state = torch.load(path, weights_only=True)
            self.load_state_dict(state)
            log.info("FactorioPolicy loaded from %s", path)
        except Exception:
            log.warning("FactorioPolicy.load failed for path=%s", path, exc_info=True)
            raise
