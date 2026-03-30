"""Translate agent action dicts into RCON command strings."""
import json
import logging
from dataclasses import dataclass

log = logging.getLogger("biged.factorio.actions")

DIRECTION_MAP = {"north": 0, "east": 4, "south": 8, "west": 12}  # Factorio 2.0 (16-dir)
KNOWN_ACTIONS = {"place", "remove", "set_recipe", "craft", "research",
                 "move", "connect", "observe", "wait", "mine", "insert"}


def _direction_to_int(d) -> int:
    if d is None:
        return 0
    if isinstance(d, int):
        return d
    return DIRECTION_MAP.get(str(d).lower(), 0)


@dataclass
class TranslatedAction:
    action_type: str
    rcon_command: str | None
    description: str


def translate_action(action: dict) -> TranslatedAction:
    action_type = action.get("action", "unknown")

    if action_type not in KNOWN_ACTIONS:
        log.warning(f"Unknown action type: {action_type}")
        return TranslatedAction(action_type="unknown", rcon_command=None,
                                description=f"Unknown: {action_type}")

    if action_type == "wait":
        ticks = action.get("ticks", 60)
        return TranslatedAction(action_type="wait", rcon_command=None,
                                description=f"Wait {ticks} ticks")

    elif action_type == "mine":
        pos = action.get("position", {})
        x, y = pos.get("x", 0), pos.get("y", 0)
        cmd = json.dumps({"action": "mine", "position": {"x": x, "y": y}})
        return TranslatedAction(action_type="mine", rcon_command=cmd,
                                description=f"Mine at ({x}, {y})")

    payload = dict(action)
    if "direction" in payload:
        payload["direction"] = _direction_to_int(payload["direction"])

    for key in ("position", "from", "to"):
        if key in payload and isinstance(payload[key], dict):
            pos = payload[key]
            # Snap to 0.5 grid for proper alignment of 2x2 entities (drills, furnaces)
            pos["x"] = round(pos.get("x", 0) * 2) / 2
            pos["y"] = round(pos.get("y", 0) * 2) / 2

    cmd_json = json.dumps(payload, separators=(",", ":"))
    desc = _describe_action(action_type, action)

    return TranslatedAction(
        action_type=action_type,
        rcon_command=f"/biged-cmd {cmd_json}",
        description=desc,
    )


def translate_batch(actions: list[dict]) -> list[TranslatedAction]:
    return [translate_action(a) for a in actions]


def _describe_action(action_type: str, action: dict) -> str:
    if action_type == "place":
        ent = action.get("entity", "?")
        pos = action.get("position", {})
        return f"Place {ent} at ({pos.get('x', 0)}, {pos.get('y', 0)})"
    if action_type == "craft":
        return f"Craft {action.get('count', 1)}x {action.get('recipe', '?')}"
    if action_type == "research":
        return f"Research {action.get('technology', '?')}"
    if action_type == "move":
        pos = action.get("position", {})
        return f"Move to ({pos.get('x', 0)}, {pos.get('y', 0)})"
    if action_type == "remove":
        return f"Remove entity {action.get('unit_number', action.get('position', '?'))}"
    if action_type == "set_recipe":
        return f"Set recipe {action.get('recipe', '?')} on #{action.get('unit_number', '?')}"
    if action_type == "connect":
        return f"Connect {action.get('entity', 'belt')} from {action.get('from', '?')} to {action.get('to', '?')}"
    if action_type == "insert":
        pos = action.get("position", {})
        return f"Insert {action.get('count', 1)}x {action.get('item', '?')} at ({pos.get('x', 0)}, {pos.get('y', 0)})"
    return action_type
