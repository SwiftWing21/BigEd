"""Human feedback on agent outputs — split from db.py (TD-04)."""
import logging

log = logging.getLogger(__name__)


def submit_feedback(output_path, verdict, feedback_text="", agent_name="", skill_type="", reviewer=""):
    """Store human feedback on an agent output.

    verdict must be 'approved', 'rejected', or 'neutral'.
    Upserts: a new review on the same path replaces the previous one.
    """
    from db import get_conn, _retry_write

    if verdict not in ("approved", "rejected", "neutral"):
        raise ValueError(f"Invalid verdict: {verdict!r}")
    def _do():
        with get_conn() as conn:
            conn.execute("DELETE FROM output_feedback WHERE output_path = ?", (output_path,))
            conn.execute(
                """INSERT INTO output_feedback
                   (output_path, verdict, feedback_text, operator, agent_name, skill_type, reviewer)
                   VALUES (?, ?, ?, 'human', ?, ?, ?)""",
                (output_path, verdict, feedback_text, agent_name, skill_type, reviewer),
            )
    _retry_write(_do)


def get_feedback(output_path):
    """Get feedback for a specific output. Returns dict or None."""
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM output_feedback WHERE output_path = ? ORDER BY id DESC LIMIT 1",
            (output_path,),
        ).fetchone()
        return dict(row) if row else None


def get_feedback_stats(days=7):
    """Get approval/rejection stats by agent and skill over recent window."""
    from db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT agent_name, skill_type, verdict, COUNT(*) as cnt
               FROM output_feedback
               WHERE created_at >= datetime('now', ?)
               GROUP BY agent_name, skill_type, verdict
               ORDER BY cnt DESC""",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_feedback_bulk(output_paths):
    """Get feedback verdicts for multiple paths in one query.

    Returns a dict mapping output_path -> verdict string.
    Paths without feedback are omitted from the result.
    """
    from db import get_conn

    if not output_paths:
        return {}
    with get_conn() as conn:
        placeholders = ",".join("?" * len(output_paths))
        rows = conn.execute(
            f"""SELECT output_path, verdict FROM output_feedback
                WHERE output_path IN ({placeholders})""",
            list(output_paths),
        ).fetchall()
        return {r["output_path"]: r["verdict"] for r in rows}
