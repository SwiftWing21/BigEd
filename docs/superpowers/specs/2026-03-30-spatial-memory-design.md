# Factorio RL Agent — Persistent Spatial Memory

**Date:** 2026-03-30
**Status:** Approved
**Goal:** Give the RL agent a persistent map memory so it knows where resources and infrastructure are beyond its 64x64 local grid.

---

## Problem

The RL agent's perception resets every tick — a 64x64 player-centric grid (±32 tiles) that drops everything outside that window. Ore patches 50+ tiles away are invisible. The agent can't plan movement toward distant resources, can't remember where it built things, and has no concept of "base layout."

## Design Decisions

- **No LLM required** — pure data structure, updated from existing RCON data
- **Sparse dict** — stores only what's been seen, grows with exploration
- **Minimal schema** — resource patches (type, position, amount) + built entities (type, position). Ready for richer data later.
- **Feature injection via extra dims** — extend the 64-dim feature vector to 80 dims with bearing/distance to nearest resources and infrastructure. No grid architecture change yet (second grid planned for later).
- **Free per-tick updates** — piggyback on existing `get_state` data, zero extra RCON calls
- **Episode-start survey** — one RCON scan (~200 tile radius) on reset to fill in the map

## Architecture

### SpatialMemory Class (`fleet/factorio/spatial_memory.py`)

```python
import math
from dataclasses import dataclass

@dataclass
class ResourceEntry:
    name: str           # "iron-ore", "copper-ore", "coal", "stone"
    x: int              # world x (integer, floored)
    y: int              # world y (integer, floored)
    amount: int         # last known ore amount
    last_seen_tick: int  # game tick when last observed

@dataclass
class EntityEntry:
    name: str           # "stone-furnace", "burner-mining-drill", etc.
    x: float            # world x
    y: float            # world y
    unit_number: int    # Factorio unique ID
    last_seen_tick: int

class SpatialMemory:
    """Sparse persistent map of the Factorio world.

    Owned by FactorioBridge, persists across episodes within a session.
    Starts empty on bridge restart, rebuilds from get_state + episode survey.
    """

    def __init__(self):
        self.resources: dict[str, ResourceEntry] = {}   # key: "{name}_{x}_{y}"
        self.entities: dict[int, EntityEntry] = {}       # key: unit_number
        self._last_local_entity_ids: set[int] = set()    # for removal detection

    def update_from_state(self, state: GameState, current_tick: int) -> None:
        """Upsert resources + entities from the current get_state response.
        Detects removed entities within local grid (±32 tiles)."""

    def update_from_survey(self, survey_data: list[dict]) -> None:
        """Bulk-update resources from wide-area RCON scan."""

    def clear_entities_in_radius(self, center: tuple[float, float], radius: float) -> None:
        """Called on episode reset — clears built structures that were destroyed."""

    def nearest_resource(self, px: float, py: float, resource_name: str) -> tuple[float, float] | None:
        """Returns (bearing_radians, distance_tiles) or None.
        Bearing: atan2(dy, dx), range [0, 2*pi).
        Distance: Euclidean, in tiles."""

    def nearest_entity_by_name(self, px: float, py: float, name: str) -> tuple[float, float] | None:
        """Returns (bearing, distance) to nearest entity with given name."""

    def resource_summary(self) -> dict[str, int]:
        """Returns {resource_name: patch_count}. Missing types return 0."""

    def entity_summary(self) -> dict[str, int]:
        """Returns {entity_name: count}."""

    def get_features(self, px: float, py: float) -> list[float]:
        """Returns exactly 16 floats for injection into state encoder.
        See Feature Injection section. All values in [0, 1]."""
```

**Note:** `EntityEntry` uses `name` only (e.g., "stone-furnace"). No separate `entity_type` field — name is sufficient for queries and summaries. The Factorio `type` field ("furnace", "mining-drill") is not stored; queries use `name` directly.

### Update Flow

**Every tick (free — piggyback on get_state):**
1. For each resource in `state.resource_positions`: upsert into `resources` dict with key `f"{name}_{int(x)}_{int(y)}"`
2. For each entity in `state.entities`: upsert into `entities` dict by `unit_number`
3. Track current-tick entity `unit_number`s in `_last_local_entity_ids`
4. Any entity in previous `_last_local_entity_ids` NOT in current state → remove from `entities` dict
5. ~0.1ms overhead per tick (dict operations only)

**Episode reset (one separate RCON call in bridge.py, after soft_reset):**
1. Bridge calls `self._rcon.command(SURVEY_LUA)` — see Lua snippet below
2. Parse JSON response into `list[dict]`
3. Call `self._spatial_memory.update_from_survey(parsed)`
4. Call `self._spatial_memory.clear_entities_in_radius((0, 0), 200)` since soft_reset destroys structures

Survey Lua (sent as a separate RCON command, NOT added to `_SOFT_RESET_LUA`):
```lua
/c local s=game.get_surface("nauvis"); local out={}; for _,r in pairs(s.find_entities_filtered{type="resource", position={0,0}, radius=200}) do out[#out+1]={name=r.name, x=math.floor(r.position.x), y=math.floor(r.position.y), amount=r.amount} end; rcon.print(game.helpers.table_to_json(out))
```

**Persistence:**
- Owned by `FactorioBridge` instance (Python dict in process memory)
- Persists across episodes within a session
- Starts empty on bridge restart, rebuilds from first tick + first episode survey
- No disk serialization needed

### Feature Injection

Extend feature vector from 64 to 80 dims. New features (16 floats appended):

| Index | Feature | Normalization |
|-------|---------|---------------|
| 64-65 | Nearest iron-ore (bearing, distance) | bearing/(2*pi), min(distance, 200)/200 |
| 66-67 | Nearest copper-ore (bearing, distance) | same |
| 68-69 | Nearest coal (bearing, distance) | same |
| 70-71 | Nearest stone (bearing, distance) | same |
| 72-75 | Remembered resource patch counts (iron, copper, coal, stone) | min(count, 100)/100 |
| 76-77 | Total built furnaces, total built drills | min(count, 20)/20 |
| 78-79 | Nearest furnace (bearing, distance) | bearing/(2*pi), min(distance, 200)/200 |

When a resource/entity type has no remembered entries: bearing=0.0, distance=1.0 (max distance signal).

**Bearing calculation:** `atan2(target_y - player_y, target_x - player_x)`, mapped to `[0, 2*pi)` then divided by `2*pi` → `[0, 1)`.

**Distance calculation:** Euclidean `sqrt(dx^2 + dy^2)`, clipped at 200, divided by 200 → `[0, 1]`.

### Integration Details

**`fleet/factorio/state_encoder.py` changes:**

```python
# Module constant change
_SPATIAL_FEATURE_DIM = 16
_BASE_FEATURE_DIM = 64  # rename from _FEATURE_DIM

class StateEncoder:
    def __init__(self, phase=1, grid_size=64, spatial_memory=None):
        self._spatial_memory = spatial_memory
        # feature_dim is now dynamic

    @property
    def feature_dim(self) -> int:
        if self._spatial_memory is not None:
            return _BASE_FEATURE_DIM + _SPATIAL_FEATURE_DIM  # 80
        return _BASE_FEATURE_DIM  # 64

    def encode(self, state, metrics=None):
        grid = ...  # unchanged
        features = ...  # existing 64 floats
        if self._spatial_memory is not None:
            px = state.player_position.get("x", 0)
            py = state.player_position.get("y", 0)
            features.extend(self._spatial_memory.get_features(px, py))
        return grid, features
```

**`fleet/factorio/ml_policy.py` changes:**

The `_Trunk` module currently has `nn.Linear(192, 256)` where 192 = 128 (grid) + 64 (features). With spatial memory, features become 80, so context encoder output changes.

```python
# FactorioPolicy.__init__ already accepts feature_dim parameter.
# _FeatureEncoder output is always 64 (internal MLP dim).
# BUT: if feature_dim=80, _FeatureEncoder input changes from 64→80.
# _FeatureEncoder output stays 64 (its hidden dim), so _Trunk stays 192.
```

Actually — `_FeatureEncoder` is an MLP that maps `feature_dim → 64`. So:
- Input: 80 (from encoder) → `_FeatureEncoder(80 → 64)` → 64
- `_Trunk` input: 128 (grid) + 64 (features) = 192 — **unchanged**

The only change in ml_policy.py: pass `feature_dim=80` when constructing `FactorioPolicy` in bridge.py.

**`fleet/factorio/bridge.py` changes (FactorioBridge.__init__):**

```python
# After existing encoder creation:
self._spatial_memory = SpatialMemory()
self._encoder = StateEncoder(
    phase=config.current_phase,
    spatial_memory=self._spatial_memory,  # NEW
)
```

**`fleet/factorio/bridge.py` changes (ml_tick):**

```python
# After state fetch, before encode:
self._spatial_memory.update_from_state(state, state.tick)

# encode() already uses self._spatial_memory internally
grid, features = self._encoder.encode(state, raw_metrics)
```

**`fleet/factorio/bridge.py` changes (after episode reset):**

```python
# In the run() method or wherever reset() is awaited:
await self._episode_mgr.reset()
# Survey and update spatial memory
try:
    survey_raw = await self.rcon.command(SURVEY_LUA)
    survey_data = json.loads(survey_raw)
    self._spatial_memory.update_from_survey(survey_data)
    self._spatial_memory.clear_entities_in_radius((0, 0), 200)
except Exception:
    log.warning("Post-reset survey failed", exc_info=True)
```

### File Changes

| File | Change | Details |
|------|--------|---------|
| **New:** `fleet/factorio/spatial_memory.py` | SpatialMemory class | Entries, upsert, removal, nearest queries, get_features |
| **Modify:** `fleet/factorio/state_encoder.py` | Accept spatial_memory param | Dynamic feature_dim (64 or 80), extend encode() output |
| **Modify:** `fleet/factorio/bridge.py` | Create + wire SpatialMemory | Init in __init__, update in ml_tick, survey after reset |
| **Modify:** `fleet/factorio/ml_policy.py` | No structural change | feature_dim=80 passed from bridge, _FeatureEncoder handles it |
| **Modify:** `fleet/factorio/episode_manager.py` | No change | Survey lives in bridge.py, not episode_manager |
| **New:** `tests/factorio/test_spatial_memory.py` | Unit tests | All SpatialMemory methods |

### Error Handling

- `nearest_resource` / `nearest_entity_by_name`: return `None` when no entries exist → `get_features` maps to `(0.0, 1.0)`
- `update_from_state`: skip entries with missing position data (malformed RCON response)
- `update_from_survey`: wrapped in try/except in bridge.py, logs warning on failure, memory just stays as-is
- Bearing math: `atan2` handles all quadrants correctly; normalize negative angles by adding `2*pi`
- Distance math: `sqrt` of zero (same position) → distance=0.0, normalized to 0.0

### Testing

1. **SpatialMemory unit tests** (`tests/factorio/test_spatial_memory.py`):
   - `test_upsert_resource` — add, update amount, verify key
   - `test_upsert_entity` — add by unit_number
   - `test_remove_entity` — entity in local grid disappears
   - `test_nearest_resource` — bearing and distance with known positions
   - `test_nearest_resource_none` — no entries returns None
   - `test_nearest_resource_same_position` — distance = 0
   - `test_resource_summary` — correct counts per type
   - `test_entity_summary` — correct counts per type
   - `test_get_features_length` — exactly 16 floats
   - `test_get_features_values` — verify bearing/distance with known memory
   - `test_get_features_empty` — all distances = 1.0 when empty
   - `test_clear_entities_in_radius` — clears structures, keeps distant ones
   - `test_update_from_survey` — bulk resource update

2. **State encoder test** (`tests/factorio/test_state_encoder_spatial.py`):
   - Encoder with spatial_memory returns 80-dim features
   - Encoder without spatial_memory returns 64-dim features
   - feature_dim property reflects correctly

3. **Policy dimension test** (in existing test file or new):
   - FactorioPolicy(feature_dim=80) constructs without error
   - Forward pass with (1, 4, 64, 64) grid + (1, 80) features succeeds

### Future Extensions (not in this iteration)

- **Memory grid channels** — second low-res grid showing remembered resources beyond local view
- **Staleness decay** — reduce confidence in entries not seen for many ticks
- **Disk persistence** — save/load memory across bridge restarts
- **Terrain layer** — water, cliffs, trees for pathfinding
- **Rich entity state** — recipe, crafting status, belt contents per remembered entity
