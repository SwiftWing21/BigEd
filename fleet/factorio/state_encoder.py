# fleet/factorio/state_encoder.py
"""
Convert Factorio GameState into tensors for the CNN+MLP policy network.

Grid  (5 channels, grid_size x grid_size):
  ch 0 — entity type ID from ENTITY_REGISTRY, normalized  (id / max_id)
  ch 1 — entity direction, normalized 0-1  (direction / 7.0)
  ch 2 — resource density  (ore type id / 4, from resource_positions)
  ch 3 — resource amount   (amount / 5000, from resource_positions)
  ch 4 — terrain type  (water=1.0, grass~0.2, sand~0.4, concrete~0.7)

Feature vector (69 base dims, 85 with spatial memory):
  [0:30]   inventory counts for TRACKED_ITEMS, normalized per item
  [30:50]  research tech one-hot (20 slots from TECH_REGISTRY)
  [50]     research progress 0-1
  [51:54]  power status: satisfaction, capacity_mw norm, entity count norm
  [54:56]  time: tick norm, episode step placeholder (0)
  [56:60]  curriculum phase one-hot (4 phases)
  [60]     lesson index normalized (/ 20)
  [61:64]  strategy goal vector (zeros by default)
  [64:68]  global resource counts (iron/copper/coal/stone, normalized)
  [68]     active pack progress 0-1
"""
import logging
import math
import numpy as np
from factorio.state_parser import GameState, GameMetrics
from factorio.action_space import ENTITY_REGISTRY, TECH_REGISTRY, PHASE_ENTITIES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRACKED_ITEMS: list[str] = [
    "iron-ore", "copper-ore", "coal", "stone", "wood",
    "iron-plate", "copper-plate", "steel-plate", "stone-brick",
    "iron-gear-wheel", "iron-stick", "copper-cable", "electronic-circuit",
    "automation-science-pack", "logistic-science-pack",
    "transport-belt", "inserter", "burner-inserter", "fast-inserter",
    "small-electric-pole", "pipe", "boiler", "steam-engine",
    "assembling-machine-1", "assembling-machine-2",
    "burner-mining-drill", "electric-mining-drill",
    "stone-furnace", "lab", "wooden-chest",
]
assert len(TRACKED_ITEMS) == 30, "TRACKED_ITEMS must have exactly 30 entries"

# Per-item normalisation caps (raw item counts are clipped then divided)
_ITEM_MAX: dict[str, float] = {
    "iron-ore": 500.0,   "copper-ore": 500.0,  "coal": 500.0,
    "stone": 500.0,      "wood": 200.0,
    "iron-plate": 500.0, "copper-plate": 500.0, "steel-plate": 200.0,
    "stone-brick": 200.0,
    "iron-gear-wheel": 200.0, "iron-stick": 200.0, "copper-cable": 500.0,
    "electronic-circuit": 200.0,
    "automation-science-pack": 100.0, "logistic-science-pack": 100.0,
    "transport-belt": 200.0, "inserter": 100.0, "burner-inserter": 100.0,
    "fast-inserter": 100.0,
    "small-electric-pole": 100.0, "pipe": 200.0, "boiler": 20.0,
    "steam-engine": 20.0,
    "assembling-machine-1": 50.0, "assembling-machine-2": 50.0,
    "burner-mining-drill": 50.0, "electric-mining-drill": 50.0,
    "stone-furnace": 50.0, "lab": 20.0, "wooden-chest": 50.0,
}

_NUM_TECHS = 20            # fixed one-hot width (TECH_REGISTRY has 11 entries)
_MAX_ENTITY_ID = max(ENTITY_REGISTRY.values()) if ENTITY_REGISTRY else 1
_BASE_FEATURE_DIM = 69     # 30 + 20 + 1 + 3 + 2 + 4 + 1 + 3 + 4 (global resources) + 1 (pack progress)
_SPATIAL_FEATURE_DIM = 16  # bearing/distance to resources + counts + nearest entity
_AGENT_FEATURE_DIM = 10    # 4 agent one-hot + 6 peer proximity (3 peers × 2: bearing, distance)
_GRID_CHANNELS = 5         # entity type, direction, resource type, resource amount, terrain
_TICK_NORM = 216_000.0     # ~1 hour of Factorio ticks (60 tps × 3600 s)

# Terrain tile type → normalized ID for grid channel 4
_TERRAIN_IDS: dict[str, float] = {
    "water": 1.0, "deepwater": 1.0, "water-green": 1.0,
    "grass-1": 0.2, "grass-2": 0.2, "grass-3": 0.25, "grass-4": 0.25,
    "dirt-1": 0.3, "dirt-2": 0.3, "dirt-3": 0.35, "dirt-4": 0.35,
    "sand-1": 0.4, "sand-2": 0.4, "sand-3": 0.45,
    "dry-dirt": 0.35, "red-desert-0": 0.5, "red-desert-1": 0.5,
    "red-desert-2": 0.55, "red-desert-3": 0.55,
    "concrete": 0.7, "refined-concrete": 0.75, "stone-path": 0.65,
    "hazard-concrete-left": 0.7, "hazard-concrete-right": 0.7,
    "landfill": 0.6,
    "out-of-map": 0.0,
}
_POWER_CAP_MW = 100.0      # normalisation cap for capacity_mw
_POWER_COUNT_CAP = 50.0    # normalisation cap for electric entity count


# ---------------------------------------------------------------------------
# StateEncoder
# ---------------------------------------------------------------------------

class StateEncoder:
    """Encode a Factorio GameState into (grid, features) numpy arrays."""

    def __init__(
        self,
        phase: int = 1,
        grid_size: int = 64,
        lesson_index: int = 0,
        strategy_goal: list[float] | None = None,
        spatial_memory=None,
        num_agents: int = 1,
    ) -> None:
        self._phase = max(1, min(4, phase))
        self._grid_size = grid_size
        self._lesson_index = lesson_index
        self._strategy_goal: list[float] = list(strategy_goal) if strategy_goal else [0.0, 0.0, 0.0]
        self._half = grid_size // 2
        self._spatial_memory = spatial_memory
        self._num_agents = num_agents

    # ------------------------------------------------------------------
    # Public setters
    # ------------------------------------------------------------------

    def set_phase(self, phase: int) -> None:
        self._phase = max(1, min(4, phase))

    def set_lesson_index(self, index: int) -> None:
        self._lesson_index = index

    def set_strategy_goal(self, goal: list[float]) -> None:
        self._strategy_goal = list(goal)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def feature_dim(self) -> int:
        dim = _BASE_FEATURE_DIM
        if self._spatial_memory is not None:
            dim += _SPATIAL_FEATURE_DIM
        if self._num_agents > 1:
            dim += _AGENT_FEATURE_DIM
        return dim

    @property
    def grid_channels(self) -> int:
        return _GRID_CHANNELS

    @property
    def world_grid_channels(self) -> int:
        return 4  # resource type, amount, entity type, player marker

    @property
    def grid_size(self) -> int:
        return self._grid_size

    # ------------------------------------------------------------------
    # Main encode
    # ------------------------------------------------------------------

    def encode(
        self,
        state: GameState,
        metrics: GameMetrics | None = None,
        spatial_memory=None,
        agent_id: int = 1,
        other_agent_positions: list[tuple[float, float]] | None = None,
        pack_progress: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (grid, world_grid, features) as float32 ndarrays.

        grid       — shape (5, grid_size, grid_size) — local view
        world_grid — shape (4, 64, 64) — zoomed-out minimap from spatial memory
        features   — shape (feature_dim,)

        Args:
            spatial_memory: Per-agent spatial memory override (uses self._spatial_memory if None).
            agent_id: 1-based agent index for identity features.
            other_agent_positions: List of (x, y) positions of peer agents for proximity features.
        """
        grid = self._encode_grid(state)
        base_features = self._encode_features(state, metrics, pack_progress=pack_progress)
        px = state.player_position.get("x", 0.0)
        py = state.player_position.get("y", 0.0)
        mem = spatial_memory if spatial_memory is not None else self._spatial_memory
        if mem is not None:
            spatial = np.array(mem.get_features(px, py), dtype=np.float32)
            features = np.concatenate([base_features, spatial])
            world_grid = mem.render_world_grid(px, py)
        else:
            features = base_features
            world_grid = np.zeros((4, 64, 64), dtype=np.float32)

        # Multi-agent identity + proximity features
        if self._num_agents > 1:
            agent_feats = self._encode_agent_features(
                agent_id, px, py, other_agent_positions or [])
            features = np.concatenate([features, agent_feats])

        return grid, world_grid, features

    def _encode_agent_features(
        self,
        agent_id: int,
        px: float, py: float,
        other_positions: list[tuple[float, float]],
    ) -> np.ndarray:
        """Encode agent identity (one-hot) + bearing/distance to up to 3 peers."""
        feats = np.zeros(_AGENT_FEATURE_DIM, dtype=np.float32)
        # [0:4] agent one-hot (supports up to 4 agents)
        idx = max(0, min(3, agent_id - 1))
        feats[idx] = 1.0
        # [4:10] peer proximity: 3 slots × (bearing_norm, distance_norm)
        _DIST_NORM = 60.0  # normalize distance over ~60 tiles
        for i, (ox, oy) in enumerate(other_positions[:3]):
            dx = ox - px
            dy = oy - py
            dist = math.sqrt(dx * dx + dy * dy)
            bearing = math.atan2(dy, dx) / math.pi  # [-1, 1]
            feats[4 + i * 2] = bearing
            feats[4 + i * 2 + 1] = min(dist / _DIST_NORM, 1.0)
        return feats

    # ------------------------------------------------------------------
    # Grid encoding
    # ------------------------------------------------------------------

    def _encode_grid(self, state: GameState) -> np.ndarray:
        g = self._grid_size
        grid = np.zeros((_GRID_CHANNELS, g, g), dtype=np.float32)

        px = state.player_position.get("x", 0.0)
        py = state.player_position.get("y", 0.0)
        half = self._half

        for entity in state.entities:
            try:
                ex = entity.position.get("x", 0.0)
                ey = entity.position.get("y", 0.0)
                gx = round(ex - px) + half
                gy = round(ey - py) + half
                if not (0 <= gx < g and 0 <= gy < g):
                    continue
                entity_id = ENTITY_REGISTRY.get(entity.name, 0)
                if entity_id == 0:
                    continue
                grid[0, gy, gx] = entity_id / _MAX_ENTITY_ID
                grid[1, gy, gx] = entity.direction / 15.0  # 16-direction encoding (0-15)
            except Exception:
                log.warning("Failed to encode entity %r", entity.name, exc_info=True)

        # Channel 2-3: resource positions (ore visibility)
        _RESOURCE_IDS = {"iron-ore": 1, "copper-ore": 2, "coal": 3, "stone": 4}
        for rp in getattr(state, "resource_positions", []):
            try:
                rx = rp.get("x", 0.0)
                ry = rp.get("y", 0.0)
                gx = round(rx - px) + half
                gy = round(ry - py) + half
                if not (0 <= gx < g and 0 <= gy < g):
                    continue
                rid = _RESOURCE_IDS.get(rp.get("name", ""), 0)
                if rid == 0:
                    continue
                grid[2, gy, gx] = rid / 4.0
                grid[3, gy, gx] = min(rp.get("amount", 0) / 5000.0, 1.0)
            except Exception:
                pass

        # Channel 4: terrain type (water=1.0, grass~0.2, sand~0.4, concrete~0.7)
        for tile in getattr(state, "terrain", []):
            try:
                tx = tile.get("x", 0.0)
                ty = tile.get("y", 0.0)
                gx = round(tx - px) + half
                gy = round(ty - py) + half
                if not (0 <= gx < g and 0 <= gy < g):
                    continue
                tid = _TERRAIN_IDS.get(tile.get("t", ""), 0.15)
                grid[4, gy, gx] = tid
            except Exception:
                pass

        return grid

    # ------------------------------------------------------------------
    # Feature vector encoding
    # ------------------------------------------------------------------

    def _encode_features(
        self,
        state: GameState,
        metrics: GameMetrics | None,
        pack_progress: float = 0.0,
    ) -> np.ndarray:
        feats = np.zeros(_BASE_FEATURE_DIM, dtype=np.float32)

        # [0:30] inventory
        for i, item in enumerate(TRACKED_ITEMS):
            count = state.inventory.get(item, 0)
            cap = _ITEM_MAX.get(item, 100.0)
            feats[i] = float(min(count, cap)) / cap

        # [30:50] research tech one-hot (up to _NUM_TECHS slots)
        research_name = state.research_name or (
            metrics.current_research if metrics else ""
        )
        if research_name:
            tech_id = TECH_REGISTRY.get(research_name, 0)
            if 1 <= tech_id <= _NUM_TECHS:
                feats[30 + tech_id - 1] = 1.0

        # [50] research progress
        research_progress = state.research_progress or (
            metrics.current_research_progress if metrics else 0.0
        )
        feats[50] = float(np.clip(research_progress, 0.0, 1.0))

        # [51:54] power status
        if metrics is not None:
            try:
                satisfaction = float(metrics.electric_satisfaction or 0.0)
            except (ValueError, TypeError):
                satisfaction = 0.0
            cap_mw = float(metrics.electric_capacity_mw or 0.0) / _POWER_CAP_MW
            ent_count = float(metrics.electric_entity_count or 0) / _POWER_COUNT_CAP
            feats[51] = float(np.clip(satisfaction, 0.0, 1.0))
            feats[52] = float(np.clip(cap_mw, 0.0, 1.0))
            feats[53] = float(np.clip(ent_count, 0.0, 1.0))
        # else zeros

        # [54:56] time
        feats[54] = float(np.clip(state.tick / _TICK_NORM, 0.0, 1.0))
        feats[55] = 0.0  # episode step placeholder

        # [56:60] curriculum phase one-hot
        phase_idx = max(0, min(3, self._phase - 1))
        feats[56 + phase_idx] = 1.0

        # [60] lesson index normalized
        feats[60] = float(self._lesson_index) / 20.0

        # [61:64] strategy goal
        goal = self._strategy_goal
        for j in range(min(3, len(goal))):
            feats[61 + j] = float(goal[j])

        # [64:68] global resource counts (normalized, strategic awareness)
        _GLOBAL_NORM = 1e9  # 1 billion ore as cap
        _GLOBAL_RESOURCES = ["iron-ore", "copper-ore", "coal", "stone"]
        global_res = getattr(state, "global_resources", {})
        for i, rname in enumerate(_GLOBAL_RESOURCES):
            feats[64 + i] = float(min(global_res.get(rname, 0) / _GLOBAL_NORM, 1.0))

        # [68] active pack progress
        feats[68] = float(np.clip(pack_progress, 0.0, 1.0))

        return feats
