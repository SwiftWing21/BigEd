"""Tests for the Factorio action space / entity & recipe registry."""
import pytest
from factorio.action_space import (
    ActionSpace, ENTITY_REGISTRY, RECIPE_REGISTRY,
    PHASE_ENTITIES, PHASE_RECIPES, ActionType,
)


def test_entity_registry_has_common_entities():
    assert "stone-furnace" in ENTITY_REGISTRY
    assert "transport-belt" in ENTITY_REGISTRY
    assert "inserter" in ENTITY_REGISTRY
    assert isinstance(ENTITY_REGISTRY["stone-furnace"], int)
    assert ENTITY_REGISTRY["stone-furnace"] > 0  # 0 = empty


def test_phase_entities_expand():
    assert len(PHASE_ENTITIES[1]) <= len(PHASE_ENTITIES[2])
    assert len(PHASE_ENTITIES[2]) <= len(PHASE_ENTITIES[3])
    assert "stone-furnace" in PHASE_ENTITIES[1]
    assert "assembling-machine-1" in PHASE_ENTITIES[2]


def test_action_type_enum():
    assert ActionType.PLACE.value == 0
    assert ActionType.CRAFT.value == 1
    assert ActionType.MINE.value == 7
    assert len(ActionType) == 8


def test_action_space_init():
    space = ActionSpace(phase=1)
    assert space.num_entity_types > 0
    assert space.num_recipe_types > 0


def test_encode_decode_place_action():
    space = ActionSpace(phase=1)
    action_dict = {"action": "place", "entity": "stone-furnace",
                   "position": {"x": 3, "y": -2}, "direction": "north"}
    encoded = space.encode_action(action_dict)
    decoded = space.decode_action(encoded)
    assert decoded["action"] == "place"
    assert decoded["entity"] == "stone-furnace"
    assert decoded["direction"] == "north"


def test_encode_decode_craft_action():
    space = ActionSpace(phase=1)
    action_dict = {"action": "craft", "recipe": "iron-gear-wheel", "count": 5}
    encoded = space.encode_action(action_dict)
    decoded = space.decode_action(encoded)
    assert decoded["action"] == "craft"
    assert decoded["recipe"] == "iron-gear-wheel"
    assert decoded["count"] == 5


def test_encode_decode_mine_action():
    space = ActionSpace(phase=1)
    action_dict = {"action": "mine", "position": {"x": 2, "y": -1}}
    encoded = space.encode_action(action_dict)
    decoded = space.decode_action(encoded)
    assert decoded["action"] == "mine"


def test_invalid_action_mask():
    space = ActionSpace(phase=1)
    inventory = {}
    mask = space.get_action_type_mask(inventory, phase=1)
    assert mask[ActionType.WAIT.value] == 1  # wait always valid
    assert mask[ActionType.MOVE.value] == 1  # move always valid
    assert len(mask) == 8


def test_phase_updates_available_entities():
    space1 = ActionSpace(phase=1)
    space2 = ActionSpace(phase=2)
    assert space2.num_entity_types >= space1.num_entity_types
