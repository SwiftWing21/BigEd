"""Skill Recommendation Engine — co-occurrence-based skill suggestions.

Analyzes task history to find skill affinities: "users who ran code_review
also ran security_audit". Powers the /api/recommendations/<skill> endpoint
and skill chaining suggestions in the dashboard.

v0.200.00b: Initial implementation.
"""
import logging
import time
from collections import defaultdict

log = logging.getLogger("skill_recommender")

# ── Cache ─────────────────────────────────────────────────────────────────────
_cooccurrence_cache = None
_cache_ts = 0.0
_CACHE_TTL = 300  # 5 minutes


def _build_cooccurrence_matrix(days=30):
    """Build a co-occurrence matrix from task history.

    Groups tasks by agent+day into "sessions". Skills that appear in the
    same session are considered co-occurring. Returns a dict of
    {skill: {other_skill: count}}.
    """
    try:
        import db

        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT assigned_to, type,
                       DATE(created_at) as task_day
                FROM tasks
                WHERE status = 'DONE'
                  AND created_at >= datetime('now', ?)
                  AND assigned_to IS NOT NULL
                  AND type IS NOT NULL
                ORDER BY assigned_to, task_day
            """, (f"-{days} days",)).fetchall()

        # Group by (agent, day) -> set of skills
        sessions = defaultdict(set)
        for row in rows:
            key = (row["assigned_to"], row["task_day"])
            sessions[key].add(row["type"])

        # Build co-occurrence counts
        matrix = defaultdict(lambda: defaultdict(int))
        for skills in sessions.values():
            skill_list = list(skills)
            for i, s1 in enumerate(skill_list):
                for s2 in skill_list:
                    if s1 != s2:
                        matrix[s1][s2] += 1

        return dict(matrix)

    except Exception:
        log.warning("Failed to build co-occurrence matrix", exc_info=True)
        return {}


def _get_matrix():
    """Return cached co-occurrence matrix, rebuilding if stale."""
    global _cooccurrence_cache, _cache_ts
    now = time.time()
    if _cooccurrence_cache is None or (now - _cache_ts) > _CACHE_TTL:
        _cooccurrence_cache = _build_cooccurrence_matrix()
        _cache_ts = now
    return _cooccurrence_cache


def get_recommendations(completed_skill, n=5):
    """Get skill recommendations based on co-occurrence with completed_skill.

    Returns a list of dicts: [{"skill": name, "score": float, "reason": str}]
    sorted by co-occurrence score descending.
    """
    try:
        matrix = _get_matrix()
        neighbors = matrix.get(completed_skill, {})
        if not neighbors:
            return []

        # Normalize scores to 0-1 range
        max_count = max(neighbors.values()) if neighbors else 1
        results = []
        for skill, count in sorted(neighbors.items(), key=lambda x: -x[1]):
            results.append({
                "skill": skill,
                "score": round(count / max_count, 3),
                "co_occurrences": count,
                "reason": f"Often run together with {completed_skill}",
            })
            if len(results) >= n:
                break

        return results

    except Exception:
        log.warning("get_recommendations failed", exc_info=True)
        return []


def get_skill_chain(starting_skill, depth=3):
    """Build a recommended skill sequence starting from a given skill.

    Greedily picks the highest co-occurrence neighbor at each step,
    avoiding already-visited skills. Returns a list of skill names.
    """
    try:
        matrix = _get_matrix()
        chain = [starting_skill]
        visited = {starting_skill}

        for _ in range(depth):
            current = chain[-1]
            neighbors = matrix.get(current, {})
            # Pick the highest co-occurrence skill not yet in chain
            best = None
            best_count = 0
            for skill, count in neighbors.items():
                if skill not in visited and count > best_count:
                    best = skill
                    best_count = count
            if best is None:
                break
            chain.append(best)
            visited.add(best)

        return chain

    except Exception:
        log.warning("get_skill_chain failed", exc_info=True)
        return [starting_skill]


def get_popular_skills(n=10):
    """Return the most-used skills by task count over the last 30 days.

    Returns a list of dicts: [{"skill": name, "task_count": int, "success_rate": float}]
    """
    try:
        import db

        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT type as skill,
                       COUNT(*) as task_count,
                       SUM(CASE WHEN status = 'DONE' THEN 1 ELSE 0 END) as done_count,
                       SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as fail_count
                FROM tasks
                WHERE created_at >= datetime('now', '-30 days')
                  AND type IS NOT NULL
                GROUP BY type
                ORDER BY task_count DESC
                LIMIT ?
            """, (n,)).fetchall()

        results = []
        for row in rows:
            total = row["task_count"]
            done = row["done_count"] or 0
            rate = round(done / total, 3) if total > 0 else 0.0
            results.append({
                "skill": row["skill"],
                "task_count": total,
                "success_rate": rate,
                "fail_count": row["fail_count"] or 0,
            })

        return results

    except Exception:
        log.warning("get_popular_skills failed", exc_info=True)
        return []


def invalidate_cache():
    """Force cache refresh on next call (e.g. after bulk task import)."""
    global _cooccurrence_cache, _cache_ts
    _cooccurrence_cache = None
    _cache_ts = 0.0
