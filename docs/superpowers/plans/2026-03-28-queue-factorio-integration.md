# Queue ↔ Factorio Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend AgentBrain to accept prioritized external plans from fleet workers, with a Focus toggle for worker reservation, enabling collaborative fleet/Factorio operation.

**Architecture:** AgentBrain gets a priority plan queue (heapq) replacing its flat `_plan` list. Fleet workers submit plans/directives via bridge REST API. A Focus toggle (flag file) reserves workers for Factorio analysis tasks. The supervisor auto-generates `factorio_analyze` tasks when Focus is ON.

**Tech Stack:** Python 3.11+, Flask (bridge API), heapq, dataclasses, JSON flag files, existing fleet db/worker/supervisor infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-28-queue-factorio-integration-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `fleet/factorio/agent_brain.py` | Priority plan queue, submit_plan/directive, conflict detection, shelving | Modify |
| `fleet/factorio/bridge_api.py` | 5 new REST endpoints for plan/directive/focus | Modify |
| `fleet/factorio/bridge_config.py` | Focus config fields | Modify |
| `fleet/factorio/bridge.py` | Wire focus state read, pass to brain status | Modify |
| `fleet/skills/factorio_analyze.py` | New skill — state analysis + plan/directive submission | Create |
| `fleet/skills/factorio_plan.py` | Update to submit plans via bridge API | Modify |
| `fleet/skills/factorio_act.py` | Update to submit plans via bridge API | Modify |
| `fleet/worker.py` | Affinity-based claiming for Focus-reserved workers | Modify |
| `fleet/supervisor.py` | Focus toggle handling, auto-generate factorio_analyze tasks | Modify |
| `fleet/lead_client.py` | New CLI commands (focus, plans) | Modify |
| `fleet/dashboard.py` | Focus toggle endpoint, plan queue proxies | Modify |
| `fleet/fleet.toml` | New config keys | Modify |
| `tests/test_plan_queue.py` | Unit tests for priority plan queue | Create |
| `tests/test_focus_toggle.py` | Unit tests for focus toggle mechanism | Create |

---

### Task 1: PlanSubmission Dataclass + Priority Constants

**Files:**
- Modify: `fleet/factorio/agent_brain.py:50-56`
- Create: `tests/test_plan_queue.py`

- [ ] **Step 1: Write the test for PlanSubmission creation**

```python
# tests/test_plan_queue.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet', 'factorio'))

from agent_brain import PlanSubmission, PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW

def test_plan_submission_creation():
    ps = PlanSubmission(
        actions=[{"action": "craft", "item": "stone-furnace", "count": 4}],
        priority=PRIORITY_HIGH,
        source="worker-2",
        source_type="worker",
        rationale="Need more smelting capacity",
        confidence=0.85,
    )
    assert ps.priority == 75
    assert ps.source_type == "worker"
    assert ps.confidence == 0.85
    assert len(ps.actions) == 1

def test_priority_constants():
    assert PRIORITY_CRITICAL == 100
    assert PRIORITY_HIGH == 75
    assert PRIORITY_NORMAL == 50
    assert PRIORITY_LOW == 25
    assert PRIORITY_CRITICAL > PRIORITY_HIGH > PRIORITY_NORMAL > PRIORITY_LOW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py -v`
Expected: FAIL with ImportError (PlanSubmission not defined)

- [ ] **Step 3: Add PlanSubmission dataclass and priority constants to agent_brain.py**

Add after the existing `Directive` dataclass (line 56):

```python
# Priority bands for plan queue
PRIORITY_CRITICAL = 100  # Human commands — bypass brain veto
PRIORITY_HIGH = 75       # Fleet worker plans/directives
PRIORITY_NORMAL = 50     # Brain's own Ollama-generated plans
PRIORITY_LOW = 25        # Speculative/low-confidence

@dataclass
class PlanSubmission:
    actions: list[dict]
    priority: int
    source: str
    source_type: str       # "worker" | "brain" | "human"
    rationale: str
    confidence: float
    plan_id: str = ""      # assigned on submission
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_plan_queue.py
git commit -m "feat(factorio): add PlanSubmission dataclass and priority constants"
```

---

### Task 2: Extend Directive Dataclass with Priority and Source

**Files:**
- Modify: `fleet/factorio/agent_brain.py:50-56` (Directive dataclass)
- Modify: `fleet/factorio/agent_brain.py:142` (add_directive method)
- Add to: `tests/test_plan_queue.py`

- [ ] **Step 1: Write the test for extended Directive**

```python
# Add to tests/test_plan_queue.py
from agent_brain import Directive

def test_directive_has_priority_and_source():
    d = Directive(
        id="abc123",
        text="focus on iron",
        sticky=False,
        plans_remaining=3,
        created_at=1000.0,
        priority=75,
        source="worker-2",
    )
    assert d.priority == 75
    assert d.source == "worker-2"

def test_directive_defaults():
    d = Directive(
        id="abc123",
        text="focus on iron",
        sticky=False,
        plans_remaining=3,
        created_at=1000.0,
    )
    assert d.priority == 50  # default NORMAL
    assert d.source == "human"  # default human
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py::test_directive_has_priority_and_source -v`
Expected: FAIL (Directive doesn't accept priority/source)

- [ ] **Step 3: Extend Directive dataclass**

Modify the existing `Directive` dataclass at line 50-56 of `agent_brain.py`:

```python
@dataclass
class Directive:
    id: str
    text: str
    sticky: bool
    plans_remaining: int
    created_at: float
    priority: int = 50         # affects ordering in planning prompt
    source: str = "human"      # "human", "worker-2", etc.
```

- [ ] **Step 4: Update get_directives to include priority and source**

At line 169-181 of `agent_brain.py`, update `get_directives()` to include the new fields:

```python
def get_directives(self) -> list[dict]:
    """Return list of directive dicts."""
    with self._lock:
        return [
            {
                "id": d.id,
                "text": d.text,
                "sticky": d.sticky,
                "plans_remaining": d.plans_remaining,
                "created_at": d.created_at,
                "priority": d.priority,
                "source": d.source,
            }
            for d in self._directives
        ]
```

- [ ] **Step 5: Update add_directive to accept priority and source**

Modify `add_directive()` at line 142 of `agent_brain.py`. Current signature is `add_directive(self, text, sticky=False, plans=1)`. Change to:

```python
def add_directive(self, text: str, sticky: bool = False, plans: int = 1,
                  priority: int = 50, source: str = "human") -> str:
    d_id = uuid.uuid4().hex[:8]
    d = Directive(
        id=d_id, text=text, sticky=sticky,
        plans_remaining=plans, created_at=time.time(),
        priority=priority, source=source,
    )
    with self._lock:
        self._directives.append(d)
    return d_id
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_plan_queue.py
git commit -m "feat(factorio): extend Directive with priority and source fields"
```

---

### Task 3: Priority Plan Queue in AgentBrain

**Files:**
- Modify: `fleet/factorio/agent_brain.py` (add plan queue state, submit_plan, shelving)
- Add to: `tests/test_plan_queue.py`

- [ ] **Step 1: Write tests for plan queue operations**

```python
# Add to tests/test_plan_queue.py
import time
from agent_brain import AgentBrain, PlanSubmission, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_CRITICAL
from world_model import WorldModel
from bridge_config import BridgeConfig

def _make_brain():
    """Create a minimal AgentBrain for testing (no Ollama needed)."""
    cfg = BridgeConfig()
    wm = WorldModel()
    brain = AgentBrain(cfg, wm)
    return brain

def test_submit_plan_accepted():
    brain = _make_brain()
    ps = PlanSubmission(
        actions=[{"action": "craft", "item": "furnace", "count": 2}],
        priority=PRIORITY_HIGH,
        source="worker-1",
        source_type="worker",
        rationale="test",
        confidence=0.9,
    )
    result = brain.submit_plan(ps)
    assert result["status"] in ("accepted", "queued")
    assert "plan_id" in result

def test_submit_plan_queue_ordering():
    brain = _make_brain()
    # Submit LOW then HIGH — HIGH should be first in queue
    low = PlanSubmission(actions=[{"action": "move"}], priority=25,
                         source="spec", source_type="worker", rationale="", confidence=0.5)
    high = PlanSubmission(actions=[{"action": "craft"}], priority=75,
                          source="w1", source_type="worker", rationale="", confidence=0.9)
    brain.submit_plan(low)
    brain.submit_plan(high)
    # Queue should have high-priority first
    assert len(brain._plan_queue) == 2
    # heapq: (-priority, seq, plan) — first element has most negative priority (highest)
    assert brain._plan_queue[0][0] == -75  # HIGH is first

def test_submit_plan_queue_depth_limit():
    brain = _make_brain()
    # Fill queue to MAX_PLAN_QUEUE_DEPTH
    for i in range(brain.MAX_PLAN_QUEUE_DEPTH):
        ps = PlanSubmission(actions=[{"action": "move"}], priority=50,
                            source=f"w{i}", source_type="worker", rationale="", confidence=0.5)
        brain.submit_plan(ps)
    # Next submission should be rejected
    overflow = PlanSubmission(actions=[{"action": "move"}], priority=25,
                              source="overflow", source_type="worker", rationale="", confidence=0.5)
    result = brain.submit_plan(overflow)
    assert result["status"] == "rejected"
    assert "full" in result["reason"].lower()

def test_plan_history_logged():
    brain = _make_brain()
    ps = PlanSubmission(actions=[{"action": "craft"}], priority=75,
                        source="w1", source_type="worker", rationale="test", confidence=0.9)
    brain.submit_plan(ps)
    assert len(brain._plan_history) == 1
    assert brain._plan_history[0]["status"] in ("accepted", "queued")

def test_shelve_and_restore():
    brain = _make_brain()
    # Set a current plan as if brain generated it
    brain._plan = [{"action": "mine", "resource": "iron"}]
    brain._plan_index = 0
    brain._current_priority = PRIORITY_NORMAL

    # Submit a CRITICAL plan — should preempt
    critical = PlanSubmission(
        actions=[{"action": "craft", "item": "repair-pack"}],
        priority=PRIORITY_CRITICAL,
        source="human",
        source_type="human",
        rationale="emergency",
        confidence=1.0,
    )
    result = brain.submit_plan(critical)
    assert result["status"] == "preempted_current"
    assert brain._shelved_plan is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py::test_submit_plan_accepted -v`
Expected: FAIL (submit_plan not defined)

- [ ] **Step 3: Add plan queue state to AgentBrain.__init__**

In `agent_brain.py`, add after `self._idle_assembler_count = 0` (around line 112):

```python
# Priority plan queue (heapq min-heap, entries: (-priority, seq, PlanSubmission))
self._plan_queue: list[tuple] = []
self._shelved_plan: PlanSubmission | None = None
self._plan_history: deque = deque(maxlen=50)
self._plan_seq: int = 0
self._current_priority: int = PRIORITY_NORMAL
self.MAX_PLAN_QUEUE_DEPTH: int = 20
```

Add `from collections import deque` to imports at top if not already present. Add `import heapq` to imports.

- [ ] **Step 4: Implement submit_plan method**

Add to `AgentBrain` class (after `add_directive`):

```python
def submit_plan(self, submission: PlanSubmission) -> dict:
    """Accept or reject an external plan based on priority and conflict rules."""
    import heapq

    plan_id = f"p-{uuid.uuid4().hex[:8]}"
    submission.plan_id = plan_id

    with self._lock:
        # Queue depth limit
        if len(self._plan_queue) >= self.MAX_PLAN_QUEUE_DEPTH:
            entry = {"plan_id": plan_id, "status": "rejected",
                     "reason": "Plan queue full", "source": submission.source,
                     "timestamp": time.time()}
            self._plan_history.append(entry)
            return {"status": "rejected", "plan_id": plan_id,
                    "position": -1, "reason": "Plan queue full"}

        # Conflict check (V1: no-op, always accepts)
        conflicts, reason = self._check_conflict(submission)
        if conflicts and submission.source_type != "human":
            entry = {"plan_id": plan_id, "status": "rejected",
                     "reason": reason, "source": submission.source,
                     "timestamp": time.time()}
            self._plan_history.append(entry)
            return {"status": "rejected", "plan_id": plan_id,
                    "position": -1, "reason": reason}

        # Preemption check: incoming >= current + 25
        if (self._plan and
                submission.priority >= self._current_priority + 25):
            self._shelve_current_plan()
            self._plan = submission.actions
            self._plan_index = 0
            self._current_priority = submission.priority
            entry = {"plan_id": plan_id, "status": "preempted_current",
                     "source": submission.source, "timestamp": time.time()}
            self._plan_history.append(entry)
            return {"status": "preempted_current", "plan_id": plan_id,
                    "position": 0, "reason": None}

        # Queue the plan
        seq = self._plan_seq
        self._plan_seq += 1
        heapq.heappush(self._plan_queue, (-submission.priority, seq, submission))

        position = sum(1 for p in self._plan_queue
                       if p[0] < -submission.priority) + (1 if self._plan else 0)
        status = "accepted" if not self._plan else "queued"
        entry = {"plan_id": plan_id, "status": status,
                 "source": submission.source, "timestamp": time.time()}
        self._plan_history.append(entry)
        return {"status": status, "plan_id": plan_id,
                "position": position, "reason": None}

def _check_conflict(self, incoming: PlanSubmission) -> tuple[bool, str]:
    """V1: no-op — always accepts. Priority rules handle ordering."""
    return (False, "")

def _shelve_current_plan(self) -> None:
    """Save current plan for potential resumption after preempting plan completes."""
    if self._plan:
        remaining_actions = self._plan[self._plan_index:]
        if remaining_actions:
            self._shelved_plan = PlanSubmission(
                actions=remaining_actions,
                priority=self._current_priority,
                source="brain-shelved",
                source_type="brain",
                rationale="Shelved by preemption",
                confidence=0.5,
                plan_id=f"shelved-{uuid.uuid4().hex[:8]}",
            )

def _restore_shelved_plan(self) -> None:
    """Restore a previously shelved plan if still valid."""
    import heapq
    if self._shelved_plan:
        seq = self._plan_seq
        self._plan_seq += 1
        heapq.heappush(self._plan_queue,
                        (-self._shelved_plan.priority, seq, self._shelved_plan))
        self._shelved_plan = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_plan_queue.py
git commit -m "feat(factorio): add priority plan queue with submit_plan, shelving, conflict check"
```

---

### Task 4: Modify next_action to Drain from Priority Queue

**Files:**
- Modify: `fleet/factorio/agent_brain.py:380` (next_action method)
- Add to: `tests/test_plan_queue.py`

- [ ] **Step 1: Write test for queue draining**

```python
# Add to tests/test_plan_queue.py
def test_next_action_drains_queued_plan():
    brain = _make_brain()
    # Brain has no current plan, submit one to queue
    ps = PlanSubmission(
        actions=[{"action": "craft", "item": "furnace", "count": 1}],
        priority=PRIORITY_HIGH,
        source="w1", source_type="worker", rationale="test", confidence=0.9,
    )
    brain.submit_plan(ps)
    assert len(brain._plan_queue) == 1

    # Simulate: brain._plan is empty, next tick should pop from queue
    brain._plan = []
    brain._plan_index = 0
    brain._pop_next_plan()
    assert brain._plan == [{"action": "craft", "item": "furnace", "count": 1}]
    assert len(brain._plan_queue) == 0

def test_shelved_plan_restored_after_preempt_completes():
    brain = _make_brain()
    # Set current plan
    brain._plan = [{"action": "mine"}, {"action": "smelt"}]
    brain._plan_index = 0
    brain._current_priority = PRIORITY_NORMAL

    # Preempt with critical
    critical = PlanSubmission(
        actions=[{"action": "repair"}],
        priority=PRIORITY_CRITICAL,
        source="human", source_type="human", rationale="fix", confidence=1.0,
    )
    brain.submit_plan(critical)

    # Brain now has critical plan, shelved plan exists
    assert brain._plan == [{"action": "repair"}]
    assert brain._shelved_plan is not None

    # Simulate: critical plan exhausted, brain tries to pop next
    brain._plan = []
    brain._plan_index = 0
    brain._restore_shelved_plan()
    brain._pop_next_plan()
    # Should restore shelved plan (remaining actions: mine, smelt)
    assert brain._plan == [{"action": "mine"}, {"action": "smelt"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py::test_next_action_drains_queued_plan -v`
Expected: FAIL (_pop_next_plan not defined)

- [ ] **Step 3: Add _pop_next_plan method**

Add to `AgentBrain` class:

```python
def _pop_next_plan(self) -> bool:
    """Pop highest-priority plan from queue into _plan. Returns True if a plan was loaded."""
    import heapq
    with self._lock:
        if self._plan_queue:
            neg_pri, _seq, submission = heapq.heappop(self._plan_queue)
            self._plan = submission.actions
            self._plan_index = 0
            self._current_priority = submission.priority
            return True
    return False
```

- [ ] **Step 4: Modify next_action to check queue when plan is exhausted**

In `next_action()` (around line 380), find the section where the brain checks if `_plan` is empty and calls Ollama. Before the Ollama call, add queue drain logic:

```python
# In next_action(), when plan is exhausted (self._plan_index >= len(self._plan)):
# First try to restore shelved plan, then check queue
if self._shelved_plan:
    self._restore_shelved_plan()
if self._pop_next_plan():
    # Got a queued plan — skip Ollama call, drain from it
    pass  # falls through to action translation below
else:
    # No queued plans — call Ollama for a new plan as before
    # (existing Ollama planning code)
```

The exact edit depends on the current structure of `next_action()`. The key change: before calling Ollama, check `_pop_next_plan()`. If it returns True, skip Ollama and proceed to drain the loaded plan. When the brain generates its own plan via Ollama, set `self._current_priority = PRIORITY_NORMAL`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_plan_queue.py
git commit -m "feat(factorio): next_action drains from priority queue before Ollama"
```

---

### Task 5: Update _build_prompt to Sort Directives by Priority

**Files:**
- Modify: `fleet/factorio/agent_brain.py:217` (_build_prompt method)
- Add to: `tests/test_plan_queue.py`

- [ ] **Step 1: Write test for directive priority ordering**

```python
# Add to tests/test_plan_queue.py
def test_directives_sorted_by_priority_in_prompt():
    brain = _make_brain()
    brain.add_directive("low priority task", priority=25, source="w1")
    brain.add_directive("high priority task", priority=75, source="w2")
    brain.add_directive("normal priority task", priority=50, source="w3")

    # Access directives — they should be sorted by priority desc in prompt
    with brain._lock:
        sorted_directives = sorted(brain._directives, key=lambda d: d.priority, reverse=True)
    assert sorted_directives[0].text == "high priority task"
    assert sorted_directives[1].text == "normal priority task"
    assert sorted_directives[2].text == "low priority task"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py::test_directives_sorted_by_priority_in_prompt -v`
Expected: PASS (sorting is tested on the list, not _build_prompt itself)

- [ ] **Step 3: Modify _build_prompt to sort directives by priority**

In `_build_prompt()` at line 242-243 of `agent_brain.py`, sort directives by priority at the point where `active_directives` is assigned. This covers **both** the template path (line 251-255) and the fallback path (line 265-268):

```python
# Line 242-243: change the assignment to sort by priority
with self._lock:
    active_directives = sorted(self._directives, key=lambda d: d.priority, reverse=True)
```

Then update the directive line format in both paths to include source:

```python
# Template path (line 253-254) — change:
for d in active_directives:
    directive_lines.append(f"- [{d.source}] {d.text}")

# Fallback path (line 267-268) — change:
for d in active_directives:
    lines.append(f"- [{d.source}] {d.text}")
```

- [ ] **Step 4: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_plan_queue.py
git commit -m "feat(factorio): sort directives by priority in planning prompt"
```

---

### Task 6: Bridge API — Plan Submission Endpoints

**Files:**
- Modify: `fleet/factorio/bridge_api.py`
- Add to: `tests/test_plan_queue.py`

- [ ] **Step 1: Write test for plan submission endpoint**

```python
# Add to tests/test_plan_queue.py
import json

def test_plan_submit_api_schema():
    """Test that PlanSubmission can be created from API request JSON."""
    request_json = {
        "actions": [{"action": "craft", "item": "stone-furnace", "count": 4}],
        "priority": 75,
        "source": "worker-2",
        "source_type": "worker",
        "rationale": "Iron smelting capacity needed",
        "confidence": 0.85,
    }
    ps = PlanSubmission(**request_json)
    assert ps.priority == 75
    assert len(ps.actions) == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_plan_queue.py::test_plan_submit_api_schema -v`
Expected: PASS

- [ ] **Step 3: Add plan submission endpoints to bridge_api.py**

In `bridge_api.py`, **inside the `create_api()` function** (all routes are defined inside this closure — `app` does not exist at module scope), after the existing `/api/directive` endpoint (around line 97), add:

```python
    @app.route("/api/plan/submit", methods=["POST"])
    def plan_submit():
    data = request.get_json(force=True)
    required = ["actions", "priority", "source", "source_type", "rationale", "confidence"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400
    try:
        ps = PlanSubmission(
            actions=data["actions"],
            priority=int(data["priority"]),
            source=str(data["source"]),
            source_type=str(data["source_type"]),
            rationale=str(data["rationale"]),
            confidence=float(data["confidence"]),
        )
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    result = _brain.submit_plan(ps)
    status_code = 200 if result["status"] != "rejected" else 409
    return jsonify(result), status_code

@app.route("/api/plan/queue", methods=["GET"])
def plan_queue():
    with _brain._lock:
        current = None
        if _brain._plan:
            current = {
                "actions": _brain._plan,
                "index": _brain._plan_index,
                "total": len(_brain._plan),
                "priority": _brain._current_priority,
            }
        queued = []
        for neg_pri, seq, ps in sorted(_brain._plan_queue):
            queued.append({
                "plan_id": ps.plan_id,
                "actions": ps.actions,
                "priority": ps.priority,
                "source": ps.source,
                "source_type": ps.source_type,
                "rationale": ps.rationale,
                "confidence": ps.confidence,
            })
        shelved = None
        if _brain._shelved_plan:
            sp = _brain._shelved_plan
            shelved = {"plan_id": sp.plan_id, "actions": sp.actions,
                       "priority": sp.priority}
    return jsonify({"current": current, "queued": queued, "shelved": shelved})

@app.route("/api/plan/history", methods=["GET"])
def plan_history():
    with _brain._lock:
        history = list(_brain._plan_history)
    return jsonify({"history": history})
```

Add `from factorio.agent_brain import PlanSubmission` to the imports at the top of `bridge_api.py`. **Important:** All route definitions (`@app.route(...)`) must be indented inside `create_api()` — they are closures that capture `_brain`, `_world_model`, etc.

- [ ] **Step 4: Add directive submission endpoint**

In `bridge_api.py`, after the plan endpoints:

```python
@app.route("/api/directive/submit", methods=["POST"])
def directive_submit():
    data = request.get_json(force=True)
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "Missing 'text' field"}), 400
    d_id = _brain.add_directive(
        text=text,
        sticky=data.get("sticky", False),
        plans=data.get("max_plans", 3),
        priority=data.get("priority", 50),
        source=data.get("source", "worker"),
    )
    return jsonify({"id": d_id, "status": "accepted"})
```

- [ ] **Step 5: Add focus state read-only endpoint**

```python
@app.route("/api/focus", methods=["GET"])
def focus_state():
    focus_file = os.path.join(os.path.dirname(__file__), '..', '.factorio_focus.json')
    try:
        with open(focus_file) as f:
            return jsonify(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify({"on": False, "workers": []})
```

Add `import os, json` to bridge_api.py imports if not present.

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/bridge_api.py
git commit -m "feat(factorio): add plan/submit, plan/queue, plan/history, directive/submit, focus endpoints"
```

---

### Task 7: Focus Toggle — Flag File + Config

**Files:**
- Modify: `fleet/factorio/bridge_config.py:8-54`
- Modify: `fleet/fleet.toml`
- Create: `tests/test_focus_toggle.py`

- [ ] **Step 1: Write test for focus state read/write**

```python
# tests/test_focus_toggle.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))

def test_focus_state_file_write_and_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        focus_file = os.path.join(tmpdir, ".factorio_focus.json")
        state = {"on": True, "workers": ["coder_1", "coder_2"]}
        with open(focus_file, "w") as f:
            json.dump(state, f)
        with open(focus_file) as f:
            loaded = json.load(f)
        assert loaded["on"] is True
        assert loaded["workers"] == ["coder_1", "coder_2"]

def test_focus_state_missing_file():
    focus_file = "/tmp/nonexistent_focus_state_12345.json"
    try:
        with open(focus_file) as f:
            json.load(f)
        assert False, "Should have raised"
    except FileNotFoundError:
        pass  # expected
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_focus_toggle.py -v`
Expected: PASS

- [ ] **Step 3: Add config keys to fleet.toml**

Add to the `[factorio]` section in `fleet/fleet.toml`:

```toml
# Focus mode — worker reservation for Factorio analysis
focus_workers_default = 2
analyze_interval_ticks = 50
```

- [ ] **Step 4: Add config fields to BridgeConfig**

In `fleet/factorio/bridge_config.py`, add to the `BridgeConfig` dataclass:

```python
focus_workers_default: int = 2
analyze_interval_ticks: int = 50
```

- [ ] **Step 5: Commit**

```bash
git add fleet/fleet.toml fleet/factorio/bridge_config.py tests/test_focus_toggle.py
git commit -m "feat(factorio): add focus toggle config keys and flag file tests"
```

---

### Task 8: Worker Affinity — Focus-Aware Claim Loop

**Files:**
- Modify: `fleet/worker.py:663-679`
- Add to: `tests/test_focus_toggle.py`

- [ ] **Step 1: Write test for focus state reader**

```python
# Add to tests/test_focus_toggle.py
import time

def test_read_focus_state_cached(tmp_path):
    focus_file = tmp_path / ".factorio_focus.json"
    focus_file.write_text(json.dumps({"on": True, "workers": ["coder_1"]}))

    # Import the reader function (will be created in worker.py)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))
    from worker import _read_focus_state

    state = _read_focus_state(str(focus_file), _force=True)
    assert state is not None
    assert state["on"] is True
    assert state["workers"] == ["coder_1"]

def test_read_focus_state_missing_file():
    from worker import _read_focus_state
    state = _read_focus_state("/tmp/nonexistent_12345.json", _force=True)
    assert state is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_focus_toggle.py::test_read_focus_state_cached -v`
Expected: FAIL (_read_focus_state not defined)

- [ ] **Step 3: Add _read_focus_state function to worker.py**

Add near the top of `worker.py` (after imports, before the main loop):

```python
# Focus state reader with caching (5s TTL)
_focus_cache = {"state": None, "mtime": 0.0, "checked": 0.0}
FOCUS_FILE = os.path.join(os.path.dirname(__file__), ".factorio_focus.json")

def _read_focus_state(path: str = None, _force: bool = False) -> dict | None:
    """Read .factorio_focus.json with 5s cache. Returns {on, workers} or None.
    Pass _force=True to bypass cache (for testing)."""
    path = path or FOCUS_FILE
    now = time.time()
    if not _force and now - _focus_cache["checked"] < 5.0:
        return _focus_cache["state"]
    _focus_cache["checked"] = now
    try:
        with open(path) as f:
            state = json.load(f)
        _focus_cache["state"] = state
        return state
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _focus_cache["state"] = None
        return None
```

- [ ] **Step 4: Modify the claim loop to use focus affinity**

In `worker.py` at the claim loop (around line 663-679), modify the claiming section. After the `.queue_paused` check and before `db.claim_task()`:

```python
# Check Focus mode for Factorio affinity
_factorio_affinity = None
_focus = _read_focus_state()
if _focus and _focus.get("on") and worker_name in _focus.get("workers", []):
    _factorio_affinity = ["factorio_observe", "factorio_analyze",
                          "factorio_plan", "factorio_act", "factorio_train"]

# Use batch claiming when queue is deep
_depth = db.queue_depth()
if _depth > 3:
    _batch = db.claim_tasks(role, n=2,
                            affinity_skills=_factorio_affinity or affinity_skills)
    task = _batch[0] if _batch else None
else:
    task = db.claim_task(role,
                         affinity_skills=_factorio_affinity or affinity_skills)

# Fallback: if Focus worker found nothing with affinity, try normal queue
if not task and _factorio_affinity:
    task = db.claim_task(role, affinity_skills=affinity_skills)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_focus_toggle.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/worker.py tests/test_focus_toggle.py
git commit -m "feat(factorio): focus-aware worker claim loop with affinity fallback"
```

---

### Task 9: Supervisor — Focus Toggle Handler + Task Generation

**Files:**
- Modify: `fleet/supervisor.py`
- Modify: `fleet/dashboard.py`

- [ ] **Step 1: Add Focus toggle endpoint to dashboard.py**

In `fleet/dashboard.py`, add near the existing Factorio proxy endpoints (around line 1221-1270):

```python
@app.route("/api/factorio/focus", methods=["POST"])
def factorio_focus_toggle():
    data = request.get_json(force=True)
    on = bool(data.get("on", False))
    worker_count = int(data.get("workers", 2))
    focus_file = os.path.join(os.path.dirname(__file__), ".factorio_focus.json")

    if on:
        # Pick least-busy workers from agents table
        import db as _db
        agents = _db.get_agents()
        idle = [a for a in agents if a.get("status") == "IDLE"]
        busy = [a for a in agents if a.get("status") != "IDLE"]
        # Prefer idle, then sort busy by last_heartbeat (most recent = least busy)
        candidates = idle + sorted(busy, key=lambda a: a.get("last_heartbeat", 0), reverse=True)
        selected = [a["name"] for a in candidates[:worker_count]]
        state = {"on": True, "workers": selected}
    else:
        state = {"on": False, "workers": []}

    with open(focus_file, "w") as f:
        json.dump(state, f)
    return jsonify(state)

@app.route("/api/factorio/focus", methods=["GET"])
def factorio_focus_state():
    focus_file = os.path.join(os.path.dirname(__file__), ".factorio_focus.json")
    try:
        with open(focus_file) as f:
            return jsonify(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify({"on": False, "workers": []})

@app.route("/api/factorio/plans", methods=["GET"])
def factorio_plans_proxy():
    import urllib.request
    port = _cfg.get("factorio", {}).get("bridge_port", 27016)
    try:
        resp = urllib.request.urlopen(f"http://localhost:{port}/api/plan/queue", timeout=10)
        return jsonify(json.loads(resp.read()))
    except Exception:
        return jsonify({"error": "Bridge unreachable"}), 502

@app.route("/api/factorio/plan-history", methods=["GET"])
def factorio_plan_history_proxy():
    import urllib.request
    port = _cfg.get("factorio", {}).get("bridge_port", 27016)
    try:
        resp = urllib.request.urlopen(f"http://localhost:{port}/api/plan/history", timeout=10)
        return jsonify(json.loads(resp.read()))
    except Exception:
        return jsonify({"error": "Bridge unreachable"}), 502
```

- [ ] **Step 2: Add auto-generation of factorio_analyze tasks in supervisor**

In `fleet/supervisor.py`, in the health check / tick loop (around line 145-148), add Focus-mode task generation:

```python
# Focus mode: auto-generate factorio_analyze tasks
_focus_file = os.path.join(os.path.dirname(__file__), ".factorio_focus.json")
_last_analyze_gen = 0

def _maybe_generate_factorio_tasks(now):
    global _last_analyze_gen
    try:
        with open(_focus_file) as f:
            focus = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if not focus.get("on"):
        return
    # Generate factorio_analyze every 60s
    if now - _last_analyze_gen < 60:
        return
    _last_analyze_gen = now
    import db
    # Check if there's already a pending factorio_analyze task
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM tasks WHERE type='factorio_analyze' AND status='PENDING' LIMIT 1"
        ).fetchone()
    if row:
        return
    def _do():
        db.post_task("factorio_analyze",
                     json.dumps({"auto_generated": True, "priority": 75}),
                     priority=75)
    db._retry_write(_do)
    log.info("Focus mode: auto-generated factorio_analyze task")
```

Call `_maybe_generate_factorio_tasks(now)` in the supervisor tick loop.

- [ ] **Step 3: Commit**

```bash
git add fleet/dashboard.py fleet/supervisor.py
git commit -m "feat(factorio): focus toggle endpoint, plan proxies, auto-generate analyze tasks"
```

---

### Task 10: New Skill — factorio_analyze

**Files:**
- Create: `fleet/skills/factorio_analyze.py`

- [ ] **Step 1: Create the factorio_analyze skill**

```python
# fleet/skills/factorio_analyze.py
"""Analyze Factorio game state and submit plans or directives to the brain."""

SKILL_NAME = "factorio_analyze"
DESCRIPTION = "Analyze Factorio game state and submit plans or directives to the brain"
REQUIRES_NETWORK = True
COMPLEXITY = "complex"

def run(payload, config):
    import json
    import urllib.request
    import logging

    log = logging.getLogger(SKILL_NAME)
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    base = f"http://localhost:{bridge_port}"

    # 1. Fetch game state
    try:
        resp = urllib.request.urlopen(f"{base}/api/state", timeout=15)
        state = json.loads(resp.read())
    except Exception:
        log.warning("Failed to fetch bridge state", exc_info=True)
        return {"status": "error", "error": "Bridge unreachable"}

    # 2. Fetch brain's current plan + objectives
    try:
        resp = urllib.request.urlopen(f"{base}/api/plan/queue", timeout=15)
        plan_info = json.loads(resp.read())
    except Exception:
        plan_info = {"current": None, "queued": []}

    # 3. Call provider for analysis
    try:
        import providers
        prompt = _build_analysis_prompt(state, plan_info)
        result = providers.llm_call(
            prompt,
            system="You are a Factorio strategy advisor. Analyze the game state and "
                   "suggest the best next actions. Return JSON with 'confidence' (0-1), "
                   "'actions' (list of action dicts), and 'rationale' (string). "
                   "If confidence < 0.7, return 'directive' (string) instead of actions.",
            timeout=30,
        )
        analysis = json.loads(result) if isinstance(result, str) else result
    except Exception:
        log.warning("LLM analysis failed", exc_info=True)
        return {"status": "error", "error": "LLM call failed"}

    # 4. Confidence gate
    confidence = float(analysis.get("confidence", 0.5))
    if confidence >= 0.7 and "actions" in analysis:
        # High confidence — submit full plan
        plan_data = {
            "actions": analysis["actions"],
            "priority": 75,
            "source": payload.get("worker_name", "analyzer"),
            "source_type": "worker",
            "rationale": analysis.get("rationale", "Auto-analysis"),
            "confidence": confidence,
        }
        try:
            req = urllib.request.Request(
                f"{base}/api/plan/submit",
                data=json.dumps(plan_data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            submit_result = json.loads(resp.read())
        except Exception:
            log.warning("Failed to submit plan", exc_info=True)
            submit_result = {"status": "error"}
        return {"status": "ok", "action": "plan_submitted", "result": submit_result}
    else:
        # Lower confidence — submit directive
        directive_text = analysis.get("directive", analysis.get("rationale", "No guidance"))
        directive_data = {
            "text": directive_text,
            "priority": 75,
            "source": payload.get("worker_name", "analyzer"),
            "sticky": False,
            "max_plans": 3,
        }
        try:
            req = urllib.request.Request(
                f"{base}/api/directive/submit",
                data=json.dumps(directive_data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            submit_result = json.loads(resp.read())
        except Exception:
            log.warning("Failed to submit directive", exc_info=True)
            submit_result = {"status": "error"}
        return {"status": "ok", "action": "directive_submitted", "result": submit_result}


def _build_analysis_prompt(state, plan_info):
    """Build analysis prompt from game state and plan queue info."""
    lines = ["Current Factorio game state:"]
    lines.append(json.dumps(state, indent=2, default=str)[:3000])  # truncate large states
    if plan_info.get("current"):
        lines.append(f"\nCurrent plan: step {plan_info['current'].get('index', 0)}"
                     f"/{plan_info['current'].get('total', 0)}")
    if plan_info.get("queued"):
        lines.append(f"\n{len(plan_info['queued'])} plans queued")
    lines.append("\nAnalyze the state and recommend next actions.")
    import json
    return "\n".join(lines)
```

- [ ] **Step 2: Verify skill loads**

Run: `cd fleet && python -c "import skills.factorio_analyze as s; print(s.SKILL_NAME, s.DESCRIPTION)"`
Expected: `factorio_analyze Analyze Factorio game state and submit plans or directives to the brain`

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/factorio_analyze.py
git commit -m "feat(factorio): add factorio_analyze skill with confidence-gated plan/directive submission"
```

---

### Task 11: Update Existing Factorio Skills to Use Plan Submission

**Files:**
- Modify: `fleet/skills/factorio_plan.py`
- Modify: `fleet/skills/factorio_act.py`

- [ ] **Step 1: Update factorio_plan.py to submit via bridge API**

The current `factorio_plan.py` returns `plan_context` (state + task + history) but doesn't generate actions or call the bridge. Rewrite `run()` to fetch state from the bridge, generate a plan via LLM, and submit it:

```python
# fleet/skills/factorio_plan.py — full replacement of run()
def run(payload, config):
    """Generate strategic plan from game state and submit to bridge."""
    import json
    import urllib.request
    import logging

    log = logging.getLogger(SKILL_NAME)
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    base = f"http://localhost:{bridge_port}"

    # Get state from payload or fetch from bridge
    state = payload.get("state")
    if not state:
        try:
            resp = urllib.request.urlopen(f"{base}/api/state", timeout=15)
            state = json.loads(resp.read())
        except Exception:
            return {"status": "error", "error": "No state available"}

    task = payload.get("task", "Build a factory")

    # For now, return plan context — LLM planning will be added by factorio_analyze
    # Submit the task objective as a directive so the brain knows what to prioritize
    directive_data = {
        "text": f"Strategic objective: {task}",
        "priority": 75,
        "source": payload.get("worker_name", "planner"),
        "sticky": False,
        "max_plans": 5,
    }
    try:
        req = urllib.request.Request(
            f"{base}/api/directive/submit",
            data=json.dumps(directive_data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        submit_result = json.loads(resp.read())
    except Exception:
        log.warning("Failed to submit directive", exc_info=True)
        submit_result = {"status": "error"}

    return {"status": "ok", "submit_result": submit_result, "state_summary": str(state)[:500]}
```

- [ ] **Step 2: Update factorio_act.py to submit via plan/submit**

Update `fleet/skills/factorio_act.py` to use `/api/plan/submit` instead of `/api/command`:

```python
# Replace the existing POST to /api/command with:
plan_data = {
    "actions": actions,
    "priority": payload.get("priority", 75),
    "source": payload.get("worker_name", "actor"),
    "source_type": "worker",
    "rationale": payload.get("rationale", "Direct action"),
    "confidence": payload.get("confidence", 0.9),
}
try:
    req = urllib.request.Request(
        f"http://localhost:{bridge_port}/api/plan/submit",
        data=json.dumps(plan_data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read())
    return {"status": "ok", **result}
except Exception as e:
    return {"status": "error", "error": str(e)}
```

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/factorio_plan.py fleet/skills/factorio_act.py
git commit -m "feat(factorio): update plan/act skills to use plan/submit API"
```

---

### Task 12: CLI Commands — Focus + Plans

**Files:**
- Modify: `fleet/lead_client.py:975-1079`

- [ ] **Step 1: Register argparse subparsers for focus and plans**

In `fleet/lead_client.py` at lines 1293-1310 (after `fac_sub.add_parser("clear-directives", ...)`), add:

```python
    p_ffocus = fac_sub.add_parser("focus", help="Toggle Factorio focus mode")
    p_ffocus.add_argument("action", nargs="?", default=None, choices=["on", "off"],
                          help="on/off (omit to show current state)")
    p_ffocus.add_argument("--workers", type=int, default=2, help="Number of reserved workers")

    fac_sub.add_parser("plans", help="Show plan queue (active + queued + history)")
```

- [ ] **Step 2: Add dashboard API helper and focus/plans handlers**

In `_handle_factorio()` (around line 975), add a dashboard API helper alongside the existing `_fapi` (bridge) helper:

```python
    # Dashboard API helper (port 5555) — for focus toggle
    from config import load_config as _lc
    _dcfg = _lc()
    _dport = _dcfg.get("dashboard", {}).get("port", 5555)
    _dbase = f"http://127.0.0.1:{_dport}"

    def _dapi(method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(
            f"{_dbase}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"Error: {e}")
            return None
```

Then add the command handlers (after existing `elif cmd == "clear-directives":` block):

```python
    elif cmd == "focus":
        action = getattr(args, "action", None)
        if action is None:
            # GET current state
            r = _dapi("GET", "/api/factorio/focus")
            if r:
                state = "ON" if r.get("on") else "OFF"
                workers = ", ".join(r.get("workers", [])) or "none"
                print(f"Focus: {state}  Workers: {workers}")
        elif action == "on":
            r = _dapi("POST", "/api/factorio/focus",
                      {"on": True, "workers": args.workers})
            if r:
                print(f"Focus ON — reserved workers: {', '.join(r.get('workers', []))}")
        elif action == "off":
            r = _dapi("POST", "/api/factorio/focus", {"on": False})
            if r:
                print("Focus OFF — workers released")

    elif cmd == "plans":
        r = _dapi("GET", "/api/factorio/plans")
        if r:
            current = r.get("current")
            if current:
                print(f"Active: step {current['index']}/{current['total']} "
                      f"(priority {current['priority']})")
            else:
                print("Active: none")
            queued = r.get("queued", [])
            if queued:
                print(f"\nQueued ({len(queued)}):")
                for p in queued:
                    print(f"  [{p['priority']}] {p['plan_id']} from {p['source']} "
                          f"— {p['rationale'][:60]}")
            shelved = r.get("shelved")
            if shelved:
                print(f"\nShelved: {shelved['plan_id']} (priority {shelved['priority']})")
```

- [ ] **Step 3: Enhance status subcommand to include Focus state**

In the existing `factorio status` handler (around line 1009-1024), add after the directives display:

```python
        # Focus state via dashboard API
        focus = _dapi("GET", "/api/factorio/focus")
        if focus:
            fstate = "ON" if focus.get("on") else "OFF"
            fworkers = ", ".join(focus.get("workers", [])) or "none"
            print(f"Focus: {fstate}  Workers: {fworkers}")
```

- [ ] **Step 4: Commit**

```bash
git add fleet/lead_client.py
git commit -m "feat(factorio): add focus/plans CLI commands to lead_client"
```

---

### Task 13: Integration Smoke Test

**Files:**
- No new files — uses existing smoke test patterns

- [ ] **Step 1: Verify all imports work**

```bash
cd fleet && python -c "
from factorio.agent_brain import (AgentBrain, PlanSubmission, Directive,
    PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW)
from factorio.bridge_config import BridgeConfig
print('AgentBrain imports OK')
print(f'BridgeConfig fields: focus_workers_default={BridgeConfig().focus_workers_default}')
import skills.factorio_analyze as fa
print(f'factorio_analyze: {fa.SKILL_NAME}')
from worker import _read_focus_state
print('worker focus reader OK')
print('All imports passed')
"
```

- [ ] **Step 2: Run full test suites**

```bash
cd fleet && python -m pytest ../tests/test_plan_queue.py ../tests/test_focus_toggle.py -v
```

Expected: ALL PASS

- [ ] **Step 3: Run existing smoke tests to verify no regressions**

```bash
cd fleet && python smoke_test.py --fast
```

Expected: 33/33 pass (no regressions)

- [ ] **Step 4: Commit any fixes**

If any tests fail, fix and commit individually.

- [ ] **Step 5: Final commit — tag integration complete**

```bash
git add -A
git commit -m "feat(factorio): queue-factorio integration — all tasks complete

Priority plan queue, focus toggle, factorio_analyze skill,
updated plan/act skills, CLI commands, dashboard endpoints.

Spec: docs/superpowers/specs/2026-03-28-queue-factorio-integration-design.md"
```

---

## Dependency Graph

```
Task 1 (PlanSubmission) ──┐
Task 2 (Directive ext)  ──┼── Task 3 (Plan Queue) ── Task 4 (next_action drain)
                           │                          │
                           │   Task 5 (prompt sort) ──┘
                           │
                           └── Task 6 (Bridge API) ── Task 10 (analyze skill)
                                                    ── Task 11 (update skills)

Task 7 (Config/flag file) ── Task 8 (Worker affinity) ── Task 9 (Supervisor/Dashboard)
                                                       ── Task 12 (CLI)

All ── Task 13 (Integration smoke test)
```

**Parallel groups:**
- **Group A** (Tasks 1-6): AgentBrain + Bridge API — sequential
- **Group B** (Tasks 7-9): Focus toggle infrastructure — sequential, independent of Group A
- **Group C** (Tasks 10-12): Skills + CLI — depends on both Group A and B
- **Task 13**: Integration — depends on all
