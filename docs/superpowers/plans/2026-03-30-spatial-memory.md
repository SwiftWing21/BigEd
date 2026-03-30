# Spatial Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Factorio RL agent a persistent spatial memory so it remembers resource locations and built infrastructure beyond its 64x64 local grid.

**Architecture:** A `SpatialMemory` class (sparse dict) stores resource patches and entities seen via RCON. Updated every tick from existing `get_state` data (free) plus a wide-area survey on episode reset. Injects 16 extra features into the state encoder (bearing/distance to nearest resources and infrastructure), extending feature_dim from 64 to 80. The `_FeatureEncoder` MLP absorbs the dimension change — `_Trunk` stays at 192.

**Tech Stack:** Python 3.14, numpy, torch, pytest

**Spec:** `docs/superpowers/specs/2026-03-30-spatial-memory-design.md`

---

## File Map

| File | Role | Action |
|------|------|--------|
| `fleet/factorio/spatial_memory.py` | SpatialMemory class — entries, upsert, queries, feature generation | Create |
| `tests/factorio/test_spatial_memory.py` | Unit tests for all SpatialMemory methods | Create |
| `fleet/factorio/state_encoder.py` | Accept optional SpatialMemory, dynamic feature_dim, extend encode() | Modify |
| `tests/factorio/test_state_encoder_spatial.py` | Verify encoder dim changes with/without spatial memory | Create |
| `fleet/factorio/bridge.py` | Create SpatialMemory, wire into ml_tick + post-reset survey | Modify |

**No changes to `ml_policy.py`** — it already accepts `feature_dim` as a constructor param. `_FeatureEncoder(80)` produces 64-dim output. `_Trunk(192)` unchanged.

**No changes to `episode_manager.py`** — the survey RCON call lives in `bridge.py` after `reset()` returns.

---

### Task 1: SpatialMemory Core — Data Entries + Upsert + Summary

**Files:**
- Create: `fleet/factorio/spatial_memory.py`
- Create: `tests/factorio/test_spatial_memory.py`

This task builds the data structure and basic operations. No spatial math yet.

- [ ] **Step 1: Write failing tests for ResourceEntry and EntityEntry**

```python
# tests/factorio/test_spatial_memory.py
import pytest
from factorio.spatial_memory import SpatialMemory, ResourceEntry, EntityEntry

def test_upsert_resource():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 5, 50000, tick=100)
    assert "iron-ore_10_5" in mem.resources
    entry = mem.resources["iron-ore_10_5"]
    assert entry.name == "iron-ore"
    assert entry.amount == 50000
    assert entry.last_seen_tick == 100

def test_upsert_resource_update():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 5, 50000, tick=100)
    mem._upsert_resource("iron-ore", 10, 5, 40000, tick=200)
    assert mem.resources["iron-ore_10_5"].amount == 40000
    assert mem.resources["iron-ore_10_5"].last_seen_tick == 200

def test_upsert_entity():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.5, 0.5, unit_number=42, tick=100)
    assert 42 in mem.entities
    assert mem.entities[42].name == "stone-furnace"

def test_remove_entity():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.5, 0.5, unit_number=42, tick=100)
    mem.remove_entity(42)
    assert 42 not in mem.entities

def test_resource_summary():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 5, 50000, tick=100)
    mem._upsert_resource("iron-ore", 12, 5, 30000, tick=100)
    mem._upsert_resource("coal", 5, 15, 20000, tick=100)
    summary = mem.resource_summary()
    assert summary["iron-ore"] == 2
    assert summary["coal"] == 1
    assert summary.get("copper-ore", 0) == 0

def test_entity_summary():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.5, 0.5, unit_number=42, tick=100)
    mem._upsert_entity("stone-furnace", 10.5, 0.5, unit_number=43, tick=100)
    mem._upsert_entity("burner-mining-drill", 3.0, 0.0, unit_number=44, tick=100)
    summary = mem.entity_summary()
    assert summary["stone-furnace"] == 2
    assert summary["burner-mining-drill"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_spatial_memory.py -v
```
Expected: ImportError — `spatial_memory` module doesn't exist yet.

- [ ] **Step 3: Implement SpatialMemory core**

```python
# fleet/factorio/spatial_memory.py
"""Persistent spatial memory for the Factorio RL agent."""
import math
from dataclasses import dataclass

__all__ = ["SpatialMemory", "ResourceEntry", "EntityEntry"]


@dataclass
class ResourceEntry:
    name: str
    x: int
    y: int
    amount: int
    last_seen_tick: int


@dataclass
class EntityEntry:
    name: str
    x: float
    y: float
    unit_number: int
    last_seen_tick: int


class SpatialMemory:
    """Sparse persistent map of the Factorio world."""

    def __init__(self):
        self.resources: dict[str, ResourceEntry] = {}
        self.entities: dict[int, EntityEntry] = {}
        self._last_local_entity_ids: set[int] = set()

    def _upsert_resource(self, name: str, x: int, y: int, amount: int, tick: int) -> None:
        key = f"{name}_{x}_{y}"
        self.resources[key] = ResourceEntry(name=name, x=x, y=y, amount=amount, last_seen_tick=tick)

    def _upsert_entity(self, name: str, x: float, y: float, unit_number: int, tick: int) -> None:
        self.entities[unit_number] = EntityEntry(name=name, x=x, y=y, unit_number=unit_number, last_seen_tick=tick)

    def remove_entity(self, unit_number: int) -> None:
        self.entities.pop(unit_number, None)

    def resource_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.resources.values():
            counts[entry.name] = counts.get(entry.name, 0) + 1
        return counts

    def entity_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entities.values():
            counts[entry.name] = counts.get(entry.name, 0) + 1
        return counts
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_spatial_memory.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/spatial_memory.py tests/factorio/test_spatial_memory.py
git commit -m "feat(factorio): SpatialMemory core — entries, upsert, summary"
```

---

### Task 2: SpatialMemory — Nearest Queries + Bearing/Distance Math

**Files:**
- Modify: `fleet/factorio/spatial_memory.py`
- Modify: `tests/factorio/test_spatial_memory.py`

Adds spatial query methods with bearing/distance calculations.

- [ ] **Step 1: Write failing tests for nearest queries**

```python
# Append to tests/factorio/test_spatial_memory.py
import math

def test_nearest_resource_basic():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 0, 50000, tick=100)
    result = mem.nearest_resource(0.0, 0.0, "iron-ore")
    assert result is not None
    bearing, distance = result
    assert abs(distance - 10.0) < 0.01
    assert abs(bearing - 0.0) < 0.01  # due east = 0 radians

def test_nearest_resource_picks_closest():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 100, 0, 50000, tick=100)
    mem._upsert_resource("iron-ore", 5, 0, 30000, tick=100)
    _, distance = mem.nearest_resource(0.0, 0.0, "iron-ore")
    assert abs(distance - 5.0) < 0.01

def test_nearest_resource_none():
    mem = SpatialMemory()
    assert mem.nearest_resource(0.0, 0.0, "iron-ore") is None

def test_nearest_resource_same_position():
    mem = SpatialMemory()
    mem._upsert_resource("coal", 5, 5, 10000, tick=100)
    _, distance = mem.nearest_resource(5.0, 5.0, "coal")
    assert distance == 0.0

def test_nearest_resource_bearing_north():
    mem = SpatialMemory()
    mem._upsert_resource("coal", 0, -10, 10000, tick=100)  # north (negative y)
    bearing, _ = mem.nearest_resource(0.0, 0.0, "coal")
    # atan2(-10, 0) = -pi/2 → normalized to 3*pi/2
    assert abs(bearing - 3 * math.pi / 2) < 0.01

def test_nearest_entity_by_name():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.0, 0.0, unit_number=42, tick=100)
    result = mem.nearest_entity_by_name(0.0, 0.0, "stone-furnace")
    assert result is not None
    _, distance = result
    assert abs(distance - 8.0) < 0.01

def test_nearest_entity_by_name_none():
    mem = SpatialMemory()
    assert mem.nearest_entity_by_name(0.0, 0.0, "stone-furnace") is None
```

- [ ] **Step 2: Run tests to verify new tests fail**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_spatial_memory.py -v -k "nearest"
```
Expected: FAIL — methods not implemented yet.

- [ ] **Step 3: Implement nearest queries**

Add to `SpatialMemory` in `fleet/factorio/spatial_memory.py`:

```python
    @staticmethod
    def _bearing_distance(px: float, py: float, tx: float, ty: float) -> tuple[float, float]:
        dx = tx - px
        dy = ty - py
        distance = math.sqrt(dx * dx + dy * dy)
        bearing = math.atan2(dy, dx)
        if bearing < 0:
            bearing += 2 * math.pi
        return bearing, distance

    def nearest_resource(self, px: float, py: float, resource_name: str) -> tuple[float, float] | None:
        best = None
        best_dist = float("inf")
        for entry in self.resources.values():
            if entry.name != resource_name:
                continue
            _, dist = self._bearing_distance(px, py, entry.x, entry.y)
            if dist < best_dist:
                best_dist = dist
                best = entry
        if best is None:
            return None
        return self._bearing_distance(px, py, best.x, best.y)

    def nearest_entity_by_name(self, px: float, py: float, name: str) -> tuple[float, float] | None:
        best = None
        best_dist = float("inf")
        for entry in self.entities.values():
            if entry.name != name:
                continue
            _, dist = self._bearing_distance(px, py, entry.x, entry.y)
            if dist < best_dist:
                best_dist = dist
                best = entry
        if best is None:
            return None
        return self._bearing_distance(px, py, best.x, best.y)
```

- [ ] **Step 4: Run tests**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_spatial_memory.py -v
```
Expected: All tests PASS (original 6 + 7 new = 13).

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/spatial_memory.py tests/factorio/test_spatial_memory.py
git commit -m "feat(factorio): SpatialMemory nearest queries with bearing/distance"
```

---

### Task 3: SpatialMemory — get_features + update_from_state + update_from_survey

**Files:**
- Modify: `fleet/factorio/spatial_memory.py`
- Modify: `tests/factorio/test_spatial_memory.py`

Adds the 16-float feature vector, state ingestion, and survey bulk update.

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/factorio/test_spatial_memory.py
def test_get_features_length():
    mem = SpatialMemory()
    features = mem.get_features(0.0, 0.0)
    assert len(features) == 16

def test_get_features_empty_defaults():
    mem = SpatialMemory()
    features = mem.get_features(0.0, 0.0)
    # No resources → all distances = 1.0, bearings = 0.0
    # Indices 0,2,4,6 = bearings (0.0), 1,3,5,7 = distances (1.0)
    assert features[1] == 1.0  # iron distance
    assert features[3] == 1.0  # copper distance
    assert features[0] == 0.0  # iron bearing

def test_get_features_with_resources():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 100, 0, 50000, tick=1)
    features = mem.get_features(0.0, 0.0)
    # iron bearing = 0.0 (due east), distance = 100/200 = 0.5
    assert abs(features[0] - 0.0) < 0.01  # bearing normalized
    assert abs(features[1] - 0.5) < 0.01  # distance normalized

def test_get_features_clipping():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 500, 0, 50000, tick=1)
    features = mem.get_features(0.0, 0.0)
    assert features[1] == 1.0  # 500 tiles > 200 cap → clipped to 1.0

def test_clear_entities_in_radius():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 5.0, 5.0, unit_number=1, tick=100)
    mem._upsert_entity("stone-furnace", 500.0, 500.0, unit_number=2, tick=100)
    mem.clear_entities_in_radius((0.0, 0.0), 200.0)
    assert 1 not in mem.entities  # within radius, cleared
    assert 2 in mem.entities      # outside radius, kept

def test_update_from_survey():
    mem = SpatialMemory()
    survey = [
        {"name": "iron-ore", "x": 10, "y": 5, "amount": 99000},
        {"name": "coal", "x": -20, "y": 3, "amount": 50000},
    ]
    mem.update_from_survey(survey)
    assert "iron-ore_10_5" in mem.resources
    assert "coal_-20_3" in mem.resources
    assert mem.resources["iron-ore_10_5"].amount == 99000
```

- [ ] **Step 2: Run tests to verify new tests fail**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_spatial_memory.py -v -k "get_features or clear or survey"
```

- [ ] **Step 3: Implement get_features, clear, survey, and update_from_state**

Add to `SpatialMemory` in `fleet/factorio/spatial_memory.py`:

```python
    _RESOURCE_TYPES = ["iron-ore", "copper-ore", "coal", "stone"]
    _DISTANCE_CAP = 200.0
    _RESOURCE_COUNT_CAP = 100.0
    _ENTITY_COUNT_CAP = 20.0

    def get_features(self, px: float, py: float) -> list[float]:
        """Return 16 normalized floats for state encoder injection."""
        features: list[float] = []

        # Bearing/distance to nearest of each resource type (8 floats)
        for rtype in self._RESOURCE_TYPES:
            result = self.nearest_resource(px, py, rtype)
            if result is None:
                features.extend([0.0, 1.0])
            else:
                bearing, distance = result
                features.append(bearing / (2 * math.pi))
                features.append(min(distance, self._DISTANCE_CAP) / self._DISTANCE_CAP)

        # Resource patch counts (4 floats)
        summary = self.resource_summary()
        for rtype in self._RESOURCE_TYPES:
            features.append(min(summary.get(rtype, 0), self._RESOURCE_COUNT_CAP) / self._RESOURCE_COUNT_CAP)

        # Entity counts: furnaces, drills (2 floats)
        esummary = self.entity_summary()
        features.append(min(esummary.get("stone-furnace", 0), self._ENTITY_COUNT_CAP) / self._ENTITY_COUNT_CAP)
        features.append(min(esummary.get("burner-mining-drill", 0), self._ENTITY_COUNT_CAP) / self._ENTITY_COUNT_CAP)

        # Nearest furnace bearing/distance (2 floats)
        furnace = self.nearest_entity_by_name(px, py, "stone-furnace")
        if furnace is None:
            features.extend([0.0, 1.0])
        else:
            bearing, distance = furnace
            features.append(bearing / (2 * math.pi))
            features.append(min(distance, self._DISTANCE_CAP) / self._DISTANCE_CAP)

        return features

    def update_from_survey(self, survey_data: list[dict]) -> None:
        """Bulk-update resources from wide-area RCON scan."""
        for entry in survey_data:
            name = entry.get("name", "")
            x = int(entry.get("x", 0))
            y = int(entry.get("y", 0))
            amount = int(entry.get("amount", 0))
            if name:
                self._upsert_resource(name, x, y, amount, tick=0)

    def clear_entities_in_radius(self, center: tuple[float, float], radius: float) -> None:
        """Remove built entities within radius of center (episode reset)."""
        cx, cy = center
        to_remove = []
        for uid, entry in self.entities.items():
            dx = entry.x - cx
            dy = entry.y - cy
            if math.sqrt(dx * dx + dy * dy) <= radius:
                to_remove.append(uid)
        for uid in to_remove:
            del self.entities[uid]

    def update_from_state(self, state, current_tick: int) -> None:
        """Upsert from get_state response. Detect removed local entities."""
        # Resources
        for r in getattr(state, "resource_positions", []):
            name = r.get("name", "")
            if not name:
                continue
            x = int(r.get("x", 0))
            y = int(r.get("y", 0))
            amount = int(r.get("amount", 0))
            self._upsert_resource(name, x, y, amount, tick=current_tick)

        # Entities
        current_ids = set()
        for e in getattr(state, "entities", []):
            uid = getattr(e, "unit_number", 0) if hasattr(e, "unit_number") else e.get("unit_number", 0)
            if not uid:
                continue
            name = getattr(e, "name", "") if hasattr(e, "name") else e.get("name", "")
            pos = getattr(e, "position", {}) if hasattr(e, "position") else e.get("position", {})
            x = pos.get("x", 0.0) if isinstance(pos, dict) else 0.0
            y = pos.get("y", 0.0) if isinstance(pos, dict) else 0.0
            self._upsert_entity(name, x, y, uid, tick=current_tick)
            current_ids.add(uid)

        # Detect removed entities (were in local grid last tick, gone now)
        removed = self._last_local_entity_ids - current_ids
        for uid in removed:
            self.remove_entity(uid)
        self._last_local_entity_ids = current_ids
```

- [ ] **Step 4: Run all tests**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_spatial_memory.py -v
```
Expected: All 19 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/spatial_memory.py tests/factorio/test_spatial_memory.py
git commit -m "feat(factorio): SpatialMemory features, survey, state updates"
```

---

### Task 4: StateEncoder Integration — Dynamic feature_dim

**Files:**
- Modify: `fleet/factorio/state_encoder.py`
- Create: `tests/factorio/test_state_encoder_spatial.py`

Wire SpatialMemory into the encoder so feature_dim becomes 80 when memory is attached.

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_state_encoder_spatial.py
import sys
sys.path.insert(0, "fleet")
import pytest
from factorio.state_encoder import StateEncoder
from factorio.spatial_memory import SpatialMemory
from factorio.state_parser import GameState

def test_feature_dim_without_memory():
    enc = StateEncoder(phase=1)
    assert enc.feature_dim == 64

def test_feature_dim_with_memory():
    mem = SpatialMemory()
    enc = StateEncoder(phase=1, spatial_memory=mem)
    assert enc.feature_dim == 80

def test_encode_output_shape_with_memory():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 0, 50000, tick=1)
    enc = StateEncoder(phase=1, spatial_memory=mem)
    state = GameState(player_position={"x": 0, "y": 0}, player_alive=True)
    grid, features = enc.encode(state)
    assert grid.shape == (4, 64, 64)
    assert features.shape == (80,)

def test_encode_output_shape_without_memory():
    enc = StateEncoder(phase=1)
    state = GameState(player_position={"x": 0, "y": 0}, player_alive=True)
    grid, features = enc.encode(state)
    assert features.shape == (64,)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_state_encoder_spatial.py -v
```
Expected: FAIL — StateEncoder doesn't accept `spatial_memory` param yet.

- [ ] **Step 3: Modify StateEncoder**

In `fleet/factorio/state_encoder.py`:

1. Add module constants:
```python
_BASE_FEATURE_DIM = 64
_SPATIAL_FEATURE_DIM = 16
```

2. Change `_FEATURE_DIM = 64` to `_FEATURE_DIM = _BASE_FEATURE_DIM` (alias for backward compat).

3. Update `__init__` signature:
```python
def __init__(
    self,
    phase: int = 1,
    grid_size: int = 64,
    lesson_index: int = 0,
    strategy_goal: list[float] | None = None,
    spatial_memory=None,  # SpatialMemory | None
) -> None:
    # ... existing code ...
    self._spatial_memory = spatial_memory
```

4. Update `feature_dim` property:
```python
@property
def feature_dim(self) -> int:
    if self._spatial_memory is not None:
        return _BASE_FEATURE_DIM + _SPATIAL_FEATURE_DIM
    return _BASE_FEATURE_DIM
```

5. Update `encode()` to append spatial features:
```python
def encode(self, state, metrics=None):
    grid = self._encode_grid(state)
    features = self._encode_features(state, metrics)
    if self._spatial_memory is not None:
        px = state.player_position.get("x", 0)
        py = state.player_position.get("y", 0)
        spatial_feats = self._spatial_memory.get_features(px, py)
        features = np.concatenate([features, np.array(spatial_feats, dtype=np.float32)])
    return grid, features
```

- [ ] **Step 4: Run tests**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/test_state_encoder_spatial.py -v
```
Expected: All 4 tests PASS.

- [ ] **Step 5: Run existing factorio tests to confirm no breakage**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/ -v 2>&1 | tail -20
```
Expected: All existing tests still pass (encoder without memory returns 64 dims as before).

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/state_encoder.py tests/factorio/test_state_encoder_spatial.py
git commit -m "feat(factorio): StateEncoder accepts SpatialMemory, dynamic feature_dim"
```

---

### Task 5: Bridge Integration — Wire SpatialMemory into ML tick + Survey

**Files:**
- Modify: `fleet/factorio/bridge.py`

Create SpatialMemory in bridge init, call update_from_state every tick, run post-reset survey.

- [ ] **Step 1: Add SpatialMemory import and creation in `__init__`**

In `fleet/factorio/bridge.py`, inside the `if self.config.mode == "ml":` block (around line 55-64):

After `from factorio.curriculum_manager import CurriculumManager`, add:
```python
from factorio.spatial_memory import SpatialMemory
```

After `self._encoder = StateEncoder(phase=config.current_phase)` (line 64), change to:
```python
self._spatial_memory = SpatialMemory()
self._encoder = StateEncoder(
    phase=config.current_phase,
    spatial_memory=self._spatial_memory,
)
```

- [ ] **Step 2: Add update_from_state call in ml_tick**

In `ml_tick()`, after the state fetch and body check block, before the `# 0b. Hybrid teacher` section, add:

```python
# 0a. Update spatial memory from current state
self._spatial_memory.update_from_state(state, state.tick)
```

- [ ] **Step 3: Add post-reset survey**

Find where `await self._episode_mgr.reset()` is called (in the episode end block around line 490). After it, add:

```python
# Survey wide area for spatial memory
try:
    survey_lua = (
        '/c local s=game.get_surface("nauvis"); local out={}; '
        'for _,r in pairs(s.find_entities_filtered{type="resource", '
        'position={0,0}, radius=200}) do '
        'out[#out+1]={name=r.name, x=math.floor(r.position.x), '
        'y=math.floor(r.position.y), amount=r.amount} end; '
        'rcon.print(game.helpers.table_to_json(out))'
    )
    survey_raw = await self.rcon.command(survey_lua)
    import json as _json
    survey_data = _json.loads(survey_raw)
    self._spatial_memory.update_from_survey(survey_data)
    self._spatial_memory.clear_entities_in_radius((0, 0), 200)
    log.info("Spatial memory survey: %d resources loaded", len(survey_data))
except Exception:
    log.warning("Post-reset spatial survey failed", exc_info=True)
```

- [ ] **Step 4: Verify bridge imports and syntax**

```bash
cd c:/Users/max/Projects/Education && python -c "import ast; ast.parse(open('fleet/factorio/bridge.py').read()); print('Syntax OK')"
```

- [ ] **Step 5: Run smoke tests**

```bash
cd c:/Users/max/Projects/Education && python fleet/smoke_test.py --fast 2>&1 | tail -5
```
Expected: 51/51 pass (bridge isn't started by smoke tests).

- [ ] **Step 6: Run all factorio tests**

```bash
cd c:/Users/max/Projects/Education && python -m pytest tests/factorio/ -v 2>&1 | tail -20
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "feat(factorio): wire SpatialMemory into bridge — tick updates + reset survey"
```
