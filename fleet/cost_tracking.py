"""Cost Intelligence (CT-1 through CT-4) — usage tracking, summaries, deltas."""

from db import get_conn, _retry_write


def log_usage(skill, model, input_tokens, output_tokens,
              cache_read_tokens=0, cache_create_tokens=0,
              cost_usd=0.0, task_id=None, agent=None):
    """Insert a usage record after each API call. Must never raise."""
    def _do():
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO usage (skill, model, input_tokens, output_tokens,
                   cache_read_tokens, cache_create_tokens, cost_usd, task_id, agent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill, model, input_tokens, output_tokens,
                 cache_read_tokens, cache_create_tokens, cost_usd, task_id, agent),
            )
    _retry_write(_do)


def get_usage_summary(period="week", group_by="skill"):
    """Aggregate usage data by period and grouping.

    Args:
        period: "day", "week", or "month"
        group_by: "skill", "model", or "agent"
    Returns:
        list of dicts with group key, calls, total_input, total_output,
        total_cache_reads, total_cache_creates, total_cost.
    """
    period_map = {"day": "-1 day", "week": "-7 days", "month": "-30 days"}
    since = period_map.get(period, "-7 days")
    # Whitelist group_by to prevent SQL injection
    if group_by not in ("skill", "model", "agent"):
        group_by = "skill"
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT {group_by},
                   COUNT(*) as calls,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_reads,
                   SUM(cache_create_tokens) as total_cache_creates,
                   SUM(cost_usd) as total_cost
            FROM usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY {group_by}
            ORDER BY total_cost DESC
        """, (since,)).fetchall()
        return [dict(r) for r in rows]


def get_usage_delta(from_start, from_end, to_start, to_end):
    """Compare per-skill usage between two date ranges.

    Returns list of {skill, metric, previous, current, delta_pct, direction}.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT skill,
                   SUM(CASE WHEN created_at BETWEEN ? AND ? THEN input_tokens END) as prev_input,
                   SUM(CASE WHEN created_at BETWEEN ? AND ? THEN input_tokens END) as curr_input,
                   SUM(CASE WHEN created_at BETWEEN ? AND ? THEN output_tokens END) as prev_output,
                   SUM(CASE WHEN created_at BETWEEN ? AND ? THEN output_tokens END) as curr_output,
                   SUM(CASE WHEN created_at BETWEEN ? AND ? THEN cost_usd END) as prev_cost,
                   SUM(CASE WHEN created_at BETWEEN ? AND ? THEN cost_usd END) as curr_cost
            FROM usage
            GROUP BY skill
            HAVING prev_input IS NOT NULL OR curr_input IS NOT NULL
        """, (from_start, from_end, to_start, to_end,
              from_start, from_end, to_start, to_end,
              from_start, from_end, to_start, to_end)).fetchall()

        result = []
        for r in rows:
            prev = r["prev_cost"] or 0
            curr = r["curr_cost"] or 0
            delta_pct = round((curr - prev) / prev * 100, 1) if prev else 0
            direction = "up" if delta_pct > 1 else ("down" if delta_pct < -1 else "flat")
            result.append({
                "skill": r["skill"],
                "previous_input": r["prev_input"] or 0,
                "current_input": r["curr_input"] or 0,
                "previous_output": r["prev_output"] or 0,
                "current_output": r["curr_output"] or 0,
                "previous_cost": round(prev, 6),
                "current_cost": round(curr, 6),
                "delta_pct": delta_pct,
                "direction": direction,
            })
        return result
