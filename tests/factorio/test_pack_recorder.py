"""Tests for PackRecorder — learned pack discovery from successful runs."""

from factorio.pack_recorder import PackRecorder


def test_recorder_records_actions():
    rec = PackRecorder(max_length=10)
    rec.record({"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}})
    rec.record({"action": "insert", "item": "coal", "count": 5})
    assert len(rec.buffer) == 2


def test_recorder_caps_at_max_length():
    rec = PackRecorder(max_length=3)
    for i in range(5):
        rec.record({"action": "wait", "i": i})
    assert len(rec.buffer) == 3


def test_recorder_extracts_candidate_on_checkpoint():
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
    rec = PackRecorder(max_length=100)
    for i in range(3):
        rec.record({"action": "wait"})
    candidate = rec.on_checkpoint_complete(checkpoint_id=0)
    assert candidate is None


def test_recorder_truncates_long_sequences():
    rec = PackRecorder(max_length=200)
    for i in range(60):
        rec.record({"action": "wait", "i": i})
    candidate = rec.on_checkpoint_complete(checkpoint_id=0)
    assert candidate is not None
    assert len(candidate.actions) <= 50


def test_recorder_clear():
    rec = PackRecorder(max_length=10)
    for i in range(5):
        rec.record({"action": "wait"})
    assert len(rec.buffer) == 5
    rec.clear()
    assert len(rec.buffer) == 0


def test_recorder_truncates_takes_most_recent():
    """When >50 actions, the most recent 50 should be kept."""
    rec = PackRecorder(max_length=200)
    for i in range(60):
        rec.record({"action": "wait", "i": i})
    candidate = rec.on_checkpoint_complete(checkpoint_id=2)
    assert candidate is not None
    # The last action in the candidate should be the most recent one
    assert candidate.actions[-1]["i"] == 59
    # The first action should be i=10 (60-50=10)
    assert candidate.actions[0]["i"] == 10
