"""Cost Intelligence (CT-1 through CT-4) — usage tracking, summaries, deltas."""
import queue
import threading

_usage_queue = queue.Queue()
_usage_thread_started = False
_usage_thread_lock = threading.Lock()


def _get_conn():
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db.get_conn()


def _retry_write(fn, retries=8):
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db._retry_write(fn, retries)


def _start_usage_logger():
    global _usage_thread_started
    with _usage_thread_lock:
        if _usage_thread_started:
            return
        _usage_thread_started = True

    def _flush_loop():
        while True:
            batch = []
            try:
                item = _usage_queue.get(timeout=5)
                batch.append(item)
                while len(batch) < 10:
                    try:
                        batch.append(_usage_queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                continue
            for entry in batch:
                try:
                    _log_usage_sync(**entry)
                except Exception:
                    pass

    t = threading.Thread(target=_flush_loop, daemon=True)
    t.start()


def _log_usage_sync(skill, model, input_tokens, output_tokens,
                    cache_read_tokens=0, cache_create_tokens=0,
                    cost_usd=0.0, task_id=None, agent=None,
                    eval_duration_ms=None, prompt_duration_ms=None,
                    tokens_per_sec=None, provider=None):
    """Synchronous INSERT — called from the background flush thread."""
    def _do():
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO usage (skill, model, input_tokens, output_tokens,
                   cache_read_tokens, cache_create_tokens, cost_usd, task_id, agent,
                   eval_duration_ms, prompt_duration_ms, tokens_per_sec, provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill, model, input_tokens, output_tokens,
                 cache_read_tokens, cache_create_tokens, cost_usd, task_id, agent,
                 eval_duration_ms, prompt_duration_ms, tokens_per_sec, provider),
            )
    _retry_write(_do)


def log_usage(skill, model, input_tokens, output_tokens,
              cache_read_tokens=0, cache_create_tokens=0,
              cost_usd=0.0, task_id=None, agent=None,
              eval_duration_ms=None, prompt_duration_ms=None,
              tokens_per_sec=None, provider=None):
    """Non-blocking usage logging — buffers entries and flushes in background thread."""
    _start_usage_logger()
    _usage_queue.put({
        "skill": skill, "model": model,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens, "cache_create_tokens": cache_create_tokens,
        "cost_usd": cost_usd, "task_id": task_id, "agent": agent,
        "eval_duration_ms": eval_duration_ms, "prompt_duration_ms": prompt_duration_ms,
        "tokens_per_sec": tokens_per_sec, "provider": provider,
    })


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
    with _get_conn() as conn:
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
    with _get_conn() as conn:
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


def forecast_cost(days_ahead: int = 30) -> dict:
    """Project future costs based on recent usage trends.

    Uses last 14 days average daily cost to project forward.
    """
    with _get_conn() as conn:
        # Get daily totals for last 14 days
        rows = conn.execute("""
            SELECT date(created_at) as day, SUM(cost_usd) as daily_cost
            FROM usage
            WHERE created_at >= datetime('now', '-14 days')
            GROUP BY date(created_at)
            ORDER BY day
        """).fetchall()

    if not rows:
        return {"forecast_usd": 0, "avg_daily_usd": 0, "data_days": 0, "days_ahead": days_ahead}

    daily_costs = [r["daily_cost"] or 0 for r in rows]
    avg_daily = sum(daily_costs) / len(daily_costs)
    forecast = avg_daily * days_ahead

    # Trend: compare last 7 days vs first 7 days
    if len(daily_costs) >= 7:
        recent = sum(daily_costs[-7:]) / min(7, len(daily_costs[-7:]))
        earlier = sum(daily_costs[:7]) / min(7, len(daily_costs[:7]))
        trend = "increasing" if recent > earlier * 1.1 else "decreasing" if recent < earlier * 0.9 else "stable"
    else:
        trend = "insufficient data"

    return {
        "forecast_usd": round(forecast, 2),
        "avg_daily_usd": round(avg_daily, 4),
        "data_days": len(daily_costs),
        "days_ahead": days_ahead,
        "trend": trend,
    }


def get_daily_cost_series(days: int = 30) -> list:
    """Get daily cost time series for chart rendering."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT date(created_at) as day,
                   SUM(cost_usd) as total_cost,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   COUNT(*) as call_count
            FROM usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY date(created_at)
            ORDER BY day
        """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]


def get_skill_cost_breakdown(period: str = "week") -> list:
    """Get cost breakdown by skill for pie/bar chart."""
    period_map = {"day": "-1 day", "week": "-7 days", "month": "-30 days"}
    since = period_map.get(period, "-7 days")
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT skill,
                   SUM(cost_usd) as total_cost,
                   COUNT(*) as calls,
                   SUM(input_tokens + output_tokens) as total_tokens
            FROM usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY skill
            ORDER BY total_cost DESC
            LIMIT 10
        """, (since,)).fetchall()
        return [dict(r) for r in rows]


def get_model_usage_breakdown(period: str = "week") -> list:
    """Get usage breakdown by model for chart rendering."""
    period_map = {"day": "-1 day", "week": "-7 days", "month": "-30 days"}
    since = period_map.get(period, "-7 days")
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT model,
                   SUM(cost_usd) as total_cost,
                   COUNT(*) as calls,
                   SUM(cache_read_tokens) as cache_hits,
                   SUM(input_tokens) as total_input
            FROM usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY model
            ORDER BY total_cost DESC
        """, (since,)).fetchall()
        return [dict(r) for r in rows]
