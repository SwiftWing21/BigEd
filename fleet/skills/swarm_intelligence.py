"""Tier 3: Swarm intelligence — agent specialization tracking, task decomposition, adaptive affinity."""
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

SKILL_NAME = "swarm_intelligence"
DESCRIPTION = "Swarm behavior — agent specialization tracking, task decomposition, adaptive affinity"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
SPECIALIZATION_FILE = FLEET_DIR / "knowledge" / "swarm" / "specializations.json"


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "analyze")

    if action == "analyze":
        return _analyze_specializations()
    elif action == "recommend_affinity":
        return _recommend_affinity_updates()
    elif action == "decompose":
        return _decompose_complex_task(payload, config)
    elif action == "agent_fitness":
        return _agent_fitness_report()
    elif action == "swarm_status":
        return _swarm_status()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _analyze_specializations():
    """Analyze task history to discover agent specializations."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    try:
        with db.get_conn() as conn:
            # Get per-agent, per-skill success rates
            rows = conn.execute("""
                SELECT assigned_to, type, status, COUNT(*) as n
                FROM tasks
                WHERE assigned_to IS NOT NULL AND status IN ('DONE', 'FAILED')
                GROUP BY assigned_to, type, status
            """).fetchall()

        agents = defaultdict(lambda: defaultdict(lambda: {"done": 0, "failed": 0}))
        for r in rows:
            agent = r["assigned_to"]
            skill = r["type"]
            if r["status"] == "DONE":
                agents[agent][skill]["done"] += 1
            else:
                agents[agent][skill]["failed"] += 1

        # Find specializations: skills where agent has >80% success and >3 completions
        specializations = {}
        for agent, skills in agents.items():
            top_skills = []
            for skill, stats in skills.items():
                total = stats["done"] + stats["failed"]
                if total >= 3:
                    rate = stats["done"] / total
                    if rate >= 0.8:
                        top_skills.append({
                            "skill": skill,
                            "success_rate": round(rate * 100, 1),
                            "total": total,
                        })
            top_skills.sort(key=lambda x: (-x["success_rate"], -x["total"]))
            if top_skills:
                specializations[agent] = top_skills[:5]

        # Save
        _save_specializations(specializations)

        return json.dumps({"specializations": specializations, "agents_analyzed": len(agents)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _recommend_affinity_updates():
    """Recommend affinity routing changes based on observed performance."""
    specs = _load_specializations()
    if not specs:
        return json.dumps({"recommendations": [], "message": "Run analyze first"})

    from config import load_config
    config = load_config()
    current_affinity = config.get("affinity", {})

    recommendations = []
    for agent, skills in specs.items():
        base_role = agent.split("_")[0] if "_" in agent else agent
        current = set(current_affinity.get(base_role, []))
        discovered = {s["skill"] for s in skills}

        # Skills the agent is good at but not in their affinity
        new_skills = discovered - current
        if new_skills:
            recommendations.append({
                "agent": agent,
                "role": base_role,
                "add_to_affinity": list(new_skills),
                "reason": "High success rate on these skills",
            })

    return json.dumps({"recommendations": recommendations})


def _decompose_complex_task(payload, config):
    """Break a complex task into subtasks assigned to best-fit agents."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    from skills._models import call_complex

    task_description = payload.get("description", "")
    if not task_description:
        return json.dumps({"error": "description required"})

    # Ask LLM to decompose
    system = """You are a project manager decomposing a complex task into subtasks.
Available skills: web_search, summarize, code_write, code_review, security_audit,
lead_research, analyze_results, discuss, flashcard, rag_query.
Available agents: researcher, coder, coder_1, coder_2, coder_3, archivist, analyst,
sales, security, planner, implementation, legal, account_manager.
Output JSON: {"subtasks": [{"skill": "...", "agent": "...", "description": "...", "depends_on": []}]}"""

    try:
        result = call_complex(system, task_description, config, max_tokens=1024,
                             skill_name="swarm_intelligence")

        # Parse and dispatch
        try:
            parsed = json.loads(result)
            subtasks = parsed.get("subtasks", [])
        except json.JSONDecodeError:
            return json.dumps({"status": "parse_error", "raw": result[:500]})

        # Dispatch as task chain
        task_ids = []
        name_to_id = {}
        for st in subtasks:
            deps = [name_to_id[d] for d in st.get("depends_on", []) if d in name_to_id]
            tid = db.post_task(
                st.get("skill", "summarize"),
                json.dumps({"description": st.get("description", "")}),
                priority=5,
                assigned_to=st.get("agent"),
                depends_on=deps if deps else None,
            )
            name_to_id[st.get("description", f"task_{len(task_ids)}")] = tid
            task_ids.append({"skill": st["skill"], "agent": st.get("agent"), "task_id": tid})

        return json.dumps({
            "status": "decomposed",
            "subtasks": len(task_ids),
            "tasks": task_ids,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def _agent_fitness_report():
    """Comprehensive fitness report for each agent."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    try:
        with db.get_conn() as conn:
            agents = conn.execute("""
                SELECT name, role, status, last_heartbeat FROM agents ORDER BY name
            """).fetchall()

            report = []
            for a in agents:
                name = a["name"]
                # Task stats
                stats = conn.execute("""
                    SELECT status, COUNT(*) as n FROM tasks
                    WHERE assigned_to=? GROUP BY status
                """, (name,)).fetchall()

                task_summary = {r["status"]: r["n"] for r in stats}
                done = task_summary.get("DONE", 0)
                failed = task_summary.get("FAILED", 0)
                total = done + failed

                report.append({
                    "name": name,
                    "role": a["role"],
                    "status": a["status"],
                    "tasks_done": done,
                    "tasks_failed": failed,
                    "success_rate": round(done / total * 100, 1) if total > 0 else 0,
                    "specializations": _load_specializations().get(name, []),
                })

            report.sort(key=lambda x: (-x["tasks_done"], -x["success_rate"]))
            return json.dumps({"agents": report})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _swarm_status():
    """Overall swarm intelligence status."""
    specs = _load_specializations()
    return json.dumps({
        "agents_with_specializations": len(specs),
        "total_specializations": sum(len(v) for v in specs.values()),
        "specialization_file": str(SPECIALIZATION_FILE),
    })


def _save_specializations(data):
    try:
        SPECIALIZATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPECIALIZATION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_specializations():
    try:
        if SPECIALIZATION_FILE.exists():
            return json.loads(SPECIALIZATION_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}
