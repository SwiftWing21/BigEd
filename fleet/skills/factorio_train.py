"""Curriculum evaluation — check success criteria against game state."""
SKILL_NAME = "factorio_train"
DESCRIPTION = "Evaluate Factorio curriculum lesson criteria against current game state"
VERSION = "0.1.0"
REQUIRES_NETWORK = True
COMPLEXITY = "medium"
TAGS = ["factorio", "sandbox", "training"]


def run(payload, config):
    import logging
    log = logging.getLogger("biged.skill.factorio_train")

    mode = config.get("factorio", {}).get("mode", "ml")
    if mode == "ml":
        # In ML mode, report training progress from the bridge
        import urllib.request
        import json
        bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
        try:
            resp = urllib.request.urlopen(
                f"http://localhost:{bridge_port}/api/training/status", timeout=10
            )
            training = json.loads(resp.read())
            return {"status": "ok", "mode": "ml", "training": training}
        except Exception as e:
            log.warning("Training status fetch failed: %s", e)
            return {"status": "error", "error": f"Training status unavailable: {e}"}

    state = payload.get("state", {})
    criteria = payload.get("success_criteria", "")
    instruction = payload.get("instruction", "")
    name = payload.get("name", "unknown")

    if not state or not criteria:
        return {"error": "Missing state or success_criteria in payload"}

    try:
        from factorio.curriculum import evaluate_criteria
        passed = evaluate_criteria(criteria, state)
    except Exception as e:
        log.warning(f"Criteria evaluation failed: {e}")
        return {"error": f"Criteria evaluation failed: {e}"}

    # Feed result into IQ scoring system
    try:
        import reinforcement
        score = 1.0 if passed else -0.5
        reinforcement.record_outcome(
            skill="factorio_train", label=name, score=score
        )
    except Exception:
        log.warning("Could not record IQ outcome", exc_info=True)

    return {
        "status": "ok",
        "lesson": name,
        "instruction": instruction,
        "criteria": criteria,
        "passed": passed,
    }
