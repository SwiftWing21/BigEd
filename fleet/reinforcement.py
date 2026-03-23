"""Reinforcement loop — processes human feedback into IQ adjustments.

Converts approved/rejected verdicts from the output_feedback table into
intelligence_score changes on the tasks table.  Never crashes the caller —
all public functions are wrapped in try/except and return safe defaults.

Wired into:
- dashboard.py: api_submit_feedback() calls process_approved/process_rejected
- supervisor.py: periodic age_out_unreviewed() every 10 minutes
"""
import json
import logging
import time

log = logging.getLogger("reinforcement")

# ── Constants ────────────────────────────────────────────────────────────────
IQ_BOOST = 0.05       # IQ bump on approval
IQ_CAP = 1.0          # maximum intelligence_score
AGE_OUT_DAYS = 3       # days before unreviewed outputs become neutral


def process_approved(output_path, agent_name, skill_type):
    """Boost IQ for agent+skill pair on approved output.

    Finds the most recent DONE task matching the output_path in its
    result_json, then bumps intelligence_score by IQ_BOOST (capped at 1.0).

    Returns the new score, or None if no matching task found.
    """
    try:
        from db import get_conn, _retry_write

        task_row = _find_task_for_output(output_path, agent_name, skill_type)
        if not task_row:
            log.debug("No task found for approved output: %s", output_path)
            return None

        task_id = task_row["id"]
        current = task_row["intelligence_score"] or 0.0
        new_score = round(min(IQ_CAP, current + IQ_BOOST), 3)

        def _do():
            with get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET intelligence_score=? WHERE id=?",
                    (new_score, task_id),
                )

        _retry_write(_do)
        log.info("IQ boost: task %d %.3f -> %.3f (approved: %s)",
                 task_id, current, new_score, output_path)
        return new_score

    except Exception as e:
        log.debug("process_approved error: %s", e)
        return None


def process_rejected(output_path, agent_name, skill_type, feedback_text=""):
    """Handle rejected output -- dispatch re-review task.

    Posts a new 'evaluate' task targeting the rejected output so another
    agent can review it.  Includes the human feedback_text in the payload
    so the reviewer agent knows what was wrong.

    Returns the new task ID, or None on failure.
    """
    try:
        from db import post_task

        payload = {
            "action": "re_review",
            "output_path": output_path,
            "agent_name": agent_name,
            "skill_type": skill_type,
            "feedback_text": feedback_text,
            "reason": "human_rejected",
        }
        task_id = post_task(
            "evaluate",
            json.dumps(payload),
            priority=6,  # slightly above default — review is important
        )
        log.info("Re-review dispatched: task %s for rejected output %s",
                 task_id, output_path)
        return task_id

    except Exception as e:
        log.debug("process_rejected error: %s", e)
        return None


def age_out_unreviewed(days=AGE_OUT_DAYS):
    """Mark outputs older than N days without feedback as neutral.

    Scans the tasks table for DONE tasks with a result_json and a
    created_at older than `days` that have no corresponding row in
    output_feedback.  Inserts a 'neutral' feedback row so they no
    longer show as pending review.

    Returns the count of aged-out entries.
    """
    try:
        from db import get_conn, _retry_write

        aged = [0]

        def _do():
            with get_conn() as conn:
                # Find DONE tasks older than N days with no feedback yet.
                # We join against output_feedback on a best-effort basis:
                # tasks store result_json which may contain file paths, but
                # the canonical key is the task ID itself used as output_path
                # in the form "task:<id>".
                rows = conn.execute("""
                    SELECT t.id FROM tasks t
                    LEFT JOIN output_feedback f
                        ON f.output_path = ('task:' || CAST(t.id AS TEXT))
                    WHERE t.status = 'DONE'
                      AND t.created_at < datetime('now', ?)
                      AND t.intelligence_score IS NOT NULL
                      AND f.id IS NULL
                    LIMIT 100
                """, (f"-{days} days",)).fetchall()

                for row in rows:
                    task_id = row["id"]
                    conn.execute(
                        """INSERT INTO output_feedback
                           (output_path, verdict, feedback_text, operator, agent_name, skill_type)
                           VALUES (?, 'neutral', 'auto-aged', 'system', '', '')""",
                        (f"task:{task_id}",),
                    )
                aged[0] = len(rows)

        _retry_write(_do)
        if aged[0]:
            log.info("Aged out %d unreviewed outputs (>%d days)", aged[0], days)
        return aged[0]

    except Exception as e:
        log.debug("age_out_unreviewed error: %s", e)
        return 0


def process_ditl_rejection(output_path, agent_name, feedback_text=""):
    """DITL mode: rejected clinical output -> PHI audit + re-review pipeline.

    Logs the rejection to the phi_audit table for HIPAA compliance, then
    dispatches a clinical_review skill task on the rejected output.

    Returns dict with audit_id and task_id, or None on failure.
    """
    try:
        from db import get_conn, _retry_write, post_task

        audit_id = [None]

        def _log_audit():
            with get_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO phi_audit
                       (user_id, action, data_scope, model_used, phi_detected, deidentified)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        agent_name or "unknown",
                        "feedback_rejection",
                        output_path,
                        "human_review",
                        1,   # PHI detected (conservative — assume yes for rejections)
                        0,   # not yet deidentified
                    ),
                )
                audit_id[0] = cur.lastrowid

        _retry_write(_log_audit)

        # Dispatch clinical review on the rejected output
        payload = {
            "action": "ditl_re_review",
            "output_path": output_path,
            "agent_name": agent_name,
            "feedback_text": feedback_text,
            "phi_audit_id": audit_id[0],
            "reason": "ditl_human_rejected",
        }
        task_id = post_task(
            "clinical_review",
            json.dumps(payload),
            priority=8,  # high priority — clinical content
            classification="restricted",
        )
        log.info("DITL rejection: audit=%s, re-review task=%s for %s",
                 audit_id[0], task_id, output_path)
        return {"audit_id": audit_id[0], "task_id": task_id}

    except Exception as e:
        log.debug("process_ditl_rejection error: %s", e)
        return None


# ── Internal helpers ─────────────────────────────────────────────────────────

def _find_task_for_output(output_path, agent_name="", skill_type=""):
    """Find the most recent DONE task that produced a given output.

    Search strategy (in order):
    1. Exact match on output_path in result_json
    2. Task with matching agent + skill if output_path is 'task:<id>' format
    """
    from db import get_conn

    with get_conn() as conn:
        # Strategy 1: output_path is 'task:<id>' — direct lookup
        if output_path.startswith("task:"):
            try:
                task_id = int(output_path.split(":", 1)[1])
                row = conn.execute(
                    "SELECT id, intelligence_score FROM tasks WHERE id=? AND status='DONE'",
                    (task_id,),
                ).fetchone()
                if row:
                    return dict(row)
            except (ValueError, IndexError):
                pass

        # Strategy 2: search result_json for the output_path string
        # Use LIKE for a substring match — covers file paths embedded in JSON
        row = conn.execute(
            """SELECT id, intelligence_score FROM tasks
               WHERE status='DONE'
                 AND result_json LIKE ?
               ORDER BY id DESC LIMIT 1""",
            (f"%{output_path}%",),
        ).fetchone()
        if row:
            return dict(row)

        # Strategy 3: match by agent + skill (latest task)
        if agent_name and skill_type:
            row = conn.execute(
                """SELECT id, intelligence_score FROM tasks
                   WHERE status='DONE'
                     AND assigned_to=?
                     AND type=?
                   ORDER BY id DESC LIMIT 1""",
                (agent_name, skill_type),
            ).fetchone()
            if row:
                return dict(row)

    return None
