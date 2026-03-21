"""Idle Evolution (v0.42+0.30.01a) — workers self-improve when no tasks pending."""
import time

# Staleness cache — keyed by (agent, ts_bucket) where ts_bucket = epoch // 60
_staleness_cache = {}
_STALENESS_TTL = 60  # seconds


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


def get_least_evolved_skill(agent=None):
    """Pick a skill to evolve — weighted random from the least-evolved bottom 30%.

    v0.30.01a: Replaces deterministic selection to prevent all agents
    converging on the same skill (topic diversity fix).

    - Weighted random from bottom 30% least-evolved skills
    - 4h per-agent cooldown (skip skills this agent evolved recently)
    - Cross-worker dedup (skip skills another agent is currently evolving)
    - 60s TTL cache on the staleness DB query to reduce poll overhead
    """
    import random
    _AGENT_COOLDOWN_H = 4

    # Cache key rounded to 60s so all agents share the same staleness snapshot
    cache_key = int(time.time()) // _STALENESS_TTL
    cached = _staleness_cache.get(cache_key)

    with _get_conn() as conn:
        if cached is not None:
            skill_staleness = cached
            skill_names = [s for s, _ in skill_staleness]
        else:
            # Get all skill types that have been dispatched at least once
            active_skills = conn.execute(
                "SELECT DISTINCT type FROM tasks WHERE status='DONE' ORDER BY type"
            ).fetchall()
            if not active_skills:
                return None
            skill_names = [r["type"] for r in active_skills]

            # Get last evolution time for each skill
            skill_staleness = []
            for skill in skill_names:
                row = conn.execute(
                    "SELECT MAX(created_at) as last_run FROM idle_runs WHERE skill=?",
                    (skill,)
                ).fetchone()
                last_run = row["last_run"] if row and row["last_run"] else "2000-01-01"
                skill_staleness.append((skill, last_run))

            # Evict stale cache entries, store new result
            _staleness_cache.clear()
            _staleness_cache[cache_key] = skill_staleness

        # Sort by staleness (oldest first = most stale)
        skill_staleness.sort(key=lambda x: x[1])

        # Take bottom 30% (at least 3 skills)
        n_candidates = max(3, len(skill_staleness) * 30 // 100)
        candidates = skill_staleness[:n_candidates]

        # Filter: per-agent cooldown (skip skills this agent evolved in last 4h)
        if agent:
            cooled = conn.execute(
                "SELECT DISTINCT skill FROM idle_runs WHERE agent=? "
                "AND created_at >= datetime('now', ?)",
                (agent, f"-{_AGENT_COOLDOWN_H} hours")
            ).fetchall()
            cooled_skills = {r["skill"] for r in cooled}
            candidates = [(s, t) for s, t in candidates if s not in cooled_skills]

        # Filter: cross-worker dedup (skip skills currently being evolved by another agent)
        currently_evolving = conn.execute(
            "SELECT payload_json FROM tasks WHERE status='RUNNING' "
            "AND type IN ('skill_test', 'evolution_coordinator', 'skill_evolve')"
        ).fetchall()
        evolving_skills = set()
        import json
        for row in currently_evolving:
            try:
                p = json.loads(row["payload_json"] or "{}")
                if p.get("skill"):
                    evolving_skills.add(p["skill"])
            except Exception:
                pass
        candidates = [(s, t) for s, t in candidates if s not in evolving_skills]

        if not candidates:
            # Fallback: random from all skills
            return random.choice(skill_names) if skill_names else None

        # Weighted random: older = higher weight
        # Weight = index position (1-based, where 1 = most stale = highest weight)
        weights = list(range(len(candidates), 0, -1))
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return chosen[0]
