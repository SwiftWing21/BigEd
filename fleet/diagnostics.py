"""Diagnostics (DT) — quarantine, failure detection, stuck review monitoring."""
import json


def _get_conn():
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db.get_conn()


def _retry_write(fn, retries=8):
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db._retry_write(fn, retries)


def quarantine_agent(name, reason):
    """Set agent status to QUARANTINED with reason stored in messages."""
    def _do():
        with _get_conn() as conn:
            conn.execute(
                "UPDATE agents SET status='QUARANTINED' WHERE name=?", (name,))
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json, channel)
                VALUES ('watchdog', ?, ?, 'fleet')
            """, (name, json.dumps({"type": "quarantine", "reason": reason})))
    _retry_write(_do)


def clear_quarantine(name):
    """Remove quarantine status — agent returns to IDLE."""
    def _do():
        with _get_conn() as conn:
            conn.execute(
                "UPDATE agents SET status='IDLE' WHERE name=? AND status='QUARANTINED'",
                (name,))
    _retry_write(_do)


def get_failure_streaks(threshold=3):
    """Find agents with N+ consecutive recent task failures.

    Returns list of {agent, consecutive_failures, last_error}.
    """
    with _get_conn() as conn:
        # Get agents with recent failures
        rows = conn.execute("""
            SELECT assigned_to as agent,
                   COUNT(*) as fail_count,
                   MAX(error) as last_error
            FROM (
                SELECT assigned_to, error,
                       ROW_NUMBER() OVER (PARTITION BY assigned_to ORDER BY id DESC) as rn
                FROM tasks
                WHERE assigned_to IS NOT NULL AND status IN ('FAILED', 'DONE')
            )
            WHERE rn <= ? AND status = 'FAILED'
            GROUP BY assigned_to
            HAVING fail_count >= ?
        """, (threshold + 2, threshold)).fetchall()
        # Fallback: simpler query if window functions cause issues
        if not rows:
            rows = conn.execute("""
                SELECT assigned_to as agent, COUNT(*) as fail_count,
                       MAX(error) as last_error
                FROM (
                    SELECT * FROM tasks
                    WHERE assigned_to IS NOT NULL AND status = 'FAILED'
                    ORDER BY id DESC LIMIT ?
                )
                GROUP BY assigned_to
                HAVING fail_count >= ?
            """, (threshold * 20, threshold)).fetchall()
        return [dict(r) for r in rows]


def get_stuck_reviews(timeout_minutes=30):
    """Find tasks stuck in REVIEW status for too long."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, type, assigned_to
            FROM tasks
            WHERE status = 'REVIEW'
              AND (julianday('now') - julianday(created_at)) * 1440 > ?
        """, (timeout_minutes,)).fetchall()
        return [dict(r) for r in rows]
