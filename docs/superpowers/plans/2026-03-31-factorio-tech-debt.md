# Factorio RL Tech Debt — Implementation Plan

**Date:** 2026-03-31
**Status:** Ready to execute
**Est. Total Tokens:** ~L (25-35k across 7 tasks)

---

## Task 1: Extract `_ml_tick_agent` into focused methods (bridge.py)

**Goal:** Break the 600-line `_ml_tick_agent` method (lines 451-1060) into 4 focused methods with no behavior changes.

**File:** `fleet/factorio/bridge.py`

### 1.1 Identify extraction boundaries

The current method has clear logical sections:

| New method | Source lines | Responsibility |
|---|---|---|
| `_handle_pack_in_flight(agent_id, state, _executor, raw_metrics, other_positions)` | 611-671 | Pack continuation: next step, RCON exec, accumulate reward, store transition on completion |
| `_run_policy_and_sample(state, raw_metrics, agent_mem, agent_id, other_positions, _executor)` | 673-853 | Encode state, run policy forward pass, sample params, decode action, handle PACK/STAMP selection, position conversion + leash |
| `_execute_rcon_action(action_dict, agent_id, translated, raw_metrics)` | 855-902 | RCON command execution, pack recording, insert tracking, checkpoint auto-save |
| `_post_step(state, raw_metrics, agent_id, action_type, result, grid, features, world_grid, mask, log_prob, value, other_positions, agent_mem, _executor, current_lesson)` | 904-1060 | Curriculum check, reward compute, transition store, PPO update, training status push, episode end |

### 1.2 Implementation steps (TDD)

**Test first:** Add a new test in `tests/factorio/test_bridge_ml_mode.py`:

```python
def test_ml_tick_agent_method_extraction():
    """Verify extracted methods exist and are callable on FactorioBridge."""
    from factorio.bridge import FactorioBridge
    assert hasattr(FactorioBridge, '_handle_pack_in_flight')
    assert hasattr(FactorioBridge, '_run_policy_and_sample')
    assert hasattr(FactorioBridge, '_execute_rcon_action')
    assert hasattr(FactorioBridge, '_post_step')
```

**Refactor steps:**

1. Extract `_handle_pack_in_flight`:
   - Cut lines 611-671 into new method
   - Returns `True` if pack was in-flight (caller should `return`), `False` otherwise
   - Signature: `async def _handle_pack_in_flight(self, agent_id, state, _executor, raw_metrics, other_positions) -> bool`

2. Extract `_run_policy_and_sample`:
   - Cut lines 673-853 into new method
   - Returns `(action_dict, action_type, log_prob, value, grid, features, world_grid, mask, encoded)` or `None` if early-returned (leash rejection, pack started)
   - Signature: `async def _run_policy_and_sample(self, state, raw_metrics, agent_mem, agent_id, other_positions, _executor) -> tuple | None`

3. Extract `_execute_rcon_action`:
   - Cut lines 855-902 into new method
   - Returns `result` dict
   - Signature: `async def _execute_rcon_action(self, action_dict, agent_id, raw_metrics) -> dict`

4. Extract `_post_step`:
   - Cut lines 904-1060 into new method
   - Handles curriculum, reward, PPO, episode end
   - Signature: `async def _post_step(self, state, raw_metrics, agent_id, action_type_val, result, grid, features, world_grid, mask, log_prob, value, other_positions, agent_mem, _executor, current_lesson) -> None`

5. Rewrite `_ml_tick_agent` as ~80-line dispatcher calling the 4 methods

**Verify:** Run existing tests:
```bash
python -m pytest tests/factorio/test_bridge_ml_mode.py tests/factorio/test_ml_e2e.py -x -q
```

**Commit:** `refactor(factorio): extract _ml_tick_agent into 4 focused methods`

---

## Task 2: Make reward constants configurable

**Goal:** Move 20+ hardcoded reward constants from `fleet/factorio/reward.py` into `fleet.toml` under `[factorio.reward]`, loaded via `config.load_config()`.

**Files:** `fleet/factorio/reward.py`, `fleet/fleet.toml`

### 2.1 Test first

Add to `tests/factorio/test_reward.py`:

```python
def test_reward_constants_from_config(tmp_path):
    """RewardComputer reads constants from config when provided."""
    import toml
    cfg_path = tmp_path / "fleet.toml"
    cfg_path.write_text(toml.dumps({
        "factorio": {"reward": {
            "time_penalty": -0.05,
            "lesson_pass_bonus": 5.0,
        }}
    }))
    # Patch config loader to use tmp_path
    from factorio.reward import RewardComputer
    rc = RewardComputer(phase=1, reward_config={
        "time_penalty": -0.05,
        "lesson_pass_bonus": 5.0,
    })
    assert rc._time_penalty == -0.05
    assert rc._lesson_pass_bonus == 5.0

def test_reward_defaults_without_config():
    """RewardComputer uses hardcoded defaults when no config provided."""
    from factorio.reward import RewardComputer
    rc = RewardComputer(phase=1)
    assert rc._time_penalty == -0.001
    assert rc._lesson_pass_bonus == 2.0
```

### 2.2 Add `[factorio.reward]` section to `fleet.toml`

```toml
[factorio.reward]
time_penalty = -0.001
failed_action_penalty = -0.02
successful_action_bonus = 0.05
lesson_pass_bonus = 2.0
phase_complete_bonus = 10.0
new_item_bonus = 0.1
research_progress_scale = 0.2
new_entity_bonus = 0.5
production_delta_scale = 0.05
production_delta_cap = 5.0
wander_penalty_scale = -0.02
wander_distance_threshold = 10
near_resource_bonus = 0.003
economic_scale = 0.05
economic_inventory_scale = 0.005
consecutive_move_penalty = -0.01
cluster_penalty_scale = -0.01
cluster_distance_threshold = 8.0
pack_complete_bonus = 1.0
stamp_complete_bonus = 2.0
pack_abort_penalty = -0.5
ore_proximity_bonus = 0.1
```

### 2.3 Modify `RewardComputer.__init__`

- Add `reward_config: dict | None = None` parameter
- If `reward_config` is None, try `load_config().get("factorio", {}).get("reward", {})`
- Store each constant as `self._time_penalty = reward_config.get("time_penalty", -0.001)` etc.
- Replace all module-level constant references in `_raw_reward` and `_clustering_penalty` with `self._` attributes
- Keep module-level constants as documentation/defaults (prefixed with `_DEFAULT_`)

### 2.4 Update bridge.py

Where `RewardComputer` is instantiated (lines 75-79), pass the loaded config:

```python
from config import load_config
cfg = load_config()
reward_cfg = cfg.get("factorio", {}).get("reward", {})
self._agent_reward[aid] = RewardComputer(
    phase=config.current_phase,
    spatial_memory=self._agent_spatial[aid],
    economic_scorer=self._economic_scorer,
    reward_config=reward_cfg,
)
```

**Verify:**
```bash
python -m pytest tests/factorio/test_reward.py tests/factorio/test_reward_packs.py -x -q
```

**Commit:** `feat(factorio): make reward constants configurable via fleet.toml`

---

## Task 3: Fix state encoder phase clamping

**Goal:** Expand the phase one-hot encoding from 4 to 8 slots. Currently clamped to `max(1, min(4, phase))` but 8 phases exist. This bumps `_BASE_FEATURE_DIM` from 69 to 73.

**Files:** `fleet/factorio/state_encoder.py`, `fleet/factorio/ml_policy.py` (feature_dim), `fleet/factorio/bridge.py` (verify encoder init)

### 3.1 Test first

Add to `tests/factorio/test_state_encoder.py`:

```python
def test_phase_onehot_8_phases():
    """Phase one-hot uses 8 slots, not 4."""
    from factorio.state_encoder import StateEncoder
    for phase in range(1, 9):
        enc = StateEncoder(phase=phase)
        assert enc.feature_dim >= 73  # was 69 with 4-slot
        assert enc._phase == phase  # no clamping

def test_phase_8_feature_dim():
    """Feature dim increases by 4 (4 extra phase slots)."""
    from factorio.state_encoder import StateEncoder, _BASE_FEATURE_DIM
    assert _BASE_FEATURE_DIM == 73
```

### 3.2 Changes in `state_encoder.py`

1. **Line 68:** Change `_BASE_FEATURE_DIM = 69` to `_BASE_FEATURE_DIM = 73`
2. **Line 107:** Change `self._phase = max(1, min(4, phase))` to `self._phase = max(1, min(8, phase))`
3. **Line 120 (set_phase):** Change `self._phase = max(1, min(4, phase))` to `self._phase = max(1, min(8, phase))`
4. **Line 18 (docstring):** Change `[56:60]  curriculum phase one-hot (4 phases)` to `[56:64]  curriculum phase one-hot (8 phases)`
5. **Line 333-335 (_encode_features):** Change:
   ```python
   phase_idx = max(0, min(3, self._phase - 1))
   feats[56 + phase_idx] = 1.0
   ```
   to:
   ```python
   phase_idx = max(0, min(7, self._phase - 1))
   feats[56 + phase_idx] = 1.0
   ```
6. **Update all feature indices after 60:** Shift by +4:
   - `feats[60]` lesson index -> `feats[64]`
   - `feats[61:64]` strategy goal -> `feats[65:68]`
   - `feats[64:68]` global resources -> `feats[68:72]`
   - `feats[68]` pack progress -> `feats[72]`
7. **Update docstring** at top of file to reflect new indices

### 3.3 Downstream impact

- `ml_policy.py`: The `feature_dim` is passed dynamically from `StateEncoder.feature_dim`, so no hardcoded 69 to fix — but verify the MLP input layer adapts.
- `bridge.py`: `self._encoder.feature_dim` is already used dynamically at line 90. No change needed.
- **Existing checkpoints become incompatible** — add a note in the commit message. Old checkpoints trained with 69-dim features cannot be loaded without a migration shim.

**Verify:**
```bash
python -m pytest tests/factorio/test_state_encoder.py tests/factorio/test_state_encoder_spatial.py tests/factorio/test_state_encoder_pack.py tests/factorio/test_ml_e2e.py -x -q
```

**Commit:** `fix(factorio): expand phase one-hot from 4 to 8 slots — bumps feature_dim 69->73`

---

## Task 4: Extract `fn_exec_cmd` action handlers in Lua mod

**Goal:** Break the 480-line `fn_exec_cmd` (lines 415-893) in `control.lua` into individual handler functions. `fn_exec_cmd` becomes a dispatcher.

**File:** `fleet/factorio/lua_mod/control.lua` (and sync to `fleet/factorio/server_data/` if applicable)

### 4.1 Identify handlers to extract

| Handler function | Action | Source lines |
|---|---|---|
| `handle_place(parsed, ctx)` | `place` | 435-494 |
| `handle_set_recipe(parsed, ctx)` | `set_recipe` | 496-516 |
| `handle_remove(parsed, ctx)` | `remove` | 518-544 |
| `handle_craft(parsed, ctx)` | `craft` | 546-595 |
| `handle_research(parsed, ctx)` | `research` | 597-609 |
| `handle_move(parsed, ctx)` | `move` | 611-640 |
| `handle_connect(parsed, ctx)` | `connect` | 642-694 |
| `handle_mine(parsed, ctx)` | `mine` | 696-749 |
| `handle_insert(parsed, ctx)` | `insert` | 751-806 |
| `handle_place_near_resource(parsed, ctx)` | `place_near_resource` | 808-887 |

### 4.2 Implementation steps

1. Extract each `elseif action == "xxx" then ... end` block into a `local function handle_xxx(parsed, ctx)` that returns the result table.
2. Each handler receives `parsed` (the JSON-parsed command) and `ctx` (agent context with `.surface`, `.force`, `.inventory`, `.character`).
3. Each handler creates its own `local result = { action = parsed.action, success = false }` and returns `helpers.table_to_json(result)`.
4. Rewrite `fn_exec_cmd` as a dispatcher:

```lua
local function fn_exec_cmd(json_str)
    if not json_str then
        return '{"error": "no command provided"}'
    end
    local ok, parsed = pcall(helpers.json_to_table, json_str)
    if not ok or not parsed then
        return '{"error": "invalid JSON"}'
    end
    local agent_id = parsed.agent_id or 1
    local ctx = get_agent_context(agent_id)
    if not ctx.surface then
        return helpers.table_to_json({error = "no surface available"})
    end

    local handlers = {
        place              = handle_place,
        set_recipe         = handle_set_recipe,
        remove             = handle_remove,
        craft              = handle_craft,
        research           = handle_research,
        move               = handle_move,
        connect            = handle_connect,
        mine               = handle_mine,
        insert             = handle_insert,
        place_near_resource = handle_place_near_resource,
    }
    local handler = handlers[parsed.action]
    if handler then
        return handler(parsed, ctx)
    end
    return helpers.table_to_json({
        action = parsed.action, success = false,
        error = "unknown action: " .. tostring(parsed.action)
    })
end
```

### 4.3 Lua mod sync

Check if `fleet/factorio/server_data/` contains a copy of `control.lua`. If so, copy the updated file there too. (The quick-wins plan mentioned a sync mechanism — use it if it exists, otherwise manual copy.)

### 4.4 Testing

No automated Lua tests exist yet (see Task 7). Manual verification:
- Start a Factorio server with the mod
- Execute each action type via RCON and verify same responses
- Alternatively, diff the JSON output of identical commands before/after refactor

**Verify:** Ensure the mod loads without syntax errors:
```bash
# Check Lua syntax (if luac available)
luac -p fleet/factorio/lua_mod/control.lua 2>&1 || echo "No luac available — manual test needed"
```

**Commit:** `refactor(factorio): extract fn_exec_cmd into per-action handler functions`

---

## Task 5: Vectorize GAE + add checkpoint rotation (trainer.py)

**Goal:** Three improvements to `fleet/factorio/trainer.py`:
1. Replace Python list iteration in `_compute_gae` with torch tensor ops
2. Add `max_checkpoints=5` config with oldest-deletion
3. Fix terminal bootstrap: use V(s_T) for truncated episodes, 0 for true terminals

**File:** `fleet/factorio/trainer.py`

### 5.1 Test first

Add to `tests/factorio/test_trainer.py`:

```python
def test_gae_vectorized_matches_scalar():
    """Vectorized GAE produces same results as the original list-based version."""
    import torch
    from factorio.trainer import PPOTrainer
    # Create trainer with dummy policy
    # ... (use existing test fixtures)
    rewards = [0.1, 0.2, -0.1, 0.5, 0.0]
    values = [1.0, 0.9, 0.8, 0.7, 0.6]
    dones = [False, False, True, False, False]
    next_value = 0.5
    # Compare old (list) vs new (tensor) implementation
    # Keep old implementation as _compute_gae_list for comparison
    trainer = ...  # create with fixtures
    list_result = trainer._compute_gae_list(rewards, values, dones, next_value)
    tensor_result = trainer._compute_gae(rewards, values, dones, next_value)
    assert torch.allclose(
        torch.tensor(list_result),
        tensor_result if isinstance(tensor_result, torch.Tensor) else torch.tensor(tensor_result),
        atol=1e-6,
    )

def test_checkpoint_rotation(tmp_path):
    """Only max_checkpoints files are kept; oldest deleted."""
    from factorio.trainer import PPOTrainer
    # Create trainer with max_checkpoints=3
    # Save 5 checkpoints
    # Assert only 3 remain (the 3 most recent)

def test_terminal_bootstrap_truncated():
    """Truncated episodes use V(s_T), true terminals use 0."""
    # Buffer with done=True on last step (true terminal) -> next_value=0
    # Buffer with done=False on last step (truncated) -> next_value=V(s_T)
```

### 5.2 Vectorize `_compute_gae`

Replace current implementation (lines 195-217) with:

```python
def _compute_gae(
    self,
    rewards: torch.Tensor,
    values: torch.Tensor,
    dones: torch.Tensor,
    next_value: float,
) -> torch.Tensor:
    """Vectorized GAE using torch tensors."""
    T = len(rewards)
    advantages = torch.zeros(T, device=rewards.device)
    gae = torch.tensor(0.0, device=rewards.device)
    # Append next_value for indexing
    values_ext = torch.cat([values, torch.tensor([next_value], device=values.device)])
    masks = 1.0 - dones
    for t in reversed(range(T)):
        delta = rewards[t] + self._gamma * values_ext[t + 1] * masks[t] - values[t]
        gae = delta + self._gamma * self._gae_lambda * masks[t] * gae
        advantages[t] = gae
    return advantages
```

Note: True full vectorization of GAE requires a scan operation. The reversed loop is the standard approach even in reference PPO implementations. The improvement here is operating on tensors directly instead of Python lists, and doing the computation on GPU when available.

Update the caller in `update()` (lines 239-244) to pass tensors:

```python
advantages = self._compute_gae(
    tensors["rewards"], tensors["values"], tensors["dones"], next_value
)
```

### 5.3 Fix terminal bootstrap

In `update()` line 243, replace:
```python
next_value = 0.0  # terminal bootstrap
```
with:
```python
# Use V(s_T) for truncated episodes; 0 for true terminal
last_done = buffer.get_all()[-1].done
if last_done:
    next_value = 0.0  # true terminal
else:
    # Truncated: bootstrap from last value estimate
    next_value = buffer.get_all()[-1].value
```

### 5.4 Add checkpoint rotation

1. Add `max_checkpoints: int = 5` to `__init__` params
2. Store as `self._max_checkpoints`
3. After `save_checkpoint`, call `self._rotate_checkpoints()`

```python
def _rotate_checkpoints(self) -> None:
    """Delete oldest checkpoints if count exceeds max_checkpoints."""
    if self._max_checkpoints <= 0:
        return
    pts = sorted(self._checkpoint_dir.glob("ppo_ep*.pt"))
    while len(pts) > self._max_checkpoints:
        oldest = pts.pop(0)
        try:
            oldest.unlink()
            log.info("Rotated old checkpoint: %s", oldest.name)
        except Exception:
            log.warning("Failed to delete old checkpoint: %s", oldest, exc_info=True)
```

4. Add `max_checkpoints` to `fleet.toml`:
```toml
ml_max_checkpoints = 5
```

5. Wire through `bridge.py` -> `PPOTrainer.__init__`

**Verify:**
```bash
python -m pytest tests/factorio/test_trainer.py -x -q
```

**Commit:** `feat(factorio): vectorize GAE, fix terminal bootstrap, add checkpoint rotation`

---

## Task 6: Deduplicate PHASE_ENTITIES / _LESSON_RECIPES (action_space.py)

**Goal:** Eliminate inline repetition in `PHASE_ENTITIES` phases 1-3 and `_LESSON_RECIPES` lessons 0-7.

**File:** `fleet/factorio/action_space.py`

### 6.1 Test first

Add to `tests/factorio/test_action_space.py`:

```python
def test_phase_entities_cumulative():
    """Each phase is a superset of the previous phase."""
    from factorio.action_space import PHASE_ENTITIES
    for phase in range(2, 9):
        assert PHASE_ENTITIES[phase - 1].issubset(PHASE_ENTITIES[phase]), \
            f"Phase {phase} is not a superset of phase {phase - 1}"

def test_phase_entities_values_unchanged():
    """Refactor preserves exact entity sets for all 8 phases."""
    from factorio.action_space import PHASE_ENTITIES
    # Snapshot current values (hardcode expected sets)
    assert PHASE_ENTITIES[1] == {
        "stone-furnace", "burner-mining-drill", "wooden-chest",
        "transport-belt", "inserter", "burner-inserter",
        "small-electric-pole", "boiler",
    }
    assert "steam-engine" in PHASE_ENTITIES[2]
    assert "long-handed-inserter" in PHASE_ENTITIES[3]
    assert len(PHASE_ENTITIES[8]) == 37  # all entities

def test_lesson_recipes_values_unchanged():
    """Refactor preserves exact recipe sets for all lessons."""
    from factorio.action_space import ActionSpace
    space = ActionSpace(phase=1)
    assert space._LESSON_RECIPES[0] == space._LESSON_RECIPES[1]
    assert "burner-inserter" in space._LESSON_RECIPES[4]
    assert "transport-belt" in space._LESSON_RECIPES[6]
```

### 6.2 Refactor PHASE_ENTITIES (lines 217-288)

Current: Phases 1-3 repeat all items inline. Phases 4-8 already use `{*prev, ...}`.

Replace phases 1-3 with cumulative pattern:

```python
PHASE_ENTITIES: dict[int, set[str]] = {
    1: {
        "stone-furnace", "burner-mining-drill", "wooden-chest",
        "transport-belt", "inserter", "burner-inserter",
        "small-electric-pole", "boiler",
    },
}
PHASE_ENTITIES[2] = {
    *PHASE_ENTITIES[1],
    "steam-engine", "offshore-pump", "pipe",
    "assembling-machine-1", "lab", "iron-chest", "electric-mining-drill",
}
PHASE_ENTITIES[3] = {
    *PHASE_ENTITIES[2],
    "long-handed-inserter", "fast-inserter", "underground-belt", "splitter",
}
# Phases 4-8 already cumulative — no change needed
```

### 6.3 Refactor _LESSON_RECIPES (lines 553-577)

Current: 8 near-identical sets with progressive additions.

Replace with cumulative build:

```python
_LESSON_RECIPES: dict[int, set[str]] = {}
_BASE_RECIPES = {"stone-furnace", "burner-mining-drill", "iron-gear-wheel",
                 "iron-plate", "copper-plate", "coal"}
# Lessons 0-3: base recipes only
for _i in range(4):
    _LESSON_RECIPES[_i] = set(_BASE_RECIPES)
# Lessons 4-5: add inserter + ores
_LESSON_4 = {*_BASE_RECIPES, "burner-inserter", "iron-ore", "copper-ore"}
_LESSON_RECIPES[4] = _LESSON_4
_LESSON_RECIPES[5] = set(_LESSON_4)
# Lessons 6-7: add belts + containers
_LESSON_RECIPES[6] = {*_LESSON_4, "transport-belt", "wooden-chest"}
_LESSON_RECIPES[7] = {*_LESSON_RECIPES[6], "inserter", "small-electric-pole"}
```

Note: This must be a class-level dict on `ActionSpace`, not module-level, since it's currently `ActionSpace._LESSON_RECIPES`. Keep it as a class attribute.

**Verify:**
```bash
python -m pytest tests/factorio/test_action_space.py tests/factorio/test_bridge_ml_mode.py -x -q
```

**Commit:** `refactor(factorio): deduplicate PHASE_ENTITIES and _LESSON_RECIPES with cumulative sets`

---

## Task 7: Factorio core unit tests

**Goal:** Add test coverage for `action_translator.py`, `state_parser.py`, and `rcon_client.py` (packet encode/decode only — no live server).

### 7.1 `tests/factorio/test_action_translator.py`

```python
"""Tests for action_translator.py — grid snapping, direction conversion, RCON command format."""

# Test cases:
# 1. translate_action with "place" — verify JSON payload has entity, position, direction
# 2. translate_action with "mine" — verify position in JSON
# 3. translate_action with "wait" — verify rcon_command is None
# 4. translate_action with unknown action — verify warning + unknown type
# 5. Direction conversion: "north"->0, "east"->4, "south"->8, "west"->12
# 6. Grid snapping: 2x2 entities (stone-furnace) snap to integer coords
# 7. Grid snapping: 1x1 entities (inserter) snap to half-tile coords
# 8. Grid snapping: 3x3 entities (assembling-machine-1) snap to half-tile coords
# 9. translate_batch with mixed actions — verify all translated
# 10. insert action — verify item, count, position in payload
```

**File structure:**

```python
import json
import pytest
from factorio.action_translator import translate_action, translate_batch, _direction_to_int

class TestDirectionConversion:
    def test_string_directions(self):
        assert _direction_to_int("north") == 0
        assert _direction_to_int("east") == 4
        assert _direction_to_int("south") == 8
        assert _direction_to_int("west") == 12

    def test_int_passthrough(self):
        assert _direction_to_int(4) == 4

    def test_none_default(self):
        assert _direction_to_int(None) == 0

class TestTranslateAction:
    def test_place_command(self):
        result = translate_action({
            "action": "place", "entity": "stone-furnace",
            "position": {"x": 10, "y": 20}, "direction": "north",
        })
        assert result.action_type == "place"
        assert result.rcon_command is not None
        payload = json.loads(result.rcon_command)
        assert payload["entity"] == "stone-furnace"

    def test_wait_no_rcon(self):
        result = translate_action({"action": "wait", "ticks": 30})
        assert result.action_type == "wait"
        assert result.rcon_command is None

    def test_unknown_action(self):
        result = translate_action({"action": "fly_to_moon"})
        assert result.action_type == "unknown"

    # ... grid snapping tests using _EVEN_SIZE_ENTITIES
```

### 7.2 `tests/factorio/test_state_parser.py`

```python
"""Tests for state_parser.py — JSON -> GameState/GameMetrics parsing."""

# Test cases:
# 1. parse_state with full valid JSON — verify all fields populated
# 2. parse_state with minimal JSON — verify defaults
# 3. parse_state with empty string / None — verify graceful handling
# 4. parse_state entity parsing — verify Entity dataclass fields
# 5. parse_state resource_positions parsing
# 6. parse_metrics with valid JSON — verify all metric fields
# 7. parse_metrics with missing fields — verify defaults
# 8. state_to_markdown — verify returns string with key sections
```

### 7.3 `tests/factorio/test_rcon_client.py`

```python
"""Tests for rcon_client.py — packet encode/decode (no live server)."""

# Test cases:
# 1. encode_packet produces correct binary format (size + id + type + body + padding)
# 2. decode_packet round-trips with encode_packet
# 3. decode_packet with short data raises ValueError
# 4. encode_packet with empty body
# 5. encode_packet with unicode body
# 6. AUTH packet type constant = 3
# 7. AUTH_RESPONSE constant = 2
# 8. EXECCOMMAND constant = 2
```

**File structure:**

```python
import struct
import pytest
from factorio.rcon_client import encode_packet, decode_packet, SERVERDATA_AUTH

class TestPacketEncoding:
    def test_roundtrip(self):
        packet = encode_packet(42, SERVERDATA_AUTH, "password123")
        req_id, pkt_type, body = decode_packet(packet)
        assert req_id == 42
        assert pkt_type == SERVERDATA_AUTH
        assert body == "password123"

    def test_short_packet_raises(self):
        with pytest.raises(ValueError, match="too short"):
            decode_packet(b"\x00" * 5)

    def test_empty_body(self):
        packet = encode_packet(1, 0, "")
        _, _, body = decode_packet(packet)
        assert body == ""

    def test_packet_structure(self):
        packet = encode_packet(1, 2, "hello")
        size = struct.unpack("<i", packet[:4])[0]
        assert size == 4 + 4 + 5 + 2  # id + type + body + padding
```

**Verify all new tests:**
```bash
python -m pytest tests/factorio/test_action_translator.py tests/factorio/test_state_parser.py tests/factorio/test_rcon_client.py -x -q
```

**Commit:** `test(factorio): add unit tests for action_translator, state_parser, rcon_client`

---

## Execution Order

Dependencies between tasks are minimal. Recommended order for safety:

1. **Task 7** (tests) — zero risk, adds safety net
2. **Task 6** (dedup) — pure refactor, easy to verify
3. **Task 3** (phase clamping fix) — small but changes tensor dims
4. **Task 2** (reward config) — additive, no breaking changes
5. **Task 4** (Lua refactor) — isolated to mod, needs manual test
6. **Task 5** (GAE + checkpoints) — training behavior change
7. **Task 1** (bridge extraction) — largest refactor, benefits from all other tests passing first

Tasks 2, 4, 6, and 7 can be parallelized (no file conflicts). Tasks 1, 3, and 5 should be sequential.
