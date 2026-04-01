# Factorio Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 54 findings from the Factorio module audit, prioritized by training impact, then safety, then architecture.

**Architecture:** Three tiers of fixes applied sequentially. Tier 1 (Tasks 1-6) fixes RL training — the root cause for lesson 2 being stuck at 8,362 steps. Tier 2 (Tasks 7-10) fixes silent failures and safety issues. Tier 3 (Tasks 11-15) fixes architecture and performance. Each task is independently testable and committable.

**Tech Stack:** Python 3.14, PyTorch, Flask, pytest

---

## File Map

| File | Changes | Reason |
|---|---|---|
| `fleet/factorio/bridge_config.py:75` | Modify | Entropy coeff 0.01→0.05 |
| `fleet/factorio/ml_policy.py:484-513` | Modify | Add action_mask param to evaluate_action |
| `fleet/factorio/trainer.py:64-99,220-290,335-338` | Modify | Store mask in Transition, pass to evaluate, weights_only fix |
| `fleet/factorio/action_space.py:586-620` | Modify | Separate has_drill from has_placeable |
| `fleet/factorio/reward.py:107-223` | Modify | Add ore_proximity_bonus for lesson 2 |
| `fleet/factorio/curriculum.py:90-121` | Modify | Add max_attempts enforcement to LessonTracker |
| `fleet/factorio/curriculum_manager.py:94-118` | Modify | Wire max_attempts into check_progress |
| `fleet/factorio/bridge.py:486-506,597-602,667-669,821-868,1000` | Modify | Multiple Tier 2+3 fixes |
| `fleet/factorio/bridge_api.py:179-212,262-270` | Modify | None guards, graceful shutdown |
| `fleet/factorio/state_encoder.py:245` | Modify | Direction normalization /15.0 |
| `tests/factorio/test_reward.py` | Modify | Add ore proximity tests |
| `tests/factorio/test_ml_policy.py` | Modify | Add evaluate_action mask test |
| `tests/factorio/test_ml_e2e.py` | Modify | Update E2E for new Transition shape |
| `tests/factorio/test_curriculum_checkpoints.py` | Modify | Add max_attempts tests |
| `tests/factorio/test_bridge_config.py` | Modify | Add entropy coeff test |
| `tests/factorio/test_action_space.py` | Modify | Add has_drill mask test |
| `tests/factorio/test_state_encoder.py` | Modify | Direction normalization test |

---

## TIER 1: Training Fixes (unblock lesson 2)

### Task 1: Increase entropy coefficient

**Files:**
- Modify: `fleet/factorio/bridge_config.py:75`
- Test: `tests/factorio/test_bridge_config.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/factorio/test_bridge_config.py — add this test
def test_entropy_coeff_default():
    """Entropy coeff must be >= 0.05 for Phase 1 exploration."""
    from factorio.bridge_config import BridgeConfig
    cfg = BridgeConfig()
    assert cfg.ml_entropy_coeff >= 0.05, (
        f"entropy_coeff={cfg.ml_entropy_coeff} is too low — policy will collapse to MINE/WAIT"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_bridge_config.py::test_entropy_coeff_default -v`
Expected: FAIL — current default is 0.01

- [ ] **Step 3: Change the default**

In `fleet/factorio/bridge_config.py:75`, change:
```python
ml_entropy_coeff: float = 0.05
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/factorio/test_bridge_config.py::test_entropy_coeff_default -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge_config.py tests/factorio/test_bridge_config.py
git commit -m "fix(factorio): entropy coeff 0.01→0.05 — prevent policy collapse to MINE/WAIT"
```

---

### Task 2: Apply action mask in evaluate_action (PPO fix)

**Files:**
- Modify: `fleet/factorio/ml_policy.py:484-513`
- Modify: `fleet/factorio/trainer.py:18-30,64-99,220-290`
- Test: `tests/factorio/test_ml_policy.py`

This is the most impactful correctness fix. During PPO updates, `evaluate_action()` recomputes log-probs WITHOUT the action mask that was applied during rollout collection. This makes the importance-sampling ratio `r = exp(new_log_prob - old_log_prob)` systematically wrong, corrupting the PPO objective.

- [ ] **Step 1: Write the failing test**

```python
# In tests/factorio/test_ml_policy.py — add this test
def test_evaluate_action_respects_mask():
    """evaluate_action must produce same log_probs as act() for masked actions."""
    import torch
    from factorio.ml_policy import FactorioPolicy

    policy = FactorioPolicy(grid_channels=5, feature_dim=69)
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)

    # Mask: only WAIT(0) and MINE(1) allowed
    mask = torch.zeros(1, 12, dtype=torch.bool)
    mask[0, 0] = True  # WAIT
    mask[0, 1] = True  # MINE

    # Get action with mask
    action_type, log_prob_act, _, _ = policy.act(grid, feat, mask)

    # evaluate_action with same mask should give same log_prob
    log_prob_eval, _, _ = policy.evaluate_action(
        grid, feat, action_type, action_mask=mask,
    )

    assert torch.allclose(log_prob_act, log_prob_eval, atol=1e-5), (
        f"act log_prob={log_prob_act.item():.6f} != evaluate log_prob={log_prob_eval.item():.6f}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_ml_policy.py::test_evaluate_action_respects_mask -v`
Expected: FAIL — `evaluate_action` doesn't accept `action_mask` parameter

- [ ] **Step 3: Add action_mask parameter to evaluate_action**

In `fleet/factorio/ml_policy.py`, modify `evaluate_action` (line 484):

```python
def evaluate_action(
    self,
    grid: torch.Tensor,
    features: torch.Tensor,
    action_type: torch.Tensor,
    world_grid: torch.Tensor | None = None,
    action_mask: torch.Tensor | None = None,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Evaluate stored actions for PPO update.

    Parameters
    ----------
    grid         : (B, C, H, W)
    features     : (B, feature_dim)
    action_type  : (B,) integer action indices
    world_grid   : optional (B, C, H, W) — zoomed-out minimap
    action_mask  : optional (B, num_action_types) — must match mask used during act()

    Returns
    -------
    log_prob : (B,)
    value    : (B,)
    entropy  : scalar
    """
    shared = self._shared_forward(grid, features, world_grid)
    action_logits = self.action_head(shared)
    if action_mask is not None:
        action_logits = action_logits.masked_fill(~action_mask, -1e8)
    dist = Categorical(logits=action_logits)
    log_prob = dist.log_prob(action_type)
    value = self.value_head(shared).squeeze(-1)
    entropy = dist.entropy().mean()
    return log_prob, value, entropy
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/factorio/test_ml_policy.py::test_evaluate_action_respects_mask -v`
Expected: PASS

- [ ] **Step 5: Add action_mask to Transition dataclass**

In `fleet/factorio/trainer.py`, modify the `Transition` dataclass (around line 18):

```python
@dataclass
class Transition:
    grid: np.ndarray
    features: np.ndarray
    action_type: int
    log_prob: float
    value: float
    reward: float
    done: bool
    world_grid: np.ndarray | None = None
    action_mask: list | None = None  # NEW: store mask used during act()
```

- [ ] **Step 6: Store action_mask in to_tensors**

In `fleet/factorio/trainer.py`, in `TrajectoryBuffer.to_tensors()` (around line 64), add after the `dones` tensor:

```python
# Action masks — needed for evaluate_action during PPO update
has_masks = all(t.action_mask is not None for t in self._data)
if has_masks:
    action_masks = torch.tensor(
        [t.action_mask for t in self._data],
        dtype=torch.bool, device=device,
    )
else:
    action_masks = None
```

And include `"action_masks": action_masks` in the returned dict.

- [ ] **Step 7: Add action_masks to to_tensors return dict**

In `fleet/factorio/trainer.py`, modify the return dict in `to_tensors()` (line 103-112):

```python
return {
    "grids": grids,
    "world_grids": world_grids,
    "features": features,
    "actions": actions,
    "old_log_probs": old_log_probs,
    "values": values,
    "rewards": rewards,
    "dones": dones,
    "action_masks": action_masks,  # NEW — None if any transition lacks mask
}
```

- [ ] **Step 8: Pass action_mask through PPO update**

In `fleet/factorio/trainer.py`, in `update()` method, add after line 247 (`old_log_probs = tensors["old_log_probs"]`):

```python
action_masks = tensors.get("action_masks")  # may be None
```

Then modify the minibatch loop (line 271-273). Replace:

```python
new_log_probs, new_values, entropy = self.policy.evaluate_action(
    mb_grids, mb_features, mb_actions, world_grid=mb_world_grids
)
```

With:

```python
mb_masks = action_masks[idx] if action_masks is not None else None
new_log_probs, new_values, entropy = self.policy.evaluate_action(
    mb_grids, mb_features, mb_actions,
    world_grid=mb_world_grids,
    action_mask=mb_masks,
)
```

- [ ] **Step 9: Update ALL Transition creation sites in bridge.py to pass action_mask**

There are 4 `Transition(...)` call sites in `fleet/factorio/bridge.py`. ALL must pass `action_mask=mask` (the list from `get_action_type_mask`). The `mask` variable is computed at line 673-674 and is in scope for all sites.

**Site 1 — pack completion (line 606):** Add `action_mask=saved.get("action_mask")` to the Transition constructor. Also update the `_pack_pending_transition` save dict (around line 780-790) to include `"action_mask": mask`.

**Site 2 — leash/fail path (line 727):** Add `action_mask=mask` to the Transition constructor.

**Site 3 — normal transition (line 930):** Add `action_mask=mask` to the Transition constructor.

**Site 4 — any other Transition() calls:** Search for `Transition(` in bridge.py and update all of them.

Example for the main transition at line 930:
```python
self._trajectory_buf.add(Transition(
    grid=grid, features=features,
    action_type=action_type.item(),
    log_prob=log_prob.item(),
    value=value.item(),
    reward=reward, done=done,
    world_grid=world_grid,
    action_mask=mask,  # NEW — pass the mask used during act()
))
```

- [ ] **Step 10: Run all ML tests**

Run: `python -m pytest tests/factorio/test_ml_policy.py tests/factorio/test_trainer.py tests/factorio/test_ml_e2e.py -v`
Expected: ALL PASS (test_ml_e2e may need Transition updated to include action_mask=None)

- [ ] **Step 11: Commit**

```bash
git add fleet/factorio/ml_policy.py fleet/factorio/trainer.py tests/factorio/test_ml_policy.py
git commit -m "fix(factorio): apply action mask in evaluate_action — PPO ratio was systematically wrong"
```

---

### Task 3: PLACE_NEAR should check has_drill separately

**Files:**
- Modify: `fleet/factorio/action_space.py:586-620`
- Test: `tests/factorio/test_action_space.py`

In lesson 2, if agent has no drills (only furnaces), both PLACE and PLACE_NEAR are disabled. The agent is trapped in MINE/WAIT. PLACE_NEAR should stay enabled if any drill is in inventory, regardless of furnaces.

- [ ] **Step 1: Write the failing test**

```python
# In tests/factorio/test_action_space.py
def test_place_near_enabled_with_drill_only():
    """In lesson 2, PLACE_NEAR must be enabled if burner-mining-drill is in inventory."""
    from factorio.action_space import ActionSpace, ActionType
    space = ActionSpace(phase=1)
    # Inventory with drill but no furnace
    inventory = {"burner-mining-drill": 3}
    mask = space.get_action_type_mask(inventory, phase=1, lesson_index=2)
    assert mask[ActionType.PLACE.value] == 1, "PLACE should be enabled with drill"
    assert mask[ActionType.PLACE_NEAR.value] == 1, "PLACE_NEAR should be enabled with drill"


def test_place_near_disabled_no_placeable():
    """In lesson 2, PLACE/PLACE_NEAR disabled if inventory has no placeable entities."""
    from factorio.action_space import ActionSpace, ActionType
    space = ActionSpace(phase=1)
    inventory = {"coal": 50, "iron-ore": 100}  # no placeable entities
    mask = space.get_action_type_mask(inventory, phase=1, lesson_index=2)
    assert mask[ActionType.PLACE.value] == 0
    assert mask[ActionType.PLACE_NEAR.value] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_action_space.py::test_place_near_enabled_with_drill_only -v`
Expected: May PASS if drill is in `_entity_to_local`. Verify both tests. The key is that the mask logic is correct.

- [ ] **Step 3: Ensure drill-specific logic is explicit**

In `fleet/factorio/action_space.py`, around line 586, make the `has_placeable` check more explicit:

```python
# PLACE requires at least one placeable entity in inventory
has_placeable = any(
    item in self._entity_to_local for item in inventory
)
mask[ActionType.PLACE.value] = 1 if has_placeable else 0

# PLACE_NEAR: same requirement as PLACE (need placeable entity in inventory)
if ActionType.PLACE_NEAR.value < len(mask):
    mask[ActionType.PLACE_NEAR.value] = mask[ActionType.PLACE.value]
```

Verify `burner-mining-drill` IS in `_entity_to_local` for Phase 1. If not, add it. The fix is ensuring the entity registry includes drills so `has_placeable` is True when drills are present.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/factorio/test_action_space.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/action_space.py tests/factorio/test_action_space.py
git commit -m "fix(factorio): ensure PLACE_NEAR enabled when drill in inventory for lesson 2"
```

---

### Task 4: Add ore-proximity shaped reward for lesson 2

**Files:**
- Modify: `fleet/factorio/reward.py:107-223`
- Test: `tests/factorio/test_reward.py`

The agent has no gradient toward "stand next to ore and PLACE drill." Add a small bonus when the agent is adjacent to an ore tile during lesson 2.

- [ ] **Step 1: Write the failing test**

```python
# In tests/factorio/test_reward.py
def test_ore_proximity_bonus_lesson2():
    """Agent near ore in lesson 2 should get a proximity bonus."""
    from factorio.reward import RewardComputer
    from factorio.state_parser import GameState

    rc = RewardComputer(phase=1)

    prev = GameState(player_position={"x": 0, "y": 0}, inventory={}, entities=[])
    # Agent at (5, 5), near ore
    curr = GameState(player_position={"x": 5, "y": 5}, inventory={}, entities=[])

    # Without ore_near context — baseline
    r_base = rc.compute(prev, curr, action_success=True, lesson_passed=False,
                        phase_complete=False, action_type=0, lesson_index=3)

    rc2 = RewardComputer(phase=1)
    # With lesson_index=2 and near_ore=True
    r_near = rc2.compute(prev, curr, action_success=True, lesson_passed=False,
                         phase_complete=False, action_type=0,
                         lesson_index=2, near_ore=True)

    assert r_near > r_base, "Near-ore bonus should increase reward in lesson 2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_reward.py::test_ore_proximity_bonus_lesson2 -v`
Expected: FAIL — `compute()` doesn't accept `lesson_index` or `near_ore` params

- [ ] **Step 3: Add ore proximity bonus**

Add constant at top of `fleet/factorio/reward.py`:
```python
_ORE_PROXIMITY_BONUS = 0.1  # lesson 2: reward for being adjacent to ore
```

Add `lesson_index` and `near_ore` to BOTH `compute()` AND `_raw_reward()` signatures, and forward them:

In `compute()` (line 107):
```python
def compute(
    self,
    prev_state: GameState,
    curr_state: GameState,
    action_success: bool,
    lesson_passed: bool,
    phase_complete: bool,
    metrics=None,
    action_type: int = -1,
    other_agent_positions: list[tuple[float, float]] | None = None,
    pack_completed: bool = False,
    pack_aborted: bool = False,
    lesson_index: int = -1,   # NEW
    near_ore: bool = False,   # NEW
) -> float:
```

Forward them in the `_raw_reward` call (line 138-141):
```python
reward = self._raw_reward(
    prev_state, curr_state, action_success, lesson_passed, phase_complete,
    metrics, action_type, other_agent_positions,
    pack_completed, pack_aborted,
    lesson_index, near_ore,  # NEW — forward to _raw_reward
)
```

In `_raw_reward()` (line 154):
```python
def _raw_reward(
    self, prev, curr, action_success, lesson_passed, phase_complete,
    metrics=None, action_type=-1, other_agent_positions=None,
    pack_completed=False, pack_aborted=False,
    lesson_index: int = -1, near_ore: bool = False,  # NEW
) -> float:
```

Add to `_raw_reward()` body (after the entity placement bonus block, around line 212):
```python
# Ore proximity bonus — lesson 2 only, guides agent toward ore for drill placement
if lesson_index == 2 and near_ore:
    r += _ORE_PROXIMITY_BONUS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/factorio/test_reward.py::test_ore_proximity_bonus_lesson2 -v`
Expected: PASS

- [ ] **Step 5: Wire near_ore into bridge.py**

In `fleet/factorio/bridge.py`, in the reward computation section of `_ml_tick_agent`, compute `near_ore` from spatial memory before calling `reward.compute()`:

```python
# Check if agent is near ore (for lesson 2 shaped reward)
near_ore = False
if current_lesson == 2 and agent_mem:
    for rtype in ("iron-ore", "copper-ore", "coal", "stone"):
        result = agent_mem.nearest_resource(px, py, rtype)
        if result is not None and result[1] < 3.0:  # within 3 tiles
            near_ore = True
            break
```

Pass `lesson_index=current_lesson, near_ore=near_ore` to the main `reward.compute()` call (around line 919).

**Important:** There are 4 `reward.compute()` call sites in bridge.py (lines ~643, ~721, ~775, ~919). The new `lesson_index` and `near_ore` params default to `-1` and `False`, so callers that don't pass them will still work correctly (no bonus applied). Only the main reward path at line ~919 needs the new params. The other call sites (pack completion, leash failure, stamp path) are fine with defaults since they're not in lesson 2's primary loop.

- [ ] **Step 6: Run all reward tests**

Run: `python -m pytest tests/factorio/test_reward.py tests/factorio/test_reward_packs.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/reward.py fleet/factorio/bridge.py tests/factorio/test_reward.py
git commit -m "feat(factorio): ore-proximity reward bonus in lesson 2 — guides agent toward drill placement"
```

---

### Task 5: Enforce max_attempts in LessonTracker

**Files:**
- Modify: `fleet/factorio/curriculum.py:90-121`
- Modify: `fleet/factorio/curriculum_manager.py:94-118`
- Test: `tests/factorio/test_curriculum_checkpoints.py`

`max_attempts` is defined in TOML but never enforced. Agent loops forever on stuck lessons.

- [ ] **Step 1: Write the failing test**

```python
# In tests/factorio/test_curriculum_checkpoints.py
def test_max_attempts_auto_skip():
    """LessonTracker should auto-pass lesson when max_attempts exceeded."""
    from factorio.curriculum import LessonTracker

    tracker = LessonTracker(total_lessons=3, max_attempts=[100, 100, 100])
    # Simulate 101 attempts on lesson 0
    for _ in range(101):
        tracker.mark_attempt(0)
    # Lesson 0 should be auto-passed
    assert tracker._passed[0] is True, "Lesson should auto-pass after max_attempts"
    assert tracker.current_index == 1, "Should advance to lesson 1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_curriculum_checkpoints.py::test_max_attempts_auto_skip -v`
Expected: FAIL — LessonTracker doesn't accept max_attempts

- [ ] **Step 3: Add max_attempts to LessonTracker**

In `fleet/factorio/curriculum.py`, modify `LessonTracker`:

```python
class LessonTracker:
    def __init__(self, total_lessons: int, max_attempts: list[int] | None = None):
        self._passed = [False] * total_lessons
        self._attempts = [0] * total_lessons
        self._max_attempts = max_attempts or [0] * total_lessons  # 0 = no limit

    def mark_attempt(self, index: int) -> None:
        if 0 <= index < len(self._attempts):
            self._attempts[index] += 1
            # Auto-pass if max_attempts exceeded (0 = no limit)
            limit = self._max_attempts[index] if index < len(self._max_attempts) else 0
            if limit > 0 and self._attempts[index] > limit and not self._passed[index]:
                self._passed[index] = True
                log.info("Lesson %d auto-passed: exceeded max_attempts=%d", index, limit)
```

- [ ] **Step 4: Wire max_attempts from TOML in CurriculumManager**

In `fleet/factorio/curriculum_manager.py`, in `_load_phase()`, after loading lessons:

```python
# Extract max_attempts from lesson definitions (default 0 = no limit)
max_attempts = [lesson.get("max_attempts", 0) for lesson in self._lessons]
self._tracker = LessonTracker(total_lessons=len(self._lessons), max_attempts=max_attempts)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/factorio/test_curriculum_checkpoints.py::test_max_attempts_auto_skip -v`
Expected: PASS

- [ ] **Step 6: Run all curriculum tests**

Run: `python -m pytest tests/factorio/test_curriculum_checkpoints.py tests/factorio/test_criteria_parser.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/curriculum.py fleet/factorio/curriculum_manager.py tests/factorio/test_curriculum_checkpoints.py
git commit -m "feat(factorio): enforce max_attempts in LessonTracker — auto-skip stuck lessons"
```

---

### Task 6: Import reward constants in bridge.py

**Files:**
- Modify: `fleet/factorio/bridge.py:597-602`

Hardcoded `1.0` and `-0.5` in bridge.py duplicate constants from reward.py. If reward.py values change, bridge silently uses wrong values.

- [ ] **Step 1: Add import at top of bridge.py**

After the existing imports in `fleet/factorio/bridge.py`, add:

```python
from factorio.reward import _PACK_COMPLETE_BONUS, _PACK_ABORT_PENALTY
```

- [ ] **Step 2: Replace hardcoded values**

In `fleet/factorio/bridge.py` around line 599-602, replace:

```python
# Before:
if pack_completed:
    cum_reward += 1.0   # _PACK_COMPLETE_BONUS
elif pack_aborted:
    cum_reward += -0.5  # _PACK_ABORT_PENALTY

# After:
if pack_completed:
    cum_reward += _PACK_COMPLETE_BONUS
elif pack_aborted:
    cum_reward += _PACK_ABORT_PENALTY
```

- [ ] **Step 3: Run E2E test to verify no regression**

Run: `python -m pytest tests/factorio/test_ml_e2e.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "fix(factorio): import reward constants — stop hardcoding _PACK_COMPLETE_BONUS/_PACK_ABORT_PENALTY"
```

---

## TIER 2: Silent Failures & Safety

### Task 7: Replace bare `pass` with log.warning

**Files:**
- Modify: `fleet/factorio/bridge.py:487-488,505-506`

Two bare `pass` blocks silently swallow exceptions in the hot ML tick path.

- [ ] **Step 1: Fix resupply exception handler (line 487-488)**

```python
# Before:
except Exception:
    pass  # non-critical

# After:
except Exception:
    log.warning("Resupply failed for agent %d", agent_id, exc_info=True)
```

- [ ] **Step 2: Fix leash teleport exception handler (line 505-506)**

```python
# Before:
except Exception:
    pass

# After:
except Exception:
    log.warning("Spawn leash teleport failed for agent %d", agent_id, exc_info=True)
```

- [ ] **Step 3: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "fix(factorio): log resupply/leash failures — was silently swallowing exceptions"
```

---

### Task 8: Add None guards on plan_queue/plan_history endpoints

**Files:**
- Modify: `fleet/factorio/bridge_api.py:179-212`

Missing `_brain is None` checks cause 500 + traceback leak.

- [ ] **Step 1: Add guards**

In `fleet/factorio/bridge_api.py`, wrap `plan_queue()` (line 180) and `plan_history()` (line 209):

```python
@app.route("/api/plan/queue", methods=["GET"])
def plan_queue():
    if _brain is None:
        return jsonify({"error": "AgentBrain not initialized"}), 503
    with _brain._lock:
        # ... existing code ...

@app.route("/api/plan/history", methods=["GET"])
def plan_history():
    if _brain is None:
        return jsonify({"error": "AgentBrain not initialized"}), 503
    with _brain._lock:
        history = list(_brain._plan_history)
    return jsonify({"history": history})
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge_api.py
git commit -m "fix(factorio): add None guards on plan_queue/plan_history — prevent 500 traceback leak"
```

---

### Task 9: Replace os._exit with graceful shutdown

**Files:**
- Modify: `fleet/factorio/bridge_api.py:262-270`

`os._exit(0)` bypasses all Python cleanup.

- [ ] **Step 1: Replace shutdown handler**

```python
# Before:
threading.Timer(0.5, lambda: os._exit(0)).start()

# After:
# Use bridge._running flag for graceful shutdown (Windows-safe — SIGTERM unreliable on Windows)
if _bridge_ref is not None:
    _bridge_ref.stop()  # sets _running = False, lets asyncio loop exit cleanly
else:
    import signal
    os.kill(os.getpid(), signal.SIGINT)
```

Note: This requires storing a reference to the bridge instance in a module-level `_bridge_ref` variable (set during `create_api()` call). Add `_bridge_ref: FactorioBridge | None = None` at the module level alongside the other globals, and set it in `create_api()`.

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge_api.py
git commit -m "fix(factorio): graceful shutdown via SIGTERM — os._exit bypassed all cleanup"
```

---

### Task 10: Fix weights_only in trainer checkpoint load

**Files:**
- Modify: `fleet/factorio/trainer.py:338`

- [ ] **Step 1: Change to weights_only=True**

```python
# Before:
ckpt = torch.load(path, map_location=self._device, weights_only=False)

# After:
ckpt = torch.load(path, map_location=self._device, weights_only=True)
```

Note: Optimizer state dicts are plain tensors/dicts — `weights_only=True` handles them fine in PyTorch >= 2.6. If this causes a load error, add a comment explaining why False is needed and pin to a specific PyTorch version check.

- [ ] **Step 2: Run trainer tests**

Run: `python -m pytest tests/factorio/test_trainer.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add fleet/factorio/trainer.py
git commit -m "fix(factorio): weights_only=True for checkpoint load — prevent arbitrary code execution"
```

---

## TIER 3: Architecture & Performance

### Task 11: torch.from_numpy instead of torch.tensor

**Files:**
- Modify: `fleet/factorio/bridge.py:667-669`

`torch.tensor()` on numpy arrays copies data. `torch.from_numpy()` is zero-copy.

- [ ] **Step 1: Replace tensor creation**

```python
# Before:
grid_t = torch.tensor(grid).unsqueeze(0)
world_t = torch.tensor(world_grid).unsqueeze(0)
feat_t = torch.tensor(features).unsqueeze(0)

# After:
grid_t = torch.from_numpy(grid).unsqueeze(0).float()
world_t = torch.from_numpy(world_grid).unsqueeze(0).float()
feat_t = torch.from_numpy(features).unsqueeze(0).float()
```

- [ ] **Step 2: Run E2E test**

Run: `python -m pytest tests/factorio/test_ml_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "perf(factorio): torch.from_numpy for zero-copy tensor creation — 12 fewer copies per tick"
```

---

### Task 12: Move hasattr lazy inits into __init__

**Files:**
- Modify: `fleet/factorio/bridge.py:127-137,821,867-869`

Four `hasattr` guards in the hot path should be `__init__` initializations.

- [ ] **Step 1: Add initializations to __init__ (ML branch)**

In `fleet/factorio/bridge.py`, inside the `if self.config.mode == "ml":` block in `__init__` (after line 137):

```python
self._pack_prev_results: dict[int, dict] = {}
self._insert_count: int = 0
self._production_snapshot: dict = {}
self._last_checkpoint_save: int = 0
```

- [ ] **Step 2: Remove hasattr guards**

Remove the `hasattr` checks at lines 821, 867-869, and any other occurrences. Replace with direct usage since fields are now always initialized.

```python
# Before (line 821):
if not hasattr(self, '_pack_prev_results'):
    self._pack_prev_results = {}

# After: (just delete the guard — already initialized in __init__)
```

Same for `_insert_count` and `_production_snapshot` at lines 867-869.

- [ ] **Step 3: Run E2E test**

Run: `python -m pytest tests/factorio/test_ml_e2e.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "refactor(factorio): move hasattr lazy inits to __init__ — explicit state contract"
```

---

### Task 13: Fix direction normalization

**Files:**
- Modify: `fleet/factorio/state_encoder.py:245`
- Test: `tests/factorio/test_state_encoder.py`

Factorio 2.0 uses 16-direction encoding (0-15). Dividing by 7.0 produces features >1.0 for directions 8-15.

- [ ] **Step 1: Write the failing test**

```python
# In tests/factorio/test_state_encoder.py
def test_direction_normalization_range():
    """All direction features must be in [0, 1] range (16-dir encoding)."""
    from factorio.state_encoder import StateEncoder
    from factorio.state_parser import GameState, Entity

    encoder = StateEncoder(phase=1)
    # Entity with direction 12 (south in 16-dir) — use proper Entity dataclass
    entities = [Entity(name='stone-furnace', position={'x': 0, 'y': 0},
                       direction=12, type='furnace', unit_number=1)]
    state = GameState(player_position={"x": 0, "y": 0}, entities=entities,
                      inventory={}, resources=[])
    grid, _, _ = encoder.encode(state, None)
    # Direction channel (ch 1) should be <= 1.0
    assert grid[1].max() <= 1.0, f"Direction feature {grid[1].max():.2f} > 1.0 — wrong normalization"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/factorio/test_state_encoder.py::test_direction_normalization_range -v`
Expected: FAIL — direction 12 / 7.0 = 1.71 > 1.0

- [ ] **Step 3: Fix normalization**

In `fleet/factorio/state_encoder.py:245`:

```python
# Before:
grid[1, gy, gx] = entity.direction / 7.0

# After:
grid[1, gy, gx] = entity.direction / 15.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/factorio/test_state_encoder.py::test_direction_normalization_range -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/state_encoder.py tests/factorio/test_state_encoder.py
git commit -m "fix(factorio): direction normalization /7→/15 — Factorio 2.0 uses 16 directions"
```

---

### Task 14: Fix per-agent reward phase advancement

**Files:**
- Modify: `fleet/factorio/bridge.py:1000`

On phase advancement, only the compat fallback `_reward` gets `set_phase()`. Per-agent reward computers in `_agent_reward` are never updated.

- [ ] **Step 1: Update all per-agent reward computers**

In `fleet/factorio/bridge.py`, around line 1000, after `self._reward.set_phase(new_phase)`:

```python
self._reward.set_phase(new_phase)
self._reward.reset_normalizer()
# Update ALL per-agent reward computers too
for agent_reward in self._agent_reward.values():
    agent_reward.set_phase(new_phase)
    agent_reward.reset_normalizer()
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "fix(factorio): update per-agent reward computers on phase advance — was only updating fallback"
```

---

### Task 15: Run full test suite

**Files:** None (verification only)

- [ ] **Step 1: Run all Factorio tests**

Run: `python -m pytest tests/factorio/ -v --tb=short`
Expected: ALL PASS (223+ tests)

- [ ] **Step 2: Run smoke tests**

Run: `python fleet/smoke_test.py --fast`
Expected: 51/51 PASS

- [ ] **Step 3: Final commit (if any test fixups needed)**

```bash
git add -A
git commit -m "test(factorio): fix any test regressions from audit fixes"
```

---

## Summary

| Tier | Tasks | Commits | Impact |
|---|---|---|---|
| 1: Training | 1-6 | 6 | Unblocks lesson 2 — entropy, PPO mask, reward shaping |
| 2: Safety | 7-10 | 4 | Silent failures visible, security fixes |
| 3: Arch/Perf | 11-14 | 4 | Zero-copy tensors, clean init, correct normalization |
| Verify | 15 | 0-1 | Full regression test |
| **Total** | **15** | **~15** | **54 audit findings addressed** |

### Not in this plan (deferred)
- **bridge.py god-object decomposition** — needs its own plan (Task 11 from arch audit)
- **CurriculumManager.sync_from()** — needs design for thread-safe curriculum sync
- **Test coverage for 0% files** (rcon_client, state_parser, etc.) — separate testing sprint
- **P0 security fixes** (JWT bypass, path traversal) — separate security plan
