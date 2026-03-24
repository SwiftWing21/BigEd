"""Analyze ML router performance — success rates by skill/agent, worst performers.

Queries the tasks table for completed tasks (DONE/FAILED) in the last N days,
computes success rates by skill and by agent, and identifies the worst-performing
skill-agent pairs (>50% failure rate with a minimum sample threshold).

Payload:
  days    int   lookback window in days (default: 7)

Returns:
  {"status": "ok", "period_days": N, "total_tasks": X,
   "skill_stats": {...}, "agent_stats": {...}, "worst_skills": [...]}
"""
import logging

SKILL_NAME = "router_analyze"
DESCRIPTION = "Analyze ML router performance — success rates by skill and agent"
REQUIRES_NETWORK = False

log = logging.getLogger(__name__)

_MIN_SAMPLES = 3  # minimum tasks to flag a skill-agent pair


def run(payload, config):
    try:
        import db

        days = max(1, int(payload.get("days", 7)))

        with db.get_conn() as conn:
            # All completed tasks in the window
            rows = conn.execute(
                """SELECT type, assigned_to, status
                   FROM tasks
                   WHERE status IN ('DONE', 'FAILED')
                     AND created_at >= datetime('now', ?)""",
                (f"-{days} days",),
            ).fetchall()

        if not rows:
            return {
                "status": "ok",
                "period_days": days,
                "total_tasks": 0,
                "skill_stats": {},
                "agent_stats": {},
                "worst_skills": [],
                "message": "No completed tasks in the period",
            }

        # Aggregate by skill
        skill_counts = {}  # skill -> {"done": X, "failed": Y}
        agent_counts = {}  # agent -> {"done": X, "failed": Y}
        pair_counts = {}   # (skill, agent) -> {"done": X, "failed": Y}

        for row in rows:
            skill = row["type"] or "unknown"
            agent = row["assigned_to"] or "unassigned"
            status = row["status"]

            for bucket, key in [(skill_counts, skill), (agent_counts, agent)]:
                if key not in bucket:
                    bucket[key] = {"done": 0, "failed": 0}
                bucket[key]["done" if status == "DONE" else "failed"] += 1

            pair_key = f"{skill}|{agent}"
            if pair_key not in pair_counts:
                pair_counts[pair_key] = {"done": 0, "failed": 0, "skill": skill, "agent": agent}
            pair_counts[pair_key]["done" if status == "DONE" else "failed"] += 1

        # Compute rates
        def _rate(d):
            total = d["done"] + d["failed"]
            return round(d["done"] / max(total, 1), 3)

        skill_stats = {
            k: {"done": v["done"], "failed": v["failed"],
                "total": v["done"] + v["failed"], "success_rate": _rate(v)}
            for k, v in sorted(skill_counts.items())
        }

        agent_stats = {
            k: {"done": v["done"], "failed": v["failed"],
                "total": v["done"] + v["failed"], "success_rate": _rate(v)}
            for k, v in sorted(agent_counts.items())
        }

        # Identify worst-performing pairs (>50% failure, min samples)
        worst_skills = []
        for pair_data in pair_counts.values():
            total = pair_data["done"] + pair_data["failed"]
            if total < _MIN_SAMPLES:
                continue
            failure_rate = pair_data["failed"] / total
            if failure_rate > 0.5:
                worst_skills.append({
                    "skill": pair_data["skill"],
                    "agent": pair_data["agent"],
                    "total": total,
                    "done": pair_data["done"],
                    "failed": pair_data["failed"],
                    "failure_rate": round(failure_rate, 3),
                })

        worst_skills.sort(key=lambda x: x["failure_rate"], reverse=True)

        return {
            "status": "ok",
            "period_days": days,
            "total_tasks": len(rows),
            "skill_stats": skill_stats,
            "agent_stats": agent_stats,
            "worst_skills": worst_skills,
        }

    except Exception:
        log.warning("router_analyze failed", exc_info=True)
        return {"status": "error", "message": "router analysis failed — see logs"}
