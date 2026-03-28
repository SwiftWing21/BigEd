"""Submit actions to the Factorio bridge CommandQueue."""
SKILL_NAME = "factorio_act"
DESCRIPTION = "Translate a plan into Factorio actions and submit to the bridge for execution"
VERSION = "0.1.0"
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

    url = f"http://127.0.0.1:{bridge_port}/api/command"
    body = json.dumps({"actions": actions}).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        return {"status": "ok", "queued": True, "command_id": result.get("command_id")}
    except Exception as e:
        log.warning(f"Failed to submit actions: {e}")
        return {"error": f"Bridge API error: {e}"}
