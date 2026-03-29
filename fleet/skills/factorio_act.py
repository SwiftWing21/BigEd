"""Submit actions to the Factorio bridge via plan/submit API."""
SKILL_NAME = "factorio_act"
DESCRIPTION = "Translate a plan into Factorio actions and submit to the bridge for execution"
VERSION = "0.2.0"
REQUIRES_NETWORK = True
COMPLEXITY = "medium"
TAGS = ["factorio", "sandbox"]


def run(payload, config):
    import urllib.request
    import json
    import logging

    log = logging.getLogger("biged.skill.factorio_act")
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    actions = payload.get("actions", [])

    if not actions:
        return {"error": "No actions provided in payload"}

    mode = config.get("factorio", {}).get("mode", "ml")
    if mode == "ml":
        log.info("ML mode active — submitting plan as human override (priority elevated)")
        plan_data_priority = max(payload.get("priority", 75), 90)  # Elevate to override ML
    else:
        plan_data_priority = payload.get("priority", 75)

    plan_data = {
        "actions": actions,
        "priority": plan_data_priority,
        "source": payload.get("worker_name", "actor"),
        "source_type": "worker",
        "rationale": payload.get("rationale", "Direct action"),
        "confidence": payload.get("confidence", 0.9),
    }
    try:
        req = urllib.request.Request(
            f"http://localhost:{bridge_port}/api/plan/submit",
            data=json.dumps(plan_data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return {"status": "ok", **result}
    except Exception as e:
        log.warning(f"Failed to submit plan: {e}")
        return {"status": "error", "error": str(e)}
