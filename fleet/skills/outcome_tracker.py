"""Track task outcomes over time — trends, regressions, per-skill/agent breakdowns.

Compares current-period vs previous-period success rates to surface improving
and degrading skills/agents.  Generates a markdown report with actionable
insights saved to knowledge/outcomes/.
"""

SKILL_NAME = "outcome_tracker"
DESCRIPTION = "Track task outcome trends: success rates, regressions, per-skill/agent breakdowns."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ml"


def run(payload, config):
    import db
    import json
    from datetime import date
    from pathlib import Path

    FLEET_DIR = Path(__file__).parent.parent
    OUT_DIR = FLEET_DIR / "knowledge" / "outcomes"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    hours = payload.get("hours", 24)
    min_tasks = payload.get("min_tasks", 3)  # minimum tasks to include in trend

    with db.get_conn() as conn:
        # --- Current period ---
        cur_rows = conn.execute("""
            SELECT type, assigned_to, status, intelligence_score
            FROM tasks
            WHERE created_at >= datetime('now', ? || ' hours')
              AND type IS NOT NULL
        """, (f"-{hours}",)).fetchall()

        # --- Previous period (same length, immediately before) ---
        prev_rows = conn.execute("""
            SELECT type, assigned_to, status, intelligence_score
            FROM tasks
            WHERE created_at >= datetime('now', ? || ' hours')
              AND created_at <  datetime('now', ? || ' hours')
              AND type IS NOT NULL
        """, (f"-{hours * 2}", f"-{hours}")).fetchall()

    # --- Aggregate helper ---
    def _aggregate(rows):
        skills = {}
        agents = {}
        for r in rows:
            skill = r["type"]
            agent = r["assigned_to"] or "unassigned"
            status = r["status"]

            for bucket, key in ((skills, skill), (agents, agent)):
                bucket.setdefault(key, {"done": 0, "failed": 0, "total": 0, "iq_scores": []})
                bucket[key]["total"] += 1
                if status == "DONE":
                    bucket[key]["done"] += 1
                elif status == "FAILED":
                    bucket[key]["failed"] += 1
                if r["intelligence_score"] is not None:
                    bucket[key]["iq_scores"].append(r["intelligence_score"])
        return skills, agents

    cur_skills, cur_agents = _aggregate(cur_rows)
    prev_skills, prev_agents = _aggregate(prev_rows)

    # --- Trend detection ---
    def _rate(bucket):
        return round(bucket["done"] / max(bucket["total"], 1) * 100, 1)

    def _avg_iq(bucket):
        scores = bucket["iq_scores"]
        return round(sum(scores) / len(scores), 3) if scores else None

    improving = []
    degrading = []
    stable_good = []

    for skill, cur in cur_skills.items():
        if cur["total"] < min_tasks:
            continue
        cur_rate = _rate(cur)
        prev = prev_skills.get(skill)
        if prev and prev["total"] >= min_tasks:
            prev_rate = _rate(prev)
            delta = cur_rate - prev_rate
            entry = {
                "skill": skill,
                "current_rate": cur_rate,
                "previous_rate": prev_rate,
                "delta": round(delta, 1),
                "current_tasks": cur["total"],
                "avg_iq": _avg_iq(cur),
            }
            if delta >= 10:
                improving.append(entry)
            elif delta <= -10:
                degrading.append(entry)
            elif cur_rate >= 80:
                stable_good.append(entry)
        elif cur_rate >= 80:
            stable_good.append({
                "skill": skill,
                "current_rate": cur_rate,
                "current_tasks": cur["total"],
                "avg_iq": _avg_iq(cur),
            })

    # --- Agent performance ---
    agent_summary = []
    for agent, cur in sorted(cur_agents.items(), key=lambda x: x[1]["total"], reverse=True):
        agent_summary.append({
            "agent": agent,
            "done": cur["done"],
            "failed": cur["failed"],
            "total": cur["total"],
            "rate": _rate(cur),
            "avg_iq": _avg_iq(cur),
        })

    # --- Overall stats ---
    total_cur = len(cur_rows)
    done_cur = sum(1 for r in cur_rows if r["status"] == "DONE")
    failed_cur = sum(1 for r in cur_rows if r["status"] == "FAILED")
    overall_rate = round(done_cur / max(total_cur, 1) * 100, 1)

    total_prev = len(prev_rows)
    done_prev = sum(1 for r in prev_rows if r["status"] == "DONE")
    prev_rate = round(done_prev / max(total_prev, 1) * 100, 1)

    # --- Build markdown report ---
    md = [
        f"# Outcome Tracker — {date.today().isoformat()}",
        "",
        f"**Period:** Last {hours}h | **Tasks:** {total_cur} | "
        f"**Success Rate:** {overall_rate}% (prev: {prev_rate}%)",
        "",
        "---",
        "",
    ]

    if degrading:
        md.append("## Degrading Skills")
        md.append("")
        degrading.sort(key=lambda x: x["delta"])
        for d in degrading:
            iq = f" | IQ: {d['avg_iq']}" if d.get("avg_iq") is not None else ""
            md.append(
                f"- **{d['skill']}**: {d['current_rate']}% "
                f"(was {d['previous_rate']}%, {d['delta']:+.1f}pp){iq}"
            )
        md.append("")

    if improving:
        md.append("## Improving Skills")
        md.append("")
        improving.sort(key=lambda x: -x["delta"])
        for i in improving:
            iq = f" | IQ: {i['avg_iq']}" if i.get("avg_iq") is not None else ""
            md.append(
                f"- **{i['skill']}**: {i['current_rate']}% "
                f"(was {i['previous_rate']}%, {i['delta']:+.1f}pp){iq}"
            )
        md.append("")

    if stable_good:
        md.append("## Stable (80%+ Success)")
        md.append("")
        for s in sorted(stable_good, key=lambda x: -x["current_rate"])[:15]:
            iq = f" | IQ: {s['avg_iq']}" if s.get("avg_iq") is not None else ""
            md.append(f"- {s['skill']}: {s['current_rate']}% ({s['current_tasks']} tasks){iq}")
        md.append("")

    md.append("## Agent Performance")
    md.append("")
    md.append("| Agent | Done | Failed | Total | Rate | Avg IQ |")
    md.append("|-------|------|--------|-------|------|--------|")
    for a in agent_summary[:10]:
        iq_str = f"{a['avg_iq']:.3f}" if a["avg_iq"] is not None else "—"
        md.append(
            f"| {a['agent']} | {a['done']} | {a['failed']} | "
            f"{a['total']} | {a['rate']}% | {iq_str} |"
        )
    md.append("")

    report_text = "\n".join(md)
    out_file = OUT_DIR / f"outcomes_{date.today().isoformat()}.md"
    out_file.write_text(report_text, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "period_hours": hours,
        "total_tasks": total_cur,
        "overall_rate": overall_rate,
        "improving": len(improving),
        "degrading": len(degrading),
        "stable_good": len(stable_good),
    }
