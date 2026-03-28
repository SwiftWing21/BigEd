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
