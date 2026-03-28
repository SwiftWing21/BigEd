"""Read Factorio WorldModel via bridge API, return markdown observation."""
SKILL_NAME = "factorio_observe"
DESCRIPTION = "Fetch current Factorio game state from the bridge and return a markdown summary"
VERSION = "0.1.0"
REQUIRES_NETWORK = False
COMPLEXITY = "simple"
TAGS = ["factorio", "sandbox"]


def run(payload, config):
    import urllib.request
    import json
    import logging

    log = logging.getLogger("biged.skill.factorio_observe")
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    url = f"http://127.0.0.1:{bridge_port}/api/state"

    try:
        resp = urllib.request.urlopen(url, timeout=5)
        state = json.loads(resp.read())
    except Exception as e:
        log.warning(f"Bridge API unreachable: {e}")
        return {"error": f"Bridge API unreachable at {url}: {e}"}

    return {"status": "ok", "state": state, "tick": state.get("tick", 0)}
