"""
Workload planner — surveys fleet state and queues a batch of tasks.
Uses call_complex to decide what work is most valuable next.

Payload:
  focus:     "research" | "leads" | "security" | "business" | "all"  (default "all")
  max_tasks: int 5–500  (default 20)
  dry_run:   bool  (default false — set true to see plan without queuing)

Returns:
  {"queued": int, "focus": str, "saved_to": str, "errors": list}
"""
import json
import re
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

SKILL_CATALOG = """\
web_search       {"query": "specific search query"}
summarize        {"url": "https://..."} | {"description": "topic"}
arxiv_fetch      {"query": "ML/AI paper topic"}
discuss          {"topic": "topic", "agent_name": "planner", "role_perspective": "strategic planner", "round": 1}
code_discuss     {"topic": "code topic", "agent_name": "coder_1", "role_perspective": "software architect", "round": 1}
code_write       {"instructions": "what to build", "create_files": ["main.py"], "project_dir": "optional path"}
code_write_review {"project_dir": "path to workspace", "perspective": "software architect", "agent_name": "coder_1"}
lead_research    {"industry": "healthcare|accounting|legal", "zip_code": "95076", "radius_miles": 25}
synthesize       {"doc_type": "business_pitch|agent_prep|strategic_report", "topic": "optional topic", "output_name": "filename_no_ext"}
flashcard        {"topic": "subject"}
"""


def _survey_db(recent_n=40):
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    with db.get_conn() as conn:
        done = conn.execute(
            "SELECT type, payload_json FROM tasks WHERE status='DONE' ORDER BY created_at DESC LIMIT ?",
            (recent_n,)
        ).fetchall()
        pending = conn.execute(
            "SELECT type, payload_json FROM tasks WHERE status='PENDING'"
        ).fetchall()
    return {
        "recent_done": [{"type": r["type"], "payload": r["payload_json"][:80]} for r in done],
        "pending":     [{"type": r["type"], "payload": r["payload_json"][:80]} for r in pending],
    }


def _survey_knowledge():
    def _names(path, pattern, limit):
        d = KNOWLEDGE_DIR / path
        return [f.name for f in sorted(d.glob(pattern), reverse=True)[:limit]] if d.exists() else []

    return {
        "summaries": _names("summaries", "*.md", 15),
        "reports":   _names("reports", "*.md", 5),
        "leads":     _names("leads", "*.json", 10),
        "plans":     _names("plans", "*.json", 3),
    }


def _parse_tasks(raw: str) -> list:
    """Extract and parse the first JSON array from the LLM response."""
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        raise ValueError("no JSON array in response")
    return json.loads(m.group(0))


def run(payload, config):
    from skills._models import call_complex

    focus     = payload.get("focus", "all")
    max_tasks = max(5, min(500, int(payload.get("max_tasks", 20))))
    dry_run   = bool(payload.get("dry_run", False))

    state     = _survey_db()
    knowledge = _survey_knowledge()

    system = """\
You are the workload planner for a local AI agent fleet.
Identify the most valuable tasks for the fleet to work on next.

The fleet supports a local AI consulting business (Watsonville CA 95076):
- Sells/implements local AI to healthcare, accounting/tax, and legal SMBs
- Tracks ML/AI research (autoresearch training experiments, arxiv papers)
- Generates leads and market intelligence for Santa Cruz County
- Maintains security posture and fleet health

Output ONLY a valid JSON array. Each element:
  {"type": "skill_name", "payload": {...}, "priority": <1-10>}

Rules:
- Do NOT duplicate tasks already in the pending list
- Be specific — concrete queries, real industries, exact topics
- Vary task types — don't queue the same type more than 30% of the batch
- Higher priority (8-10) for tasks with clear business value
- Lower priority (3-5) for background research
"""

    user = (
        f"Focus area: {focus}\n"
        f"Generate exactly {max_tasks} tasks.\n\n"
        f"AVAILABLE SKILLS:\n{SKILL_CATALOG}\n"
        f"RECENT DONE ({len(state['recent_done'])}):\n"
        f"{json.dumps(state['recent_done'][:20], indent=2)}\n\n"
        f"CURRENTLY PENDING ({len(state['pending'])}):\n"
        f"{json.dumps(state['pending'], indent=2)}\n\n"
        f"KNOWLEDGE FILES:\n{json.dumps(knowledge, indent=2)}\n\n"
        f"Output JSON array only:"
    )

    raw = call_complex(system, user, config, max_tokens=4096, cache_system=True)

    try:
        tasks = _parse_tasks(raw)
    except Exception as e:
        return {"error": f"parse failed: {e}", "raw": raw[:300]}

    if not isinstance(tasks, list) or not tasks:
        return {"error": "LLM returned empty or non-list", "raw": raw[:300]}

    tasks = tasks[:max_tasks]

    if dry_run:
        return {"dry_run": True, "planned": len(tasks), "tasks": tasks}

    # Queue tasks
    import db
    queued = 0
    errors = []
    for t in tasks:
        try:
            skill = str(t.get("type", "")).strip()
            if not skill:
                continue
            task_payload = t.get("payload", {})
            priority = max(1, min(10, int(t.get("priority", 5))))
            db.post_task(skill, json.dumps(task_payload), priority=priority)
            queued += 1
        except Exception as e:
            errors.append(str(e))

    # Save plan log
    plan_dir = KNOWLEDGE_DIR / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_file = plan_dir / f"workload_{ts}.json"
    plan_file.write_text(json.dumps({
        "timestamp": ts,
        "focus": focus,
        "max_tasks": max_tasks,
        "queued": queued,
        "tasks": tasks,
        "errors": errors,
    }, indent=2))

    return {
        "queued": queued,
        "focus": focus,
        "saved_to": str(plan_file),
        "errors": errors[:5] if errors else [],
    }
