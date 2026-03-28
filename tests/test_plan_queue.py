import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet', 'factorio'))

from agent_brain import PlanSubmission, Directive, PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW


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
