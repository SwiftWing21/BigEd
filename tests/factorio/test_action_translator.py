"""Tests for action_translator.py — grid snapping, direction conversion, RCON command format."""
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "fleet"))

from factorio.action_translator import (
    translate_action,
    translate_batch,
    _direction_to_int,
    TranslatedAction,
)


class TestDirectionConversion:
    def test_string_directions(self):
        assert _direction_to_int("north") == 0
        assert _direction_to_int("east") == 4
        assert _direction_to_int("south") == 8
        assert _direction_to_int("west") == 12

    def test_int_passthrough(self):
        assert _direction_to_int(4) == 4
        assert _direction_to_int(0) == 0
        assert _direction_to_int(12) == 12

    def test_none_default(self):
        assert _direction_to_int(None) == 0

    def test_unknown_string_default(self):
        assert _direction_to_int("diagonal") == 0

    def test_case_insensitive(self):
        assert _direction_to_int("North") == 0
        assert _direction_to_int("EAST") == 4


class TestTranslateAction:
    def test_place_command(self):
        result = translate_action({
            "action": "place", "entity": "stone-furnace",
            "position": {"x": 10, "y": 20}, "direction": "north",
        })
        assert result.action_type == "place"
        assert result.rcon_command is not None
        assert result.rcon_command.startswith("/biged-cmd ")
        payload = json.loads(result.rcon_command.replace("/biged-cmd ", ""))
        assert payload["entity"] == "stone-furnace"
        assert payload["direction"] == 0

    def test_mine_command(self):
        result = translate_action({
            "action": "mine", "position": {"x": 5, "y": -3},
        })
        assert result.action_type == "mine"
        assert result.rcon_command is not None
        payload = json.loads(result.rcon_command)
        assert payload["position"]["x"] == 5
        assert payload["position"]["y"] == -3

    def test_wait_no_rcon(self):
        result = translate_action({"action": "wait", "ticks": 30})
        assert result.action_type == "wait"
        assert result.rcon_command is None

    def test_unknown_action(self):
        result = translate_action({"action": "fly_to_moon"})
        assert result.action_type == "unknown"
        assert result.rcon_command is None

    def test_craft_command(self):
        result = translate_action({
            "action": "craft", "recipe": "iron-gear-wheel", "count": 5,
        })
        assert result.action_type == "craft"
        assert result.rcon_command is not None
        assert "iron-gear-wheel" in result.rcon_command

    def test_insert_command(self):
        result = translate_action({
            "action": "insert", "item": "coal", "count": 10,
            "position": {"x": 3, "y": 4},
        })
        assert result.action_type == "insert"
        assert result.rcon_command is not None
        payload = json.loads(result.rcon_command.replace("/biged-cmd ", ""))
        assert payload["item"] == "coal"
        assert payload["count"] == 10

    def test_move_command(self):
        result = translate_action({
            "action": "move", "position": {"x": 15, "y": -7},
        })
        assert result.action_type == "move"
        assert result.rcon_command is not None

    def test_research_command(self):
        result = translate_action({
            "action": "research", "technology": "automation",
        })
        assert result.action_type == "research"
        assert "automation" in result.rcon_command

    def test_position_snap_to_half_grid(self):
        """Position values should snap to 0.5 grid."""
        result = translate_action({
            "action": "place", "entity": "inserter",
            "position": {"x": 3.3, "y": 4.7}, "direction": "north",
        })
        payload = json.loads(result.rcon_command.replace("/biged-cmd ", ""))
        # 3.3 -> round(3.3*2)/2 = round(6.6)/2 = 7/2 = 3.5
        assert payload["position"]["x"] == 3.5
        # 4.7 -> round(4.7*2)/2 = round(9.4)/2 = 9/2 = 4.5
        assert payload["position"]["y"] == 4.5


class TestTranslateBatch:
    def test_batch_mixed_actions(self):
        actions = [
            {"action": "place", "entity": "furnace", "position": {"x": 0, "y": 0}, "direction": "north"},
            {"action": "wait", "ticks": 10},
            {"action": "mine", "position": {"x": 5, "y": 5}},
        ]
        results = translate_batch(actions)
        assert len(results) == 3
        assert results[0].action_type == "place"
        assert results[1].action_type == "wait"
        assert results[2].action_type == "mine"

    def test_batch_empty(self):
        results = translate_batch([])
        assert results == []


class TestDescriptions:
    def test_place_description(self):
        result = translate_action({
            "action": "place", "entity": "stone-furnace",
            "position": {"x": 10, "y": 20}, "direction": "north",
        })
        assert "stone-furnace" in result.description

    def test_craft_description(self):
        result = translate_action({
            "action": "craft", "recipe": "iron-gear-wheel", "count": 5,
        })
        assert "iron-gear-wheel" in result.description
        assert "5" in result.description
