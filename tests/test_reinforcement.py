"""Unit tests for fleet/reinforcement.py — human feedback loop / IQ scoring."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest


# ── 1. process_approved — no task found returns None ─────────────────────────

def test_process_approved_no_match(tmp_db):
    """Returns None when no matching task exists."""
    import reinforcement as rf
    result = rf.process_approved("task:999999", "agent_x", "summarize")
    assert result is None


# ── 2. process_approved — exact task:id match boosts IQ ──────────────────────

def test_process_approved_boosts_iq(tmp_db):
    """Approving a task that exists via 'task:<id>' bumps intelligence_score."""
    import db
    import reinforcement as rf

    # Create and complete a task with a known IQ score
    task_id = db.post_task("summarize", json.dumps({"text": "hello"}))
    db.claim_task("agent_x")
    db.complete_task(task_id, json.dumps({"result": "done"}))

    # Set initial IQ score
    def _set_iq():
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET intelligence_score=0.5 WHERE id=?", (task_id,))
    db._retry_write(_set_iq)

    new_score = rf.process_approved(f"task:{task_id}", "agent_x", "summarize")
    assert new_score is not None
    assert new_score == pytest.approx(0.55, abs=1e-3)


# ── 3. process_approved — IQ is capped at 1.0 ────────────────────────────────

def test_process_approved_cap(tmp_db):
    """IQ boost never exceeds 1.0."""
    import db
    import reinforcement as rf

    task_id = db.post_task("summarize", json.dumps({}))
    db.claim_task("agent_x")
    db.complete_task(task_id, json.dumps({}))

    # Set IQ just below cap
    def _set_iq():
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET intelligence_score=0.99 WHERE id=?", (task_id,))
    db._retry_write(_set_iq)

    score = rf.process_approved(f"task:{task_id}", "agent_x", "summarize")
    assert score == pytest.approx(1.0, abs=1e-3)


# ── 4. process_rejected — dispatches a re-review task ────────────────────────

def test_process_rejected_dispatches_task(tmp_db):
    """Rejection creates an 'evaluate' task in the queue."""
    import db
    import reinforcement as rf

    task_id = rf.process_rejected(
        "task:42", "agent_x", "summarize", feedback_text="wrong format"
    )
    assert task_id is not None

    # Verify the task was posted
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT type, payload_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    assert row is not None
    assert row["type"] == "evaluate"
    payload = json.loads(row["payload_json"])
    assert payload["action"] == "re_review"
    assert payload["feedback_text"] == "wrong format"
    assert payload["reason"] == "human_rejected"


# ── 5. age_out_unreviewed — returns 0 on empty DB ────────────────────────────

def test_age_out_unreviewed_empty(tmp_db):
    """Returns 0 when no tasks exist."""
    import reinforcement as rf
    count = rf.age_out_unreviewed(days=3)
    assert count == 0


# ── 6. age_out_unreviewed — ages tasks older than N days ─────────────────────

def test_age_out_unreviewed_ages_old_task(tmp_db):
    """Tasks completed long ago with IQ score get aged out."""
    import db
    import reinforcement as rf

    task_id = db.post_task("summarize", json.dumps({}))
    db.claim_task("worker_a")
    db.complete_task(task_id, json.dumps({"result": "x"}))

    # Backdate the task to 10 days ago
    def _backdate():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET created_at=datetime('now','-10 days'), intelligence_score=0.5 WHERE id=?",
                (task_id,),
            )
    db._retry_write(_backdate)

    count = rf.age_out_unreviewed(days=3)
    assert count >= 1

    # Verify a neutral feedback row was inserted
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT verdict FROM output_feedback WHERE output_path=?",
            (f"task:{task_id}",),
        ).fetchone()
    assert row is not None
    assert row["verdict"] == "neutral"


# ── 7. age_out_unreviewed — does not re-age already-reviewed tasks ────────────

def test_age_out_unreviewed_skips_reviewed(tmp_db):
    """Tasks that already have a feedback row should not be aged again."""
    import db
    import reinforcement as rf

    task_id = db.post_task("summarize", json.dumps({}))
    db.claim_task("worker_a")
    db.complete_task(task_id, json.dumps({}))

    # Insert feedback manually
    def _feedback():
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO output_feedback
                   (output_path, verdict, feedback_text, operator, agent_name, skill_type)
                   VALUES (?, 'approved', '', 'admin', '', '')""",
                (f"task:{task_id}",),
            )
    db._retry_write(_feedback)

    # Backdate
    def _backdate():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET created_at=datetime('now','-10 days'), intelligence_score=0.6 WHERE id=?",
                (task_id,),
            )
    db._retry_write(_backdate)

    count = rf.age_out_unreviewed(days=3)
    # The task should have been skipped (already reviewed)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM output_feedback WHERE output_path=?",
            (f"task:{task_id}",),
        ).fetchone()
    assert rows["cnt"] == 1  # still just the one we inserted


# ── 8. IQ constants are within expected ranges ────────────────────────────────

def test_iq_constants():
    """IQ_BOOST should be small and positive; IQ_CAP should be 1.0."""
    import reinforcement as rf
    assert 0 < rf.IQ_BOOST <= 0.2
    assert rf.IQ_CAP == 1.0
    assert rf.AGE_OUT_DAYS > 0
