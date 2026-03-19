"""Tier 1: Coordinated multi-agent skill evolution with cross-skill learning."""
import json
import time
from datetime import datetime
from pathlib import Path

SKILL_NAME = "evolution_coordinator"
DESCRIPTION = "Coordinate multi-agent skill evolution with leaderboard tracking"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
EVOLUTION_LOG = FLEET_DIR / "knowledge" / "evolution" / "evolution_log.jsonl"
LEADERBOARD = FLEET_DIR / "knowledge" / "evolution" / "leaderboard.json"


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "evolve")

    if action == "evolve":
        return _coordinate_evolution(payload, config)
    elif action == "leaderboard":
        return _get_leaderboard()
    elif action == "cross_learn":
        return _cross_skill_learning(payload, config)
    elif action == "status":
        return _evolution_status()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _coordinate_evolution(payload, config):
    """Launch a coordinated evolution pipeline for a skill."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    from workflows import execute_workflow

    skill = payload.get("skill")
    if not skill:
        # Auto-select: find least-evolved skill
        least = db.get_least_evolved_skill()
        skill = least or "summarize"

    result = execute_workflow("skill_evolution_pipeline", {"skill": skill, "description": f"Evolve {skill}"})

    # Log evolution attempt
    _log_evolution(skill, "started", result.get("task_ids", []))

    return json.dumps(result)


def _cross_skill_learning(payload, config):
    """When one skill improves, trigger re-evaluation of dependent skills."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    improved_skill = payload.get("skill", "")
    if not improved_skill:
        return json.dumps({"error": "skill required"})

    # Find skills that commonly run after the improved skill
    related = _find_related_skills(improved_skill)

    # Dispatch skill_test for each related skill
    triggered = []
    for related_skill in related[:3]:  # cap at 3
        tid = db.post_task("skill_test", json.dumps({
            "skill": related_skill,
            "trigger": f"cross-learn from {improved_skill}",
        }), priority=2)
        triggered.append({"skill": related_skill, "task_id": tid})

    return json.dumps({"status": "triggered", "source": improved_skill, "related": triggered})


def _find_related_skills(skill_name):
    """Find skills that are commonly used alongside this one."""
    # Skill dependency map (manual for now, could be learned from task history)
    relations = {
        "web_search": ["summarize", "rag_index"],
        "summarize": ["flashcard", "discuss"],
        "code_write": ["code_review", "code_quality", "skill_test"],
        "code_review": ["code_quality", "code_refactor"],
        "skill_draft": ["skill_test", "skill_evolve"],
        "skill_evolve": ["skill_test", "deploy_skill"],
        "lead_research": ["account_review", "marketing"],
        "security_audit": ["security_review", "pen_test"],
    }
    return relations.get(skill_name, [])


def _get_leaderboard():
    """Evolution leaderboard — which skills improved most, which agents contributed."""
    if not LEADERBOARD.exists():
        return json.dumps({"skills": [], "agents": [], "message": "No evolution data yet"})
    try:
        data = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
        return json.dumps(data)
    except Exception:
        return json.dumps({"skills": [], "agents": []})


def _evolution_status():
    """Current evolution activity."""
    if not EVOLUTION_LOG.exists():
        return json.dumps({"total": 0, "recent": []})
    entries = []
    for line in EVOLUTION_LOG.read_text(encoding="utf-8").splitlines()[-20:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return json.dumps({"total": len(entries), "recent": entries[-10:]})


def _log_evolution(skill, status, task_ids=None):
    """Append to evolution log."""
    try:
        EVOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "skill": skill,
            "status": status,
            "task_ids": task_ids or [],
        }
        with open(EVOLUTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _update_leaderboard(skill, agent, improvement):
    """Update the evolution leaderboard."""
    try:
        LEADERBOARD.parent.mkdir(parents=True, exist_ok=True)
        data = {"skills": {}, "agents": {}}
        if LEADERBOARD.exists():
            data = json.loads(LEADERBOARD.read_text(encoding="utf-8"))

        # Update skill score
        skills = data.setdefault("skills", {})
        skills.setdefault(skill, {"evolutions": 0, "improvements": 0})
        skills[skill]["evolutions"] += 1
        if improvement:
            skills[skill]["improvements"] += 1

        # Update agent contribution
        agents = data.setdefault("agents", {})
        agents.setdefault(agent, {"contributions": 0})
        agents[agent]["contributions"] += 1

        LEADERBOARD.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass
