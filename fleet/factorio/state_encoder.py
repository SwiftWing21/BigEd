# fleet/factorio/state_encoder.py
"""
Convert Factorio GameState into tensors for the CNN+MLP policy network.

Grid  (4 channels, grid_size x grid_size):
  ch 0 — entity type ID from ENTITY_REGISTRY, normalized  (id / max_id)
  ch 1 — entity direction, normalized 0-1  (direction / 7.0)
  ch 2 — resource density  (deferred — zeros)
  ch 3 — connectivity      (deferred — zeros)

Feature vector (64 dims):
  [0:30]   inventory counts for TRACKED_ITEMS, normalized per item
  [30:50]  research tech one-hot (20 slots from TECH_REGISTRY)
  [50]     research progress 0-1
  [51:54]  power status: satisfaction, capacity_mw norm, entity count norm
  [54:56]  time: tick norm, episode step placeholder (0)
  [56:60]  curriculum phase one-hot (4 phases)
  [60]     lesson index normalized (/ 20)
  [61:64]  strategy goal vector (zeros by default)
"""
import logging
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
_FEATURE_DIM = 64          # 30 + 20 + 1 + 3 + 2 + 4 + 1 + 3
_GRID_CHANNELS = 4
_TICK_NORM = 216_000.0     # ~1 hour of Factorio ticks (60 tps × 3600 s)
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
    ) -> None:
        self._phase = max(1, min(4, phase))
        self._grid_size = grid_size
        self._lesson_index = lesson_index
        self._strategy_goal: list[float] = list(strategy_goal) if strategy_goal else [0.0, 0.0, 0.0]
        self._half = grid_size // 2

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
        return _FEATURE_DIM

    @property
    def grid_channels(self) -> int:
        return _GRID_CHANNELS

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
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (grid, features) as float32 ndarrays.

        grid    — shape (4, grid_size, grid_size)
        features — shape (feature_dim,)
        """
        grid = self._encode_grid(state)
        features = self._encode_features(state, metrics)
        return grid, features

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
                grid[1, gy, gx] = entity.direction / 7.0
                # channels 2 and 3 are deferred — remain 0
            except Exception:
                log.warning("Failed to encode entity %r", entity.name, exc_info=True)

        return grid

    # ------------------------------------------------------------------
    # Feature vector encoding
    # ------------------------------------------------------------------

    def _encode_features(
        self,
        state: GameState,
        metrics: GameMetrics | None,
    ) -> np.ndarray:
        feats = np.zeros(_FEATURE_DIM, dtype=np.float32)

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

        return feats
