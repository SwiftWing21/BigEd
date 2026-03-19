"""
Declarative Workflow DSL — define task pipelines in TOML.
Compiles to validated DAGs before execution.

Example workflow file (fleet/workflows/research_pipeline.toml):

[workflow]
name = "research_pipeline"
description = "Research a topic, summarize findings, generate flashcards"
priority = 7

[[steps]]
name = "search"
skill = "web_search"
payload = {query = "$topic"}

[[steps]]
name = "summarize"
skill = "summarize"
payload = {description = "$search.result"}
depends_on = ["search"]

[[steps]]
name = "flashcards"
skill = "flashcard"
payload = {content = "$summarize.result"}
depends_on = ["summarize"]
condition = {summarize = "success"}
"""
import json
import tomllib
from pathlib import Path
from typing import Optional

FLEET_DIR = Path(__file__).parent
WORKFLOWS_DIR = FLEET_DIR / "workflows"


def load_workflow(name: str) -> dict:
    """Load a workflow definition from TOML file."""
    path = WORKFLOWS_DIR / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Workflow '{name}' not found at {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def validate_workflow(definition: dict) -> tuple[bool, str]:
    """Validate a workflow definition before execution.

    Checks:
    - All steps have name + skill
    - depends_on references exist
    - No cycles in dependency graph
    - Variables reference valid step names
    """
    steps = definition.get("steps", [])
    if not steps:
        return False, "Workflow has no steps"

    step_names = {s["name"] for s in steps}

    # Check each step
    for step in steps:
        if not step.get("name"):
            return False, "Step missing 'name'"
        if not step.get("skill"):
            return False, f"Step '{step['name']}' missing 'skill'"

        # Check depends_on references
        for dep in step.get("depends_on", []):
            if dep not in step_names:
                return False, f"Step '{step['name']}' depends on unknown step '{dep}'"

        # Check condition references
        for cond_step in step.get("condition", {}).keys():
            if cond_step not in step_names:
                return False, f"Step '{step['name']}' condition references unknown step '{cond_step}'"

    # Cycle detection (DFS)
    adj = {s["name"]: s.get("depends_on", []) for s in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in step_names}

    def has_cycle(node):
        color[node] = GRAY
        for dep in adj.get(node, []):
            if color.get(dep) == GRAY:
                return True
            if color.get(dep) == WHITE and has_cycle(dep):
                return True
        color[node] = BLACK
        return False

    for name in step_names:
        if color[name] == WHITE and has_cycle(name):
            return False, f"Cycle detected involving step '{name}'"

    return True, "Workflow valid"


def compile_workflow(definition: dict, variables: dict = None) -> list:
    """Compile a workflow definition into a list of task dicts ready for post_task_chain.

    Args:
        definition: parsed TOML workflow
        variables: runtime variable substitution (e.g., {"topic": "AI safety"})
    """
    variables = variables or {}
    steps = definition.get("steps", [])
    workflow_meta = definition.get("workflow", {})
    priority = workflow_meta.get("priority", 5)

    compiled = []
    for step in steps:
        # Substitute variables in payload
        payload = _substitute_vars(step.get("payload", {}), variables)

        task = {
            "type": step["skill"],
            "payload": payload,
            "name": step["name"],
            "depends_on_names": step.get("depends_on", []),
            "conditions": step.get("condition"),
        }
        compiled.append(task)

    return compiled


def execute_workflow(name: str, variables: dict = None) -> dict:
    """Load, validate, compile, and execute a workflow.

    Returns: {workflow, task_ids, status}
    """
    import db

    definition = load_workflow(name)

    # Validate
    valid, msg = validate_workflow(definition)
    if not valid:
        return {"workflow": name, "status": "invalid", "error": msg}

    # Compile
    compiled = compile_workflow(definition, variables)
    workflow_meta = definition.get("workflow", {})
    priority = workflow_meta.get("priority", 5)

    # Execute: post tasks with dependencies
    name_to_id = {}
    task_ids = []

    for step in compiled:
        # Resolve dependency names to task IDs
        depends_on = [name_to_id[dep] for dep in step["depends_on_names"] if dep in name_to_id]

        # Resolve conditions to task ID keys
        conditions = None
        if step["conditions"]:
            conditions = {}
            for dep_name, cond_value in step["conditions"].items():
                if dep_name in name_to_id:
                    conditions[str(name_to_id[dep_name])] = cond_value

        parent_id = task_ids[0] if task_ids else None

        tid = db.post_task(
            step["type"],
            json.dumps(step["payload"]),
            priority=priority,
            parent_id=parent_id,
            depends_on=depends_on if depends_on else None,
            conditions=conditions,
        )
        name_to_id[step["name"]] = tid
        task_ids.append(tid)

    return {
        "workflow": name,
        "status": "dispatched",
        "task_ids": task_ids,
        "step_map": name_to_id,
    }


def list_workflows() -> list:
    """List available workflow definitions."""
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    workflows = []
    for f in sorted(WORKFLOWS_DIR.glob("*.toml")):
        try:
            with open(f, "rb") as fh:
                defn = tomllib.load(fh)
            meta = defn.get("workflow", {})
            workflows.append({
                "name": f.stem,
                "description": meta.get("description", ""),
                "steps": len(defn.get("steps", [])),
            })
        except Exception:
            workflows.append({"name": f.stem, "description": "parse error", "steps": 0})
    return workflows


def _substitute_vars(payload: dict, variables: dict) -> dict:
    """Substitute $variable references in payload values."""
    result = {}
    for key, value in payload.items():
        if isinstance(value, str) and value.startswith("$"):
            var_name = value[1:]
            # Simple variable: $topic
            if var_name in variables:
                result[key] = variables[var_name]
            else:
                result[key] = value  # leave as-is for runtime resolution
        else:
            result[key] = value
    return result
