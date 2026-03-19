"""Idle Evolution (v0.42) — workers self-improve when no tasks pending."""


def _get_conn():
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db.get_conn()


def _retry_write(fn, retries=8):
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db._retry_write(fn, retries)


def log_idle_run(agent, skill, result=None, cost_usd=0.0):
    """Record an idle evolution run."""
    def _do():
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO idle_runs (agent, skill, result, cost_usd) VALUES (?, ?, ?, ?)",
                (agent, skill, result, cost_usd)
            )
    _retry_write(_do)


def get_idle_stats(period="week"):
    """Get idle run statistics."""
    period_map = {"day": "-1 day", "week": "-7 days", "month": "-30 days"}
    since = period_map.get(period, "-7 days")
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT skill, COUNT(*) as runs, SUM(cost_usd) as total_cost
            FROM idle_runs WHERE created_at >= datetime('now', ?)
            GROUP BY skill ORDER BY runs DESC
        """, (since,)).fetchall()
        return [dict(r) for r in rows]


def get_least_evolved_skill():
    """Find the skill with the oldest (or no) idle evolution run."""
    with _get_conn() as conn:
        # Get all skill types that have been dispatched at least once
        active_skills = conn.execute(
            "SELECT DISTINCT type FROM tasks WHERE status='DONE' ORDER BY type"
        ).fetchall()
        if not active_skills:
            return None
        skill_names = [r["type"] for r in active_skills]
        # Find which has the oldest idle_run (or none at all)
        for skill in skill_names:
            row = conn.execute(
                "SELECT MAX(created_at) as last_run FROM idle_runs WHERE skill=?",
                (skill,)
            ).fetchone()
            if not row or not row["last_run"]:
                return skill  # never evolved
        # All have been evolved — return oldest
        row = conn.execute("""
            SELECT skill, MAX(created_at) as last_run
            FROM idle_runs GROUP BY skill ORDER BY last_run ASC LIMIT 1
        """).fetchone()
        return row["skill"] if row else skill_names[0]
