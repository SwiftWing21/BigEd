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
- **Feature injection via extra dims** — extend the 64-dim feature vector to ~80 dims with bearing/distance to nearest resources and infrastructure. No grid architecture change yet (second grid planned for later).
- **Free per-tick updates** — piggyback on existing `get_state` data, zero extra RCON calls
- **Episode-start survey** — one RCON scan (~200 tile radius) on reset to fill in the map

## Architecture

### SpatialMemory Class (`fleet/factorio/spatial_memory.py`)

```python
class SpatialMemory:
    """Sparse persistent map of the Factorio world."""

    # Two stores:
    #   resources: dict[str, ResourceEntry]  — keyed by "{name}_{x}_{y}"
    #   entities:  dict[int, EntityEntry]    — keyed by unit_number

    def update_from_state(self, state: GameState, current_tick: int) -> None:
        """Upsert resources + entities from the current get_state response.
        Also detect removed entities (in local grid but no longer in state)."""

    def update_from_survey(self, survey_data: list[dict]) -> None:
        """Bulk-update from wide-area RCON scan (episode start)."""

    def clear_entities_in_radius(self, center: tuple, radius: float) -> None:
        """Called on episode reset — clears built structures that were destroyed."""

    def nearest_resource(self, player_pos: tuple, resource_name: str) -> tuple[float, float] | None:
        """Returns (bearing_radians, distance_tiles) to nearest patch of given type.
        Returns None if no patches of that type are remembered."""

    def nearest_entity(self, player_pos: tuple, entity_type: str) -> tuple[float, float] | None:
        """Returns (bearing, distance) to nearest built entity of given type."""

    def resource_summary(self) -> dict[str, int]:
        """Returns {resource_name: patch_count} for all remembered resources."""

    def entity_summary(self) -> dict[str, int]:
        """Returns {entity_name: count} for all remembered entities."""

    def get_features(self, player_pos: tuple) -> list[float]:
        """Returns ~16 floats for injection into state encoder feature vector.
        See Feature Injection section below."""
```

### Data Entries

```python
@dataclass
class ResourceEntry:
    name: str           # "iron-ore", "copper-ore", "coal", "stone"
    x: int              # world x (integer)
    y: int              # world y (integer)
    amount: int         # last known ore amount
    last_seen_tick: int  # game tick when last observed

@dataclass
class EntityEntry:
    name: str           # "stone-furnace", "burner-mining-drill", etc.
    x: float            # world x
    y: float            # world y
    entity_type: str    # "furnace", "mining-drill", etc.
    unit_number: int    # Factorio unique ID
    last_seen_tick: int
```

### Update Flow

**Every tick (free — piggyback on get_state):**
1. For each entity in `state.entities`: upsert into `entities` store
2. For each resource in `state.resource_positions`: upsert into `resources` store
3. For entities previously in local grid range (±32 tiles of player) that are NOT in current state: mark as removed from `entities` store
4. ~0.1ms overhead per tick (dict lookups only)

**Episode reset (one extra RCON call):**
1. After soft_reset completes, send wide-area resource scan:
   ```lua
   /c local s=game.get_surface("nauvis"); local out={};
   for _,r in pairs(s.find_entities_filtered{type="resource", position={0,0}, radius=200}) do
   table.insert(out, {name=r.name, x=math.floor(r.position.x), y=math.floor(r.position.y), amount=r.amount})
   end; rcon.print(game.helpers.table_to_json(out))
   ```
2. Call `memory.update_from_survey(parsed_json)`
3. Call `memory.clear_entities_in_radius((0,0), 200)` since soft_reset destroyed structures

**Persistence:**
- Lives in bridge process memory (Python dict)
- Persists across episodes within a session
- Starts empty on bridge restart, rebuilds within first episode
- No disk serialization needed

### Feature Injection

Extend `StateEncoder.encode()` output from 64 to 80 dims. New features (16 floats):

| Index | Feature | Normalization |
|-------|---------|---------------|
| 64-65 | Nearest iron-ore (bearing, distance) | bearing/(2*pi), distance/200 clipped [0,1] |
| 66-67 | Nearest copper-ore (bearing, distance) | same |
| 68-69 | Nearest coal (bearing, distance) | same |
| 70-71 | Nearest stone (bearing, distance) | same |
| 72-75 | Remembered resource patch counts (iron, copper, coal, stone) | count/100 clipped [0,1] |
| 76-77 | Total built furnaces, drills | count/20 clipped [0,1] |
| 78-79 | Nearest furnace (bearing, distance) | bearing/(2*pi), distance/200 |

When a resource type has no remembered patches, bearing=0, distance=1.0 (max distance signal).

### File Changes

| File | Change |
|------|--------|
| **New:** `fleet/factorio/spatial_memory.py` | SpatialMemory class, entries, queries, feature generation |
| **Modify:** `fleet/factorio/state_encoder.py` | Accept optional SpatialMemory, append 16 features, update `feature_dim` property |
| **Modify:** `fleet/factorio/bridge.py` | Create SpatialMemory instance, call `update_from_state` each tick, pass to encoder |
| **Modify:** `fleet/factorio/episode_manager.py` | Add wide-area RCON survey after soft_reset, return data for memory update |
| **Modify:** `fleet/factorio/ml_policy.py` | Update feature_dim default from 64 to 80 |
| **New:** `tests/factorio/test_spatial_memory.py` | Unit tests for all SpatialMemory methods |

### Testing

1. **SpatialMemory unit tests:**
   - `test_upsert_resource` — add, update amount, verify key structure
   - `test_upsert_entity` — add, verify by unit_number
   - `test_remove_entity` — entity disappears from local grid
   - `test_nearest_resource` — bearing and distance math with known positions
   - `test_nearest_resource_none` — no entries returns None
   - `test_resource_summary` — correct counts per type
   - `test_entity_summary` — correct counts per type
   - `test_get_features` — verify 16-dim vector with known memory state
   - `test_clear_entities_in_radius` — episode reset clears structures

2. **State encoder integration test:**
   - Mock SpatialMemory with known data
   - Verify encoder output is 80 dims (not 64)
   - Verify feature values at indices 64-79 match expected

3. **Policy dimension test:**
   - Verify FactorioPolicy accepts feature_dim=80 without error
   - Forward pass with (1, 4, 64, 64) grid + (1, 80) features succeeds

### Future Extensions (not in this iteration)

- **Memory grid channels** — second (2, 128, 128) low-res grid showing remembered resources beyond local view, fed as extra channels to the policy CNN
- **Staleness decay** — reduce confidence in entries not seen for many ticks
- **Disk persistence** — save/load memory across bridge restarts
- **Terrain layer** — water, cliffs, trees for pathfinding
- **Rich entity state** — recipe, crafting status, belt contents per remembered entity
