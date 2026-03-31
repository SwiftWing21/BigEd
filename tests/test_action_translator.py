"""Tests for action translation — agent dict → RCON command string."""
import json
import pytest


def test_translate_place():
    from factorio.action_translator import translate_action
    action = {"action": "place", "entity": "stone-furnace",
              "position": {"x": 5, "y": -3}, "direction": "south"}
    result = translate_action(action)
    assert result.rcon_command.startswith("/biged-cmd")
    payload = json.loads(result.rcon_command.split(" ", 1)[1])
    assert payload["action"] == "place"
    assert payload["entity"] == "stone-furnace"
    assert payload["direction"] == 8  # south = 8 (Factorio 2.0 16-dir)


def test_translate_craft():
    from factorio.action_translator import translate_action
    action = {"action": "craft", "recipe": "iron-gear-wheel", "count": 5}
    result = translate_action(action)
    payload = json.loads(result.rcon_command.split(" ", 1)[1])
    assert payload["recipe"] == "iron-gear-wheel"
    assert payload["count"] == 5


def test_translate_direction_names():
    from factorio.action_translator import _direction_to_int
    assert _direction_to_int("north") == 0
    assert _direction_to_int("east") == 4
    assert _direction_to_int("south") == 8
    assert _direction_to_int("west") == 12
    assert _direction_to_int(4) == 4
    assert _direction_to_int(None) == 0


def test_translate_wait():
    from factorio.action_translator import translate_action
    action = {"action": "wait", "ticks": 120}
    result = translate_action(action)
    assert result.action_type == "wait"
    assert result.rcon_command is None


def test_translate_batch():
    from factorio.action_translator import translate_batch
    actions = [
        {"action": "craft", "recipe": "iron-gear-wheel", "count": 2},
        {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
    ]
    results = translate_batch(actions)
    assert len(results) == 2
    assert results[0].action_type == "craft"
    assert results[1].action_type == "place"


def test_translate_unknown_action():
    from factorio.action_translator import translate_action
    action = {"action": "fly_to_moon"}
    result = translate_action(action)
    assert result.action_type == "unknown"
    assert result.rcon_command is None
