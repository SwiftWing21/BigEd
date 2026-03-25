SKILL_NAME = "retrospective"
DESCRIPTION = "Generate structured retrospective: achievements, failures, action items, metrics."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False


def run(payload, config):
    import db
    import json
    from datetime import date
    from pathlib import Path

    FLEET_DIR = Path(__file__).parent.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    RETRO_DIR = KNOWLEDGE_DIR / "retrospectives"
    RETRO_DIR.mkdir(parents=True, exist_ok=True)

    hours = payload.get("hours", 24)
    focus = payload.get("focus", "")  # optional focus area

    # Gather metrics
    with db.get_conn() as conn:
        # Task stats
        task_stats = conn.execute("""
            SELECT status, COUNT(*) as n
            FROM tasks
            WHERE created_at >= datetime('now', ? || ' hours')
            GROUP BY status
        """, (f"-{hours}",)).fetchall()

        tasks = {r["status"]: r["n"] for r in task_stats}
        total_tasks = sum(tasks.values())
        done = tasks.get("DONE", 0)
        failed = tasks.get("FAILED", 0)
        success_rate = round(done / max(total_tasks, 1) * 100, 1)

        # Top skills by volume
        top_skills = conn.execute("""
            SELECT type, status, COUNT(*) as n
            FROM tasks
            WHERE created_at >= datetime('now', ? || ' hours')
              AND type IS NOT NULL
            GROUP BY type, status
            ORDER BY n DESC
            LIMIT 30
        """, (f"-{hours}",)).fetchall()

        # Skill success/fail breakdown
        skill_stats = {}
        for r in top_skills:
            skill = r["type"]
            skill_stats.setdefault(skill, {"done": 0, "failed": 0, "total": 0})
            if r["status"] == "DONE":
                skill_stats[skill]["done"] += r["n"]
            elif r["status"] == "FAILED":
                skill_stats[skill]["failed"] += r["n"]
            skill_stats[skill]["total"] += r["n"]

        # Agent activity
        agent_stats = conn.execute("""
            SELECT assigned_to, status, COUNT(*) as n
            FROM tasks
            WHERE created_at >= datetime('now', ? || ' hours')
              AND assigned_to IS NOT NULL
            GROUP BY assigned_to, status
        """, (f"-{hours}",)).fetchall()

        agents = {}
        for r in agent_stats:
            agent = r["assigned_to"]
            agents.setdefault(agent, {"done": 0, "failed": 0, "total": 0})
            if r["status"] == "DONE":
                agents[agent]["done"] += r["n"]
            elif r["status"] == "FAILED":
                agents[agent]["failed"] += r["n"]
            agents[agent]["total"] += r["n"]

        # Cost data
        cost_data = conn.execute("""
            SELECT SUM(cost_usd) as total_cost, COUNT(*) as api_calls
            FROM usage
            WHERE created_at >= datetime('now', ? || ' hours')
        """, (f"-{hours}",)).fetchone()

        total_cost = round(cost_data["total_cost"] or 0, 4) if cost_data else 0
        api_calls = cost_data["api_calls"] or 0 if cost_data else 0

        # Feedback data
        feedback = conn.execute("""
            SELECT verdict, COUNT(*) as n
            FROM output_feedback
            WHERE created_at >= datetime('now', ? || ' hours')
            GROUP BY verdict
        """, (f"-{hours}",)).fetchall()
        feedback_stats = {r["verdict"]: r["n"] for r in feedback}

    # Identify achievements (high success skills)
    achievements = []
    for skill, stats in sorted(skill_stats.items(), key=lambda x: x[1]["done"], reverse=True):
        if stats["done"] > 0 and stats["failed"] == 0:
            achievements.append(f"{skill}: {stats['done']} tasks completed, zero failures")
        elif stats["done"] > 3 and stats["done"] / max(stats["total"], 1) > 0.8:
            rate = round(stats["done"] / stats["total"] * 100)
            achievements.append(f"{skill}: {stats['done']}/{stats['total']} ({rate}% success)")

    # Identify failures (high fail rate skills)
    failures = []
    for skill, stats in sorted(skill_stats.items(), key=lambda x: x[1]["failed"], reverse=True):
        if stats["failed"] > 0:
            rate = round(stats["failed"] / max(stats["total"], 1) * 100)
            failures.append(f"{skill}: {stats['failed']}/{stats['total']} failed ({rate}% failure rate)")

    # Generate action items
    action_items = []
    for skill, stats in skill_stats.items():
        if stats["total"] > 2 and stats["failed"] / max(stats["total"], 1) > 0.5:
            action_items.append(f"Investigate {skill} — {stats['failed']}/{stats['total']} failure rate")
    if total_cost > 1.0:
        action_items.append(f"Review API costs — ${total_cost:.2f} in {hours}h")
    if feedback_stats.get("rejected", 0) > 3:
        action_items.append(f"Review rejected outputs — {feedback_stats['rejected']} rejections in {hours}h")

    # Top performing agents
    top_agents = sorted(agents.items(), key=lambda x: x[1]["done"], reverse=True)[:5]

    # Build markdown report
    md = [
        f"# Fleet Retrospective — {date.today().isoformat()}",
        f"",
        f"**Period:** Last {hours} hours | **Total Tasks:** {total_tasks} | **Success Rate:** {success_rate}%",
        f"**API Calls:** {api_calls} | **Total Cost:** ${total_cost:.4f}",
        f"",
        f"---",
        f"",
        f"## Achievements",
        f"",
    ]
    if achievements:
        for a in achievements[:10]:
            md.append(f"- {a}")
    else:
        md.append("- No notable achievements in this period")
    md.append("")

    md.append("## Failures")
    md.append("")
    if failures:
        for f_item in failures[:10]:
            md.append(f"- {f_item}")
    else:
        md.append("- No failures recorded")
    md.append("")

    md.append("## Action Items")
    md.append("")
    if action_items:
        for ai in action_items:
            md.append(f"- [ ] {ai}")
    else:
        md.append("- No action items needed")
    md.append("")

    md.append("## Agent Performance")
    md.append("")
    md.append("| Agent | Done | Failed | Total | Rate |")
    md.append("|-------|------|--------|-------|------|")
    for agent, stats in top_agents:
        rate = round(stats["done"] / max(stats["total"], 1) * 100)
        md.append(f"| {agent} | {stats['done']} | {stats['failed']} | {stats['total']} | {rate}% |")
    md.append("")

    md.append("## Task Breakdown")
    md.append("")
    for status, count in sorted(tasks.items()):
        md.append(f"- **{status}**: {count}")
    md.append("")

    if feedback_stats:
        md.append("## Feedback")
        md.append("")
        for verdict, count in sorted(feedback_stats.items()):
            md.append(f"- {verdict}: {count}")
        md.append("")

    report = "\n".join(md)
    out_file = RETRO_DIR / f"retro_{date.today().isoformat()}.md"
    out_file.write_text(report, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "period_hours": hours,
        "total_tasks": total_tasks,
        "success_rate": success_rate,
        "total_cost": total_cost,
        "achievements": len(achievements),
        "failures": len(failures),
        "action_items": len(action_items),
    }
