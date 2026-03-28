# tests/test_world_model.py
"""Tests for WorldModel — state diffing + event detection."""
import pytest
from factorio.state_parser import GameState, GameMetrics, Entity


def _make_state(tick=100, entities=None, resources=None, research_name="", inventory=None):
    return GameState(
        tick=tick,
        entities=entities or [],
        resources=resources or [],
        research_name=research_name,
        inventory=inventory or {},
    )


def test_update_tracks_entity_count():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    state = _make_state(entities=[
        Entity(name="furnace", unit_number=1),
        Entity(name="belt", unit_number=2),
    ])
    wm.update(state)
    assert wm.entity_count == 2


def test_update_detects_entity_destroyed():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    s1 = _make_state(tick=100, entities=[Entity(name="wall", unit_number=1)])
    s2 = _make_state(tick=200, entities=[])
    wm.update(s1)
    events = wm.update(s2)
    event_types = [e.event_type for e in events]
    assert "entity_destroyed" in event_types


def test_update_detects_research_complete():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    s1 = _make_state(tick=100, research_name="automation")
    wm.update(s1)
    s2 = _make_state(tick=200, research_name="logistics")
    events = wm.update(s2)
    event_types = [e.event_type for e in events]
    assert "research_complete" in event_types


def test_update_detects_resource_depleted():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    s1 = _make_state(tick=100, resources=[{"name": "iron-ore", "total_amount": 1000, "patches": 5}])
    wm.update(s1)
    s2 = _make_state(tick=200, resources=[{"name": "iron-ore", "total_amount": 200, "patches": 5}])
    events = wm.update(s2)
    event_types = [e.event_type for e in events]
    assert "resource_depleted" in event_types


def test_no_events_on_first_update():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    state = _make_state(tick=100, entities=[Entity(name="belt", unit_number=1)])
    events = wm.update(state)
    assert len(events) == 0


def test_get_snapshot_returns_copy():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    wm.update(_make_state(tick=100, inventory={"iron-plate": 50}))
    snap = wm.get_snapshot()
    assert snap["tick"] == 100
    assert snap["inventory"]["iron-plate"] == 50
