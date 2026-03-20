"""Tier 2: Autonomous research → synthesize → train cycle."""
import json
from datetime import datetime
from pathlib import Path
from collections import Counter

SKILL_NAME = "research_loop"
DESCRIPTION = "Autonomous research cycle — detect gaps, research, synthesize, generate training data"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "detect_gaps")

    if action == "detect_gaps":
        return _detect_knowledge_gaps()
    elif action == "research_cycle":
        return _run_research_cycle(payload, config)
    elif action == "quality_scores":
        return _get_quality_scores()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _detect_knowledge_gaps():
    """Scan knowledge/ for thin coverage areas."""
    coverage = {}
    if not KNOWLEDGE_DIR.exists():
        return json.dumps({"gaps": [], "coverage": {}})

    for subdir in sorted(KNOWLEDGE_DIR.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue
        files = list(subdir.rglob("*.md")) + list(subdir.rglob("*.json"))
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        coverage[subdir.name] = {
            "files": len(files),
            "size_kb": round(total_size / 1024, 1),
        }

    # Identify gaps: directories with < 3 files or < 5KB
    gaps = []
    for name, stats in coverage.items():
        if stats["files"] < 3:
            gaps.append({"area": name, "reason": f"only {stats['files']} files", "priority": "high"})
        elif stats["size_kb"] < 5:
            gaps.append({"area": name, "reason": f"only {stats['size_kb']}KB content", "priority": "medium"})

    return json.dumps({"gaps": gaps, "coverage": coverage, "total_areas": len(coverage)})


def _run_research_cycle(payload, config):
    """Full research cycle: detect gap → dispatch research → synthesize → generate training data."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    topic = payload.get("topic")
    if not topic:
        # Auto-detect from gaps
        gaps_result = json.loads(_detect_knowledge_gaps())
        gaps = gaps_result.get("gaps", [])
        if gaps:
            import random
            topic = random.choice(gaps[:3])["area"]  # pick from top 3 gaps
        else:
            return json.dumps({"status": "no_gaps", "message": "Knowledge base is well-covered"})

    # Dispatch research chain
    tasks = []

    # Step 1: Research
    t1 = db.post_task("web_search", json.dumps({"query": f"{topic} best practices 2026"}), priority=4)
    tasks.append({"step": "research", "task_id": t1, "skill": "web_search"})

    # Step 2: Summarize (depends on research)
    t2 = db.post_task("summarize", json.dumps({"description": f"Summarize research on {topic}"}),
                       priority=3, depends_on=[t1])
    tasks.append({"step": "summarize", "task_id": t2, "skill": "summarize"})

    # Step 3: Generate training data (depends on summary)
    t3 = db.post_task("dataset_synthesize", json.dumps({
        "topic": topic, "format": "instruction", "count": 10,
    }), priority=2, depends_on=[t2])
    tasks.append({"step": "training_data", "task_id": t3, "skill": "dataset_synthesize"})

    return json.dumps({
        "status": "dispatched",
        "topic": topic,
        "tasks": tasks,
        "pipeline": "research → summarize → training_data",
    })


def _get_quality_scores():
    """Aggregate quality scores from task results."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    try:
        with db.get_conn() as conn:
            # Count completed tasks per skill with success/fail rates
            rows = conn.execute("""
                SELECT type, status, COUNT(*) as n
                FROM tasks
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY type, status
            """).fetchall()

        skills = {}
        for r in rows:
            skill = r["type"]
            if skill not in skills:
                skills[skill] = {"done": 0, "failed": 0, "total": 0}
            skills[skill]["total"] += r["n"]
            if r["status"] == "DONE":
                skills[skill]["done"] += r["n"]
            elif r["status"] == "FAILED":
                skills[skill]["failed"] += r["n"]

        # Calculate success rate
        scored = []
        for name, stats in skills.items():
            rate = stats["done"] / stats["total"] if stats["total"] > 0 else 0
            scored.append({
                "skill": name,
                "success_rate": round(rate * 100, 1),
                "total": stats["total"],
                "done": stats["done"],
                "failed": stats["failed"],
            })
        scored.sort(key=lambda x: -x["success_rate"])

        return json.dumps({"quality_scores": scored})
    except Exception as e:
        return json.dumps({"error": str(e)})
