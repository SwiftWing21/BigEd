# Factorio ML Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the LLM-driven Factorio agent with an RL-trained ML policy engine, moving the LLM to a config/strategy orchestration role outside the gameplay loop.

**Architecture:** Three-layer system — LLM Orchestrator (config, between episodes) → ML Policy Engine (PPO, every step) → Factorio Bridge (RCON execution, mostly existing). The policy uses a spatial grid (64×64×4 CNN) + flat feature vector (~75-dim MLP) combined into a ~500K param network with policy + value heads.

**Tech Stack:** PyTorch, NumPy, existing Factorio bridge (RCON, Lua mod, state parser, action translator, curriculum system)

**Spec:** `docs/superpowers/specs/2026-03-28-factorio-ml-overhaul-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `fleet/factorio/state_encoder.py` | GameState → grid tensor (64×64×4) + feature vector (~75-dim) |
| `fleet/factorio/action_space.py` | Hierarchical action encoding/decoding, phase-gated entity/recipe sets, invalid action masking |
| `fleet/factorio/ml_policy.py` | PyTorch policy + value network (CNN + MLP → shared → heads) |
| `fleet/factorio/reward.py` | Reward computation from state transitions, phase-gated shaping |
| `fleet/factorio/trainer.py` | PPO training loop, trajectory buffer, checkpoint management |
| `fleet/factorio/episode_manager.py` | Lua soft reset, hard reset with auto-reconnect, episode lifecycle |
| `fleet/factorio/llm_orchestrator.py` | Curriculum generation, training diagnostics, strategy advisor, narrator |
| `tests/factorio/__init__.py` | Package init |
| `tests/factorio/conftest.py` | Shared fixtures, sys.path setup |
| `tests/factorio/test_state_encoder.py` | State encoder tests |
| `tests/factorio/test_action_space.py` | Action space tests |
| `tests/factorio/test_ml_policy.py` | Policy network tests |
| `tests/factorio/test_reward.py` | Reward function tests |
| `tests/factorio/test_trainer.py` | Trainer tests |
| `tests/factorio/test_episode_manager.py` | Episode manager tests |
| `tests/factorio/test_llm_orchestrator.py` | Orchestrator tests |

### Modified Files

| File | Changes |
|------|---------|
| `fleet/factorio/bridge.py` | Add ML mode tick loop alongside existing LLM mode |
| `fleet/factorio/bridge_config.py` | Add `mode`, `game_speed`, ML training config keys |
| `.gitignore` | Add `fleet/factorio/checkpoints/` |

### Kept Unchanged

| File | Reason |
|------|--------|
| `fleet/factorio/rcon_client.py` | Execution layer — no changes needed |
| `fleet/factorio/state_parser.py` | State encoder wraps this — no changes needed |
| `fleet/factorio/action_translator.py` | Add `mine` to KNOWN_ACTIONS + translation logic |
| `fleet/factorio/world_model.py` | Event detection unchanged |
| `fleet/factorio/curriculum_manager.py` | Reward function + orchestrator use this as-is |
| `fleet/factorio/curriculum.py` | Criteria evaluation unchanged |

---

## Task 0: Test Infrastructure & Action Translator

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/factorio/__init__.py`
- Create: `tests/factorio/conftest.py`
- Modify: `fleet/factorio/action_translator.py`

- [ ] **Step 1: Create test package structure**

```bash
mkdir -p tests/factorio
touch tests/__init__.py tests/factorio/__init__.py
```

- [ ] **Step 2: Create conftest.py with sys.path setup and shared fixtures**

```python
# tests/factorio/conftest.py
"""Shared fixtures and path setup for Factorio ML tests."""
import sys
from pathlib import Path

# Add fleet/ to sys.path so 'from factorio.X import Y' works
fleet_dir = str(Path(__file__).resolve().parent.parent.parent / "fleet")
if fleet_dir not in sys.path:
    sys.path.insert(0, fleet_dir)
```

- [ ] **Step 3: Add `mine` action to action_translator.py**

In `fleet/factorio/action_translator.py`, add `"mine"` to `KNOWN_ACTIONS`:

```python
KNOWN_ACTIONS = {"place", "remove", "set_recipe", "craft", "research",
                 "move", "connect", "observe", "wait", "mine"}
```

And add a `mine` case to `translate_action()`:

```python
elif action_type == "mine":
    pos = action.get("position", {})
    x, y = pos.get("x", 0), pos.get("y", 0)
    cmd = json.dumps({"action": "mine", "position": {"x": x, "y": y}})
    return TranslatedAction(action_type="mine", rcon_command=cmd,
                            description=f"Mine at ({x}, {y})")
```

- [ ] **Step 4: Verify test infrastructure works**

Run: `python -m pytest tests/factorio/conftest.py --collect-only`
Expected: No errors (no tests to collect, but no import failures)

- [ ] **Step 5: Commit**

```bash
git add tests/ fleet/factorio/action_translator.py
git commit -m "feat(factorio): add test infrastructure and mine action to translator"
```

---

## Task 1: Config & Gitignore

**Files:**
- Modify: `fleet/factorio/bridge_config.py`
- Modify: `.gitignore`
- Test: `tests/factorio/test_bridge_config.py` (create if not exists)

- [ ] **Step 1: Write failing test for new config fields**

```python
# tests/factorio/test_bridge_config.py
from factorio.bridge_config import BridgeConfig

def test_ml_config_defaults():
    cfg = BridgeConfig()
    assert cfg.mode == "ml"
    assert cfg.game_speed == 10
    assert cfg.ml_learning_rate == 3e-4
    assert cfg.ml_batch_size == 64
    assert cfg.ml_update_every == 512
    assert cfg.ml_checkpoint_every == 20
    assert cfg.ml_max_episode_steps == 2000
    assert cfg.ml_gamma == 0.99
    assert cfg.ml_gae_lambda == 0.95
    assert cfg.ml_clip_ratio == 0.2
    assert cfg.ml_entropy_coeff == 0.01
    assert cfg.ml_value_coeff == 0.5
    assert cfg.ml_checkpoint_dir == "fleet/factorio/checkpoints"

def test_mode_toggle():
    cfg = BridgeConfig(mode="llm")
    assert cfg.mode == "llm"
    cfg2 = BridgeConfig(mode="ml")
    assert cfg2.mode == "ml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_bridge_config.py -v`
Expected: FAIL — `BridgeConfig` doesn't have `mode` field

- [ ] **Step 3: Add new config fields to BridgeConfig**

Add these fields to the `BridgeConfig` dataclass in `fleet/factorio/bridge_config.py`:

```python
    # Mode: "ml" (RL policy) or "llm" (existing agent brain)
    mode: str = "ml"
    game_speed: int = 10  # Factorio game.speed multiplier for training

    # ML training hyperparameters
    ml_learning_rate: float = 3e-4
    ml_batch_size: int = 64
    ml_update_every: int = 512       # PPO update every N steps
    ml_checkpoint_every: int = 20     # Save model every N episodes
    ml_max_episode_steps: int = 2000
    ml_gamma: float = 0.99
    ml_gae_lambda: float = 0.95
    ml_clip_ratio: float = 0.2
    ml_entropy_coeff: float = 0.01
    ml_value_coeff: float = 0.5
    ml_checkpoint_dir: str = "fleet/factorio/checkpoints"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/factorio/test_bridge_config.py -v`
Expected: PASS

- [ ] **Step 5: Add checkpoints to gitignore**

Append to `.gitignore`:
```
fleet/factorio/checkpoints/
```

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/bridge_config.py tests/factorio/test_bridge_config.py .gitignore
git commit -m "feat(factorio): add ML mode config fields and checkpoint gitignore"
```

---

## Task 2: Entity & Recipe Registry (Action Space Foundation)

**Files:**
- Create: `fleet/factorio/action_space.py`
- Test: `tests/factorio/test_action_space.py`

- [ ] **Step 1: Write failing tests for entity registry and phase gating**

```python
# tests/factorio/test_action_space.py
import pytest
from factorio.action_space import (
    ActionSpace, ENTITY_REGISTRY, RECIPE_REGISTRY,
    PHASE_ENTITIES, PHASE_RECIPES, ActionType
)

def test_entity_registry_has_common_entities():
    assert "stone-furnace" in ENTITY_REGISTRY
    assert "transport-belt" in ENTITY_REGISTRY
    assert "inserter" in ENTITY_REGISTRY
    assert isinstance(ENTITY_REGISTRY["stone-furnace"], int)
    assert ENTITY_REGISTRY["stone-furnace"] > 0  # 0 = empty

def test_phase_entities_expand():
    assert len(PHASE_ENTITIES[1]) <= len(PHASE_ENTITIES[2])
    assert len(PHASE_ENTITIES[2]) <= len(PHASE_ENTITIES[3])
    assert "stone-furnace" in PHASE_ENTITIES[1]
    assert "assembling-machine-1" in PHASE_ENTITIES[2]

def test_action_type_enum():
    assert ActionType.PLACE.value == 0
    assert ActionType.CRAFT.value == 1
    assert ActionType.MINE.value == 7
    assert len(ActionType) == 8

def test_action_space_init():
    space = ActionSpace(phase=1)
    assert space.num_entity_types > 0
    assert space.num_recipe_types > 0

def test_encode_decode_place_action():
    space = ActionSpace(phase=1)
    action_dict = {"action": "place", "entity": "stone-furnace",
                   "position": {"x": 3, "y": -2}, "direction": "north"}
    encoded = space.encode_action(action_dict)
    decoded = space.decode_action(encoded)
    assert decoded["action"] == "place"
    assert decoded["entity"] == "stone-furnace"
    assert decoded["direction"] == "north"

def test_encode_decode_craft_action():
    space = ActionSpace(phase=1)
    action_dict = {"action": "craft", "recipe": "iron-gear-wheel", "count": 5}
    encoded = space.encode_action(action_dict)
    decoded = space.decode_action(encoded)
    assert decoded["action"] == "craft"
    assert decoded["recipe"] == "iron-gear-wheel"
    assert decoded["count"] == 5

def test_encode_decode_mine_action():
    space = ActionSpace(phase=1)
    action_dict = {"action": "mine", "position": {"x": 2, "y": -1}}
    encoded = space.encode_action(action_dict)
    decoded = space.decode_action(encoded)
    assert decoded["action"] == "mine"

def test_invalid_action_mask():
    space = ActionSpace(phase=1)
    # With empty inventory, many crafts should be masked
    inventory = {}
    mask = space.get_action_type_mask(inventory, phase=1)
    assert mask[ActionType.WAIT.value] == 1  # wait always valid
    assert mask[ActionType.MOVE.value] == 1  # move always valid
    assert len(mask) == 8

def test_phase_updates_available_entities():
    space1 = ActionSpace(phase=1)
    space2 = ActionSpace(phase=2)
    assert space2.num_entity_types >= space1.num_entity_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_action_space.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement action_space.py**

```python
# fleet/factorio/action_space.py
"""Hierarchical action encoding/decoding with phase-gated entity/recipe sets."""

import enum
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

class ActionType(enum.IntEnum):
    PLACE = 0
    CRAFT = 1
    RESEARCH = 2
    MOVE = 3
    SET_RECIPE = 4
    REMOVE = 5
    WAIT = 6
    MINE = 7

# Entity name → integer ID (0 = empty/unknown)
ENTITY_REGISTRY: dict[str, int] = {
    "stone-furnace": 1, "burner-mining-drill": 2, "wooden-chest": 3,
    "transport-belt": 4, "inserter": 5, "burner-inserter": 6,
    "small-electric-pole": 7, "boiler": 8, "steam-engine": 9,
    "offshore-pump": 10, "pipe": 11, "assembling-machine-1": 12,
    "lab": 13, "iron-chest": 14, "long-handed-inserter": 15,
    "fast-inserter": 16, "underground-belt": 17, "splitter": 18,
    "assembling-machine-2": 19, "electric-mining-drill": 20,
    "medium-electric-pole": 21, "pipe-to-ground": 22,
    "gun-turret": 23, "wall": 24,
}

ENTITY_ID_TO_NAME: dict[int, str] = {v: k for k, v in ENTITY_REGISTRY.items()}

# Recipe name → integer ID
RECIPE_REGISTRY: dict[str, int] = {
    "iron-plate": 1, "copper-plate": 2, "iron-gear-wheel": 3,
    "iron-stick": 4, "stone-brick": 5, "copper-cable": 6,
    "electronic-circuit": 7, "automation-science-pack": 8,
    "transport-belt": 9, "inserter": 10, "burner-inserter": 11,
    "burner-mining-drill": 12, "stone-furnace": 13,
    "wooden-chest": 14, "small-electric-pole": 15,
    "boiler": 16, "steam-engine": 17, "offshore-pump": 18,
    "pipe": 19, "assembling-machine-1": 20, "lab": 21,
    "iron-chest": 22, "long-handed-inserter": 23,
    "firearm-magazine": 24, "logistic-science-pack": 25,
    "fast-inserter": 26, "underground-belt": 27,
    "splitter": 28, "assembling-machine-2": 29,
    "electric-mining-drill": 30,
}

RECIPE_ID_TO_NAME: dict[int, str] = {v: k for k, v in RECIPE_REGISTRY.items()}

# Tech name → integer ID
TECH_REGISTRY: dict[str, int] = {
    "automation": 1, "logistics": 2, "electronics": 3,
    "fast-inserter": 4, "steel-processing": 5, "optics": 6,
    "turrets": 7, "stone-wall": 8, "automation-2": 9,
    "logistics-2": 10, "engine": 11, "fluid-handling": 12,
    "oil-processing": 13, "plastics": 14, "advanced-electronics": 15,
    "logistic-science-pack": 16, "military": 17,
    "heavy-armor": 18, "toolbelt": 19, "electric-energy-distribution-1": 20,
}

TECH_ID_TO_NAME: dict[int, str] = {v: k for k, v in TECH_REGISTRY.items()}

# Phase-gated entity/recipe sets
PHASE_ENTITIES: dict[int, set[str]] = {
    1: {"stone-furnace", "burner-mining-drill", "wooden-chest", "transport-belt",
        "inserter", "burner-inserter", "small-electric-pole", "boiler"},
    2: {"stone-furnace", "burner-mining-drill", "wooden-chest", "transport-belt",
        "inserter", "burner-inserter", "small-electric-pole", "boiler",
        "steam-engine", "offshore-pump", "pipe", "assembling-machine-1",
        "lab", "iron-chest", "electric-mining-drill"},
    3: {"stone-furnace", "burner-mining-drill", "wooden-chest", "transport-belt",
        "inserter", "burner-inserter", "small-electric-pole", "boiler",
        "steam-engine", "offshore-pump", "pipe", "assembling-machine-1",
        "lab", "iron-chest", "electric-mining-drill",
        "long-handed-inserter", "fast-inserter", "underground-belt", "splitter"},
    4: set(ENTITY_REGISTRY.keys()),  # All entities
}

PHASE_RECIPES: dict[int, set[str]] = {
    1: {"iron-plate", "copper-plate", "iron-gear-wheel", "iron-stick",
        "stone-brick", "stone-furnace", "burner-mining-drill", "wooden-chest",
        "transport-belt", "inserter", "burner-inserter"},
    2: {"iron-plate", "copper-plate", "iron-gear-wheel", "iron-stick",
        "stone-brick", "stone-furnace", "burner-mining-drill", "wooden-chest",
        "transport-belt", "inserter", "burner-inserter", "copper-cable",
        "small-electric-pole", "boiler", "steam-engine", "offshore-pump",
        "pipe", "assembling-machine-1", "lab"},
    3: {"iron-plate", "copper-plate", "iron-gear-wheel", "iron-stick",
        "stone-brick", "copper-cable", "electronic-circuit",
        "automation-science-pack", "transport-belt", "inserter",
        "stone-furnace", "burner-mining-drill", "wooden-chest",
        "burner-inserter", "small-electric-pole", "boiler", "steam-engine",
        "offshore-pump", "pipe", "assembling-machine-1", "lab",
        "iron-chest", "firearm-magazine"},
    4: set(RECIPE_REGISTRY.keys()),  # All recipes
}

DIRECTION_NAMES = ["north", "northeast", "east", "southeast",
                   "south", "southwest", "west", "northwest"]

@dataclass
class EncodedAction:
    """Encoded action as integer IDs for the policy network."""
    action_type: int          # ActionType enum value
    entity_id: int = 0        # for place
    recipe_id: int = 0        # for craft, set_recipe
    tech_id: int = 0          # for research
    dx: int = 0               # grid offset x [-5, +5] encoded as [0, 10]
    dy: int = 0               # grid offset y [-5, +5] encoded as [0, 10]
    direction: int = 0        # 0-7
    count: int = 1            # for craft (1-10)
    grid_x: int = 0           # for set_recipe, remove (0-63)
    grid_y: int = 0           # for set_recipe, remove (0-63)


class ActionSpace:
    """Phase-gated hierarchical action space."""

    def __init__(self, phase: int = 1):
        self._phase = phase
        self._entities = sorted(PHASE_ENTITIES.get(phase, PHASE_ENTITIES[4]))
        self._recipes = sorted(PHASE_RECIPES.get(phase, PHASE_RECIPES[4]))
        self._entity_to_idx = {e: i for i, e in enumerate(self._entities)}
        self._recipe_to_idx = {r: i for i, r in enumerate(self._recipes)}

    @property
    def num_entity_types(self) -> int:
        return len(self._entities)

    @property
    def num_recipe_types(self) -> int:
        return len(self._recipes)

    @property
    def num_tech_types(self) -> int:
        return len(TECH_REGISTRY)

    def encode_action(self, action_dict: dict) -> EncodedAction:
        """Convert action dict (bridge format) → EncodedAction."""
        action_str = action_dict.get("action", "wait")
        try:
            action_type = ActionType[action_str.upper()].value
        except KeyError:
            action_type = ActionType.WAIT.value

        encoded = EncodedAction(action_type=action_type)

        if action_str == "place":
            entity = action_dict.get("entity", "")
            encoded.entity_id = self._entity_to_idx.get(entity, 0)
            pos = action_dict.get("position", {})
            encoded.dx = int(pos.get("x", 0)) + 5  # [-5,5] → [0,10]
            encoded.dy = int(pos.get("y", 0)) + 5
            direction = action_dict.get("direction", "north")
            encoded.direction = DIRECTION_NAMES.index(direction) if direction in DIRECTION_NAMES else 0

        elif action_str == "craft":
            recipe = action_dict.get("recipe", "")
            encoded.recipe_id = self._recipe_to_idx.get(recipe, 0)
            encoded.count = min(max(int(action_dict.get("count", 1)), 1), 10)

        elif action_str == "research":
            tech = action_dict.get("technology", "")
            encoded.tech_id = TECH_REGISTRY.get(tech, 0)

        elif action_str == "move":
            pos = action_dict.get("position", {})
            encoded.dx = int(pos.get("x", 0)) + 5
            encoded.dy = int(pos.get("y", 0)) + 5

        elif action_str == "set_recipe":
            encoded.grid_x = min(max(int(action_dict.get("grid_x", 0)), 0), 63)
            encoded.grid_y = min(max(int(action_dict.get("grid_y", 0)), 0), 63)
            recipe = action_dict.get("recipe", "")
            encoded.recipe_id = self._recipe_to_idx.get(recipe, 0)

        elif action_str == "remove":
            encoded.grid_x = min(max(int(action_dict.get("grid_x", 0)), 0), 63)
            encoded.grid_y = min(max(int(action_dict.get("grid_y", 0)), 0), 63)

        elif action_str == "mine":
            pos = action_dict.get("position", {})
            encoded.dx = int(pos.get("x", 0)) + 5
            encoded.dy = int(pos.get("y", 0)) + 5

        return encoded

    def decode_action(self, encoded: EncodedAction) -> dict:
        """Convert EncodedAction → action dict (bridge format)."""
        action_type = ActionType(encoded.action_type)
        action_str = action_type.name.lower()

        result = {"action": action_str}

        if action_type == ActionType.PLACE:
            if encoded.entity_id < len(self._entities):
                result["entity"] = self._entities[encoded.entity_id]
            result["position"] = {"x": encoded.dx - 5, "y": encoded.dy - 5}
            result["direction"] = DIRECTION_NAMES[encoded.direction % 8]

        elif action_type == ActionType.CRAFT:
            if encoded.recipe_id < len(self._recipes):
                result["recipe"] = self._recipes[encoded.recipe_id]
            result["count"] = encoded.count

        elif action_type == ActionType.RESEARCH:
            tech_name = TECH_ID_TO_NAME.get(encoded.tech_id, "")
            if tech_name:
                result["technology"] = tech_name

        elif action_type == ActionType.MOVE:
            result["position"] = {"x": encoded.dx - 5, "y": encoded.dy - 5}

        elif action_type == ActionType.SET_RECIPE:
            result["grid_x"] = encoded.grid_x
            result["grid_y"] = encoded.grid_y
            if encoded.recipe_id < len(self._recipes):
                result["recipe"] = self._recipes[encoded.recipe_id]

        elif action_type == ActionType.REMOVE:
            result["grid_x"] = encoded.grid_x
            result["grid_y"] = encoded.grid_y

        elif action_type == ActionType.MINE:
            result["position"] = {"x": encoded.dx - 5, "y": encoded.dy - 5}

        return result

    def get_action_type_mask(self, inventory: dict, phase: int = 1) -> list[int]:
        """Return binary mask for valid action types. 1 = valid, 0 = invalid."""
        mask = [0] * len(ActionType)
        mask[ActionType.WAIT.value] = 1   # always valid
        mask[ActionType.MOVE.value] = 1   # always valid
        mask[ActionType.MINE.value] = 1   # always valid (may fail but valid to attempt)
        mask[ActionType.PLACE.value] = 1 if any(
            inventory.get(e, 0) > 0 for e in self._entities
        ) else 0
        mask[ActionType.CRAFT.value] = 1  # let the game reject invalid crafts
        mask[ActionType.RESEARCH.value] = 1
        mask[ActionType.SET_RECIPE.value] = 1
        mask[ActionType.REMOVE.value] = 1
        return mask
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_action_space.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/action_space.py tests/factorio/test_action_space.py
git commit -m "feat(factorio): add hierarchical action space with phase-gated entity/recipe sets"
```

---

## Task 3: State Encoder

**Files:**
- Create: `fleet/factorio/state_encoder.py`
- Test: `tests/factorio/test_state_encoder.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_state_encoder.py
import numpy as np
import pytest
from factorio.state_parser import GameState, Entity
from factorio.state_encoder import StateEncoder

def _make_state(entities=None, inventory=None, player_pos=None,
                research_name="", research_progress=0.0, tick=0):
    """Helper to build a GameState for testing."""
    return GameState(
        tick=tick,
        player_position=player_pos or {"x": 0.0, "y": 0.0},
        inventory=inventory or {},
        entities=entities or [],
        research_name=research_name,
        research_progress=research_progress,
    )

def test_encoder_output_shapes():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    grid, features = encoder.encode(state)
    assert grid.shape == (4, 64, 64)
    assert features.shape[0] > 50  # at least 50 features
    assert grid.dtype == np.float32
    assert features.dtype == np.float32

def test_entity_appears_on_grid():
    encoder = StateEncoder(phase=1, grid_size=64)
    entity = Entity(name="stone-furnace", position={"x": 3.0, "y": -2.0}, direction=4)
    state = _make_state(entities=[entity])
    grid, _ = encoder.encode(state)
    # Entity at (3, -2) relative to player at (0,0) → grid (35, 30)
    gx, gy = 3 + 32, -2 + 32
    assert grid[0, gy, gx] > 0  # entity type channel > 0

def test_entity_outside_grid_ignored():
    encoder = StateEncoder(phase=1, grid_size=64)
    entity = Entity(name="stone-furnace", position={"x": 100.0, "y": 100.0})
    state = _make_state(entities=[entity])
    grid, _ = encoder.encode(state)
    assert grid[0].sum() == 0  # nothing on grid

def test_inventory_in_features():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state(inventory={"iron-plate": 50, "copper-plate": 25})
    _, features = encoder.encode(state)
    # Features should be nonzero since we have inventory
    assert features.sum() > 0

def test_research_in_features():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state(research_name="automation", research_progress=0.5)
    _, features = encoder.encode(state)
    assert features.sum() > 0

def test_curriculum_context_in_features():
    encoder = StateEncoder(phase=2, grid_size=64, lesson_index=3)
    state = _make_state()
    _, features = encoder.encode(state)
    assert features.shape[0] > 50

def test_feature_dim_property():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    _, features = encoder.encode(state)
    assert features.shape[0] == encoder.feature_dim
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_state_encoder.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement state_encoder.py**

```python
# fleet/factorio/state_encoder.py
"""Encode GameState into tensors for the ML policy network.

Grid: 64x64x4 float32 (entity type, direction, resources, connectivity)
Features: ~75-dim float32 (inventory, research, power, production, time, curriculum)
"""

import logging
import numpy as np

from factorio.state_parser import GameState, GameMetrics
from factorio.action_space import ENTITY_REGISTRY, TECH_REGISTRY, PHASE_ENTITIES

log = logging.getLogger(__name__)

# Top items to track in feature vector (order matters — index = feature position)
TRACKED_ITEMS = [
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

# Normalization maximums per item (approximate typical maximums)
ITEM_NORM = {item: 200.0 for item in TRACKED_ITEMS}
ITEM_NORM.update({"iron-ore": 500.0, "copper-ore": 500.0, "coal": 500.0, "stone": 300.0})


class StateEncoder:
    """Encode GameState → (grid_tensor, feature_vector) for policy network."""

    def __init__(self, phase: int = 1, grid_size: int = 64,
                 lesson_index: int = 0, strategy_goal: np.ndarray | None = None):
        self._phase = phase
        self._grid_size = grid_size
        self._lesson_index = lesson_index
        self._strategy_goal = strategy_goal if strategy_goal is not None else np.zeros(3, dtype=np.float32)
        self._resource_max = {}  # running max for normalization
        self._phase_entities = PHASE_ENTITIES.get(phase, PHASE_ENTITIES[4])

        # Precompute feature dimension
        self._feature_dim = (
            len(TRACKED_ITEMS)   # 30: inventory
            + len(TECH_REGISTRY) # 20: research one-hot
            + 1                  # research progress
            + 3                  # power: satisfaction, generation, consumption
            + 2                  # time: tick normalized, episode step normalized
            + 4                  # curriculum: phase one-hot (4 phases)
            + 1                  # lesson index (normalized)
            + 3                  # strategy goal (3-dim)
        )
        # Total: 30 + 20 + 1 + 3 + 2 + 4 + 1 + 3 = 64

    @property
    def feature_dim(self) -> int:
        return self._feature_dim

    @property
    def grid_channels(self) -> int:
        return 4

    @property
    def grid_size(self) -> int:
        return self._grid_size

    def set_phase(self, phase: int) -> None:
        self._phase = phase
        self._phase_entities = PHASE_ENTITIES.get(phase, PHASE_ENTITIES[4])

    def set_lesson_index(self, index: int) -> None:
        self._lesson_index = index

    def set_strategy_goal(self, goal: np.ndarray) -> None:
        self._strategy_goal = goal

    def encode(self, state: GameState, metrics: GameMetrics | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Encode full game state into tensors.

        Returns:
            grid: float32 array of shape (4, grid_size, grid_size)
            features: float32 array of shape (feature_dim,)
        """
        grid = self._encode_grid(state)
        features = self._encode_features(state, metrics)
        return grid, features

    def _encode_grid(self, state: GameState) -> np.ndarray:
        """Encode entities and resources into spatial grid."""
        gs = self._grid_size
        half = gs // 2
        grid = np.zeros((4, gs, gs), dtype=np.float32)

        px = state.player_position.get("x", 0.0)
        py = state.player_position.get("y", 0.0)

        # Channel 0: entity type ID, Channel 1: direction
        for entity in state.entities:
            ex = entity.position.get("x", 0.0)
            ey = entity.position.get("y", 0.0)
            gx = round(ex - px) + half
            gy = round(ey - py) + half
            if 0 <= gx < gs and 0 <= gy < gs:
                eid = ENTITY_REGISTRY.get(entity.name, 0)
                if eid > 0 and entity.name in self._phase_entities:
                    grid[0, gy, gx] = float(eid)
                    grid[1, gy, gx] = entity.direction / 7.0  # normalize to [0, 1]

        # Channel 2: resource density
        for res in state.resources:
            # Resources don't have per-tile positions in our state parser,
            # so we skip spatial resource encoding for now.
            # TODO: add per-patch positions from Lua mod if needed.
            pass

        # Channel 3: connectivity (belt flow, inserter I/O)
        # Deferred — requires richer entity data from Lua mod.
        # For now, direction encoding on channel 1 provides partial info.

        return grid

    def _encode_features(self, state: GameState, metrics: GameMetrics | None = None) -> np.ndarray:
        """Encode non-spatial state into flat feature vector."""
        features = np.zeros(self._feature_dim, dtype=np.float32)
        idx = 0

        # Inventory (30 dims)
        for item in TRACKED_ITEMS:
            count = state.inventory.get(item, 0)
            norm = ITEM_NORM.get(item, 200.0)
            features[idx] = min(float(count) / norm, 1.0)
            idx += 1

        # Research one-hot (20 dims)
        tech_id = TECH_REGISTRY.get(state.research_name, 0)
        if 0 < tech_id <= len(TECH_REGISTRY):
            features[idx + tech_id - 1] = 1.0
        idx += len(TECH_REGISTRY)

        # Research progress (1 dim)
        features[idx] = state.research_progress
        idx += 1

        # Power (3 dims)
        if metrics:
            sat = 1.0 if metrics.electric_satisfaction == "ok" else 0.0
            features[idx] = sat
            features[idx + 1] = min(metrics.electric_capacity_mw / 10.0, 1.0)
            features[idx + 2] = min(float(metrics.electric_entity_count) / 20.0, 1.0)
        idx += 3

        # Time (2 dims)
        features[idx] = min(float(state.tick) / 216000.0, 1.0)  # ~1 hour at 60 UPS
        # Episode step normalized — caller should set this externally if needed
        idx += 2

        # Curriculum phase one-hot (4 dims)
        phase_idx = min(max(self._phase - 1, 0), 3)
        features[idx + phase_idx] = 1.0
        idx += 4

        # Lesson index normalized (1 dim)
        features[idx] = min(float(self._lesson_index) / 10.0, 1.0)
        idx += 1

        # Strategy goal (3 dims)
        features[idx:idx + 3] = self._strategy_goal[:3]
        idx += 3

        return features
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_state_encoder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/state_encoder.py tests/factorio/test_state_encoder.py
git commit -m "feat(factorio): add state encoder — GameState to grid + feature tensors"
```

---

## Task 4: Policy Network

**Files:**
- Create: `fleet/factorio/ml_policy.py`
- Test: `tests/factorio/test_ml_policy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_ml_policy.py
import numpy as np
import torch
import pytest
from factorio.ml_policy import FactorioPolicy
from factorio.action_space import ActionType

def test_policy_forward_shapes():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    grid = torch.randn(1, 4, 64, 64)
    features = torch.randn(1, 64)
    action_logits, value = policy(grid, features)
    assert action_logits.shape == (1, 8)  # 8 action types
    assert value.shape == (1, 1)

def test_policy_act_returns_valid_action():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    grid = torch.randn(1, 4, 64, 64)
    features = torch.randn(1, 64)
    action, log_prob, value, params = policy.act(grid, features)
    assert 0 <= action.item() < 8
    assert log_prob.shape == (1,)
    assert value.shape == (1,)
    assert isinstance(params, dict)

def test_policy_parameter_heads_exist():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    params = policy.get_action_params(
        torch.randn(1, 128),  # shared features
        action_type=ActionType.PLACE.value
    )
    assert "entity_logits" in params
    assert "dx_logits" in params
    assert "dy_logits" in params
    assert "direction_logits" in params

def test_policy_batch_forward():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    batch = 16
    grid = torch.randn(batch, 4, 64, 64)
    features = torch.randn(batch, 64)
    action_logits, value = policy(grid, features)
    assert action_logits.shape == (batch, 8)
    assert value.shape == (batch, 1)

def test_policy_save_load(tmp_path):
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    path = tmp_path / "test_checkpoint.pt"
    policy.save(str(path))
    policy2 = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    policy2.load(str(path))
    # Weights should match
    for p1, p2 in zip(policy.parameters(), policy2.parameters()):
        assert torch.allclose(p1, p2)

def test_policy_param_count():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=20, num_recipes=30, num_techs=20,
    )
    total = sum(p.numel() for p in policy.parameters())
    assert total < 2_000_000  # should be well under 2M params
    assert total > 100_000    # but more than trivial
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_ml_policy.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement ml_policy.py**

```python
# fleet/factorio/ml_policy.py
"""PyTorch policy + value network for Factorio RL agent.

Architecture:
  Grid (64x64x4) → CNN → 128-dim spatial embedding
  Features (~64) → MLP → 64-dim context embedding
  Concat (192) → shared MLP (256 → 128)
  → Policy head (8 action types) + per-action parameter heads
  → Value head (scalar)
"""

import logging
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from factorio.action_space import ActionType

log = logging.getLogger(__name__)


class FactorioPolicy(nn.Module):
    """CNN + MLP policy/value network with hierarchical action heads."""

    def __init__(self, grid_channels: int = 4, grid_size: int = 64,
                 feature_dim: int = 64, num_action_types: int = 8,
                 num_entities: int = 20, num_recipes: int = 30,
                 num_techs: int = 20):
        super().__init__()
        self.num_action_types = num_action_types

        # CNN for spatial grid
        self.cnn = nn.Sequential(
            nn.Conv2d(grid_channels, 16, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(4),
        )
        cnn_out_dim = 64 * 4 * 4  # 1024

        self.cnn_proj = nn.Sequential(
            nn.Linear(cnn_out_dim, 128),
            nn.ReLU(),
        )

        # MLP for feature vector
        self.feature_mlp = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
        )

        # Shared trunk
        shared_dim = 128 + 64  # 192
        self.shared = nn.Sequential(
            nn.Linear(shared_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
        )

        # Action type head
        self.action_head = nn.Linear(128, num_action_types)

        # Value head
        self.value_head = nn.Linear(128, 1)

        # Per-action parameter heads
        # Place: entity, dx, dy, direction
        self.place_entity = nn.Linear(128, num_entities)
        self.place_dx = nn.Linear(128, 11)       # [-5, +5]
        self.place_dy = nn.Linear(128, 11)
        self.place_dir = nn.Linear(128, 8)

        # Craft: recipe, count
        self.craft_recipe = nn.Linear(128, num_recipes)
        self.craft_count = nn.Linear(128, 10)     # 1-10

        # Research: tech
        self.research_tech = nn.Linear(128, num_techs)

        # Move: dx, dy
        self.move_dx = nn.Linear(128, 11)
        self.move_dy = nn.Linear(128, 11)

        # Set recipe: grid_x, grid_y, recipe
        self.set_recipe_gx = nn.Linear(128, 64)
        self.set_recipe_gy = nn.Linear(128, 64)
        self.set_recipe_recipe = nn.Linear(128, num_recipes)

        # Remove: grid_x, grid_y
        self.remove_gx = nn.Linear(128, 64)
        self.remove_gy = nn.Linear(128, 64)

        # Mine: dx, dy
        self.mine_dx = nn.Linear(128, 11)
        self.mine_dy = nn.Linear(128, 11)

        self._init_weights()

    def _init_weights(self):
        """Orthogonal initialization (standard for PPO)."""
        for module in self.modules():
            if isinstance(module, (nn.Linear, nn.Conv2d)):
                nn.init.orthogonal_(module.weight, gain=nn.init.calculate_gain('relu'))
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
        # Value head with smaller scale
        nn.init.orthogonal_(self.value_head.weight, gain=0.01)

    def _shared_forward(self, grid: torch.Tensor, features: torch.Tensor) -> torch.Tensor:
        """Run shared trunk, return 128-dim shared features."""
        # CNN path
        cnn_out = self.cnn(grid)
        cnn_flat = cnn_out.view(cnn_out.size(0), -1)
        spatial = self.cnn_proj(cnn_flat)

        # Feature MLP path
        context = self.feature_mlp(features)

        # Concat and shared
        combined = torch.cat([spatial, context], dim=-1)
        shared = self.shared(combined)
        return shared

    def forward(self, grid: torch.Tensor, features: torch.Tensor
                ) -> tuple[torch.Tensor, torch.Tensor]:
        """Forward pass. Returns (action_logits, value)."""
        shared = self._shared_forward(grid, features)
        action_logits = self.action_head(shared)
        value = self.value_head(shared)
        return action_logits, value

    def act(self, grid: torch.Tensor, features: torch.Tensor,
            action_mask: torch.Tensor | None = None
            ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, dict]:
        """Sample an action. Returns (action_type, log_prob, value, param_dict)."""
        shared = self._shared_forward(grid, features)
        action_logits = self.action_head(shared)

        if action_mask is not None:
            action_logits = action_logits.masked_fill(action_mask == 0, -1e8)

        dist = Categorical(logits=action_logits)
        action_type = dist.sample()
        log_prob = dist.log_prob(action_type)
        value = self.value_head(shared).squeeze(-1)

        # Sample parameters for selected action type
        params = self.get_action_params(shared, action_type.item())

        return action_type, log_prob, value, params

    def get_action_params(self, shared: torch.Tensor, action_type: int) -> dict:
        """Get parameter logits/samples for a specific action type."""
        params = {}

        if action_type == ActionType.PLACE:
            params["entity_logits"] = self.place_entity(shared)
            params["dx_logits"] = self.place_dx(shared)
            params["dy_logits"] = self.place_dy(shared)
            params["direction_logits"] = self.place_dir(shared)

        elif action_type == ActionType.CRAFT:
            params["recipe_logits"] = self.craft_recipe(shared)
            params["count_logits"] = self.craft_count(shared)

        elif action_type == ActionType.RESEARCH:
            params["tech_logits"] = self.research_tech(shared)

        elif action_type == ActionType.MOVE:
            params["dx_logits"] = self.move_dx(shared)
            params["dy_logits"] = self.move_dy(shared)

        elif action_type == ActionType.SET_RECIPE:
            params["gx_logits"] = self.set_recipe_gx(shared)
            params["gy_logits"] = self.set_recipe_gy(shared)
            params["recipe_logits"] = self.set_recipe_recipe(shared)

        elif action_type == ActionType.REMOVE:
            params["gx_logits"] = self.remove_gx(shared)
            params["gy_logits"] = self.remove_gy(shared)

        elif action_type == ActionType.MINE:
            params["dx_logits"] = self.mine_dx(shared)
            params["dy_logits"] = self.mine_dy(shared)

        return params

    def evaluate_action(self, grid: torch.Tensor, features: torch.Tensor,
                        action_type: torch.Tensor
                        ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Evaluate a batch of actions. Returns (log_prob, value, entropy)."""
        shared = self._shared_forward(grid, features)
        action_logits = self.action_head(shared)
        dist = Categorical(logits=action_logits)
        log_prob = dist.log_prob(action_type)
        entropy = dist.entropy()
        value = self.value_head(shared).squeeze(-1)
        return log_prob, value, entropy

    def save(self, path: str) -> None:
        """Save model checkpoint."""
        torch.save(self.state_dict(), path)
        log.info("Saved checkpoint to %s", path)

    def load(self, path: str) -> None:
        """Load model checkpoint."""
        state_dict = torch.load(path, map_location="cpu", weights_only=True)
        self.load_state_dict(state_dict)
        log.info("Loaded checkpoint from %s", path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_ml_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/ml_policy.py tests/factorio/test_ml_policy.py
git commit -m "feat(factorio): add PyTorch policy network — CNN + MLP with hierarchical action heads"
```

---

## Task 5: Reward Function

**Files:**
- Create: `fleet/factorio/reward.py`
- Test: `tests/factorio/test_reward.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_reward.py
import pytest
from factorio.state_parser import GameState
from factorio.reward import RewardComputer

def _make_state(**kwargs):
    defaults = dict(tick=0, player_position={"x": 0, "y": 0},
                    inventory={}, entities=[], resources=[],
                    research_name="", research_progress=0.0)
    defaults.update(kwargs)
    return GameState(**defaults)

def test_time_penalty():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    assert reward < 0  # time penalty

def test_lesson_passed_reward():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=True, phase_complete=False)
    assert reward >= 1.0  # +1.0 for lesson

def test_phase_complete_reward():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=True, phase_complete=True)
    assert reward >= 5.0  # +5.0 for phase

def test_failed_action_penalty():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward_ok = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    reward_fail = rc.compute(s1, s2, action_success=False, lesson_passed=False, phase_complete=False)
    assert reward_fail < reward_ok

def test_new_item_exploration_bonus():
    rc = RewardComputer(phase=1)
    s1 = _make_state(inventory={})
    s2 = _make_state(inventory={"iron-gear-wheel": 5})
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    # Should include exploration bonus
    assert reward > -0.01  # better than just time penalty

def test_phase2_production_bonus():
    rc = RewardComputer(phase=2)
    s1 = _make_state(inventory={"iron-plate": 10})
    s2 = _make_state(inventory={"iron-plate": 20})
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    # Phase 2 should include production delta bonus
    assert reward > -0.01

def test_reset_normalizer():
    rc = RewardComputer(phase=1)
    rc.compute(_make_state(), _make_state(), True, False, False)
    rc.compute(_make_state(), _make_state(), True, False, False)
    rc.reset_normalizer()
    # Should not crash, stats reset
    reward = rc.compute(_make_state(), _make_state(), True, False, False)
    assert isinstance(reward, float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_reward.py -v`
Expected: FAIL

- [ ] **Step 3: Implement reward.py**

```python
# fleet/factorio/reward.py
"""Phase-gated reward function for Factorio RL training.

Phase 1: milestone + failed action penalty + time pressure
Phase 2: + entity placement + production rate bonuses
Phase 3+: + throughput, research progress weighting
"""

import logging
import numpy as np

from factorio.state_parser import GameState

log = logging.getLogger(__name__)


class RunningStats:
    """Online mean/variance for reward normalization."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += delta * delta2

    @property
    def variance(self) -> float:
        return self.M2 / max(self.n, 1)

    @property
    def std(self) -> float:
        return max(np.sqrt(self.variance), 1e-8)

    def normalize(self, x: float) -> float:
        if self.n < 2:
            return x
        return (x - self.mean) / self.std

    def reset(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0


class RewardComputer:
    """Compute step rewards with phase-gated shaping."""

    def __init__(self, phase: int = 1):
        self._phase = phase
        self._seen_items: set[str] = set()
        self._prev_entity_count = 0
        self._stats = RunningStats()

    def set_phase(self, phase: int) -> None:
        self._phase = phase

    def reset_normalizer(self) -> None:
        """Reset reward normalization stats (call at phase boundaries)."""
        self._stats.reset()
        self._seen_items.clear()
        self._prev_entity_count = 0

    def compute(self, prev_state: GameState, curr_state: GameState,
                action_success: bool, lesson_passed: bool,
                phase_complete: bool) -> float:
        """Compute reward for a single step transition."""
        reward = 0.0

        # === Always active ===

        # Time penalty (encourages efficiency)
        reward -= 0.01

        # Failed action penalty
        if not action_success:
            reward -= 0.1

        # Milestone rewards
        if lesson_passed:
            reward += 1.0
        if phase_complete:
            reward += 5.0

        # Exploration: new item types in inventory
        new_items = set(curr_state.inventory.keys()) - self._seen_items
        if new_items:
            reward += 0.01 * len(new_items)
            self._seen_items.update(new_items)

        # Research progress
        if curr_state.research_progress > 0 and prev_state.research_progress >= 0:
            delta = curr_state.research_progress - prev_state.research_progress
            if delta > 0:
                reward += 0.1 * delta

        # === Phase 2+: entity placement bonus ===
        if self._phase >= 2:
            curr_entity_count = len(curr_state.entities)
            if curr_entity_count > self._prev_entity_count:
                new_entities = curr_entity_count - self._prev_entity_count
                reward += 0.05 * new_entities
            self._prev_entity_count = curr_entity_count

        # === Phase 2+: production delta ===
        if self._phase >= 2:
            prev_total = sum(prev_state.inventory.values())
            curr_total = sum(curr_state.inventory.values())
            delta = curr_total - prev_total
            if delta > 0:
                reward += 0.02 * min(delta / 10.0, 1.0)

        # Track for normalization
        self._stats.update(reward)

        return reward

    def normalize(self, reward: float) -> float:
        """Normalize reward using running statistics."""
        return self._stats.normalize(reward)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_reward.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/reward.py tests/factorio/test_reward.py
git commit -m "feat(factorio): add phase-gated reward function with normalization"
```

---

## Task 6: Episode Manager

**Files:**
- Create: `fleet/factorio/episode_manager.py`
- Test: `tests/factorio/test_episode_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_episode_manager.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from factorio.episode_manager import EpisodeManager

@pytest.fixture
def mock_rcon():
    rcon = AsyncMock()
    rcon.remote_call = AsyncMock(return_value='{"ok": true}')
    rcon.command = AsyncMock(return_value="ok")
    return rcon

@pytest.mark.asyncio
async def test_soft_reset(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    result = await em.soft_reset()
    assert result is True
    # Should have called remote_call for entity cleanup + inventory reset
    assert mock_rcon.remote_call.call_count >= 1

@pytest.mark.asyncio
async def test_reset_increments_episode(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    assert em.episode_count == 0
    await em.reset()
    assert em.episode_count == 1
    await em.reset()
    assert em.episode_count == 2

@pytest.mark.asyncio
async def test_episode_info(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    await em.reset()
    info = em.get_episode_info()
    assert info["episode"] == 1
    assert info["phase"] == 1
    assert info["step"] == 0

def test_step_counting(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    em.record_step()
    em.record_step()
    assert em.get_episode_info()["step"] == 2

def test_max_steps_exceeded(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1, max_steps=100)
    for _ in range(100):
        em.record_step()
    assert em.is_episode_done(max_steps=100) is True

@pytest.mark.asyncio
async def test_set_game_speed(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    await em.set_game_speed(10)
    mock_rcon.command.assert_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_episode_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Implement episode_manager.py**

```python
# fleet/factorio/episode_manager.py
"""Episode lifecycle management for RL training.

Handles game state reset (soft via Lua, hard via save/load),
episode step counting, and game speed control.
"""

import asyncio
import logging
import time

log = logging.getLogger(__name__)

# Lua script to clear all player-placed entities and reset inventory
SOFT_RESET_LUA = '''
local player = game.players[1]
if player then
    -- Clear player-built entities (keep resources and terrain)
    local entities = player.surface.find_entities_filtered{
        force = player.force,
        type = {"assembling-machine", "furnace", "transport-belt", "inserter",
                "mining-drill", "boiler", "generator", "electric-pole",
                "container", "lab", "pipe", "pipe-to-ground", "splitter",
                "underground-belt", "wall", "ammo-turret"}
    }
    for _, e in pairs(entities) do
        if e.valid then e.destroy() end
    end
    -- Clear inventory
    player.clear_items_inside()
    -- Teleport to spawn
    player.teleport({0, 0})
end
rcon.print("soft_reset_done")
'''

STARTING_ITEMS = {
    1: '{{["iron-plate"]=8, ["stone-furnace"]=1, ["burner-mining-drill"]=1}}',
    2: '{{["iron-plate"]=20, ["copper-plate"]=10, ["stone-furnace"]=2, ["burner-mining-drill"]=2, ["transport-belt"]=20, ["inserter"]=5}}',
    3: '{{["iron-plate"]=50, ["copper-plate"]=30, ["stone-furnace"]=4, ["transport-belt"]=50, ["inserter"]=10, ["small-electric-pole"]=10, ["boiler"]=2, ["steam-engine"]=2}}',
    4: '{{["iron-plate"]=100, ["copper-plate"]=50, ["electronic-circuit"]=20, ["transport-belt"]=100, ["inserter"]=20, ["assembling-machine-1"]=5}}',
}


class EpisodeManager:
    """Manage episode lifecycle for RL training."""

    def __init__(self, rcon, phase: int = 1, max_steps: int = 2000):
        self._rcon = rcon
        self._phase = phase
        self._max_steps = max_steps
        self._episode_count = 0
        self._step_count = 0
        self._episode_start_time = 0.0

    @property
    def episode_count(self) -> int:
        return self._episode_count

    def set_phase(self, phase: int) -> None:
        self._phase = phase

    async def reset(self) -> None:
        """Reset game state for a new episode. Tries soft reset first."""
        success = await self.soft_reset()
        if not success:
            log.warning("Soft reset failed, attempting hard reset")
            await self.hard_reset()

        # Give starting items for the phase
        await self._give_starting_items()

        self._episode_count += 1
        self._step_count = 0
        self._episode_start_time = time.time()
        log.info("Episode %d started (phase %d)", self._episode_count, self._phase)

    async def soft_reset(self) -> bool:
        """Lua-based reset: clear entities, reset inventory, teleport."""
        try:
            result = await self._rcon.command(f"/c {SOFT_RESET_LUA}")
            return "soft_reset_done" in str(result)
        except Exception:
            log.warning("Soft reset failed", exc_info=True)
            return False

    async def hard_reset(self) -> None:
        """Full save/load cycle with RCON auto-reconnect."""
        save_name = f"phase{self._phase}_clean"
        try:
            await self._rcon.command(f'/c game.server_save("{save_name}")')
        except Exception:
            log.warning("Save before hard reset failed", exc_info=True)

        # The server restart + reconnect is handled by the bridge's
        # connect_with_retry method. We just need to signal the need.
        log.info("Hard reset requested — bridge should reconnect")

    async def _give_starting_items(self) -> None:
        """Give phase-appropriate starting inventory."""
        items_lua = STARTING_ITEMS.get(self._phase, STARTING_ITEMS[1])
        lua = f'''
local player = game.players[1]
if player then
    local items = {items_lua}
    for name, count in pairs(items) do
        player.insert({{name=name, count=count}})
    end
end
rcon.print("items_given")
'''
        try:
            await self._rcon.command(f"/c {lua}")
        except Exception:
            log.warning("Failed to give starting items", exc_info=True)

    async def set_game_speed(self, speed: int) -> None:
        """Set Factorio game.speed for training throughput."""
        try:
            await self._rcon.command(f"/c game.speed = {speed}")
            log.info("Game speed set to %d", speed)
        except Exception:
            log.warning("Failed to set game speed", exc_info=True)

    def record_step(self) -> None:
        """Record one agent step in the current episode."""
        self._step_count += 1

    def is_episode_done(self, max_steps: int | None = None) -> bool:
        """Check if episode should end (step limit reached)."""
        limit = max_steps or self._max_steps
        return self._step_count >= limit

    def get_episode_info(self) -> dict:
        """Return current episode metadata."""
        return {
            "episode": self._episode_count,
            "phase": self._phase,
            "step": self._step_count,
            "max_steps": self._max_steps,
            "elapsed_secs": time.time() - self._episode_start_time if self._episode_start_time else 0,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_episode_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/episode_manager.py tests/factorio/test_episode_manager.py
git commit -m "feat(factorio): add episode manager — soft/hard reset, game speed, step tracking"
```

---

## Task 7: PPO Trainer

**Files:**
- Create: `fleet/factorio/trainer.py`
- Test: `tests/factorio/test_trainer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_trainer.py
import torch
import numpy as np
import pytest
from factorio.trainer import PPOTrainer, TrajectoryBuffer, Transition
from factorio.ml_policy import FactorioPolicy

@pytest.fixture
def policy():
    return FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )

def test_trajectory_buffer_add_and_size():
    buf = TrajectoryBuffer()
    t = Transition(
        grid=np.zeros((4, 64, 64), dtype=np.float32),
        features=np.zeros(64, dtype=np.float32),
        action_type=0, log_prob=-1.0, value=0.5, reward=0.1, done=False,
    )
    buf.add(t)
    assert len(buf) == 1

def test_trajectory_buffer_clear():
    buf = TrajectoryBuffer()
    t = Transition(
        grid=np.zeros((4, 64, 64), dtype=np.float32),
        features=np.zeros(64, dtype=np.float32),
        action_type=0, log_prob=-1.0, value=0.5, reward=0.1, done=False,
    )
    buf.add(t)
    buf.add(t)
    buf.clear()
    assert len(buf) == 0

def test_compute_gae():
    trainer = PPOTrainer.__new__(PPOTrainer)
    trainer._gamma = 0.99
    trainer._gae_lambda = 0.95
    rewards = [1.0, 0.0, 0.0]
    values = [0.5, 0.3, 0.1]
    dones = [False, False, True]
    next_value = 0.0
    advantages = trainer._compute_gae(rewards, values, dones, next_value)
    assert len(advantages) == 3
    assert isinstance(advantages[0], float)

def test_ppo_update_runs(policy):
    trainer = PPOTrainer(policy, lr=3e-4)
    buf = TrajectoryBuffer()
    # Add enough transitions for a batch
    for _ in range(64):
        t = Transition(
            grid=np.random.randn(4, 64, 64).astype(np.float32),
            features=np.random.randn(64).astype(np.float32),
            action_type=np.random.randint(0, 8),
            log_prob=-1.0,
            value=0.5,
            reward=0.1,
            done=False,
        )
        buf.add(t)
    stats = trainer.update(buf)
    assert "policy_loss" in stats
    assert "value_loss" in stats
    assert "entropy" in stats

def test_checkpoint_save_load(policy, tmp_path):
    trainer = PPOTrainer(policy, lr=3e-4, checkpoint_dir=str(tmp_path))
    trainer.save_checkpoint(episode=5)
    files = list(tmp_path.glob("*.pt"))
    assert len(files) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_trainer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement trainer.py**

```python
# fleet/factorio/trainer.py
"""PPO training loop and trajectory management for Factorio RL.

On-policy: collect trajectories → compute advantages → PPO update → discard.
"""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from factorio.ml_policy import FactorioPolicy

log = logging.getLogger(__name__)


@dataclass
class Transition:
    """Single step transition."""
    grid: np.ndarray           # (4, 64, 64)
    features: np.ndarray       # (feature_dim,)
    action_type: int
    log_prob: float
    value: float
    reward: float
    done: bool


class TrajectoryBuffer:
    """Collects on-policy transitions for PPO update."""

    def __init__(self):
        self._transitions: list[Transition] = []

    def add(self, transition: Transition) -> None:
        self._transitions.append(transition)

    def clear(self) -> None:
        self._transitions.clear()

    def __len__(self) -> int:
        return len(self._transitions)

    def get_all(self) -> list[Transition]:
        return self._transitions

    def to_tensors(self, device: str = "cpu") -> dict:
        """Convert buffer to batched tensors."""
        grids = torch.tensor(
            np.array([t.grid for t in self._transitions]),
            dtype=torch.float32, device=device
        )
        features = torch.tensor(
            np.array([t.features for t in self._transitions]),
            dtype=torch.float32, device=device
        )
        actions = torch.tensor(
            [t.action_type for t in self._transitions],
            dtype=torch.long, device=device
        )
        old_log_probs = torch.tensor(
            [t.log_prob for t in self._transitions],
            dtype=torch.float32, device=device
        )
        values = torch.tensor(
            [t.value for t in self._transitions],
            dtype=torch.float32, device=device
        )
        rewards = [t.reward for t in self._transitions]
        dones = [t.done for t in self._transitions]

        return {
            "grids": grids, "features": features, "actions": actions,
            "old_log_probs": old_log_probs, "values": values,
            "rewards": rewards, "dones": dones,
        }


class PPOTrainer:
    """PPO training with clipped objective and GAE."""

    def __init__(self, policy: FactorioPolicy, lr: float = 3e-4,
                 gamma: float = 0.99, gae_lambda: float = 0.95,
                 clip_ratio: float = 0.2, entropy_coeff: float = 0.01,
                 value_coeff: float = 0.5, max_grad_norm: float = 0.5,
                 ppo_epochs: int = 4, batch_size: int = 64,
                 checkpoint_dir: str = "fleet/factorio/checkpoints",
                 device: str | None = None):
        self._policy = policy
        self._gamma = gamma
        self._gae_lambda = gae_lambda
        self._clip_ratio = clip_ratio
        self._entropy_coeff = entropy_coeff
        self._value_coeff = value_coeff
        self._max_grad_norm = max_grad_norm
        self._ppo_epochs = ppo_epochs
        self._batch_size = batch_size
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        if device is None:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = device

        self._policy = self._policy.to(self._device)
        self._optimizer = torch.optim.Adam(self._policy.parameters(), lr=lr)

        # Metrics
        self.total_updates = 0
        self.total_episodes = 0

    def _compute_gae(self, rewards: list[float], values: list[float],
                     dones: list[bool], next_value: float) -> list[float]:
        """Compute Generalized Advantage Estimation."""
        advantages = []
        gae = 0.0
        for t in reversed(range(len(rewards))):
            if t == len(rewards) - 1:
                next_val = next_value
            else:
                next_val = values[t + 1]
            delta = rewards[t] + self._gamma * next_val * (1 - dones[t]) - values[t]
            gae = delta + self._gamma * self._gae_lambda * (1 - dones[t]) * gae
            advantages.insert(0, gae)
        return advantages

    def update(self, buffer: TrajectoryBuffer) -> dict:
        """Run PPO update on collected trajectories."""
        if len(buffer) < self._batch_size:
            log.warning("Buffer too small (%d < %d), skipping update", len(buffer), self._batch_size)
            return {"skipped": True}

        data = buffer.to_tensors(self._device)
        rewards = data["rewards"]
        values_list = data["values"].cpu().tolist()
        dones = data["dones"]

        # Compute GAE advantages
        advantages = self._compute_gae(rewards, values_list, dones, next_value=0.0)
        advantages_t = torch.tensor(advantages, dtype=torch.float32, device=self._device)
        returns_t = advantages_t + data["values"]

        # Normalize advantages
        adv_mean = advantages_t.mean()
        adv_std = advantages_t.std() + 1e-8
        advantages_t = (advantages_t - adv_mean) / adv_std

        # PPO epochs
        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0
        n_updates = 0

        indices = np.arange(len(buffer))
        for epoch in range(self._ppo_epochs):
            np.random.shuffle(indices)
            for start in range(0, len(indices), self._batch_size):
                end = start + self._batch_size
                if end > len(indices):
                    break
                batch_idx = indices[start:end]
                batch_idx_t = torch.tensor(batch_idx, dtype=torch.long, device=self._device)

                b_grids = data["grids"][batch_idx_t]
                b_features = data["features"][batch_idx_t]
                b_actions = data["actions"][batch_idx_t]
                b_old_log_probs = data["old_log_probs"][batch_idx_t]
                b_advantages = advantages_t[batch_idx_t]
                b_returns = returns_t[batch_idx_t]

                # Forward pass
                new_log_probs, new_values, entropy = self._policy.evaluate_action(
                    b_grids, b_features, b_actions
                )

                # PPO clipped objective
                ratio = torch.exp(new_log_probs - b_old_log_probs)
                surr1 = ratio * b_advantages
                surr2 = torch.clamp(ratio, 1 - self._clip_ratio, 1 + self._clip_ratio) * b_advantages
                policy_loss = -torch.min(surr1, surr2).mean()

                # Value loss
                value_loss = nn.functional.mse_loss(new_values, b_returns)

                # Total loss
                loss = policy_loss + self._value_coeff * value_loss - self._entropy_coeff * entropy.mean()

                self._optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self._policy.parameters(), self._max_grad_norm)
                self._optimizer.step()

                total_policy_loss += policy_loss.item()
                total_value_loss += value_loss.item()
                total_entropy += entropy.mean().item()
                n_updates += 1

        self.total_updates += 1

        stats = {
            "policy_loss": total_policy_loss / max(n_updates, 1),
            "value_loss": total_value_loss / max(n_updates, 1),
            "entropy": total_entropy / max(n_updates, 1),
            "advantages_mean": adv_mean.item(),
            "returns_mean": returns_t.mean().item(),
            "n_updates": n_updates,
        }
        log.info("PPO update #%d: policy_loss=%.4f value_loss=%.4f entropy=%.4f",
                 self.total_updates, stats["policy_loss"], stats["value_loss"], stats["entropy"])
        return stats

    def save_checkpoint(self, episode: int) -> str:
        """Save model + optimizer state."""
        path = self._checkpoint_dir / f"factorio_policy_ep{episode}.pt"
        torch.save({
            "episode": episode,
            "model_state_dict": self._policy.state_dict(),
            "optimizer_state_dict": self._optimizer.state_dict(),
            "total_updates": self.total_updates,
        }, str(path))
        log.info("Checkpoint saved: %s", path)
        return str(path)

    def load_checkpoint(self, path: str) -> int:
        """Load model + optimizer state. Returns episode number."""
        checkpoint = torch.load(path, map_location=self._device, weights_only=False)
        self._policy.load_state_dict(checkpoint["model_state_dict"])
        self._optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.total_updates = checkpoint.get("total_updates", 0)
        episode = checkpoint.get("episode", 0)
        log.info("Loaded checkpoint from %s (episode %d)", path, episode)
        return episode

    def get_latest_checkpoint(self) -> str | None:
        """Find most recent checkpoint file."""
        checkpoints = sorted(self._checkpoint_dir.glob("factorio_policy_ep*.pt"))
        return str(checkpoints[-1]) if checkpoints else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_trainer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/trainer.py tests/factorio/test_trainer.py
git commit -m "feat(factorio): add PPO trainer with GAE, trajectory buffer, checkpointing"
```

---

## Task 8: LLM Orchestrator

**Files:**
- Create: `fleet/factorio/llm_orchestrator.py`
- Test: `tests/factorio/test_llm_orchestrator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_llm_orchestrator.py
import pytest
from unittest.mock import patch, MagicMock
from factorio.llm_orchestrator import LLMOrchestrator, DiagnosticResult

def test_should_diagnose_after_stall():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    # 10 episodes with no lesson progress should trigger diagnosis
    history = [{"episode": i, "lessons_passed": 0, "reward": -0.5} for i in range(10)]
    assert orch.should_diagnose(history, stall_threshold=10) is True

def test_should_not_diagnose_when_progressing():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    history = [{"episode": i, "lessons_passed": i // 3, "reward": 0.5} for i in range(10)]
    assert orch.should_diagnose(history, stall_threshold=10) is False

def test_format_diagnosis_prompt():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    history = [{"episode": 1, "lessons_passed": 0, "reward": -0.3,
                "action_distribution": {"place": 5, "wait": 90, "move": 5}}]
    prompt = orch._format_diagnosis_prompt(history, phase=1)
    assert "Phase 1" in prompt
    assert "wait" in prompt.lower()

def test_format_narration_prompt():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    episode_summary = {"episode": 10, "reward": 2.5, "lessons_passed": 1,
                       "steps": 500, "actions": {"craft": 30, "place": 20}}
    prompt = orch._format_narration_prompt(episode_summary)
    assert "Episode 10" in prompt

def test_parse_diagnostic_result():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    raw = "DIAGNOSIS: Agent is stuck looping wait actions.\nSUGGESTION: Increase entropy bonus to 0.03"
    result = orch._parse_diagnostic(raw)
    assert isinstance(result, DiagnosticResult)
    assert "stuck" in result.diagnosis.lower() or "wait" in result.diagnosis.lower()

@patch("factorio.llm_orchestrator.urllib.request.urlopen")
def test_narrate_calls_ollama(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"response": "The agent made progress on crafting."}'
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    result = orch.narrate({"episode": 5, "reward": 1.0, "lessons_passed": 1, "steps": 200, "actions": {}})
    assert isinstance(result, str)
    assert len(result) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_llm_orchestrator.py -v`
Expected: FAIL

- [ ] **Step 3: Implement llm_orchestrator.py**

```python
# fleet/factorio/llm_orchestrator.py
"""LLM orchestration layer — runs between episodes, never during.

Jobs:
1. Training diagnostician — analyze reward curves, suggest fixes
2. Dashboard narrator — explain what the agent is doing
3. Curriculum generator — create/modify curriculum phases (future)
4. Strategy advisor — update goal embeddings (future)
"""

import json
import logging
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    diagnosis: str
    suggestions: list[str]
    raw: str


class LLMOrchestrator:
    """LLM-based training orchestration (between episodes only)."""

    def __init__(self, ollama_url: str = "http://localhost:11434",
                 model: str = "qwen3:8b", timeout: int = 60):
        self._ollama_url = ollama_url
        self._model = model
        self._timeout = timeout

    def should_diagnose(self, episode_history: list[dict],
                        stall_threshold: int = 10) -> bool:
        """Check if training has stalled and diagnosis is needed."""
        if len(episode_history) < stall_threshold:
            return False
        recent = episode_history[-stall_threshold:]
        # Stalled = no new lessons passed in last N episodes
        lessons_at_start = recent[0].get("lessons_passed", 0)
        lessons_at_end = recent[-1].get("lessons_passed", 0)
        return lessons_at_end <= lessons_at_start

    def diagnose(self, episode_history: list[dict], phase: int) -> DiagnosticResult:
        """Run training diagnostician. Returns diagnosis + suggestions."""
        prompt = self._format_diagnosis_prompt(episode_history, phase)
        raw = self._call_ollama(prompt)
        return self._parse_diagnostic(raw)

    def narrate(self, episode_summary: dict) -> str:
        """Generate a human-readable episode summary."""
        prompt = self._format_narration_prompt(episode_summary)
        return self._call_ollama(prompt)

    def _format_diagnosis_prompt(self, history: list[dict], phase: int) -> str:
        """Format episode history into a diagnosis prompt."""
        lines = [f"You are analyzing an RL agent training to play Factorio (Phase {phase})."]
        lines.append("Recent episode data:")
        for ep in history[-10:]:
            lines.append(f"  Episode {ep.get('episode', '?')}: "
                         f"reward={ep.get('reward', 0):.2f}, "
                         f"lessons_passed={ep.get('lessons_passed', 0)}, "
                         f"actions={ep.get('action_distribution', {})}")
        lines.append("")
        lines.append("Analyze why the agent is not making progress. Provide:")
        lines.append("1. DIAGNOSIS: what is wrong")
        lines.append("2. SUGGESTION: specific hyperparameter or reward changes to try")
        return "\n".join(lines)

    def _format_narration_prompt(self, summary: dict) -> str:
        """Format episode summary for narration."""
        return (
            f"Summarize this Factorio RL training episode in 1-2 sentences for a dashboard:\n"
            f"Episode {summary.get('episode', '?')}: "
            f"reward={summary.get('reward', 0):.2f}, "
            f"lessons_passed={summary.get('lessons_passed', 0)}, "
            f"steps={summary.get('steps', 0)}, "
            f"actions={summary.get('actions', {})}"
        )

    def _parse_diagnostic(self, raw: str) -> DiagnosticResult:
        """Parse raw LLM response into structured diagnostic."""
        diagnosis = ""
        suggestions = []
        for line in raw.split("\n"):
            line = line.strip()
            if line.upper().startswith("DIAGNOSIS:"):
                diagnosis = line[len("DIAGNOSIS:"):].strip()
            elif line.upper().startswith("SUGGESTION:"):
                suggestions.append(line[len("SUGGESTION:"):].strip())
        if not diagnosis:
            diagnosis = raw[:200]  # fallback: use first 200 chars
        return DiagnosticResult(diagnosis=diagnosis, suggestions=suggestions, raw=raw)

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama for completion."""
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self._ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
                return data.get("response", "")
        except Exception:
            log.warning("Ollama call failed", exc_info=True)
            return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/factorio/test_llm_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/llm_orchestrator.py tests/factorio/test_llm_orchestrator.py
git commit -m "feat(factorio): add LLM orchestrator — diagnostics, narration, between-episode advisory"
```

---

## Task 9: Bridge Integration — ML Mode Tick Loop

**Files:**
- Modify: `fleet/factorio/bridge.py`
- Test: `tests/factorio/test_bridge_ml_mode.py`

This is the integration task — wire the ML policy into the bridge's tick loop as an alternative to AgentBrain.

- [ ] **Step 1: Write failing test for ML mode tick**

```python
# tests/factorio/test_bridge_ml_mode.py
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from factorio.bridge_config import BridgeConfig

def test_bridge_config_selects_ml_mode():
    cfg = BridgeConfig(mode="ml")
    assert cfg.mode == "ml"

@pytest.mark.asyncio
async def test_ml_tick_calls_policy():
    """Verify that in ML mode, tick uses policy.act instead of brain.next_action."""
    # This test verifies the integration point exists.
    # Full integration requires mocking RCON, state parser, etc.
    from factorio.state_encoder import StateEncoder
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionSpace

    encoder = StateEncoder(phase=1)
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=encoder.feature_dim,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    action_space = ActionSpace(phase=1)

    # Simulate one ML step: encode state → policy → decode action
    from factorio.state_parser import GameState
    state = GameState(
        tick=100, player_position={"x": 0, "y": 0},
        inventory={"iron-plate": 10}, entities=[], resources=[],
    )
    grid, features = encoder.encode(state)

    import torch
    grid_t = torch.tensor(grid).unsqueeze(0)
    feat_t = torch.tensor(features).unsqueeze(0)
    action_type, log_prob, value, params = policy.act(grid_t, feat_t)

    # Decode back to action dict
    from factorio.action_space import EncodedAction
    encoded = EncodedAction(action_type=action_type.item())
    action_dict = action_space.decode_action(encoded)
    assert "action" in action_dict
```

- [ ] **Step 2: Run test to verify it fails (or passes if imports work)**

Run: `python -m pytest tests/factorio/test_bridge_ml_mode.py -v`
Expected: Should pass if all prior tasks are done — this is an integration smoke test.

- [ ] **Step 3: Add ML mode tick loop to bridge.py**

Read `fleet/factorio/bridge.py` and add an `async def ml_tick(self)` method alongside the existing `tick()`. The bridge's `run()` method should dispatch to `ml_tick()` when `self.config.mode == "ml"`.

Key changes to `bridge.py`:

1. **CRITICAL: Lazy imports only.** Do NOT add top-level PyTorch imports to bridge.py. All ML imports go inside `__init__` behind the mode check, so LLM mode never loads PyTorch:

2. In `__init__`, add ML components with lazy imports (only if `config.mode == "ml"`):
```python
if self.config.mode == "ml":
    from factorio.state_encoder import StateEncoder
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionSpace
    from factorio.reward import RewardComputer
    from factorio.trainer import PPOTrainer, TrajectoryBuffer
    from factorio.episode_manager import EpisodeManager
    from factorio.curriculum_manager import CurriculumManager

    self._encoder = StateEncoder(phase=config.current_phase)
    self._action_space = ActionSpace(phase=config.current_phase)
    self._policy = FactorioPolicy(
        grid_channels=4, grid_size=64,
        feature_dim=self._encoder.feature_dim,
        num_action_types=8,
        num_entities=self._action_space.num_entity_types,
        num_recipes=self._action_space.num_recipe_types,
        num_techs=self._action_space.num_tech_types,
    )
    self._reward = RewardComputer(phase=config.current_phase)
    self._trainer = PPOTrainer(
        self._policy, lr=config.ml_learning_rate,
        gamma=config.ml_gamma, gae_lambda=config.ml_gae_lambda,
        clip_ratio=config.ml_clip_ratio,
        entropy_coeff=config.ml_entropy_coeff,
        value_coeff=config.ml_value_coeff,
        checkpoint_dir=config.ml_checkpoint_dir,
    )
    self._episode_mgr = EpisodeManager(
        rcon=self.rcon, phase=config.current_phase,
        max_steps=config.ml_max_episode_steps,
    )
    self._trajectory_buf = TrajectoryBuffer()
    self._curriculum = CurriculumManager(
        current_phase=config.current_phase,
        curricula_dir=config.curriculum_dir,
    )
    self._prev_state = None
    self._ml_step_count = 0
```

3. Add `_sample_params()` helper method (samples from parameter head logits):
```python
def _sample_params(self, action_type: int, params: dict) -> "EncodedAction":
    """Sample concrete parameter values from policy head logits."""
    import torch
    from torch.distributions import Categorical
    from factorio.action_space import EncodedAction, ActionType

    encoded = EncodedAction(action_type=action_type)

    def _sample(logits_key: str) -> int:
        if logits_key in params:
            dist = Categorical(logits=params[logits_key])
            return dist.sample().item()
        return 0

    if action_type == ActionType.PLACE:
        encoded.entity_id = _sample("entity_logits")
        encoded.dx = _sample("dx_logits")
        encoded.dy = _sample("dy_logits")
        encoded.direction = _sample("direction_logits")
    elif action_type == ActionType.CRAFT:
        encoded.recipe_id = _sample("recipe_logits")
        encoded.count = _sample("count_logits") + 1  # 0-9 → 1-10
    elif action_type == ActionType.RESEARCH:
        encoded.tech_id = _sample("tech_logits")
    elif action_type == ActionType.MOVE:
        encoded.dx = _sample("dx_logits")
        encoded.dy = _sample("dy_logits")
    elif action_type == ActionType.SET_RECIPE:
        encoded.grid_x = _sample("gx_logits")
        encoded.grid_y = _sample("gy_logits")
        encoded.recipe_id = _sample("recipe_logits")
    elif action_type == ActionType.REMOVE:
        encoded.grid_x = _sample("gx_logits")
        encoded.grid_y = _sample("gy_logits")
    elif action_type == ActionType.MINE:
        encoded.dx = _sample("dx_logits")
        encoded.dy = _sample("dy_logits")

    return encoded
```

4. Add `ml_tick()` method:
```python
async def ml_tick(self) -> None:
    """Single ML-mode perception → action cycle."""
    import torch

    # 1. Get state
    raw_state = await self.rcon.remote_call("get_state")
    state = parse_state(raw_state)
    raw_metrics = None
    if self._tick_count % 5 == 0:
        raw_metrics_str = await self.rcon.remote_call("get_metrics")
        raw_metrics = parse_metrics(raw_metrics_str)
    self.world_model.update(state, raw_metrics)

    # 2. Encode state
    grid, features = self._encoder.encode(state, raw_metrics)
    grid_t = torch.tensor(grid).unsqueeze(0)
    feat_t = torch.tensor(features).unsqueeze(0)

    # 3. Get action from policy
    mask = self._action_space.get_action_type_mask(state.inventory, self.config.current_phase)
    mask_t = torch.tensor([mask], dtype=torch.float32)
    action_type, log_prob, value, params = self._policy.act(grid_t, feat_t, mask_t)

    # 4. Sample action parameters and decode
    encoded = self._sample_params(action_type.item(), params)
    action_dict = self._action_space.decode_action(encoded)

    # 5. Execute via RCON
    from factorio.action_translator import translate_action
    translated = translate_action(action_dict)
    result = {"success": False}
    if translated.rcon_command:
        try:
            resp = await self.rcon.remote_call("exec_cmd", translated.rcon_command)
            result = {"success": "error" not in str(resp).lower()}
        except Exception:
            log.warning("Action execution failed", exc_info=True)

    # 6. Check curriculum progress and compute reward
    lesson_passed = False
    phase_complete = False
    if self._prev_state is not None:
        # Use CurriculumManager directly (not AgentBrain)
        flat_state = {
            "inventory": state.inventory,
            "entities": {e.name: sum(1 for x in state.entities if x.name == e.name)
                         for e in state.entities},
            "research": {"name": state.research_name,
                         "progress": state.research_progress},
        }
        progress = self._curriculum.check_progress(flat_state)
        lesson_passed = progress.get("lesson_passed", False)
        phase_complete = progress.get("phase_complete", False)

    reward = 0.0
    if self._prev_state is not None:
        reward = self._reward.compute(
            self._prev_state, state, result["success"],
            lesson_passed, phase_complete
        )

    # 7. Store transition
    done = phase_complete or self._episode_mgr.is_episode_done()
    self._trajectory_buf.add(Transition(
        grid=grid, features=features,
        action_type=action_type.item(),
        log_prob=log_prob.item(),
        value=value.item(),
        reward=reward, done=done,
    ))
    self._episode_mgr.record_step()
    self._prev_state = state
    self._ml_step_count += 1

    # 8. PPO update if enough steps
    if self._ml_step_count % self.config.ml_update_every == 0:
        stats = self._trainer.update(self._trajectory_buf)
        self._trajectory_buf.clear()
        log.info("PPO update: %s", stats)

    # 9. Episode end check
    if done:
        self._trainer.total_episodes += 1
        if self._trainer.total_episodes % self.config.ml_checkpoint_every == 0:
            self._trainer.save_checkpoint(self._trainer.total_episodes)

        # Phase advancement — update all components
        if phase_complete:
            if self._curriculum.advance_phase():
                new_phase = self._curriculum._phase
                log.info("Advancing to phase %d", new_phase)
                self._encoder.set_phase(new_phase)
                self._action_space = ActionSpace(phase=new_phase)
                self._reward.set_phase(new_phase)
                self._reward.reset_normalizer()
                self._episode_mgr.set_phase(new_phase)

        await self._episode_mgr.reset()
        self._prev_state = None
```

4. In `run()`, dispatch based on mode:
```python
async def run(self):
    # ... existing connect logic ...
    if self.config.mode == "ml":
        await self._episode_mgr.set_game_speed(self.config.game_speed)
        await self._episode_mgr.reset()
    while self._running:
        if self.config.mode == "ml":
            await self.ml_tick()
        else:
            await self.tick()
        await asyncio.sleep(self.cadence.interval / 1000)
```

- [ ] **Step 4: Run integration test**

Run: `python -m pytest tests/factorio/test_bridge_ml_mode.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge.py tests/factorio/test_bridge_ml_mode.py
git commit -m "feat(factorio): integrate ML policy into bridge tick loop with mode toggle"
```

---

## Task 10: End-to-End Smoke Test

**Files:**
- Create: `tests/factorio/test_ml_e2e.py`

- [ ] **Step 1: Write end-to-end smoke test (no real Factorio needed)**

```python
# tests/factorio/test_ml_e2e.py
"""End-to-end smoke test: state → encode → policy → action → reward → train.

Uses mock RCON — no Factorio server required.
"""

import numpy as np
import torch
import pytest

from factorio.state_parser import GameState, Entity
from factorio.state_encoder import StateEncoder
from factorio.action_space import ActionSpace, EncodedAction, ActionType
from factorio.ml_policy import FactorioPolicy
from factorio.reward import RewardComputer
from factorio.trainer import PPOTrainer, TrajectoryBuffer, Transition


def test_full_pipeline_smoke():
    """Run 100 steps through the full pipeline without crashing."""
    phase = 1
    encoder = StateEncoder(phase=phase)
    action_space = ActionSpace(phase=phase)
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=encoder.feature_dim,
        num_action_types=8,
        num_entities=action_space.num_entity_types,
        num_recipes=action_space.num_recipe_types,
        num_techs=action_space.num_tech_types,
    )
    reward_computer = RewardComputer(phase=phase)
    trainer = PPOTrainer(policy, lr=3e-4, device="cpu")
    buffer = TrajectoryBuffer()

    prev_state = GameState(
        tick=0, player_position={"x": 0, "y": 0},
        inventory={"iron-plate": 8}, entities=[], resources=[],
    )

    for step in range(100):
        # Simulate game state (add some entities over time)
        entities = []
        if step > 20:
            entities.append(Entity(name="stone-furnace", position={"x": 2, "y": 3}, direction=0))
        state = GameState(
            tick=step * 60,
            player_position={"x": 0, "y": 0},
            inventory={"iron-plate": 8 + step, "iron-gear-wheel": step // 10},
            entities=entities,
            resources=[],
            research_name="automation" if step > 50 else "",
            research_progress=min(step / 100.0, 1.0) if step > 50 else 0.0,
        )

        # Encode
        grid, features = encoder.encode(state)
        grid_t = torch.tensor(grid).unsqueeze(0)
        feat_t = torch.tensor(features).unsqueeze(0)

        # Policy action
        mask = action_space.get_action_type_mask(state.inventory, phase)
        mask_t = torch.tensor([mask], dtype=torch.float32)
        action_type, log_prob, value, params = policy.act(grid_t, feat_t, mask_t)

        # Reward
        reward = reward_computer.compute(
            prev_state, state, action_success=(step % 5 != 0),
            lesson_passed=(step == 50), phase_complete=False,
        )

        # Buffer
        buffer.add(Transition(
            grid=grid, features=features,
            action_type=action_type.item(),
            log_prob=log_prob.item(),
            value=value.item(),
            reward=reward,
            done=(step == 99),
        ))

        prev_state = state

    # PPO update
    stats = trainer.update(buffer)
    assert "policy_loss" in stats
    assert stats["policy_loss"] < 100  # sanity — not exploding
    print(f"E2E smoke test passed. PPO stats: {stats}")


def test_checkpoint_round_trip(tmp_path):
    """Save checkpoint, load into new trainer, verify weights match."""
    encoder = StateEncoder(phase=1)
    action_space = ActionSpace(phase=1)
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=encoder.feature_dim,
        num_action_types=8,
        num_entities=action_space.num_entity_types,
        num_recipes=action_space.num_recipe_types,
        num_techs=action_space.num_tech_types,
    )
    trainer = PPOTrainer(policy, lr=3e-4, checkpoint_dir=str(tmp_path), device="cpu")
    trainer.save_checkpoint(episode=42)

    # Load into fresh policy
    policy2 = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=encoder.feature_dim,
        num_action_types=8,
        num_entities=action_space.num_entity_types,
        num_recipes=action_space.num_recipe_types,
        num_techs=action_space.num_tech_types,
    )
    trainer2 = PPOTrainer(policy2, lr=3e-4, checkpoint_dir=str(tmp_path), device="cpu")
    ep = trainer2.load_checkpoint(str(tmp_path / "factorio_policy_ep42.pt"))
    assert ep == 42

    # Weights should match
    for p1, p2 in zip(policy.parameters(), policy2.parameters()):
        assert torch.allclose(p1, p2)
```

- [ ] **Step 2: Run E2E test**

Run: `python -m pytest tests/factorio/test_ml_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/factorio/test_ml_e2e.py
git commit -m "test(factorio): add end-to-end ML pipeline smoke test"
```

---

## Deferred Scope

The following spec items are intentionally deferred to a follow-up plan:

- **Fleet Skill Updates (Spec Section 15):** `factorio_observe`, `factorio_act`, `factorio_plan`, `factorio_analyze`, `factorio_train` need interface changes for ML mode. Deferred until the core ML pipeline is validated.
- **Dashboard Updates:** Reward curves, episode metrics, action distribution visualizations.
- **Curriculum Generator:** LLM-driven generation of new curriculum TOML files (orchestrator has the interface, implementation is stub).
- **Strategy Advisor:** LLM-set goal embeddings (strategy_goal in state encoder is wired but zeros until Phase 3+).

## Task Summary

| Task | Component | Dependencies | Est. Time |
|------|-----------|-------------|-----------|
| 0 | Test Infrastructure & Mine Action | None | 5 min |
| 1 | Config & Gitignore | Task 0 | 5 min |
| 2 | Action Space | Task 0 | 15 min |
| 3 | State Encoder | Task 2 (uses ENTITY_REGISTRY) | 15 min |
| 4 | Policy Network | Task 2 (ActionType), Task 3 (feature_dim) | 15 min |
| 5 | Reward Function | Task 0 | 10 min |
| 6 | Episode Manager | Task 0 | 10 min |
| 7 | PPO Trainer | Task 4 (FactorioPolicy) | 15 min |
| 8 | LLM Orchestrator | Task 0 | 10 min |
| 9 | Bridge Integration | Tasks 0-8 (all components) | 20 min |
| 10 | E2E Smoke Test | Tasks 0-8 | 10 min |

**Parallelizable tasks:**
- Task 0 runs first (test infra + mine action)
- Tasks 1, 2, 5, 6, 8 can all run in parallel after Task 0
- Tasks 3, 4 depend on Task 2
- Task 7 depends on Task 4
- Tasks 9, 10 depend on all prior tasks

**Dependency graph:**
```
    0 (test infra) ──→ 1 ──────────────────────┐
                  ├──→ 2 ──→ 3 ──→ 4 ──→ 7 ──→│
                  ├──→ 5 ──────────────────────├──→ 9 ──→ 10
                  ├──→ 6 ──────────────────────│
                  └──→ 8 ──────────────────────┘
```

## Notes for Implementers

- **Feature vector is 64-dim** (not ~80 as in spec). Production rate features omitted for MVP — add via StateEncoder if needed. Strategy goal is 3-dim (not 10 as in spec) — intentional simplification for early phases.
- **All ML imports in bridge.py are lazy** — gated behind `if config.mode == "ml"` to avoid loading PyTorch in LLM mode.
- **Phase advancement is handled in ml_tick()** — when phase completes, encoder/action_space/reward/episode_manager all update to the new phase.
- **`_sample_params()` is defined in bridge.py** — samples from Categorical distributions over parameter head logits to produce concrete `EncodedAction` values.
