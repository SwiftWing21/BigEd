"""Strategic planning for Factorio — state + context -> action plan."""
SKILL_NAME = "factorio_plan"
DESCRIPTION = "Analyze Factorio game state and produce a strategic plan with concrete next actions"
VERSION = "0.1.0"
REQUIRES_NETWORK = True
COMPLEXITY = "complex"
TAGS = ["factorio", "sandbox", "planning"]


def run(payload, config):
    """Plan next actions based on current state snapshot.

    payload keys:
        state: dict — WorldModel snapshot (from factorio_observe or task payload)
        task: str — current objective/instruction
        history: list — recent action results (optional)
    """
    state = payload.get("state", {})
    task = payload.get("task", "Build a factory")
    history = payload.get("history", [])

    if not state:
        return {"error": "No game state provided in payload"}

    return {
        "status": "ok",
        "plan_context": {
            "state": state,
            "task": task,
            "history": history[-5:],
        },
    }
