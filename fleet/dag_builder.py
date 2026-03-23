"""Autonomous DAG builder — parse natural language into task dependency graphs.

Converts free-form task descriptions into structured DAGs with skill routing
and dependency inference, then submits them via db.post_task() with proper
depends_on links for the fleet to execute in order.

v0.200.00b
"""

import json
import logging
import re

_log = logging.getLogger("dag_builder")

# ── Skill keyword mapping ────────────────────────────────────────────────────
# Maps natural-language keywords/phrases to fleet skill names.
# Order matters: longer/more-specific phrases checked first.
_SKILL_KEYWORDS = [
    # Security
    (r"\bsecurity\s+audit\b", "security_audit"),
    (r"\bpen\s*test\b", "pen_test"),
    (r"\bsecurity\s+review\b", "security_review"),
    (r"\bsecret\s+rotat", "secret_rotate"),
    # Code
    (r"\bcode\s+review\b", "code_review"),
    (r"\bcode\s+write\b", "code_write"),
    (r"\bcode\s+refactor\b", "code_refactor"),
    (r"\bcode\s+quality\b", "code_quality"),
    (r"\bcode\s+index\b", "code_index"),
    # Research
    (r"\bweb\s+search\b", "web_search"),
    (r"\bresearch\b", "web_search"),
    (r"\barxiv\b", "arxiv_fetch"),
    (r"\blead\s+research\b", "lead_research"),
    # Knowledge
    (r"\bingest\b", "ingest"),
    (r"\brag\s+index\b", "rag_index"),
    (r"\brag\s+query\b", "rag_query"),
    (r"\bflashcard\b", "flashcard"),
    # Analysis
    (r"\banalyze\b|\banalysis\b", "analyze_results"),
    (r"\bbenchmark\b", "benchmark"),
    (r"\bevaluat", "evaluate"),
    (r"\bstability\s+report\b", "stability_report"),
    # Synthesis / summary
    (r"\bsummariz", "summarize"),
    (r"\bsynthesize\b|\bsynthesis\b", "synthesize"),
    (r"\bdiscuss\b", "discuss"),
    # Planning
    (r"\bplan\b|\bworkload\b", "plan_workload"),
    # Git/GitHub
    (r"\bgit\b|\bbranch\b", "git_manager"),
    (r"\bgithub\b", "github_interact"),
]

# ── Dependency signal keywords ───────────────────────────────────────────────
# These words/phrases signal that the current step depends on the previous one.
_DEP_SIGNALS = re.compile(
    r"\bthen\b|\bafter\s+that\b|\bonce\s+.+?\bdone\b|\bnext\b"
    r"|\bfinally\b|\bfollowed\s+by\b|\bwhen\s+.+?\bcomplete",
    re.IGNORECASE,
)

# Parallel signals — steps that can run concurrently.
_PARALLEL_SIGNALS = re.compile(
    r"\bat\s+the\s+same\s+time\b|\bin\s+parallel\b"
    r"|\bsimultaneously\b|\bconcurrently\b|\band\s+also\b",
    re.IGNORECASE,
)


def _identify_skill(text: str) -> str:
    """Match a text fragment to the best-fit skill name."""
    lower = text.lower()
    for pattern, skill in _SKILL_KEYWORDS:
        if re.search(pattern, lower):
            return skill
    return "summarize"  # safe default


def _split_steps(text: str) -> list[str]:
    """Split a natural-language description into ordered steps.

    Handles numbered lists, bullet points, semicolons, and dependency
    signal words as step boundaries.
    """
    # Numbered list: "1. do X  2. do Y"
    numbered = re.split(r'\n\s*\d+[\.\)]\s*', text)
    if len(numbered) > 1:
        return [s.strip() for s in numbered if s.strip()]

    # Bullet points
    bullets = re.split(r'\n\s*[-*]\s+', text)
    if len(bullets) > 1:
        return [s.strip() for s in bullets if s.strip()]

    # Semicolons
    semis = text.split(";")
    if len(semis) > 1:
        return [s.strip() for s in semis if s.strip()]

    # Dependency signal words as delimiters
    parts = _DEP_SIGNALS.split(text)
    if len(parts) > 1:
        return [s.strip().strip(",").strip() for s in parts if s.strip()]

    # Commas with "and"
    parts = re.split(r',\s*(?:and\s+)?', text)
    if len(parts) > 1:
        return [s.strip() for s in parts if s.strip()]

    return [text.strip()]


def _infer_payload(skill: str, step_text: str) -> dict:
    """Build a reasonable payload dict for a skill from the step description."""
    # Try to extract quoted file paths or scope references
    file_match = re.search(r'["\']([^"\']+\.\w+)["\']', step_text)
    path_match = re.search(r'\b(fleet/\S+|BigEd/\S+|skills/\S+)', step_text)

    target = None
    if file_match:
        target = file_match.group(1)
    elif path_match:
        target = path_match.group(1)

    payload_map = {
        "code_review":     {"file": target or ".", "description": step_text},
        "security_audit":  {"scope": target or "fleet/", "description": step_text},
        "security_review": {"scope": target or "fleet/", "description": step_text},
        "pen_test":        {"target": target or "localhost", "scan_type": "quick"},
        "web_search":      {"query": step_text},
        "arxiv_fetch":     {"query": step_text},
        "summarize":       {"description": step_text},
        "synthesize":      {"doc_type": "report", "topic": step_text},
        "analyze_results": {"description": step_text},
        "benchmark":       {"description": step_text},
        "evaluate":        {"description": step_text},
        "discuss":         {"topic": step_text},
        "plan_workload":   {"description": step_text},
        "code_write":      {"description": step_text, "file": target or ""},
        "code_refactor":   {"file": target or "", "description": step_text},
        "code_quality":    {"scope": target or "fleet/", "description": step_text},
        "code_index":      {"scope": target or "fleet/"},
        "ingest":          {"path": target or "", "description": step_text},
        "rag_index":       {"content": step_text},
        "rag_query":       {"query": step_text},
        "flashcard":       {"topic": step_text},
        "git_manager":     {"action": "status", "description": step_text},
        "github_interact": {"action": "list_issues", "description": step_text},
        "lead_research":   {"industry": step_text, "zip_code": ""},
        "stability_report": {"description": step_text},
    }
    return payload_map.get(skill, {"description": step_text})


def _has_dep_signal_before(step_text: str, full_text: str) -> bool:
    """Check if there is a dependency signal word immediately before this step."""
    idx = full_text.find(step_text)
    if idx <= 0:
        return False
    prefix = full_text[:idx]
    # Check last 30 chars for a dep signal
    tail = prefix[-40:]
    return bool(_DEP_SIGNALS.search(tail))


def build_dag_from_description(text: str) -> list[dict]:
    """Parse a natural-language description into a task DAG.

    Returns a list of task dicts:
        [{"skill": str, "payload": dict, "depends_on": list[int]}]

    Index-based depends_on: [0] means "depends on task at index 0 in this list".
    Sequential by default unless parallel signals are detected.

    Raises ValueError if text is empty or would produce an empty DAG.
    """
    from config import load_config
    cfg = load_config()
    dag_cfg = cfg.get("dag", {})

    if not dag_cfg.get("enabled", True):
        raise RuntimeError("DAG builder is disabled in fleet.toml [dag] enabled = false")

    max_depth = dag_cfg.get("max_dag_depth", 10)
    max_tasks = dag_cfg.get("max_dag_tasks", 50)

    text = text.strip()
    if not text:
        raise ValueError("Empty description — cannot build DAG")

    steps = _split_steps(text)
    if not steps:
        raise ValueError("Could not parse any steps from description")

    if len(steps) > max_tasks:
        _log.warning("DAG truncated from %d to %d steps (max_dag_tasks)", len(steps), max_tasks)
        steps = steps[:max_tasks]

    tasks = []
    for i, step in enumerate(steps):
        skill = _identify_skill(step)
        payload = _infer_payload(skill, step)

        # Dependency inference
        depends_on = []
        is_parallel = bool(_PARALLEL_SIGNALS.search(step))

        if i > 0 and not is_parallel:
            # Default: sequential dependency on the previous step
            # Check depth limit
            chain_depth = 1
            dep_idx = i - 1
            while dep_idx >= 0 and tasks[dep_idx].get("depends_on"):
                deps = tasks[dep_idx]["depends_on"]
                if deps:
                    dep_idx = deps[0]
                    chain_depth += 1
                else:
                    break
            if chain_depth < max_depth:
                depends_on = [i - 1]
            else:
                _log.warning("DAG depth limit (%d) reached at step %d", max_depth, i)

        tasks.append({
            "skill": skill,
            "payload": payload,
            "depends_on": depends_on,
        })

    if not tasks:
        raise ValueError("No tasks produced from description")

    _log.info("Built DAG with %d tasks from NL description", len(tasks))
    return tasks


def submit_dag(tasks: list[dict], priority: int = 5) -> list[int]:
    """Submit a DAG of tasks to the fleet via db.post_task().

    Args:
        tasks: list from build_dag_from_description() with index-based depends_on
        priority: shared priority for all tasks (1-10)

    Returns:
        list of task IDs in submission order
    """
    import db
    import uuid

    trace_id = str(uuid.uuid4())[:8]
    task_ids = []
    root_id = None

    for i, t in enumerate(tasks):
        # Resolve index-based depends_on to actual task IDs
        real_deps = None
        if t.get("depends_on"):
            real_deps = []
            for dep_idx in t["depends_on"]:
                if 0 <= dep_idx < len(task_ids):
                    real_deps.append(task_ids[dep_idx])
            if not real_deps:
                real_deps = None

        payload_json = json.dumps(t["payload"])
        tid = db.post_task(
            type_=t["skill"],
            payload_json=payload_json,
            priority=priority,
            parent_id=root_id,
            depends_on=real_deps,
            trace_id=trace_id,
        )
        task_ids.append(tid)
        if root_id is None:
            root_id = tid

    _log.info("Submitted DAG: %d tasks, root=%d, trace=%s", len(task_ids), root_id, trace_id)
    return task_ids


def get_dag_status(root_task_id: int) -> dict:
    """Get execution status tree for a DAG rooted at root_task_id.

    Returns:
        {
            "root_id": int,
            "total": int,
            "completed": int,
            "pending": int,
            "waiting": int,
            "failed": int,
            "running": int,
            "tasks": [{"id", "skill", "status", "depends_on", "has_result"}, ...]
        }
    """
    import db

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, type, status, depends_on, result_json, error "
            "FROM tasks WHERE parent_id = ? OR id = ? ORDER BY id",
            (root_task_id, root_task_id),
        ).fetchall()

    tasks = []
    counts = {"completed": 0, "pending": 0, "waiting": 0, "failed": 0, "running": 0}

    for r in rows:
        status = r["status"].upper() if r["status"] else "UNKNOWN"
        if status in ("DONE", "COMPLETED"):
            counts["completed"] += 1
        elif status == "PENDING":
            counts["pending"] += 1
        elif status == "WAITING":
            counts["waiting"] += 1
        elif status in ("FAILED", "ERROR"):
            counts["failed"] += 1
        elif status in ("RUNNING", "ASSIGNED"):
            counts["running"] += 1

        deps = json.loads(r["depends_on"]) if r["depends_on"] else []
        tasks.append({
            "id": r["id"],
            "skill": r["type"],
            "status": status,
            "depends_on": deps,
            "has_result": bool(r["result_json"]),
            "error": r["error"] or None,
        })

    return {
        "root_id": root_task_id,
        "total": len(tasks),
        **counts,
        "tasks": tasks,
    }


def visualize_dag(root_task_id: int) -> dict:
    """Return nodes + edges suitable for dashboard DAG rendering.

    Returns:
        {
            "root_id": int,
            "nodes": [{"id", "label", "skill", "status", "level"}, ...],
            "edges": [{"from", "to"}, ...]
        }
    """
    import db

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, type, status, depends_on, result_json "
            "FROM tasks WHERE parent_id = ? OR id = ? ORDER BY id",
            (root_task_id, root_task_id),
        ).fetchall()

    nodes = []
    edges = []
    id_set = {r["id"] for r in rows}

    # Compute levels via topological ordering
    levels = {}
    for r in rows:
        deps = json.loads(r["depends_on"]) if r["depends_on"] else []
        dep_levels = [levels.get(d, 0) for d in deps if d in id_set]
        levels[r["id"]] = (max(dep_levels) + 1) if dep_levels else 0

    for r in rows:
        deps = json.loads(r["depends_on"]) if r["depends_on"] else []
        nodes.append({
            "id": r["id"],
            "label": f"{r['type']} (#{r['id']})",
            "skill": r["type"],
            "status": r["status"],
            "has_result": bool(r["result_json"]),
            "level": levels.get(r["id"], 0),
        })
        for dep_id in deps:
            if dep_id in id_set:
                edges.append({"from": dep_id, "to": r["id"]})

    return {"root_id": root_task_id, "nodes": nodes, "edges": edges}
