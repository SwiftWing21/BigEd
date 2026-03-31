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
    assert len(ActionType) == 12  # PLACE..INSERT(8) + PLACE_NEAR(9) + PACK(10) + STAMP(11)


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
    assert mask[ActionType.MOVE.value] == 0  # move disabled in Phase 1 early lessons
    assert len(mask) == len(ActionType)  # 12
    # Move enabled in Phase 2+
    mask2 = space.get_action_type_mask(inventory, phase=2)
    assert mask2[ActionType.MOVE.value] == 1


def test_phase_updates_available_entities():
    space1 = ActionSpace(phase=1)
    space2 = ActionSpace(phase=2)
    assert space2.num_entity_types >= space1.num_entity_types


# -------------------------------------------------------------------
# New tests — PACK/STAMP, expanded registries, phase gating 5-8
# -------------------------------------------------------------------

def test_action_type_has_pack_and_stamp():
    assert ActionType.PACK == 10
    assert ActionType.STAMP == 11
    assert len(ActionType) == 12


def test_action_type_mask_length_matches_enum():
    space = ActionSpace(phase=1)
    mask = space.get_action_type_mask(inventory={}, phase=1, lesson_index=0)
    assert len(mask) == 12


def test_place_near_enabled_with_drill():
    """In lesson 2, PLACE_NEAR must be enabled if burner-mining-drill is in inventory."""
    space = ActionSpace(phase=1)
    inventory = {"burner-mining-drill": 3}
    mask = space.get_action_type_mask(inventory, phase=1, lesson_index=2)
    assert mask[ActionType.PLACE.value] == 1, "PLACE should be enabled with drill"
    assert mask[ActionType.PLACE_NEAR.value] == 1, "PLACE_NEAR should be enabled with drill"


def test_place_disabled_no_placeable():
    """PLACE/PLACE_NEAR disabled if inventory has no placeable entities."""
    space = ActionSpace(phase=1)
    inventory = {"coal": 50, "iron-ore": 100}
    mask = space.get_action_type_mask(inventory, phase=1, lesson_index=2)
    assert mask[ActionType.PLACE.value] == 0
    assert mask[ActionType.PLACE_NEAR.value] == 0


def test_entity_registry_has_late_game_entities():
    for name in ("oil-refinery", "chemical-plant", "pumpjack", "storage-tank",
                 "electric-furnace", "rail", "train-stop",
                 "assembling-machine-3", "beacon", "speed-module",
                 "productivity-module", "rocket-silo", "centrifuge"):
        assert name in ENTITY_REGISTRY, f"{name} missing from ENTITY_REGISTRY"


def test_recipe_registry_has_late_game_recipes():
    for name in ("chemical-science-pack", "sulfur", "plastic-bar",
                 "production-science-pack", "utility-science-pack",
                 "rocket-part", "space-science-pack", "satellite"):
        assert name in RECIPE_REGISTRY, f"{name} missing from RECIPE_REGISTRY"


def test_entity_registry_ids_unique():
    ids = list(ENTITY_REGISTRY.values())
    assert len(ids) == len(set(ids)), "Duplicate entity IDs found"


def test_recipe_registry_ids_unique():
    ids = list(RECIPE_REGISTRY.values())
    assert len(ids) == len(set(ids)), "Duplicate recipe IDs found"


def test_phase4_entities_is_explicit_not_all():
    """Phase 4 should NOT equal all entities — it's phase 3 + a few extras."""
    assert PHASE_ENTITIES[4] != set(ENTITY_REGISTRY.keys())
    assert "oil-refinery" not in PHASE_ENTITIES[4]


def test_phase4_recipes_is_explicit_not_all():
    """Phase 4 should NOT equal all recipes — it's phase 3 + a few extras."""
    assert PHASE_RECIPES[4] != set(RECIPE_REGISTRY.keys())
    assert "chemical-science-pack" not in PHASE_RECIPES[4]


def test_phase_entities_monotonically_expand():
    for p in range(1, 8):
        assert PHASE_ENTITIES[p].issubset(PHASE_ENTITIES[p + 1]), \
            f"Phase {p} entities is not a subset of phase {p+1}"


def test_phase_recipes_monotonically_expand():
    for p in range(1, 8):
        assert PHASE_RECIPES[p].issubset(PHASE_RECIPES[p + 1]), \
            f"Phase {p} recipes is not a subset of phase {p+1}"


def test_phase8_entities_is_all():
    assert PHASE_ENTITIES[8] == set(ENTITY_REGISTRY.keys())


def test_phase8_recipes_is_all():
    assert PHASE_RECIPES[8] == set(RECIPE_REGISTRY.keys())


def test_action_space_phase8_includes_all_entities():
    space = ActionSpace(phase=8)
    assert space.num_entity_types == len(ENTITY_REGISTRY)
    assert space.num_recipe_types == len(RECIPE_REGISTRY)


# -------------------------------------------------------------------
# Snapshot tests — verify dedup refactor preserves exact values
# -------------------------------------------------------------------

def test_phase_entities_exact_phase1():
    assert PHASE_ENTITIES[1] == {
        "stone-furnace", "burner-mining-drill", "wooden-chest",
        "transport-belt", "inserter", "burner-inserter",
        "small-electric-pole", "boiler",
    }


def test_phase_entities_exact_phase2():
    assert "steam-engine" in PHASE_ENTITIES[2]
    assert "assembling-machine-1" in PHASE_ENTITIES[2]
    assert "lab" in PHASE_ENTITIES[2]
    assert len(PHASE_ENTITIES[2]) == 15  # 8 + 7 new


def test_phase_entities_exact_phase3():
    assert "long-handed-inserter" in PHASE_ENTITIES[3]
    assert "fast-inserter" in PHASE_ENTITIES[3]
    assert "underground-belt" in PHASE_ENTITIES[3]
    assert "splitter" in PHASE_ENTITIES[3]
    assert len(PHASE_ENTITIES[3]) == 19  # 15 + 4 new


def test_phase_entities_cumulative():
    """Each phase is a superset of the previous phase."""
    for phase in range(2, 9):
        assert PHASE_ENTITIES[phase - 1].issubset(PHASE_ENTITIES[phase]), \
            f"Phase {phase} is not a superset of phase {phase - 1}"


def test_lesson_recipes_exact_values():
    space = ActionSpace(phase=1)
    # Lessons 0-3: same base set
    assert space._LESSON_RECIPES[0] == space._LESSON_RECIPES[1]
    assert space._LESSON_RECIPES[1] == space._LESSON_RECIPES[2]
    assert space._LESSON_RECIPES[2] == space._LESSON_RECIPES[3]
    assert len(space._LESSON_RECIPES[0]) == 6

    # Lessons 4-5: add inserter + ores
    assert "burner-inserter" in space._LESSON_RECIPES[4]
    assert "iron-ore" in space._LESSON_RECIPES[4]
    assert space._LESSON_RECIPES[4] == space._LESSON_RECIPES[5]
    assert len(space._LESSON_RECIPES[4]) == 9

    # Lessons 6-7: add belts + containers
    assert "transport-belt" in space._LESSON_RECIPES[6]
    assert "wooden-chest" in space._LESSON_RECIPES[6]
    assert len(space._LESSON_RECIPES[6]) == 11

    assert "inserter" in space._LESSON_RECIPES[7]
    assert "small-electric-pole" in space._LESSON_RECIPES[7]
    assert len(space._LESSON_RECIPES[7]) == 13
