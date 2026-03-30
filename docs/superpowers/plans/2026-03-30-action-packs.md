# Action Packs & Blueprint Stamps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Factorio RL agents reusable action packs (multi-step sequences) and blueprint stamps (instant placement) to accelerate progression from bootstrap to white science.

**Architecture:** Extends the existing single-network PPO with two new action types (PACK=9, STAMP=10). A PackRegistry holds hardcoded packs, blueprint stamps, and learned packs. PackExecutor manages multi-tick pack execution within the bridge loop. 8-checkpoint curriculum replaces the 4-phase system.

**Tech Stack:** Python, PyTorch, TOML curriculum files, JSON pack definitions, Factorio RCON/Lua

**Spec:** `docs/superpowers/specs/2026-03-30-action-packs-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet/factorio/pack_registry.py` | **Create** | ActionPack, BlueprintStamp dataclasses, PackRegistry singleton |
| `fleet/factorio/pack_executor.py` | **Create** | PackExecutor state machine for multi-tick pack execution |
| `fleet/factorio/pack_recorder.py` | **Create** | PackRecorder for learned pack discovery |
| `fleet/factorio/packs/hardcoded/` | **Create** | JSON files for curated action packs |
| `fleet/factorio/packs/blueprints/` | **Create** | JSON files for Factorio blueprint strings |
| `fleet/factorio/packs/learned/` | **Create** | Auto-populated learned pack storage |
| `fleet/factorio/action_space.py` | **Modify** | Add PACK/STAMP to ActionType, expand registries |
| `fleet/factorio/ml_policy.py` | **Modify** | Add pack_head, offset heads, param routing |
| `fleet/factorio/state_encoder.py` | **Modify** | Bump _BASE_FEATURE_DIM 68→69, add pack_progress |
| `fleet/factorio/reward.py` | **Modify** | Add pack/stamp completion bonuses, abort penalty |
| `fleet/factorio/bridge.py` | **Modify** | Wire PackExecutor into tick loop, fix num_action_types |
| `fleet/factorio/curriculum_manager.py` | **Modify** | Add checkpoint property, support 8 phases |
| `fleet/factorio/curricula/phase5_*.toml` – `phase8_*.toml` | **Create** | Checkpoint 4-7 curriculum files |
| `tests/factorio/test_pack_registry.py` | **Create** | Registry tests |
| `tests/factorio/test_pack_executor.py` | **Create** | Executor tests |
| `tests/factorio/test_pack_recorder.py` | **Create** | Recorder tests |
| `tests/factorio/test_action_space.py` | **Modify** | Tests for new action types + expanded registries |
| `tests/factorio/test_reward.py` | **Modify** | Tests for pack/stamp reward signals |

---

## Task 1: Pack Registry — Data Models & Registry

**Files:**
- Create: `fleet/factorio/pack_registry.py`
- Test: `tests/factorio/test_pack_registry.py`

- [ ] **Step 1: Write failing tests for ActionPack and BlueprintStamp**

```python
# tests/factorio/test_pack_registry.py
import json
import pytest
from pathlib import Path


def test_action_pack_creation():
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="smelt_iron_line",
        actions=[
            {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
            {"action": "insert", "entity": "stone-furnace", "item": "coal", "count": 5},
        ],
        phase_required=0,
        origin="hardcoded",
    )
    assert pack.name == "smelt_iron_line"
    assert len(pack.actions) == 2
    assert pack.success_count == 0
    assert pack.avg_reward == 0.0


def test_action_pack_can_execute_checks_inventory():
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="test_pack",
        actions=[
            {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
        ],
        phase_required=0,
        origin="hardcoded",
        required_items={"stone-furnace": 1, "coal": 5},
    )
    assert pack.can_execute({"stone-furnace": 2, "coal": 10})
    assert not pack.can_execute({"stone-furnace": 0, "coal": 10})
    assert not pack.can_execute({})


def test_blueprint_stamp_creation():
    from factorio.pack_registry import BlueprintStamp
    stamp = BlueprintStamp(
        name="basic_power_station",
        blueprint_string="0eNRfake...",
        footprint=(5, 3),
        phase_required=1,
        required_items={"boiler": 1, "steam-engine": 1, "offshore-pump": 1},
    )
    assert stamp.name == "basic_power_station"
    assert stamp.footprint == (5, 3)
    assert stamp.can_execute({"boiler": 1, "steam-engine": 1, "offshore-pump": 1})
    assert not stamp.can_execute({"boiler": 1})


def test_blueprint_stamp_can_execute_checks_inventory():
    from factorio.pack_registry import BlueprintStamp
    stamp = BlueprintStamp(
        name="test_stamp",
        blueprint_string="0eNR...",
        footprint=(2, 2),
        phase_required=0,
        required_items={"stone-furnace": 4, "inserter": 4},
    )
    assert stamp.can_execute({"stone-furnace": 4, "inserter": 4, "coal": 100})
    assert not stamp.can_execute({"stone-furnace": 3, "inserter": 4})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'factorio.pack_registry'`

- [ ] **Step 3: Implement ActionPack and BlueprintStamp dataclasses**

```python
# fleet/factorio/pack_registry.py
"""Pack Registry — action packs, blueprint stamps, and registry for Factorio RL agents."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MAX_PACK_SLOTS = 64


@dataclass
class ActionPack:
    """A named sequence of primitive actions the agent can invoke as one decision."""

    name: str
    actions: list[dict]
    phase_required: int
    origin: str  # "hardcoded" | "learned"
    success_count: int = 0
    avg_reward: float = 0.0
    required_items: dict[str, int] = field(default_factory=dict)

    def can_execute(self, inventory: dict[str, int]) -> bool:
        """Check if the agent has enough items to start this pack."""
        for item, need in self.required_items.items():
            if inventory.get(item, 0) < need:
                return False
        return True

    total_invocations: int = 0

    def record_result(self, success: bool, reward: float) -> None:
        """Update running statistics after pack execution."""
        self.total_invocations += 1
        if success:
            self.success_count += 1
        alpha = 1.0 / self.total_invocations
        self.avg_reward = (1 - alpha) * self.avg_reward + alpha * reward

    @property
    def success_rate(self) -> float:
        """Success rate over all invocations (0.0-1.0)."""
        return self.success_count / max(self.total_invocations, 1)


@dataclass
class BlueprintStamp:
    """A real Factorio blueprint string placed via single RCON call."""

    name: str
    blueprint_string: str
    footprint: tuple[int, int]
    phase_required: int
    required_items: dict[str, int] = field(default_factory=dict)

    def can_execute(self, inventory: dict[str, int]) -> bool:
        """Check if the agent has enough items to stamp this blueprint."""
        for item, need in self.required_items.items():
            if inventory.get(item, 0) < need:
                return False
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_registry.py::test_action_pack_creation ../tests/factorio/test_pack_registry.py::test_action_pack_can_execute_checks_inventory ../tests/factorio/test_pack_registry.py::test_blueprint_stamp_creation ../tests/factorio/test_pack_registry.py::test_blueprint_stamp_can_execute_checks_inventory -v`
Expected: 4 PASS

- [ ] **Step 5: Write failing tests for PackRegistry**

```python
# Append to tests/factorio/test_pack_registry.py

def test_registry_register_pack():
    from factorio.pack_registry import PackRegistry, ActionPack
    reg = PackRegistry()
    pack = ActionPack(name="test", actions=[{"action": "wait"}], phase_required=0, origin="hardcoded")
    pack_id = reg.register_pack(pack)
    assert pack_id == 0
    assert reg.get_by_id(pack_id) is pack


def test_registry_register_stamp():
    from factorio.pack_registry import PackRegistry, BlueprintStamp
    reg = PackRegistry()
    stamp = BlueprintStamp(name="test", blueprint_string="0eNR...", footprint=(2, 2), phase_required=1)
    stamp_id = reg.register_stamp(stamp)
    assert stamp_id == 0
    assert reg.get_by_id(stamp_id) is stamp


def test_registry_get_available_filters_by_phase():
    from factorio.pack_registry import PackRegistry, ActionPack
    reg = PackRegistry()
    p0 = ActionPack(name="early", actions=[{"action": "wait"}], phase_required=0, origin="hardcoded")
    p2 = ActionPack(name="late", actions=[{"action": "wait"}], phase_required=2, origin="hardcoded")
    reg.register_pack(p0)
    reg.register_pack(p2)
    avail_0 = reg.get_available(phase=0)
    avail_2 = reg.get_available(phase=2)
    assert len(avail_0) == 1
    assert avail_0[0].name == "early"
    assert len(avail_2) == 2


def test_registry_max_slots_enforced():
    from factorio.pack_registry import PackRegistry, ActionPack, MAX_PACK_SLOTS
    reg = PackRegistry()
    for i in range(MAX_PACK_SLOTS):
        reg.register_pack(ActionPack(name=f"p{i}", actions=[], phase_required=0, origin="hardcoded"))
    with pytest.raises(ValueError, match="MAX_PACK_SLOTS"):
        reg.register_pack(ActionPack(name="overflow", actions=[], phase_required=0, origin="hardcoded"))


def test_registry_get_pack_mask():
    from factorio.pack_registry import PackRegistry, ActionPack, MAX_PACK_SLOTS
    reg = PackRegistry()
    reg.register_pack(ActionPack(
        name="needs_iron", actions=[], phase_required=0, origin="hardcoded",
        required_items={"iron-plate": 10},
    ))
    reg.register_pack(ActionPack(
        name="free", actions=[], phase_required=0, origin="hardcoded",
    ))
    mask = reg.get_pack_mask(phase=0, inventory={"iron-plate": 5})
    assert len(mask) == MAX_PACK_SLOTS
    assert mask[0] == 0  # can't afford
    assert mask[1] == 1  # free pack
    assert all(m == 0 for m in mask[2:])  # empty slots


def test_registry_is_pack_vs_stamp():
    from factorio.pack_registry import PackRegistry, ActionPack, BlueprintStamp
    reg = PackRegistry()
    reg.register_pack(ActionPack(name="p", actions=[], phase_required=0, origin="hardcoded"))
    reg.register_stamp(BlueprintStamp(name="s", blueprint_string="x", footprint=(1,1), phase_required=0))
    assert reg.is_stamp(0) is False
    assert reg.is_stamp(1) is True
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_registry.py -v`
Expected: FAIL — `PackRegistry` not defined or missing methods

- [ ] **Step 7: Implement PackRegistry**

```python
# Append to fleet/factorio/pack_registry.py

class PackRegistry:
    """Singleton registry for action packs and blueprint stamps.

    Uses copy-on-write semantics for thread safety — mutations create
    new containers and swap atomically.
    """

    def __init__(self) -> None:
        self._items: list[ActionPack | BlueprintStamp] = []
        self._stamp_flags: list[bool] = []

    def register_pack(self, pack: ActionPack) -> int:
        """Add a hardcoded/learned pack. Returns its slot ID."""
        if len(self._items) >= MAX_PACK_SLOTS:
            raise ValueError(f"MAX_PACK_SLOTS ({MAX_PACK_SLOTS}) reached")
        slot_id = len(self._items)
        new_items = list(self._items)
        new_items.append(pack)
        new_flags = list(self._stamp_flags)
        new_flags.append(False)
        self._items = new_items
        self._stamp_flags = new_flags
        return slot_id

    def register_stamp(self, stamp: BlueprintStamp) -> int:
        """Add a blueprint stamp. Returns its slot ID."""
        if len(self._items) >= MAX_PACK_SLOTS:
            raise ValueError(f"MAX_PACK_SLOTS ({MAX_PACK_SLOTS}) reached")
        slot_id = len(self._items)
        new_items = list(self._items)
        new_items.append(stamp)
        new_flags = list(self._stamp_flags)
        new_flags.append(True)
        self._items = new_items
        self._stamp_flags = new_flags
        return slot_id

    def get_by_id(self, slot_id: int) -> ActionPack | BlueprintStamp:
        """Get a pack or stamp by slot ID."""
        return self._items[slot_id]

    def is_stamp(self, slot_id: int) -> bool:
        """True if slot holds a BlueprintStamp, False if ActionPack."""
        return self._stamp_flags[slot_id]

    def get_available(self, phase: int) -> list[ActionPack | BlueprintStamp]:
        """Return packs/stamps unlocked at or before the given phase."""
        return [item for item in self._items if item.phase_required <= phase]

    def get_pack_mask(self, phase: int, inventory: dict[str, int]) -> list[int]:
        """Return a MAX_PACK_SLOTS-length binary mask. 1 = available and affordable."""
        mask = [0] * MAX_PACK_SLOTS
        for i, item in enumerate(self._items):
            if item.phase_required <= phase and item.can_execute(inventory):
                mask[i] = 1
        return mask

    def promote_learned(self, candidate: ActionPack) -> int | None:
        """Promote a learned candidate if it meets quality thresholds."""
        if len(candidate.actions) < 5 or len(candidate.actions) > 50:
            return None
        # Check overlap with existing packs
        for item in self._items:
            if isinstance(item, ActionPack) and _action_overlap(item, candidate) > 0.8:
                return None
        candidate.origin = "learned"
        return self.register_pack(candidate)

    def save_learned(self, directory: Path) -> None:
        """Serialize learned packs to JSON files."""
        directory.mkdir(parents=True, exist_ok=True)
        for item in self._items:
            if isinstance(item, ActionPack) and item.origin == "learned":
                path = directory / f"{item.name}.json"
                path.write_text(json.dumps({
                    "name": item.name,
                    "actions": item.actions,
                    "phase_required": item.phase_required,
                    "origin": item.origin,
                    "success_count": item.success_count,
                    "avg_reward": item.avg_reward,
                    "required_items": item.required_items,
                }, indent=2))

    def load_packs(self, directory: Path) -> int:
        """Load packs from JSON files in a directory. Returns count loaded."""
        if not directory.exists():
            return 0
        count = 0
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                pack = ActionPack(
                    name=data["name"],
                    actions=data["actions"],
                    phase_required=data["phase_required"],
                    origin=data.get("origin", "hardcoded"),
                    success_count=data.get("success_count", 0),
                    avg_reward=data.get("avg_reward", 0.0),
                    required_items=data.get("required_items", {}),
                )
                self.register_pack(pack)
                count += 1
            except Exception:
                log.warning("Failed to load pack from %s", path, exc_info=True)
        return count

    def load_stamps(self, directory: Path) -> int:
        """Load blueprint stamps from JSON files. Returns count loaded."""
        if not directory.exists():
            return 0
        count = 0
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                stamp = BlueprintStamp(
                    name=data["name"],
                    blueprint_string=data["blueprint_string"],
                    footprint=tuple(data["footprint"]),
                    phase_required=data["phase_required"],
                    required_items=data.get("required_items", {}),
                )
                self.register_stamp(stamp)
                count += 1
            except Exception:
                log.warning("Failed to load stamp from %s", path, exc_info=True)
        return count


    def prune_underperforming(self) -> list[str]:
        """Demote learned packs with <40% success rate over 20+ invocations."""
        pruned = []
        new_items = []
        new_flags = []
        for i, item in enumerate(self._items):
            if (isinstance(item, ActionPack) and item.origin == "learned"
                    and item.total_invocations >= 20 and item.success_rate < 0.4):
                pruned.append(item.name)
                log.info("Pruned underperforming learned pack: %s (%.0f%% success)",
                         item.name, item.success_rate * 100)
            else:
                new_items.append(item)
                new_flags.append(self._stamp_flags[i])
        if pruned:
            self._items = new_items
            self._stamp_flags = new_flags
        return pruned


def _action_overlap(a: ActionPack, b: ActionPack) -> float:
    """Compute action sequence similarity (0.0-1.0) between two packs."""
    if not a.actions or not b.actions:
        return 0.0
    a_set = {json.dumps(act, sort_keys=True) for act in a.actions}
    b_set = {json.dumps(act, sort_keys=True) for act in b.actions}
    intersection = len(a_set & b_set)
    union = len(a_set | b_set)
    return intersection / union if union > 0 else 0.0
```

- [ ] **Step 8: Run all tests**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_registry.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add fleet/factorio/pack_registry.py tests/factorio/test_pack_registry.py
git commit -m "feat(factorio): add PackRegistry with ActionPack, BlueprintStamp, pack masking"
```

---

## Task 2: Pack Executor — Multi-Tick State Machine

**Files:**
- Create: `fleet/factorio/pack_executor.py`
- Test: `tests/factorio/test_pack_executor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_pack_executor.py
import pytest


def test_executor_start_returns_first_action():
    from factorio.pack_executor import PackExecutor
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="test",
        actions=[
            {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
            {"action": "insert", "entity": "stone-furnace", "item": "coal", "count": 5},
        ],
        phase_required=0, origin="hardcoded",
    )
    exe = PackExecutor()
    first = exe.start(pack, offset=(3, -2))
    assert first["action"] == "place"
    # Position offset applied
    assert first["position"]["x"] == 3
    assert first["position"]["y"] == -2
    assert exe.is_active
    assert exe.progress == pytest.approx(0.0)


def test_executor_next_step_advances():
    from factorio.pack_executor import PackExecutor
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="test",
        actions=[
            {"action": "place", "entity": "a", "position": {"x": 0, "y": 0}},
            {"action": "place", "entity": "b", "position": {"x": 1, "y": 0}},
            {"action": "place", "entity": "c", "position": {"x": 2, "y": 0}},
        ],
        phase_required=0, origin="hardcoded",
    )
    exe = PackExecutor()
    exe.start(pack, offset=(0, 0))
    second = exe.next_step({"success": True})
    assert second is not None
    assert second["entity"] == "b"
    assert exe.progress == pytest.approx(1 / 3)


def test_executor_completes_after_last_step():
    from factorio.pack_executor import PackExecutor
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="test",
        actions=[
            {"action": "place", "entity": "a", "position": {"x": 0, "y": 0}},
        ],
        phase_required=0, origin="hardcoded",
    )
    exe = PackExecutor()
    exe.start(pack, offset=(0, 0))
    result = exe.next_step({"success": True})
    assert result is None
    assert not exe.is_active
    assert exe.completed


def test_executor_aborts_after_3_failures():
    from factorio.pack_executor import PackExecutor
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="test",
        actions=[
            {"action": "place", "entity": "a", "position": {"x": 0, "y": 0}},
            {"action": "place", "entity": "b", "position": {"x": 1, "y": 0}},
        ],
        phase_required=0, origin="hardcoded",
    )
    exe = PackExecutor()
    exe.start(pack, offset=(0, 0))
    # Fail the first step 3 times
    retry1 = exe.next_step({"success": False})
    assert retry1 is not None  # retry same step
    retry2 = exe.next_step({"success": False})
    assert retry2 is not None  # retry same step
    retry3 = exe.next_step({"success": False})
    assert retry3 is None  # abort
    assert not exe.is_active
    assert exe.abort_reason == "step_failed_3x"


def test_executor_progress_fraction():
    from factorio.pack_executor import PackExecutor
    from factorio.pack_registry import ActionPack
    pack = ActionPack(
        name="test",
        actions=[{"action": "wait"}] * 4,
        phase_required=0, origin="hardcoded",
    )
    exe = PackExecutor()
    exe.start(pack, offset=(0, 0))
    assert exe.progress == pytest.approx(0.0)
    exe.next_step({"success": True})
    assert exe.progress == pytest.approx(0.25)
    exe.next_step({"success": True})
    assert exe.progress == pytest.approx(0.5)


def test_executor_cumulative_reward_tracking():
    from factorio.pack_executor import PackExecutor
    from factorio.pack_registry import ActionPack
    pack = ActionPack(name="test", actions=[{"action": "wait"}] * 2, phase_required=0, origin="hardcoded")
    exe = PackExecutor()
    exe.start(pack, offset=(0, 0))
    exe.accumulate_reward(0.5)
    exe.next_step({"success": True})
    exe.accumulate_reward(0.3)
    exe.next_step({"success": True})
    assert exe.cumulative_reward == pytest.approx(0.8)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_executor.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement PackExecutor**

```python
# fleet/factorio/pack_executor.py
"""PackExecutor — state machine for multi-tick action pack execution."""
from __future__ import annotations

import copy
import logging
from factorio.pack_registry import ActionPack

log = logging.getLogger(__name__)

_MAX_RETRIES = 3


class PackExecutor:
    """Manages in-flight pack execution, one primitive action per tick."""

    def __init__(self) -> None:
        self._pack: ActionPack | None = None
        self._step_index: int = 0
        self._retry_count: int = 0
        self._offset: tuple[int, int] = (0, 0)
        self._abort_reason: str | None = None
        self._completed: bool = False
        self._cumulative_reward: float = 0.0

    @property
    def is_active(self) -> bool:
        return self._pack is not None

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def abort_reason(self) -> str | None:
        return self._abort_reason

    @property
    def progress(self) -> float:
        if self._pack is None or not self._pack.actions:
            return 0.0
        return self._step_index / len(self._pack.actions)

    @property
    def cumulative_reward(self) -> float:
        return self._cumulative_reward

    @property
    def current_pack(self) -> ActionPack | None:
        return self._pack

    def accumulate_reward(self, reward: float) -> None:
        """Add a per-step reward to the running total."""
        self._cumulative_reward += reward

    def start(self, pack: ActionPack, offset: tuple[int, int]) -> dict:
        """Begin pack execution. Returns the first primitive action."""
        self._pack = pack
        self._step_index = 0
        self._retry_count = 0
        self._offset = offset
        self._abort_reason = None
        self._completed = False
        self._cumulative_reward = 0.0
        return self._current_action()

    def next_step(self, prev_result: dict) -> dict | None:
        """Advance based on previous result. Returns next action, or None if done/aborted."""
        if self._pack is None:
            return None

        success = prev_result.get("success", False)

        if success:
            self._retry_count = 0
            self._step_index += 1
            if self._step_index >= len(self._pack.actions):
                self._completed = True
                self._pack = None
                return None
            return self._current_action()
        else:
            self._retry_count += 1
            if self._retry_count >= _MAX_RETRIES:
                self._abort_reason = "step_failed_3x"
                self._pack = None
                return None
            return self._current_action()

    def abort(self, reason: str) -> None:
        """Force-abort the current pack."""
        self._abort_reason = reason
        self._pack = None

    def _current_action(self) -> dict:
        """Get current action with position offset applied."""
        action = copy.deepcopy(self._pack.actions[self._step_index])
        if "position" in action:
            action["position"]["x"] = action["position"].get("x", 0) + self._offset[0]
            action["position"]["y"] = action["position"].get("y", 0) + self._offset[1]
        return action
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_executor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/pack_executor.py tests/factorio/test_pack_executor.py
git commit -m "feat(factorio): add PackExecutor state machine for multi-tick pack execution"
```

---

## Task 3: Extend ActionType + Action Space

**Files:**
- Modify: `fleet/factorio/action_space.py` (lines 29-38 for enum, lines 59-134 for registries, lines 164-254 for phase sets, lines 479-515 for mask)
- Modify: `tests/factorio/test_action_space.py`

- [ ] **Step 1: Write failing tests for new action types**

```python
# Append to tests/factorio/test_action_space.py (or create if not exists)

def test_action_type_has_pack_and_stamp():
    from factorio.action_space import ActionType
    assert ActionType.PACK == 9
    assert ActionType.STAMP == 10
    assert len(ActionType) == 11


def test_action_type_mask_length_matches_enum():
    from factorio.action_space import ActionSpace
    space = ActionSpace(phase=1)
    mask = space.get_action_type_mask(inventory={}, phase=1, lesson_index=0)
    assert len(mask) == 11  # 9 original + PACK + STAMP
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_action_space.py::test_action_type_has_pack_and_stamp ../tests/factorio/test_action_space.py::test_action_type_mask_length_matches_enum -v`
Expected: FAIL — PACK/STAMP not in ActionType

- [ ] **Step 3: Add PACK and STAMP to ActionType enum**

In `fleet/factorio/action_space.py` at lines 29-38, add after `INSERT = 8`:

```python
    PACK      = 9   # Execute a multi-step action pack
    STAMP     = 10  # Place a Factorio blueprint in one RCON call
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_action_space.py::test_action_type_has_pack_and_stamp ../tests/factorio/test_action_space.py::test_action_type_mask_length_matches_enum -v`
Expected: PASS

- [ ] **Step 5: Expand ENTITY_REGISTRY for checkpoints 3-7**

In `fleet/factorio/action_space.py`, add to `ENTITY_REGISTRY` (after line 84).

**Note:** `assembling-machine-2` already exists (ID 19). Do NOT re-add it.

```python
    # Checkpoint 3 (blue science) — phase 5
    "oil-refinery": 25,
    "chemical-plant": 26,
    "pumpjack": 27,
    "storage-tank": 28,
    # Checkpoint 4 (purple science) — phase 6
    "electric-furnace": 29,
    "rail": 30,
    "train-stop": 31,
    # Checkpoint 5 (yellow science) — phase 7
    "assembling-machine-3": 32,
    "beacon": 33,
    "speed-module": 34,
    "productivity-module": 35,
    # Checkpoint 6 (rocket) — phase 8
    "rocket-silo": 36,
    "centrifuge": 37,
```

- [ ] **Step 6: Expand PHASE_ENTITIES for phases 5-8**

**Important:** `PHASE_ENTITIES[4] = set(ENTITY_REGISTRY.keys())` includes ALL entities. Phases 5-8 must be explicitly constructed, not unpacked from phase 4, to maintain proper gating.

Add new phase entries to `PHASE_ENTITIES`. First change phase 4 to be explicit (not `set(ENTITY_REGISTRY.keys())`), then add phases 5-8:

```python
    # Phase 4 — explicit set (was set(ENTITY_REGISTRY.keys()) which defeats gating)
    4: {
        *PHASE_ENTITIES[3],
        "assembling-machine-2", "medium-electric-pole", "pipe-to-ground",
        "gun-turret", "wall",
    },
    # Phase 5 — checkpoint 3 (blue science)
    5: {
        *PHASE_ENTITIES[4],
        "oil-refinery", "chemical-plant", "pumpjack", "storage-tank",
    },
    # Phase 6 — checkpoint 4 (purple science)
    6: {
        *PHASE_ENTITIES[5],
        "electric-furnace", "rail", "train-stop",
    },
    # Phase 7 — checkpoint 5 (yellow science)
    7: {
        *PHASE_ENTITIES[6],
        "assembling-machine-3", "beacon", "speed-module", "productivity-module",
    },
    # Phase 8 — checkpoint 6-7 (rocket + space)
    8: set(ENTITY_REGISTRY.keys()),  # all entities
```

Do the same for `PHASE_RECIPES` — change phase 4 from `set(RECIPE_REGISTRY.keys())` to explicit, then add phases 5-8.

- [ ] **Step 7: Add corresponding recipes to RECIPE_REGISTRY and PHASE_RECIPES**

Add science pack recipes and intermediate products to `RECIPE_REGISTRY` and matching `PHASE_RECIPES` entries for phases 5-8.

- [ ] **Step 8: Run existing tests to verify no regressions**

Run: `cd fleet && python -m pytest ../tests/factorio/test_action_space.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add fleet/factorio/action_space.py tests/factorio/test_action_space.py
git commit -m "feat(factorio): add PACK/STAMP action types, expand entity/recipe registries for white science"
```

---

## Task 4: State Encoder — Add Pack Progress Feature

**Files:**
- Modify: `fleet/factorio/state_encoder.py` (line 67: `_BASE_FEATURE_DIM`, lines 285-349: `_encode_features()`)
- Modify: `tests/factorio/test_state_encoder.py` (if exists)

- [ ] **Step 1: Write failing test**

```python
# tests/factorio/test_state_encoder_pack.py
def test_feature_dim_includes_pack_progress():
    from factorio.state_encoder import StateEncoder, _BASE_FEATURE_DIM
    assert _BASE_FEATURE_DIM == 69  # was 68, +1 for pack progress
    enc = StateEncoder(phase=1, grid_size=64)
    assert enc.feature_dim >= 69
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/factorio/test_state_encoder_pack.py -v`
Expected: FAIL — `_BASE_FEATURE_DIM` is 68

- [ ] **Step 3: Bump `_BASE_FEATURE_DIM` and add pack_progress to feature vector**

In `fleet/factorio/state_encoder.py`:

1. Change line 67: `_BASE_FEATURE_DIM = 68` → `_BASE_FEATURE_DIM = 69`
2. Add `pack_progress` parameter to `encode()` method signature (default `0.0`)
3. In `_encode_features()`, after the global resource counts (index 64:68), add at index 68:

```python
        # Pack execution progress (0.0 = no pack, else step/total)
        features[68] = pack_progress
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/factorio/test_state_encoder_pack.py -v`
Expected: PASS

- [ ] **Step 5: Run all state encoder tests to check for regressions**

Run: `cd fleet && python -m pytest ../tests/factorio/ -k "encoder" -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/state_encoder.py tests/factorio/test_state_encoder_pack.py
git commit -m "feat(factorio): bump feature dim 68→69, add active_pack_progress to state encoder"
```

---

## Task 5: Reward — Pack/Stamp Completion Bonuses

**Files:**
- Modify: `fleet/factorio/reward.py` (lines 10-33: constants, lines 103-139: `compute()`, lines 145-203: `_raw_reward()`)
- Modify: `tests/factorio/test_reward.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/factorio/test_reward.py (or create tests/factorio/test_reward_packs.py)

def test_pack_completion_bonus():
    from factorio.reward import RewardComputer
    rc = RewardComputer(phase=1)
    reward = rc.compute(
        prev_state=_make_state(), curr_state=_make_state(),
        action_success=True, lesson_passed=False, phase_complete=False,
        metrics=None, action_type=9, other_agent_positions=[],
        pack_completed=True, pack_aborted=False,
    )
    # Should include the +1.0 pack completion bonus
    assert reward > 0.5


def test_stamp_completion_bonus():
    from factorio.reward import RewardComputer
    rc = RewardComputer(phase=1)
    reward = rc.compute(
        prev_state=_make_state(), curr_state=_make_state(),
        action_success=True, lesson_passed=False, phase_complete=False,
        metrics=None, action_type=10, other_agent_positions=[],
        pack_completed=True, pack_aborted=False,
    )
    # Should include the +2.0 stamp completion bonus
    assert reward > 1.5


def test_pack_abort_penalty():
    from factorio.reward import RewardComputer
    rc = RewardComputer(phase=1)
    reward = rc.compute(
        prev_state=_make_state(), curr_state=_make_state(),
        action_success=False, lesson_passed=False, phase_complete=False,
        metrics=None, action_type=9, other_agent_positions=[],
        pack_completed=False, pack_aborted=True,
    )
    # Should include the -0.5 abort penalty
    assert reward < -0.3
```

Note: `_make_state()` is a test helper that creates a minimal GameState. Check `tests/factorio/test_reward.py` for the existing helper pattern and reuse it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_reward_packs.py -v`
Expected: FAIL — `compute()` doesn't accept pack_completed/pack_aborted

- [ ] **Step 3: Add pack reward constants and update compute()**

In `fleet/factorio/reward.py`:

1. Add constants after line 33:
```python
_PACK_COMPLETE_BONUS = 1.0
_STAMP_COMPLETE_BONUS = 2.0
_PACK_ABORT_PENALTY = -0.5
```

2. Add `pack_completed=False` and `pack_aborted=False` parameters to `compute()` method signature.

3. In `_raw_reward()`, add after the existing signals:
```python
        # Pack/stamp completion bonuses
        if pack_completed:
            if action_type == 10:  # STAMP
                reward += _STAMP_COMPLETE_BONUS
            else:  # PACK
                reward += _PACK_COMPLETE_BONUS
        if pack_aborted:
            reward += _PACK_ABORT_PENALTY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_reward_packs.py -v`
Expected: PASS

- [ ] **Step 5: Run existing reward tests to check regressions**

Run: `cd fleet && python -m pytest ../tests/factorio/test_reward.py -v`
Expected: All PASS (new params have defaults)

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/reward.py tests/factorio/test_reward_packs.py
git commit -m "feat(factorio): add pack completion (+1.0), stamp completion (+2.0), abort penalty (-0.5)"
```

---

## Task 6: Policy Network — Pack/Stamp Heads

**Files:**
- Modify: `fleet/factorio/ml_policy.py` (lines 124-191: constructor/heads, lines 250-317: `get_action_params()`, lines 319-360: `act()`)

- [ ] **Step 1: Write failing test**

```python
# tests/factorio/test_policy_packs.py
import torch


def test_policy_has_pack_heads():
    from factorio.ml_policy import FactorioPolicy
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=11, num_entities=24, num_recipes=40, num_techs=20,
    )
    assert hasattr(policy, "pack_head")
    assert hasattr(policy, "pack_offset_dx")
    assert hasattr(policy, "pack_offset_dy")
    # pack_head outputs MAX_PACK_SLOTS=64 logits
    dummy_trunk = torch.randn(1, 128)
    assert policy.pack_head(dummy_trunk).shape == (1, 64)
    assert policy.pack_offset_dx(dummy_trunk).shape == (1, 11)
    assert policy.pack_offset_dy(dummy_trunk).shape == (1, 11)


def test_policy_get_action_params_pack():
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionType
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=11, num_entities=24, num_recipes=40, num_techs=20,
    )
    # get_action_params takes (shared_trunk_output, action_type_int)
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)
    shared = policy._shared_forward(grid, feat)  # (1, 128)
    params = policy.get_action_params(shared, ActionType.PACK.value)
    assert "pack_logits" in params
    assert "offset_dx_logits" in params
    assert "offset_dy_logits" in params
    assert params["pack_logits"].shape == (1, 64)


def test_policy_action_head_outputs_11():
    from factorio.ml_policy import FactorioPolicy
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=11, num_entities=24, num_recipes=40, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)
    # forward() returns (action_logits, value) tuple
    action_logits, value = policy.forward(grid, feat)
    assert action_logits.shape == (1, 11)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_policy_packs.py -v`
Expected: FAIL — no pack_head attribute

- [ ] **Step 3: Add pack heads to FactorioPolicy constructor**

In `fleet/factorio/ml_policy.py`, in the `__init__` method (around line 150-191), add after existing heads:

```python
        # Pack/stamp selection heads
        from factorio.pack_registry import MAX_PACK_SLOTS
        self.pack_head = nn.Linear(128, MAX_PACK_SLOTS)
        self.pack_offset_dx = nn.Linear(128, self._DX_DY_BINS)
        self.pack_offset_dy = nn.Linear(128, self._DX_DY_BINS)
```

- [ ] **Step 4: Add PACK/STAMP case to `get_action_params()`**

In `fleet/factorio/ml_policy.py`, in `get_action_params()` (around line 250-317), add a new case:

```python
        elif action_type in (ActionType.PACK, ActionType.STAMP):
            return {
                "pack_logits": self.pack_head(trunk),
                "offset_dx_logits": self.pack_offset_dx(trunk),
                "offset_dy_logits": self.pack_offset_dy(trunk),
            }
```

Import `ActionType` at the top of the method or file if not already imported.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_policy_packs.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/ml_policy.py tests/factorio/test_policy_packs.py
git commit -m "feat(factorio): add pack_head, pack_offset_dx/dy to FactorioPolicy network"
```

---

## Task 7: Bridge — Wire Pack Execution Into Tick Loop

**Files:**
- Modify: `fleet/factorio/bridge.py` (line 90: num_action_types, lines 337-380: `_sample_params()`, lines 422+: `_ml_tick_agent()`)

This is the integration task — wiring PackExecutor and PackRegistry into the bridge.

- [ ] **Step 1: Fix `num_action_types` hardcode**

In `fleet/factorio/bridge.py` at line 90, change:
```python
num_action_types=9
```
to:
```python
num_action_types=len(ActionType)
```

Ensure `ActionType` is imported at the top of the file.

- [ ] **Step 2: Initialize PackRegistry and PackExecutor in bridge constructor**

In the ML-mode setup section (around lines 55-121), add:

```python
        from factorio.pack_registry import PackRegistry
        from factorio.pack_executor import PackExecutor

        self._pack_registry = PackRegistry()
        packs_dir = Path(__file__).parent / "packs"
        self._pack_registry.load_packs(packs_dir / "hardcoded")
        self._pack_registry.load_stamps(packs_dir / "blueprints")
        self._pack_registry.load_packs(packs_dir / "learned")

        # Per-agent pack executors
        self._pack_executors: dict[int, PackExecutor] = {}
```

- [ ] **Step 3: Add PACK/STAMP case to `_sample_params()`**

In `fleet/factorio/bridge.py` at `_sample_params()` (around line 337-380), add a case.

**Note:** `_sample_params` takes `(self, action_type, params)` — it does NOT have access to `state` or `log_prob`. Pack masking must happen BEFORE calling `_sample_params`, in `_ml_tick_agent()` where state is available. The `_sample_params` method only samples from the already-masked logits.

```python
        elif action_type == ActionType.PACK or action_type == ActionType.STAMP:
            encoded.entity_id = _sample("pack_logits")   # reuse entity_id for pack_id
            encoded.dx = _sample("offset_dx_logits")
            encoded.dy = _sample("offset_dy_logits")
```

Then in `_ml_tick_agent()`, BEFORE calling `policy.act()`, apply the pack mask to the action logits:

```python
        # Get pack mask and apply to policy params before sampling
        pack_mask = self._pack_registry.get_pack_mask(
            phase=self._curriculum._phase, inventory=state.inventory
        )
        pack_mask_tensor = torch.tensor(pack_mask, dtype=torch.bool)
        # Pass pack_mask_tensor to policy.act() — see Task 6 for act() signature change
```

- [ ] **Step 4: Add pack execution logic to `_ml_tick_agent()`**

This is the most complex integration point. The key principle: **during pack execution, the policy is NOT called**. The PackExecutor replays primitives, accumulates reward, and when done, a single PPO transition is stored with the cumulative reward.

In `_ml_tick_agent()`, add these blocks. Reference the existing method structure at bridge.py:422+.

**Block A: Init executor (near top of method):**
```python
        if agent_id not in self._pack_executors:
            self._pack_executors[agent_id] = PackExecutor()
        executor = self._pack_executors[agent_id]
```

**Block B: Pack in-flight check (BEFORE policy.act() call):**
```python
        if executor.is_active:
            # Continue pack — execute next primitive, accumulate reward, skip PPO
            prev_result = getattr(self, f'_pack_prev_result_{agent_id}', {"success": True})
            action = executor.next_step(prev_result)
            if action is None:
                # Pack finished — store single PPO transition
                pack_completed = executor.completed
                pack_aborted = executor.abort_reason is not None
                pack_reward = executor.cumulative_reward
                if pack_completed:
                    pack_reward += _PACK_COMPLETE_BONUS
                elif pack_aborted:
                    pack_reward += _PACK_ABORT_PENALTY
                # Store transition: (saved_state, saved_log_prob, saved_value, pack_reward)
                # These were saved when the PACK action was first selected (Block C)
                saved = self._pack_pending_transition.pop(agent_id, None)
                if saved and self._trainer:
                    self._trainer.store_transition(
                        saved["grid"], saved["features"], saved["action_type"],
                        saved["log_prob"], saved["value"], pack_reward, done=False,
                    )
                return
            else:
                # Execute primitive
                from factorio.action_translator import translate_action
                translated = translate_action(action)
                result = await self.rcon.remote_call("exec_cmd", translated.rcon_command)
                setattr(self, f'_pack_prev_result_{agent_id}', result)
                # Compute step reward and accumulate (not stored in PPO buffer)
                step_reward = self._reward.compute(
                    prev_state, curr_state, action_success=result.get("success", False),
                    lesson_passed=False, phase_complete=False, metrics=metrics,
                    action_type=action.get("action_type", 0), other_agent_positions=[],
                )
                executor.accumulate_reward(step_reward)
                return  # skip policy call this tick
```

**Block C: After policy.act(), handle PACK/STAMP (after existing action type dispatch):**
```python
        if action_type_val == ActionType.PACK.value:
            pack_id = encoded.entity_id  # reused field
            pack = self._pack_registry.get_by_id(pack_id)
            dx, dy = encoded.dx - 5, encoded.dy - 5
            first_action = executor.start(pack, offset=(dx, dy))
            # Save transition state for deferred PPO storage (when pack completes)
            if not hasattr(self, '_pack_pending_transition'):
                self._pack_pending_transition = {}
            self._pack_pending_transition[agent_id] = {
                "grid": grid, "features": features,
                "action_type": action_type, "log_prob": log_prob, "value": value,
            }
            # Execute first primitive
            from factorio.action_translator import translate_action
            translated = translate_action(first_action)
            result = await self.rcon.remote_call("exec_cmd", translated.rcon_command)
            setattr(self, f'_pack_prev_result_{agent_id}', result)
            return

        elif action_type_val == ActionType.STAMP.value:
            pack_id = encoded.entity_id
            stamp = self._pack_registry.get_by_id(pack_id)
            dx, dy = encoded.dx - 5, encoded.dy - 5
            # state.player_position is a dict with "x"/"y" keys
            pos = {
                "x": state.player_position.get("x", 0) + dx,
                "y": state.player_position.get("y", 0) + dy,
            }
            cmd = json.dumps({"blueprint": stamp.blueprint_string, "position": pos})
            result = await self.rcon.remote_call("biged-blueprint", cmd)
            success = result.get("success", False) if isinstance(result, dict) else False
            stamp_reward = self._reward.compute(
                prev_state, curr_state, action_success=success,
                lesson_passed=False, phase_complete=False, metrics=metrics,
                action_type=10, other_agent_positions=[],
                pack_completed=success, pack_aborted=not success,
            )
            # STAMP is single-tick — store normally in PPO buffer
            if self._trainer:
                self._trainer.store_transition(
                    grid, features, action_type, log_prob, value, stamp_reward, done=False,
                )
            return
```

- [ ] **Step 5: Pass `pack_progress` to state encoder**

In `_ml_tick_agent()`, when calling `self._encoder.encode()`, add:

```python
        executor = self._pack_executors.get(agent_id)
        pack_progress = executor.progress if executor and executor.is_active else 0.0
        grid, world, features = self._encoder.encode(state, metrics, pack_progress=pack_progress)
```

- [ ] **Step 6: Run smoke tests to verify no import errors or crashes**

Run: `cd fleet && python -c "from factorio.bridge import FactorioBridge; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "feat(factorio): wire PackRegistry + PackExecutor into bridge tick loop"
```

---

## Task 8: Curriculum Manager — 8 Checkpoints

**Files:**
- Modify: `fleet/factorio/curriculum_manager.py` (lines 10-132)
- Create: `fleet/factorio/curricula/phase5_blue_science.toml`
- Create: `fleet/factorio/curricula/phase6_purple_science.toml`
- Create: `fleet/factorio/curricula/phase7_yellow_science.toml`
- Create: `fleet/factorio/curricula/phase8_rocket_space.toml`

- [ ] **Step 1: Write failing test for checkpoint property**

```python
# tests/factorio/test_curriculum_checkpoints.py
def test_curriculum_manager_has_checkpoint_property():
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1)
    assert hasattr(cm, "checkpoint")
    assert cm.checkpoint == 0  # phase 1 → checkpoint 0


def test_checkpoint_maps_phases_correctly():
    from factorio.curriculum_manager import CurriculumManager
    # Phases 1-4 map to checkpoints 0-3
    for phase, expected_cp in [(1, 0), (2, 1), (3, 2), (4, 3), (5, 4), (6, 5), (7, 6), (8, 7)]:
        cm = CurriculumManager(current_phase=phase)
        assert cm.checkpoint == expected_cp, f"Phase {phase} should be checkpoint {expected_cp}"


def test_checkpoint_completion_bonus_scales():
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1)
    assert cm.checkpoint_bonus == 10.0  # checkpoint 0 → +10
    cm2 = CurriculumManager(current_phase=8)
    assert cm2.checkpoint_bonus == 80.0  # checkpoint 7 → +80
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_curriculum_checkpoints.py -v`
Expected: FAIL

- [ ] **Step 3: Add checkpoint property to CurriculumManager**

In `fleet/factorio/curriculum_manager.py`, add:

```python
    @property
    def checkpoint(self) -> int:
        """Map phase number (1-8) to checkpoint index (0-7)."""
        return self._phase - 1

    @property
    def checkpoint_bonus(self) -> float:
        """Scaling completion bonus: checkpoint 0→+10, 1→+20, ... 7→+80."""
        return (self.checkpoint + 1) * 10.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_curriculum_checkpoints.py -v`
Expected: PASS

- [ ] **Step 5: Create phase 5-8 TOML curriculum files**

Create `fleet/factorio/curricula/phase5_blue_science.toml`:

```toml
[meta]
name = "Blue Science (Chemical)"
description = "Oil processing and chemical science"

[[lessons]]
name = "Build oil refinery"
criteria = "entities.oil-refinery >= 1 AND entities.pumpjack >= 1"
description = "Set up basic oil processing"
hint = "Place a pumpjack on an oil field, connect to a refinery"

[[lessons]]
name = "Produce sulfur"
criteria = "produced.sulfur >= 10"
description = "Create sulfur from petroleum gas"
hint = "Chemical plant with petroleum gas and water"

[[lessons]]
name = "Produce red circuits"
criteria = "produced.advanced-circuit >= 10"
description = "Create advanced circuits"
hint = "Assembler with electronic circuits, plastic, and copper cable"

[[lessons]]
name = "Produce chemical science"
criteria = "produced.chemical-science-pack >= 10"
description = "Create chemical science packs"
hint = "Assembler with sulfur, advanced circuits, and engine units"
```

Create similar files for `phase6_purple_science.toml`, `phase7_yellow_science.toml`, `phase8_rocket_space.toml` with appropriate lessons and criteria.

- [ ] **Step 6: Run tests**

Run: `cd fleet && python -m pytest ../tests/factorio/test_curriculum_checkpoints.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/curriculum_manager.py fleet/factorio/curricula/phase5_*.toml fleet/factorio/curricula/phase6_*.toml fleet/factorio/curricula/phase7_*.toml fleet/factorio/curricula/phase8_*.toml tests/factorio/test_curriculum_checkpoints.py
git commit -m "feat(factorio): 8-checkpoint curriculum — blue/purple/yellow/rocket/space science phases"
```

---

## Task 9: Hardcoded Packs — Checkpoint 0-2

**Files:**
- Create: `fleet/factorio/packs/hardcoded/smelt_iron_line.json`
- Create: `fleet/factorio/packs/hardcoded/smelt_copper_line.json`
- Create: `fleet/factorio/packs/hardcoded/gear_assembler.json`
- Create: `fleet/factorio/packs/hardcoded/red_science_assembler.json`
- Create: `fleet/factorio/packs/hardcoded/belt_assembler.json`
- Create: `fleet/factorio/packs/hardcoded/inserter_assembler.json`
- Create: `fleet/factorio/packs/hardcoded/green_science_line.json`
- Create: `fleet/factorio/packs/blueprints/basic_power_station.json`
- Create: `fleet/factorio/packs/blueprints/main_bus_starter.json`

- [ ] **Step 1: Write test that packs load and validate**

```python
# tests/factorio/test_pack_loading.py
from pathlib import Path


def test_hardcoded_packs_load():
    from factorio.pack_registry import PackRegistry
    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    count = reg.load_packs(packs_dir / "hardcoded")
    assert count >= 7  # at minimum: smelt_iron, smelt_copper, gear, red_science, belt, inserter, green_science


def test_blueprint_stamps_load():
    from factorio.pack_registry import PackRegistry
    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    count = reg.load_stamps(packs_dir / "blueprints")
    assert count >= 2  # basic_power_station, main_bus_starter


def test_loaded_packs_have_valid_actions():
    from factorio.pack_registry import PackRegistry
    from factorio.action_space import ActionType
    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    reg.load_packs(packs_dir / "hardcoded")
    valid_actions = {at.name.lower() for at in ActionType if at.value <= 8}
    for i in range(len(reg._items)):
        if not reg.is_stamp(i):
            pack = reg.get_by_id(i)
            for act in pack.actions:
                assert act.get("action") in valid_actions, f"Invalid action '{act.get('action')}' in pack {pack.name}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_loading.py -v`
Expected: FAIL — directories don't exist yet

- [ ] **Step 3: Create pack directory structure**

```bash
mkdir -p fleet/factorio/packs/hardcoded fleet/factorio/packs/blueprints fleet/factorio/packs/learned
```

- [ ] **Step 4: Create hardcoded pack JSON files**

Example `fleet/factorio/packs/hardcoded/smelt_iron_line.json`:

```json
{
  "name": "smelt_iron_line",
  "phase_required": 0,
  "origin": "hardcoded",
  "required_items": {"stone-furnace": 3, "coal": 15},
  "actions": [
    {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}, "direction": "north"},
    {"action": "insert", "entity": "stone-furnace", "item": "coal", "count": 5, "position": {"x": 0, "y": 0}},
    {"action": "insert", "entity": "stone-furnace", "item": "iron-ore", "count": 10, "position": {"x": 0, "y": 0}},
    {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 2}, "direction": "north"},
    {"action": "insert", "entity": "stone-furnace", "item": "coal", "count": 5, "position": {"x": 0, "y": 2}},
    {"action": "insert", "entity": "stone-furnace", "item": "iron-ore", "count": 10, "position": {"x": 0, "y": 2}},
    {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 4}, "direction": "north"},
    {"action": "insert", "entity": "stone-furnace", "item": "coal", "count": 5, "position": {"x": 0, "y": 4}},
    {"action": "insert", "entity": "stone-furnace", "item": "iron-ore", "count": 10, "position": {"x": 0, "y": 4}}
  ]
}
```

Create similar files for all 7 hardcoded packs and 2 blueprint stamps (with placeholder blueprint strings for stamps — real strings will be exported from Factorio saves).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_loading.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/packs/
git add tests/factorio/test_pack_loading.py
git commit -m "feat(factorio): add hardcoded packs (checkpoint 0-2) and blueprint stamp templates"
```

---

## Task 10: Pack Recorder — Learned Pack Discovery

**Files:**
- Create: `fleet/factorio/pack_recorder.py`
- Test: `tests/factorio/test_pack_recorder.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_pack_recorder.py
from collections import deque


def test_recorder_records_actions():
    from factorio.pack_recorder import PackRecorder
    rec = PackRecorder(max_length=10)
    rec.record({"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}})
    rec.record({"action": "insert", "item": "coal", "count": 5})
    assert len(rec.buffer) == 2


def test_recorder_caps_at_max_length():
    from factorio.pack_recorder import PackRecorder
    rec = PackRecorder(max_length=3)
    for i in range(5):
        rec.record({"action": "wait", "i": i})
    assert len(rec.buffer) == 3


def test_recorder_extracts_candidate_on_checkpoint():
    from factorio.pack_recorder import PackRecorder
    rec = PackRecorder(max_length=100)
    for i in range(10):
        rec.record({"action": "place", "entity": f"e{i}", "position": {"x": i, "y": 0}})
    candidate = rec.on_checkpoint_complete(checkpoint_id=1)
    assert candidate is not None
    assert candidate.name.startswith("learned_1_")
    assert len(candidate.actions) == 10
    assert candidate.phase_required == 1
    assert candidate.origin == "learned"


def test_recorder_rejects_too_short():
    from factorio.pack_recorder import PackRecorder
    rec = PackRecorder(max_length=100)
    for i in range(3):
        rec.record({"action": "wait"})
    candidate = rec.on_checkpoint_complete(checkpoint_id=0)
    assert candidate is None  # too short (<5)


def test_recorder_rejects_too_long():
    from factorio.pack_recorder import PackRecorder
    rec = PackRecorder(max_length=200)
    for i in range(60):
        rec.record({"action": "wait", "i": i})
    candidate = rec.on_checkpoint_complete(checkpoint_id=0)
    # Should truncate to 50 max or reject
    assert candidate is None or len(candidate.actions) <= 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_recorder.py -v`
Expected: FAIL

- [ ] **Step 3: Implement PackRecorder**

```python
# fleet/factorio/pack_recorder.py
"""PackRecorder — discovers reusable packs from successful training runs."""
from __future__ import annotations

import time
from collections import deque
from factorio.pack_registry import ActionPack

_MIN_PACK_LENGTH = 5
_MAX_PACK_LENGTH = 50


class PackRecorder:
    """Records agent actions and extracts candidate packs on checkpoint completion."""

    def __init__(self, max_length: int = 100) -> None:
        self.buffer: deque[dict] = deque(maxlen=max_length)

    def record(self, action: dict) -> None:
        """Record a primitive action into the rolling buffer."""
        self.buffer.append(action)

    def on_checkpoint_complete(self, checkpoint_id: int) -> ActionPack | None:
        """Extract a candidate pack from recent actions. Returns None if invalid."""
        actions = list(self.buffer)
        if len(actions) < _MIN_PACK_LENGTH:
            return None
        if len(actions) > _MAX_PACK_LENGTH:
            # Take the most recent MAX_PACK_LENGTH actions
            actions = actions[-_MAX_PACK_LENGTH:]
            if len(actions) > _MAX_PACK_LENGTH:
                return None

        name = f"learned_{checkpoint_id}_{int(time.time())}"
        return ActionPack(
            name=name,
            actions=actions,
            phase_required=checkpoint_id,
            origin="learned",
        )

    def clear(self) -> None:
        """Clear the action buffer (e.g., on phase transition)."""
        self.buffer.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_recorder.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/pack_recorder.py tests/factorio/test_pack_recorder.py
git commit -m "feat(factorio): add PackRecorder for learned pack discovery from successful runs"
```

---

## Task 11: Integration — Wire PackRecorder Into Bridge

**Files:**
- Modify: `fleet/factorio/bridge.py`

- [ ] **Step 1: Initialize PackRecorder in bridge constructor**

In the ML-mode setup section of bridge.py, add:

```python
        from factorio.pack_recorder import PackRecorder
        self._pack_recorder = PackRecorder(max_length=100)
```

- [ ] **Step 2: Record primitive actions during tick**

In `_ml_tick_agent()`, after successful action execution (for primitive actions), add:

```python
        # Record action for learned pack discovery
        if action_type.value <= 8:  # primitive only
            self._pack_recorder.record(action_dict)
```

- [ ] **Step 3: Extract candidate on checkpoint completion**

In `_ml_tick_agent()`, where curriculum progress is checked (around line 490-499), after detecting phase completion:

```python
        if progress.get("phase_complete"):
            candidate = self._pack_recorder.on_checkpoint_complete(self._curriculum.checkpoint)
            if candidate:
                slot = self._pack_registry.promote_learned(candidate)
                if slot is not None:
                    log.info("Promoted learned pack '%s' to slot %d", candidate.name, slot)
                    packs_dir = Path(__file__).parent / "packs" / "learned"
                    self._pack_registry.save_learned(packs_dir)
            self._pack_recorder.clear()
```

- [ ] **Step 4: Verify import works**

Run: `cd fleet && python -c "from factorio.bridge import FactorioBridge; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "feat(factorio): wire PackRecorder into bridge — record actions, promote on checkpoint"
```

---

## Task 12: Checkpoint Save/Load for Replay

**Files:**
- Modify: `fleet/factorio/bridge.py`

- [ ] **Step 1: Add auto-save before checkpoint attempts**

In `_ml_tick_agent()`, at the start of each curriculum check:

```python
        # Auto-save before checkpoint attempt for learned pack replay
        checkpoint_id = self._curriculum.checkpoint
        save_name = f"checkpoint_{checkpoint_id}_pre"
        if not hasattr(self, '_last_checkpoint_save') or self._last_checkpoint_save != checkpoint_id:
            await self.rcon.remote_call("save", save_name)
            self._last_checkpoint_save = checkpoint_id
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "feat(factorio): auto-save before checkpoint attempts for learned pack replay"
```

---

## Task 13: Full Integration Test

**Files:**
- Create: `tests/factorio/test_pack_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/factorio/test_pack_integration.py
"""Integration test: pack registry → executor → reward pipeline."""
import torch
import pytest


def test_pack_roundtrip():
    """Verify pack creation → registration → mask → executor → reward flow."""
    from factorio.pack_registry import PackRegistry, ActionPack, MAX_PACK_SLOTS
    from factorio.pack_executor import PackExecutor
    from factorio.action_space import ActionType

    # 1. Create and register a pack
    reg = PackRegistry()
    pack = ActionPack(
        name="test_smelt",
        actions=[
            {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
            {"action": "insert", "item": "coal", "count": 5, "position": {"x": 0, "y": 0}},
        ],
        phase_required=0,
        origin="hardcoded",
        required_items={"stone-furnace": 1},
    )
    slot = reg.register_pack(pack)
    assert slot == 0

    # 2. Check mask
    mask = reg.get_pack_mask(phase=0, inventory={"stone-furnace": 5})
    assert mask[0] == 1
    assert sum(mask) == 1  # only one pack registered

    # 3. Execute through PackExecutor
    exe = PackExecutor()
    first = exe.start(pack, offset=(10, 20))
    assert first["position"]["x"] == 10
    assert first["position"]["y"] == 20

    exe.accumulate_reward(0.5)
    second = exe.next_step({"success": True})
    assert second["action"] == "insert"
    assert second["position"]["x"] == 10  # offset applied

    exe.accumulate_reward(0.3)
    done = exe.next_step({"success": True})
    assert done is None
    assert exe.completed
    assert exe.cumulative_reward == pytest.approx(0.8)

    # 4. Verify ActionType enum has PACK and STAMP
    assert ActionType.PACK == 9
    assert ActionType.STAMP == 10
    assert len(ActionType) == 11


def test_policy_accepts_11_action_types():
    """Verify policy network works with 11 action types (9 original + PACK + STAMP)."""
    from factorio.ml_policy import FactorioPolicy
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=11, num_entities=24, num_recipes=40, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    world = torch.randn(1, 4, 64, 64)
    feat = torch.randn(1, 69)
    out = policy.forward(grid, world, feat)
    assert out["action_logits"].shape[1] == 11
```

- [ ] **Step 2: Run integration tests**

Run: `cd fleet && python -m pytest ../tests/factorio/test_pack_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite for regressions**

Run: `cd fleet && python -m pytest ../tests/factorio/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/factorio/test_pack_integration.py
git commit -m "test(factorio): add integration test for pack registry → executor → policy pipeline"
```

---

## Summary

| Task | Component | Files | Est. |
|------|-----------|-------|------|
| 1 | Pack Registry | pack_registry.py + tests | M |
| 2 | Pack Executor | pack_executor.py + tests | S |
| 3 | ActionType + Registries | action_space.py | S |
| 4 | State Encoder | state_encoder.py | XS |
| 5 | Reward Signals | reward.py | S |
| 6 | Policy Network | ml_policy.py | S |
| 7 | Bridge Integration | bridge.py | L |
| 8 | Curriculum 8 Checkpoints | curriculum_manager.py + TOMLs | M |
| 9 | Hardcoded Packs | packs/hardcoded/*.json | M |
| 10 | Pack Recorder | pack_recorder.py + tests | S |
| 11 | Recorder Integration | bridge.py | S |
| 12 | Checkpoint Save/Load | bridge.py | XS |
| 13 | Integration Tests | test_pack_integration.py | S |

**Total: 13 tasks, ~13 commits**

**Parallelization:** Tasks 1, 2, 4, 5, 10 are fully independent. Task 3 (ActionType enum) must complete before Task 6 (policy heads). Tasks 7 and 11 depend on all prior tasks. Task 13 is the final validation gate.

**Out of scope (separate plan):**
- Lua mod changes (`/biged-blueprint` RCON handler, production metrics extension) — requires Factorio modding, not Python
- Replay-based learned pack evaluation (requires save/load integration testing with running Factorio instance)
- Hardcoded packs for checkpoints 3-7 (need actual Factorio blueprint strings from game saves)
