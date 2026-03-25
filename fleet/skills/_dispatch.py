# fleet/skills/_dispatch.py
"""Action routing helper for suite-style skills."""


def dispatch_action(payload: dict, config: dict, actions: dict,
                    default: str = None) -> dict:
    """Route to handler by payload['action'] key.

    Args:
        payload: Task payload (must contain 'action' key or default is used).
        config: Fleet configuration.
        actions: Dict mapping action names to handler functions.
                 Each handler signature: handler(payload, config) -> dict.
        default: Default action if payload has no 'action' key.
                 Falls back to first key in actions dict.

    Returns:
        Handler result dict, or error dict if action is unknown.
    """
    action = payload.get("action", default or next(iter(actions), None))
    if action is None:
        return {"status": "error", "error": "No action specified and no handlers registered"}
    handler = actions.get(action)
    if not handler:
        return {
            "status": "error",
            "error": f"Unknown action: {action}",
            "valid_actions": sorted(actions.keys()),
        }
    return handler(payload, config)
