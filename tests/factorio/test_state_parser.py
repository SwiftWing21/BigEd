"""Tests for state_parser.py — JSON -> GameState/GameMetrics parsing."""
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "fleet"))

from factorio.state_parser import parse_state, parse_metrics, state_to_markdown, GameState, GameMetrics, Entity


FULL_STATE_JSON = json.dumps({
    "tick": 12345,
    "time_of_day": 0.5,
    "player": {
        "position": {"x": 10.5, "y": -3.0},
        "health": 200,
        "max_health": 250,
        "has_character": True,
        "alive": True,
    },
    "inventory": {"iron-ore": 50, "coal": 10},
    "entities": [
        {
            "name": "stone-furnace", "type": "furnace",
            "position": {"x": 5, "y": 5}, "direction": 0,
            "health": 200, "unit_number": 1,
            "recipe": "iron-plate", "crafting_progress": 0.5,
            "is_crafting": True, "input": {"iron-ore": 2},
            "output": {"iron-plate": 1}, "belt_contents": {},
            "held_item": None, "mining_target": "",
            "energy": 100.0, "status": "working",
        }
    ],
    "entity_count": 1,
    "resources": [{"name": "iron-ore", "total_amount": 50000, "patches": 3}],
    "resource_positions": [{"name": "iron-ore", "x": 20, "y": 20, "amount": 1000}],
    "terrain": [{"name": "grass-1", "position": {"x": 0, "y": 0}}],
    "global_resources": {"iron-ore": 100000, "copper-ore": 80000},
    "research": {"name": "automation", "progress": 0.75},
    "map_explored_chunks": 42,
})


class TestParseState:
    def test_full_valid_json(self):
        state = parse_state(FULL_STATE_JSON)
        assert state.tick == 12345
        assert state.time_of_day == 0.5
        assert state.player_position == {"x": 10.5, "y": -3.0}
        assert state.player_health == 200
        assert state.player_max_health == 250
        assert state.player_has_character is True
        assert state.player_alive is True
        assert state.inventory == {"iron-ore": 50, "coal": 10}
        assert len(state.entities) == 1
        assert state.entity_count == 1
        assert state.research_name == "automation"
        assert state.research_progress == 0.75
        assert state.map_explored_chunks == 42

    def test_minimal_json(self):
        state = parse_state("{}")
        assert state.tick == 0
        assert state.inventory == {}
        assert state.entities == []
        assert state.research_name == ""
        assert state.player_position == {}

    def test_empty_string(self):
        state = parse_state("")
        assert isinstance(state, GameState)
        assert state.tick == 0

    def test_none_input(self):
        state = parse_state(None)
        assert isinstance(state, GameState)
        assert state.tick == 0

    def test_entity_parsing(self):
        state = parse_state(FULL_STATE_JSON)
        e = state.entities[0]
        assert isinstance(e, Entity)
        assert e.name == "stone-furnace"
        assert e.type == "furnace"
        assert e.recipe == "iron-plate"
        assert e.is_crafting is True
        assert e.crafting_progress == 0.5
        assert e.unit_number == 1
        assert e.energy == 100.0

    def test_resource_positions(self):
        state = parse_state(FULL_STATE_JSON)
        assert len(state.resource_positions) == 1
        assert state.resource_positions[0]["name"] == "iron-ore"

    def test_global_resources(self):
        state = parse_state(FULL_STATE_JSON)
        assert state.global_resources["iron-ore"] == 100000


class TestParseMetrics:
    def test_valid_json(self):
        raw = json.dumps({
            "tick": 5000,
            "total_produced": {"iron-plate": 100},
            "total_consumed": {"iron-ore": 200},
            "flow_per_minute": {"iron-plate": 30},
            "electric": {
                "satisfaction": "full",
                "capacity_mw": 5.0,
                "entity_count": 3,
            },
            "research": {
                "completed": ["automation"],
                "current": "logistics",
                "progress": 0.3,
            },
        })
        metrics = parse_metrics(raw)
        assert metrics.tick == 5000
        assert metrics.total_produced == {"iron-plate": 100}
        assert metrics.electric_satisfaction == "full"
        assert metrics.electric_capacity_mw == 5.0
        assert metrics.completed_research == ["automation"]
        assert metrics.current_research == "logistics"

    def test_missing_fields(self):
        metrics = parse_metrics("{}")
        assert metrics.tick == 0
        assert metrics.total_produced == {}
        assert metrics.electric_satisfaction == ""
        assert metrics.completed_research == []

    def test_invalid_json(self):
        metrics = parse_metrics("not json")
        assert isinstance(metrics, GameMetrics)
        assert metrics.tick == 0


class TestStateToMarkdown:
    def test_returns_string(self):
        state = parse_state(FULL_STATE_JSON)
        md = state_to_markdown(state)
        assert isinstance(md, str)
        assert "Factory State" in md
        assert "tick 12345" in md

    def test_includes_inventory(self):
        state = parse_state(FULL_STATE_JSON)
        md = state_to_markdown(state)
        assert "iron-ore" in md

    def test_includes_research(self):
        state = parse_state(FULL_STATE_JSON)
        md = state_to_markdown(state)
        assert "automation" in md
        assert "75%" in md

    def test_empty_state(self):
        state = parse_state("{}")
        md = state_to_markdown(state)
        assert "Factory State" in md
        assert "(empty)" in md

    def test_with_metrics(self):
        state = parse_state(FULL_STATE_JSON)
        metrics = parse_metrics(json.dumps({
            "flow_per_minute": {"iron-plate": 30},
        }))
        md = state_to_markdown(state, metrics)
        assert "Production Flow" in md
