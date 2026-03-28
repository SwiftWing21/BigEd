import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet', 'factorio'))

import time
from agent_brain import AgentBrain, PlanSubmission, Directive, PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW
from world_model import WorldModel
from bridge_config import BridgeConfig


def _make_brain():
    """Create a minimal AgentBrain for testing (no Ollama needed)."""
    cfg = BridgeConfig()
    wm = WorldModel()
    brain = AgentBrain(cfg, wm)
    return brain


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
    low = PlanSubmission(actions=[{"action": "move"}], priority=25,
                         source="spec", source_type="worker", rationale="", confidence=0.5)
    high = PlanSubmission(actions=[{"action": "craft"}], priority=75,
                          source="w1", source_type="worker", rationale="", confidence=0.9)
    brain.submit_plan(low)
    brain.submit_plan(high)
    assert len(brain._plan_queue) == 2
    # heapq: (-priority, seq, plan) — first element has most negative priority (highest)
    assert brain._plan_queue[0][0] == -75


def test_submit_plan_queue_depth_limit():
    brain = _make_brain()
    for i in range(brain.MAX_PLAN_QUEUE_DEPTH):
        ps = PlanSubmission(actions=[{"action": "move"}], priority=50,
                            source=f"w{i}", source_type="worker", rationale="", confidence=0.5)
        brain.submit_plan(ps)
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
    brain._plan = [{"action": "mine", "resource": "iron"}]
    brain._plan_index = 0
    brain._current_priority = PRIORITY_NORMAL

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


def test_pop_next_plan():
    brain = _make_brain()
    ps = PlanSubmission(
        actions=[{"action": "craft", "item": "furnace", "count": 1}],
        priority=PRIORITY_HIGH,
        source="w1", source_type="worker", rationale="test", confidence=0.9,
    )
    brain.submit_plan(ps)
    assert len(brain._plan_queue) == 1
    brain._plan = []
    brain._plan_index = 0
    brain._pop_next_plan()
    assert brain._plan == [{"action": "craft", "item": "furnace", "count": 1}]
    assert len(brain._plan_queue) == 0


def test_shelved_plan_restored_after_preempt():
    brain = _make_brain()
    brain._plan = [{"action": "mine"}, {"action": "smelt"}]
    brain._plan_index = 0
    brain._current_priority = PRIORITY_NORMAL

    critical = PlanSubmission(
        actions=[{"action": "repair"}],
        priority=PRIORITY_CRITICAL,
        source="human", source_type="human", rationale="fix", confidence=1.0,
    )
    brain.submit_plan(critical)
    assert brain._plan == [{"action": "repair"}]
    assert brain._shelved_plan is not None

    brain._plan = []
    brain._plan_index = 0
    brain._restore_shelved_plan()
    brain._pop_next_plan()
    assert brain._plan == [{"action": "mine"}, {"action": "smelt"}]
