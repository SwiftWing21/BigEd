"""Tests for Factorio state parser — JSON → GameState dataclass."""
import json
import pytest

SAMPLE_STATE = {
    "tick": 5400,
    "time_of_day": 0.5,
    "player": {"position": {"x": 10.0, "y": -5.0}, "health": 250},
    "inventory": {"iron-plate": 50, "copper-plate": 30, "stone-furnace": 2},
    "entities": [
        {"name": "stone-furnace", "type": "furnace", "position": {"x": 5, "y": 0},
         "direction": 0, "health": 200, "unit_number": 1,
         "recipe": "iron-plate", "crafting_progress": 0.5, "is_crafting": True,
         "input": {"iron-ore": 10}, "output": {"iron-plate": 3}},
        {"name": "transport-belt", "type": "transport-belt",
         "position": {"x": 6, "y": 0}, "direction": 4, "health": 150,
         "unit_number": 2, "belt_contents": {"iron-plate": 2}},
    ],
    "entity_count": 2,
    "resources": [{"name": "iron-ore", "patches": 15, "total_amount": 48000}],
    "research": {"name": "automation", "progress": 0.35},
    "map_explored_chunks": 12,
}

SAMPLE_METRICS = {
    "tick": 5400,
    "total_produced": {"iron-plate": 200, "copper-plate": 50},
    "total_consumed": {"iron-plate": 80},
    "flow_per_minute": {"iron-plate": 12.5, "copper-plate": 3.0},
    "electric": {"capacity_mw": 2, "satisfaction": "ok", "entity_count": 3},
    "research": {"completed": ["automation"], "current": "logistics", "progress": 0.1},
}

def test_parse_state_basic():
    from factorio.state_parser import parse_state
    state = parse_state(json.dumps(SAMPLE_STATE))
    assert state.tick == 5400
    assert state.player_position == {"x": 10.0, "y": -5.0}
    assert state.inventory["iron-plate"] == 50
    assert len(state.entities) == 2
    assert state.entities[0].name == "stone-furnace"
    assert state.entities[0].recipe == "iron-plate"

def test_parse_state_resources():
    from factorio.state_parser import parse_state
    state = parse_state(json.dumps(SAMPLE_STATE))
    assert len(state.resources) == 1
    assert state.resources[0]["name"] == "iron-ore"
    assert state.resources[0]["total_amount"] == 48000

def test_parse_state_research():
    from factorio.state_parser import parse_state
    state = parse_state(json.dumps(SAMPLE_STATE))
    assert state.research_name == "automation"
    assert state.research_progress == 0.35

def test_parse_metrics():
    from factorio.state_parser import parse_metrics
    metrics = parse_metrics(json.dumps(SAMPLE_METRICS))
    assert metrics.flow_per_minute["iron-plate"] == 12.5
    assert "automation" in metrics.completed_research
    assert metrics.electric_satisfaction == "ok"

def test_parse_invalid_json_returns_empty():
    from factorio.state_parser import parse_state
    state = parse_state("not json at all")
    assert state.tick == 0
    assert len(state.entities) == 0

def test_state_to_markdown():
    from factorio.state_parser import parse_state, parse_metrics, state_to_markdown
    state = parse_state(json.dumps(SAMPLE_STATE))
    metrics = parse_metrics(json.dumps(SAMPLE_METRICS))
    md = state_to_markdown(state, metrics)
    assert "## Inventory" in md
    assert "iron-plate" in md
    assert "## Entities" in md
    assert "stone-furnace" in md

def test_state_to_markdown_no_metrics():
    from factorio.state_parser import parse_state, state_to_markdown
    state = parse_state(json.dumps(SAMPLE_STATE))
    md = state_to_markdown(state, None)
    assert "## Inventory" in md
    assert "Production Flow" not in md
