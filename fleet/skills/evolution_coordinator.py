"""Tier 1: Coordinated multi-agent skill evolution with cross-skill learning."""
import json
import time
from datetime import datetime
from pathlib import Path
import logging

# Evolution cascade dedup — fast-path cache to prevent duplicate skill_test dispatches
_EVOLVING_SKILLS: dict[str, float] = {}  # {skill_name: expiry_ts}
_EVOLVE_TTL = 1800  # 30 minutes

SKILL_NAME = "evolution_coordinator"
DESCRIPTION = "Coordinate multi-agent skill evolution with leaderboard tracking"
VERSION = "1.0.0"
COMPLEXITY = "complex"
REQUIRES_NETWORK = False
SUITE = "ops"
log = logging.getLogger(SKILL_NAME)

FLEET_DIR = Path(__file__).parent.parent
EVOLUTION_LOG = FLEET_DIR / "knowledge" / "evolution" / "evolution_log.jsonl"
LEADERBOARD = FLEET_DIR / "knowledge" / "evolution" / "leaderboard.json"


def run(payload: dict, config: dict) -> dict:
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
        return {"status": "error", "error": f"Unknown action: {action}"}


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

    if "status" not in result:
        result["status"] = "ok"
    return result


def _cross_skill_learning(payload, config):
    """When one skill improves, trigger re-evaluation of dependent skills."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    improved_skill = payload.get("skill", "")
    if not improved_skill:
        return {"status": "error", "error": "skill required"}

    # Find skills that commonly run after the improved skill
    related = _find_related_skills(improved_skill)
    if not related:
        return {"status": "ok", "detail": f"no relations for {improved_skill}"}

    # Evict expired entries from fast-path cache
    global _EVOLVING_SKILLS
    now = time.time()
    _EVOLVING_SKILLS = {k: v for k, v in _EVOLVING_SKILLS.items() if v > now}

    # Dispatch skill_test for each related skill (with dedup)
    dispatched = []
    skipped = []
    for related_skill in related[:3]:  # cap at 3
        # Fast-path: check in-memory cache
        if related_skill in _EVOLVING_SKILLS:
            skipped.append(f"{related_skill} (cached)")
            continue

        # DB check: is there already a PENDING/RUNNING skill_test for this skill?
        try:
            with db.get_conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM tasks WHERE type='skill_test' "
                    "AND status IN ('PENDING','RUNNING') "
                    "AND payload_json LIKE ?",
                    (f'%"{related_skill}"%',)
                ).fetchone()
            if existing:
                skipped.append(f"{related_skill} (task #{existing['id']})")
                _EVOLVING_SKILLS[related_skill] = now + _EVOLVE_TTL
                continue
        except Exception:
            pass  # If DB check fails, proceed with dispatch (fail-open)

        # Dispatch and record in cache
        tid = db.post_task("skill_test", json.dumps({
            "skill": related_skill,
            "trigger": f"cross-learn from {improved_skill}",
        }), priority=2)
        _EVOLVING_SKILLS[related_skill] = now + _EVOLVE_TTL
        dispatched.append({"skill": related_skill, "task_id": tid})

    if skipped:
        log.info("Cross-skill learning: skipped %s (already queued)", ", ".join(skipped))

    return {"status": "triggered", "source": improved_skill, "dispatched": dispatched, "skipped": skipped}


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
        return {"status": "ok", "skills": [], "agents": [], "message": "No evolution data yet"}
    try:
        data = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
        if "status" not in data:
            data["status"] = "ok"
        return data
    except Exception:
        return {"status": "ok", "skills": [], "agents": []}


def _evolution_status():
    """Current evolution activity."""
    if not EVOLUTION_LOG.exists():
        return {"status": "ok", "total": 0, "recent": []}
    entries = []
    for line in EVOLUTION_LOG.read_text(encoding="utf-8").splitlines()[-20:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return {"status": "ok", "total": len(entries), "recent": entries[-10:]}


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
