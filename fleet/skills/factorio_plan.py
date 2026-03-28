"""Strategic planning for Factorio — state + context -> action plan."""
SKILL_NAME = "factorio_plan"
DESCRIPTION = "Analyze Factorio game state and produce a strategic plan with concrete next actions"
VERSION = "0.2.0"
REQUIRES_NETWORK = True
COMPLEXITY = "complex"
TAGS = ["factorio", "sandbox", "planning"]


def run(payload, config):
    """Generate strategic plan from game state and submit to bridge."""
    import json
    import urllib.request
    import logging

    log = logging.getLogger(SKILL_NAME)
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    base = f"http://localhost:{bridge_port}"

    # Get state from payload or fetch from bridge
    state = payload.get("state")
    if not state:
        try:
            resp = urllib.request.urlopen(f"{base}/api/state", timeout=15)
            state = json.loads(resp.read())
        except Exception:
            return {"status": "error", "error": "No state available"}

    task = payload.get("task", "Build a factory")

    # Submit the task objective as a directive so the brain knows what to prioritize
    directive_data = {
        "text": f"Strategic objective: {task}",
        "priority": 75,
        "source": payload.get("worker_name", "planner"),
        "sticky": False,
        "max_plans": 5,
    }
    try:
        req = urllib.request.Request(
            f"{base}/api/directive/submit",
            data=json.dumps(directive_data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        submit_result = json.loads(resp.read())
    except Exception:
        log.warning("Failed to submit directive", exc_info=True)
        submit_result = {"status": "error"}

    return {"status": "ok", "submit_result": submit_result, "state_summary": str(state)[:500]}
