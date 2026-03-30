"""Tests for SpatialMemory — entries, queries, features, state updates."""
import math
import pytest
from factorio.spatial_memory import SpatialMemory, ResourceEntry, EntityEntry


# ── Task 1: Core data structure ────────────────────────────────


def test_upsert_resource():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 5, 50000, tick=100)
    assert "iron-ore_10_5" in mem.resources
    entry = mem.resources["iron-ore_10_5"]
    assert entry.name == "iron-ore"
    assert entry.amount == 50000
    assert entry.last_seen_tick == 100


def test_upsert_resource_update():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 5, 50000, tick=100)
    mem._upsert_resource("iron-ore", 10, 5, 40000, tick=200)
    assert mem.resources["iron-ore_10_5"].amount == 40000
    assert mem.resources["iron-ore_10_5"].last_seen_tick == 200


def test_upsert_entity():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.5, 0.5, unit_number=42, tick=100)
    assert 42 in mem.entities
    assert mem.entities[42].name == "stone-furnace"


def test_remove_entity():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.5, 0.5, unit_number=42, tick=100)
    mem.remove_entity(42)
    assert 42 not in mem.entities


def test_resource_summary():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 5, 50000, tick=100)
    mem._upsert_resource("iron-ore", 12, 5, 30000, tick=100)
    mem._upsert_resource("coal", 5, 15, 20000, tick=100)
    summary = mem.resource_summary()
    assert summary["iron-ore"] == 2
    assert summary["coal"] == 1
    assert summary.get("copper-ore", 0) == 0


def test_entity_summary():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.5, 0.5, unit_number=42, tick=100)
    mem._upsert_entity("stone-furnace", 10.5, 0.5, unit_number=43, tick=100)
    mem._upsert_entity("burner-mining-drill", 3.0, 0.0, unit_number=44, tick=100)
    summary = mem.entity_summary()
    assert summary["stone-furnace"] == 2
    assert summary["burner-mining-drill"] == 1


# ── Task 2: Nearest queries + bearing/distance ────────────────


def test_nearest_resource_basic():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 0, 50000, tick=100)
    result = mem.nearest_resource(0.0, 0.0, "iron-ore")
    assert result is not None
    bearing, distance = result
    assert abs(distance - 10.0) < 0.01
    assert abs(bearing - 0.0) < 0.01  # due east = 0 radians


def test_nearest_resource_picks_closest():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 100, 0, 50000, tick=100)
    mem._upsert_resource("iron-ore", 5, 0, 30000, tick=100)
    _, distance = mem.nearest_resource(0.0, 0.0, "iron-ore")
    assert abs(distance - 5.0) < 0.01


def test_nearest_resource_none():
    mem = SpatialMemory()
    assert mem.nearest_resource(0.0, 0.0, "iron-ore") is None


def test_nearest_resource_same_position():
    mem = SpatialMemory()
    mem._upsert_resource("coal", 5, 5, 10000, tick=100)
    _, distance = mem.nearest_resource(5.0, 5.0, "coal")
    assert distance == 0.0


def test_nearest_resource_bearing_north():
    mem = SpatialMemory()
    mem._upsert_resource("coal", 0, -10, 10000, tick=100)  # north (negative y)
    bearing, _ = mem.nearest_resource(0.0, 0.0, "coal")
    # atan2(-10, 0) = -pi/2 -> normalized to 3*pi/2
    assert abs(bearing - 3 * math.pi / 2) < 0.01


def test_nearest_entity_by_name():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 8.0, 0.0, unit_number=42, tick=100)
    result = mem.nearest_entity_by_name(0.0, 0.0, "stone-furnace")
    assert result is not None
    _, distance = result
    assert abs(distance - 8.0) < 0.01


def test_nearest_entity_by_name_none():
    mem = SpatialMemory()
    assert mem.nearest_entity_by_name(0.0, 0.0, "stone-furnace") is None


# ── Task 3: get_features, update_from_state, update_from_survey ──


def test_get_features_length():
    mem = SpatialMemory()
    features = mem.get_features(0.0, 0.0)
    assert len(features) == 16


def test_get_features_empty_defaults():
    mem = SpatialMemory()
    features = mem.get_features(0.0, 0.0)
    # No resources -> all distances = 1.0, bearings = 0.0
    assert features[1] == 1.0   # iron distance
    assert features[3] == 1.0   # copper distance
    assert features[0] == 0.0   # iron bearing


def test_get_features_with_resources():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 100, 0, 50000, tick=1)
    features = mem.get_features(0.0, 0.0)
    # iron bearing = 0.0 (due east), distance = 100/200 = 0.5
    assert abs(features[0] - 0.0) < 0.01   # bearing normalized
    assert abs(features[1] - 0.5) < 0.01   # distance normalized


def test_get_features_clipping():
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 500, 0, 50000, tick=1)
    features = mem.get_features(0.0, 0.0)
    assert features[1] == 1.0  # 500 tiles > 200 cap -> clipped to 1.0


def test_clear_entities_in_radius():
    mem = SpatialMemory()
    mem._upsert_entity("stone-furnace", 5.0, 5.0, unit_number=1, tick=100)
    mem._upsert_entity("stone-furnace", 500.0, 500.0, unit_number=2, tick=100)
    mem.clear_entities_in_radius((0.0, 0.0), 200.0)
    assert 1 not in mem.entities   # within radius, cleared
    assert 2 in mem.entities       # outside radius, kept


def test_update_from_survey():
    mem = SpatialMemory()
    survey = [
        {"name": "iron-ore", "x": 10, "y": 5, "amount": 99000},
        {"name": "coal", "x": -20, "y": 3, "amount": 50000},
    ]
    mem.update_from_survey(survey)
    assert "iron-ore_10_5" in mem.resources
    assert "coal_-20_3" in mem.resources
    assert mem.resources["iron-ore_10_5"].amount == 99000


def test_update_from_state_resources():
    """update_from_state upserts resources from state.resource_positions."""

    class FakeState:
        resource_positions = [
            {"name": "iron-ore", "x": 10, "y": 5, "amount": 50000},
            {"name": "coal", "x": -3, "y": 7, "amount": 20000},
        ]
        entities = []
        tick = 500

    mem = SpatialMemory()
    mem.update_from_state(FakeState(), current_tick=500)
    assert "iron-ore_10_5" in mem.resources
    assert "coal_-3_7" in mem.resources


def test_update_from_state_entities():
    """update_from_state upserts entities and detects removals."""

    class State1:
        resource_positions = []
        entities = [
            {"unit_number": 10, "name": "stone-furnace", "position": {"x": 5.0, "y": 5.0}},
            {"unit_number": 11, "name": "burner-mining-drill", "position": {"x": 3.0, "y": 0.0}},
        ]
        tick = 100

    class State2:
        resource_positions = []
        entities = [
            {"unit_number": 10, "name": "stone-furnace", "position": {"x": 5.0, "y": 5.0}},
            # entity 11 gone — was destroyed
        ]
        tick = 200

    mem = SpatialMemory()
    mem.update_from_state(State1(), current_tick=100)
    assert 10 in mem.entities
    assert 11 in mem.entities

    mem.update_from_state(State2(), current_tick=200)
    assert 10 in mem.entities
    assert 11 not in mem.entities  # removed because it disappeared
