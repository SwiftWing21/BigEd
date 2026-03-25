"""
Agent personality tuner — analyzes task history per agent to compute
behavioral metrics and generate personality profiles with system prompt
adjustment suggestions.

Output: knowledge/personalities/personality_<agent>_YYYY-MM-DD.md
"""
import logging
from pathlib import Path

SKILL_NAME = "agent_personality_tune"
DESCRIPTION = "Analyze agent task history, compute behavioral metrics, and suggest system prompt tuning per agent."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
PERSONALITY_DIR = KNOWLEDGE_DIR / "personalities"

log = logging.getLogger(__name__)


def _classify_complexity(payload_json):
    """Estimate task complexity from payload length as a simple heuristic."""
    if not payload_json:
        return "simple"
    length = len(str(payload_json))
    if length > 2000:
        return "complex"
    if length > 500:
        return "moderate"
    return "simple"


def _compute_agent_metrics(conn, agent_name, hours):
    """Gather behavioral metrics for a single agent from task history."""
    tasks = conn.execute("""
        SELECT type, status, payload_json, result_json, created_at
        FROM tasks
        WHERE assigned_to = ?
          AND created_at >= datetime('now', ? || ' hours')
        ORDER BY created_at DESC
    """, (agent_name, f"-{hours}")).fetchall()

    if not tasks:
        return None

    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "DONE")
    failed = sum(1 for t in tasks if t["status"] == "FAILED")
    success_rate = round(done / max(total, 1) * 100, 1)

    # Skill frequency
    skill_counts = {}
    for t in tasks:
        skill = t["type"] or "unknown"
        skill_counts[skill] = skill_counts.get(skill, 0) + 1
    preferred_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)

    # Average result length (proxy for verbosity)
    result_lengths = []
    for t in tasks:
        if t["result_json"]:
            result_lengths.append(len(str(t["result_json"])))
    avg_result_len = round(sum(result_lengths) / max(len(result_lengths), 1))

    # Success rate by complexity
    complexity_stats = {"simple": {"done": 0, "total": 0},
                        "moderate": {"done": 0, "total": 0},
                        "complex": {"done": 0, "total": 0}}
    for t in tasks:
        c = _classify_complexity(t["payload_json"])
        complexity_stats[c]["total"] += 1
        if t["status"] == "DONE":
            complexity_stats[c]["done"] += 1

    complexity_rates = {}
    for level, stats in complexity_stats.items():
        if stats["total"] > 0:
            complexity_rates[level] = round(
                stats["done"] / stats["total"] * 100, 1
            )

    # Task type distribution
    type_distribution = {}
    for t in tasks:
        ttype = t["type"] or "unknown"
        status = t["status"] or "unknown"
        type_distribution.setdefault(ttype, {"done": 0, "failed": 0, "total": 0})
        type_distribution[ttype]["total"] += 1
        if status == "DONE":
            type_distribution[ttype]["done"] += 1
        elif status == "FAILED":
            type_distribution[ttype]["failed"] += 1

    return {
        "total_tasks": total,
        "done": done,
        "failed": failed,
        "success_rate": success_rate,
        "preferred_skills": preferred_skills[:10],
        "avg_result_length": avg_result_len,
        "complexity_rates": complexity_rates,
        "type_distribution": type_distribution,
    }


def _suggest_adjustments(metrics):
    """Generate system prompt adjustment suggestions based on metrics."""
    suggestions = []

    if metrics["success_rate"] < 60:
        suggestions.append(
            "Low success rate ({:.0f}%). Consider narrowing task scope or "
            "adding more explicit instructions to reduce ambiguity.".format(
                metrics["success_rate"]
            )
        )
    elif metrics["success_rate"] > 90:
        suggestions.append(
            "High success rate ({:.0f}%). Agent may be ready for more "
            "complex task assignments.".format(metrics["success_rate"])
        )

    # Verbosity
    if metrics["avg_result_length"] > 5000:
        suggestions.append(
            "High verbosity (avg result ~{:,} chars). Add conciseness "
            "directive to system prompt.".format(metrics["avg_result_length"])
        )
    elif metrics["avg_result_length"] < 200 and metrics["total_tasks"] > 5:
        suggestions.append(
            "Very terse output (avg ~{} chars). Consider requesting "
            "more detailed explanations.".format(metrics["avg_result_length"])
        )

    # Complexity gaps
    cr = metrics["complexity_rates"]
    if cr.get("complex", 100) < 50 and cr.get("simple", 0) > 80:
        suggestions.append(
            "Strong on simple tasks but struggles with complex ones. "
            "Add chain-of-thought or step-by-step reasoning prompts."
        )

    # Specialization
    top_skills = metrics["preferred_skills"]
    if len(top_skills) >= 2:
        top_count = top_skills[0][1]
        second_count = top_skills[1][1]
        if top_count > second_count * 3:
            suggestions.append(
                "Heavily specialized in '{}'. Consider diversifying "
                "task assignments for resilience.".format(top_skills[0][0])
            )

    # Failure patterns
    for skill, stats in metrics["type_distribution"].items():
        if stats["total"] >= 3 and stats["failed"] / stats["total"] > 0.5:
            suggestions.append(
                "Skill '{}' has {}/{} failure rate. Add specific guidance "
                "or examples for this task type.".format(
                    skill, stats["failed"], stats["total"]
                )
            )

    if not suggestions:
        suggestions.append("No significant issues detected. Current profile is well-balanced.")

    return suggestions


def _build_profile_report(agent_name, metrics, suggestions, hours):
    """Render a personality profile as Markdown."""
    from datetime import date

    today = date.today().isoformat()
    lines = [
        f"# Agent Personality Profile — {agent_name}",
        f"",
        f"**Date:** {today} | **Analysis Period:** {hours} hours | "
        f"**Total Tasks:** {metrics['total_tasks']}",
        f"",
        f"---",
        f"",
        f"## Behavioral Metrics",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Success Rate | {metrics['success_rate']}% |",
        f"| Tasks Completed | {metrics['done']} |",
        f"| Tasks Failed | {metrics['failed']} |",
        f"| Avg Result Length | {metrics['avg_result_length']:,} chars |",
        f"",
    ]

    # Preferred skills
    lines.append("## Preferred Skills")
    lines.append("")
    lines.append("| Skill | Count |")
    lines.append("|-------|-------|")
    for skill, count in metrics["preferred_skills"]:
        lines.append(f"| {skill} | {count} |")
    lines.append("")

    # Complexity breakdown
    if metrics["complexity_rates"]:
        lines.append("## Success by Complexity")
        lines.append("")
        lines.append("| Complexity | Success Rate |")
        lines.append("|------------|-------------|")
        for level in ("simple", "moderate", "complex"):
            rate = metrics["complexity_rates"].get(level)
            if rate is not None:
                lines.append(f"| {level.title()} | {rate}% |")
        lines.append("")

    # Task type breakdown
    if metrics["type_distribution"]:
        lines.append("## Task Type Breakdown")
        lines.append("")
        lines.append("| Type | Done | Failed | Total | Rate |")
        lines.append("|------|------|--------|-------|------|")
        sorted_types = sorted(
            metrics["type_distribution"].items(),
            key=lambda x: x[1]["total"],
            reverse=True,
        )
        for ttype, stats in sorted_types[:15]:
            rate = round(stats["done"] / max(stats["total"], 1) * 100)
            lines.append(
                f"| {ttype} | {stats['done']} | {stats['failed']} | "
                f"{stats['total']} | {rate}% |"
            )
        lines.append("")

    # Suggestions
    lines.append("## System Prompt Adjustment Suggestions")
    lines.append("")
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")

    return "\n".join(lines)


def run(payload, config):
    """Analyze agent task history and generate personality profiles."""
    import db
    import json
    from datetime import date

    PERSONALITY_DIR.mkdir(parents=True, exist_ok=True)

    hours = payload.get("hours", 168)  # default: 7 days
    target_agent = payload.get("agent")  # optional: single agent

    try:
        with db.get_conn() as conn:
            # Get agent list
            if target_agent:
                agents = [{"name": target_agent}]
            else:
                agents = conn.execute("""
                    SELECT DISTINCT assigned_to as name
                    FROM tasks
                    WHERE assigned_to IS NOT NULL
                      AND created_at >= datetime('now', ? || ' hours')
                """, (f"-{hours}",)).fetchall()

            if not agents:
                return {
                    "status": "ok",
                    "message": "No agent task history found in the given period",
                    "profiles_generated": 0,
                }

            profiles = []
            saved_files = []
            today = date.today().isoformat()

            for agent_row in agents:
                agent_name = agent_row["name"] if hasattr(agent_row, "keys") else agent_row.get("name", str(agent_row))
                try:
                    metrics = _compute_agent_metrics(conn, agent_name, hours)
                    if not metrics:
                        continue

                    suggestions = _suggest_adjustments(metrics)
                    report = _build_profile_report(agent_name, metrics, suggestions, hours)

                    # Safe filename
                    safe_name = agent_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
                    out_file = PERSONALITY_DIR / f"personality_{safe_name}_{today}.md"
                    out_file.write_text(report, encoding="utf-8")

                    profiles.append({
                        "agent": agent_name,
                        "success_rate": metrics["success_rate"],
                        "total_tasks": metrics["total_tasks"],
                        "suggestions": len(suggestions),
                    })
                    saved_files.append(str(out_file))
                except Exception:
                    log.warning("Failed to profile agent %s", agent_name, exc_info=True)
                    continue

    except Exception:
        log.warning("agent_personality_tune failed", exc_info=True)
        return {"status": "error", "message": "Failed to read agent task history"}

    return {
        "status": "ok",
        "profiles_generated": len(profiles),
        "period_hours": hours,
        "saved_to": saved_files,
        "summary": profiles,
    }
