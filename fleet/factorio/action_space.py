"""
Factorio RL agent — hierarchical action space with phase-gated entity/recipe sets.

The policy network outputs action type logits + per-type parameter logits.
This module encodes game actions to/from integer representations the network
can work with. Phase gating limits the action space in early training phases
to help convergence.

Encoding conventions
--------------------
- dx / dy  : offset by +5 to map [-5, +5] → [0, 10]
- direction: index into DIRECTION_NAMES
- entity   : 1-based index into the *phase-local* sorted entity list
- recipe   : 1-based index into the *phase-local* sorted recipe list
- count    : raw integer (clipped to [1, 255] on encode)
"""

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action type enum
# ---------------------------------------------------------------------------

class ActionType(IntEnum):
    PLACE      = 0
    CRAFT      = 1
    RESEARCH   = 2
    MOVE       = 3
    SET_RECIPE = 4
    REMOVE     = 5
    WAIT       = 6
    MINE       = 7
    INSERT     = 8  # Insert items from inventory into a nearby entity (furnace, machine)


# ---------------------------------------------------------------------------
# Direction helpers
# ---------------------------------------------------------------------------

# 8 bins → 4 cardinal directions (Factorio entities only use cardinal).
# Adjacent bins map to the same direction for robustness.
DIRECTION_NAMES: list[str] = [
    "north", "east", "east", "south",
    "south", "west", "west", "north",
]

_DIRECTION_INDEX: dict[str, int] = {d: i for i, d in enumerate(DIRECTION_NAMES)}


# ---------------------------------------------------------------------------
# Entity registry  (0 = empty/air slot)
# ---------------------------------------------------------------------------

ENTITY_REGISTRY: dict[str, int] = {
    "stone-furnace":         1,
    "burner-mining-drill":   2,
    "wooden-chest":          3,
    "transport-belt":        4,
    "inserter":              5,
    "burner-inserter":       6,
    "small-electric-pole":   7,
    "boiler":                8,
    "steam-engine":          9,
    "offshore-pump":         10,
    "pipe":                  11,
    "assembling-machine-1":  12,
    "lab":                   13,
    "iron-chest":            14,
    "long-handed-inserter":  15,
    "fast-inserter":         16,
    "underground-belt":      17,
    "splitter":              18,
    "assembling-machine-2":  19,
    "electric-mining-drill": 20,
    "medium-electric-pole":  21,
    "pipe-to-ground":        22,
    "gun-turret":            23,
    "wall":                  24,
}

_ENTITY_ID_TO_NAME: dict[int, str] = {v: k for k, v in ENTITY_REGISTRY.items()}


# ---------------------------------------------------------------------------
# Recipe registry
# ---------------------------------------------------------------------------

RECIPE_REGISTRY: dict[str, int] = {
    "iron-plate":            1,
    "copper-plate":          2,
    "steel-plate":           3,
    "iron-gear-wheel":       4,
    "copper-cable":          5,
    "electronic-circuit":    6,
    "advanced-circuit":      7,
    "iron-chest":            8,
    "wooden-chest":          9,
    "transport-belt":        10,
    "underground-belt":      11,
    "splitter":              12,
    "inserter":              13,
    "burner-inserter":       14,
    "long-handed-inserter":  15,
    "fast-inserter":         16,
    "small-electric-pole":   17,
    "medium-electric-pole":  18,
    "pipe":                  19,
    "pipe-to-ground":        20,
    "boiler":                21,
    "steam-engine":          22,
    "offshore-pump":         23,
    "stone-furnace":         24,
    "assembling-machine-1":  25,
    "assembling-machine-2":  26,
    "lab":                   27,
    "science-pack-1":        28,
    "science-pack-2":        29,
    "automation-science-pack": 30,
    "logistic-science-pack": 31,
    "firearm-magazine":      32,
    "gun-turret":            33,
    "wall":                  34,
    "burner-mining-drill":   35,
    "electric-mining-drill": 36,
    "coal":                  37,
    "stone":                 38,
    "iron-ore":              39,
    "copper-ore":            40,
}

_RECIPE_ID_TO_NAME: dict[int, str] = {v: k for k, v in RECIPE_REGISTRY.items()}


# ---------------------------------------------------------------------------
# Technology registry
# ---------------------------------------------------------------------------

TECH_REGISTRY: dict[str, int] = {
    "automation":            1,
    "logistics":             2,
    "electronics":           3,
    "steel-processing":      4,
    "advanced-material-processing": 5,
    "military":              6,
    "turrets":               7,
    "electric-energy-distribution-1": 8,
    "engine":                9,
    "automated-rail-transportation": 10,
    "logistic-robotics":     11,
}

_TECH_ID_TO_NAME: dict[int, str] = {v: k for k, v in TECH_REGISTRY.items()}


# ---------------------------------------------------------------------------
# Phase-gated entity / recipe sets
# ---------------------------------------------------------------------------

PHASE_ENTITIES: dict[int, set[str]] = {
    1: {
        "stone-furnace",
        "burner-mining-drill",
        "wooden-chest",
        "transport-belt",
        "inserter",
        "burner-inserter",
        "small-electric-pole",
        "boiler",
    },
    2: {
        # Phase 1 +
        "stone-furnace",
        "burner-mining-drill",
        "wooden-chest",
        "transport-belt",
        "inserter",
        "burner-inserter",
        "small-electric-pole",
        "boiler",
        # New in phase 2
        "steam-engine",
        "offshore-pump",
        "pipe",
        "assembling-machine-1",
        "lab",
        "iron-chest",
        "electric-mining-drill",
    },
    3: {
        # Phase 2 +
        "stone-furnace",
        "burner-mining-drill",
        "wooden-chest",
        "transport-belt",
        "inserter",
        "burner-inserter",
        "small-electric-pole",
        "boiler",
        "steam-engine",
        "offshore-pump",
        "pipe",
        "assembling-machine-1",
        "lab",
        "iron-chest",
        "electric-mining-drill",
        # New in phase 3
        "long-handed-inserter",
        "fast-inserter",
        "underground-belt",
        "splitter",
    },
    4: set(ENTITY_REGISTRY.keys()),  # all entities
}

PHASE_RECIPES: dict[int, set[str]] = {
    1: {
        "iron-plate", "copper-plate", "iron-gear-wheel", "copper-cable",
        "transport-belt", "inserter", "burner-inserter", "stone-furnace",
        "wooden-chest", "small-electric-pole", "pipe", "boiler",
        "coal", "iron-ore", "copper-ore", "stone",
    },
    2: {
        "iron-plate", "copper-plate", "steel-plate", "iron-gear-wheel",
        "copper-cable", "electronic-circuit", "transport-belt", "inserter",
        "burner-inserter", "stone-furnace", "wooden-chest", "iron-chest",
        "small-electric-pole", "medium-electric-pole", "pipe",
        "pipe-to-ground", "boiler", "steam-engine", "offshore-pump",
        "assembling-machine-1", "lab", "science-pack-1", "science-pack-2",
        "automation-science-pack", "logistic-science-pack",
        "burner-mining-drill", "electric-mining-drill",
        "coal", "iron-ore", "copper-ore", "stone",
    },
    3: {
        "iron-plate", "copper-plate", "steel-plate", "iron-gear-wheel",
        "copper-cable", "electronic-circuit", "advanced-circuit",
        "transport-belt", "underground-belt", "splitter",
        "inserter", "burner-inserter", "long-handed-inserter", "fast-inserter",
        "stone-furnace", "wooden-chest", "iron-chest",
        "small-electric-pole", "medium-electric-pole",
        "pipe", "pipe-to-ground", "boiler", "steam-engine", "offshore-pump",
        "assembling-machine-1", "assembling-machine-2", "lab",
        "science-pack-1", "science-pack-2",
        "automation-science-pack", "logistic-science-pack",
        "burner-mining-drill", "electric-mining-drill",
        "firearm-magazine", "gun-turret", "wall",
        "coal", "iron-ore", "copper-ore", "stone",
    },
    4: set(RECIPE_REGISTRY.keys()),  # all recipes
}


# ---------------------------------------------------------------------------
# Encoded action dataclass
# ---------------------------------------------------------------------------

@dataclass
class EncodedAction:
    """Integer representation of a single Factorio agent action."""
    action_type: int = 0          # ActionType value
    entity_id:   int = 0          # phase-local entity index (1-based) or 0
    recipe_id:   int = 0          # phase-local recipe index (1-based) or 0
    tech_id:     int = 0          # tech index (1-based) or 0
    dx:          int = 5          # delta-x offset by +5  → [0, 10]
    dy:          int = 5          # delta-y offset by +5  → [0, 10]
    direction:   int = 0          # index into DIRECTION_NAMES
    count:       int = 1          # craft / mine count
    grid_x:      int = 0          # absolute grid x (for display / debug)
    grid_y:      int = 0          # absolute grid y


# ---------------------------------------------------------------------------
# ActionSpace
# ---------------------------------------------------------------------------

class ActionSpace:
    """
    Phase-gated action space for the Factorio RL agent.

    Parameters
    ----------
    phase : int
        Current curriculum phase (1–4).  Higher phases unlock more entities
        and recipes, expanding the action space.
    """

    def __init__(self, phase: int) -> None:
        self._phase = phase
        # Build sorted lists so indices are stable within a phase.
        raw_entities = PHASE_ENTITIES.get(phase, PHASE_ENTITIES[4])
        raw_recipes  = PHASE_RECIPES.get(phase, PHASE_RECIPES[4])

        # Sort for determinism; index 0 = "none", so lists are 1-based.
        self._entities: list[str] = sorted(raw_entities)
        self._recipes:  list[str] = sorted(raw_recipes)

        # Fast reverse look-up: name → 1-based local index
        self._entity_to_local: dict[str, int] = {
            name: i + 1 for i, name in enumerate(self._entities)
        }
        self._recipe_to_local: dict[str, int] = {
            name: i + 1 for i, name in enumerate(self._recipes)
        }

        log.debug(
            "ActionSpace phase=%d  entities=%d  recipes=%d",
            phase, len(self._entities), len(self._recipes),
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def num_entity_types(self) -> int:
        """Number of placeable entity types available in this phase."""
        return len(self._entities)

    @property
    def num_recipe_types(self) -> int:
        """Number of craftable recipe types available in this phase."""
        return len(self._recipes)

    @property
    def num_tech_types(self) -> int:
        """Total number of technology types (phase-independent for now)."""
        return len(TECH_REGISTRY)

    # ------------------------------------------------------------------
    # Encode
    # ------------------------------------------------------------------

    def encode_action(self, action_dict: dict) -> EncodedAction:
        """
        Encode a human-readable action dict into an ``EncodedAction``.

        Unknown entities / recipes are mapped to 0 with a warning.
        """
        raw_type: str = action_dict.get("action", "wait")
        try:
            action_type = ActionType[raw_type.upper()].value
        except KeyError:
            log.warning("encode_action: unknown action type %r, defaulting to WAIT", raw_type)
            action_type = ActionType.WAIT.value

        # Position
        pos = action_dict.get("position", {})
        raw_dx = pos.get("x", 0)
        raw_dy = pos.get("y", 0)
        dx = int(raw_dx) + 5   # offset to [0, 10]
        dy = int(raw_dy) + 5

        # Direction
        direction_name: str = action_dict.get("direction", "north")
        direction = _DIRECTION_INDEX.get(direction_name, 0)

        # Entity (for PLACE / REMOVE / SET_RECIPE actions)
        entity_name: str = action_dict.get("entity", "")
        entity_id = self._entity_to_local.get(entity_name, 0)
        if entity_name and entity_id == 0:
            log.warning("encode_action: entity %r not in phase %d registry", entity_name, self._phase)

        # Recipe (for CRAFT / SET_RECIPE actions)
        recipe_name: str = action_dict.get("recipe", "")
        recipe_id = self._recipe_to_local.get(recipe_name, 0)
        if recipe_name and recipe_id == 0:
            log.warning("encode_action: recipe %r not in phase %d registry", recipe_name, self._phase)

        # Technology (for RESEARCH actions)
        tech_name: str = action_dict.get("tech", "")
        tech_id = TECH_REGISTRY.get(tech_name, 0)

        # Count
        count = max(1, min(255, int(action_dict.get("count", 1))))

        return EncodedAction(
            action_type=action_type,
            entity_id=entity_id,
            recipe_id=recipe_id,
            tech_id=tech_id,
            dx=dx,
            dy=dy,
            direction=direction,
            count=count,
            grid_x=int(pos.get("x", 0)),
            grid_y=int(pos.get("y", 0)),
        )

    # ------------------------------------------------------------------
    # Decode
    # ------------------------------------------------------------------

    def decode_action(self, encoded: EncodedAction) -> dict:
        """
        Decode an ``EncodedAction`` back into a human-readable action dict.
        """
        try:
            action_type = ActionType(encoded.action_type)
            action_name = action_type.name.lower()
        except ValueError:
            log.warning("decode_action: invalid action_type %d", encoded.action_type)
            action_name = "wait"

        result: dict = {"action": action_name}

        # Position (undo +5 offset)
        x = encoded.dx - 5
        y = encoded.dy - 5
        if action_name in ("place", "remove", "mine", "move", "insert"):
            result["position"] = {"x": x, "y": y}

        # Direction
        direction = DIRECTION_NAMES[encoded.direction % len(DIRECTION_NAMES)]
        if action_name == "place":
            result["direction"] = direction

        # Entity
        if encoded.entity_id > 0 and encoded.entity_id <= len(self._entities):
            entity_name = self._entities[encoded.entity_id - 1]
            if action_name in ("place", "remove", "set_recipe"):
                result["entity"] = entity_name

        # Recipe / item (INSERT reuses recipe_id to select inventory item)
        if encoded.recipe_id > 0 and encoded.recipe_id <= len(self._recipes):
            recipe_name = self._recipes[encoded.recipe_id - 1]
            if action_name in ("craft", "set_recipe"):
                result["recipe"] = recipe_name
            elif action_name == "insert":
                result["item"] = recipe_name  # recipe list doubles as item selector

        # Technology
        if encoded.tech_id > 0:
            tech_name = _TECH_ID_TO_NAME.get(encoded.tech_id, "")
            if tech_name and action_name == "research":
                result["tech"] = tech_name

        # Count
        if action_name in ("craft", "mine", "insert"):
            result["count"] = encoded.count

        return result

    # ------------------------------------------------------------------
    # Action mask
    # ------------------------------------------------------------------

    # Lesson-gated recipe allowlists — only these recipes can be crafted at each lesson.
    # Agents start with generous inventory — focus on place/insert, not craft variety.
    _LESSON_RECIPES: dict[int, set[str]] = {
        # Lessons 0-3: place furnaces + drills (craft more if needed)
        0: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal"},
        1: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal"},
        2: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal"},
        3: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal"},
        # Lessons 4-5: insert + smelt (add inserter for feeding)
        4: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal", "burner-inserter", "iron-ore", "copper-ore"},
        5: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal", "burner-inserter", "iron-ore", "copper-ore"},
        # Lessons 6-7: belts + full automation
        6: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal", "burner-inserter", "transport-belt", "iron-ore",
            "copper-ore", "wooden-chest"},
        7: {"stone-furnace", "burner-mining-drill", "iron-gear-wheel", "iron-plate",
            "copper-plate", "coal", "burner-inserter", "transport-belt", "iron-ore",
            "copper-ore", "wooden-chest", "inserter", "small-electric-pole"},
    }
    # Phase 2+: all recipes unlocked
    _PHASE2_UNLOCK_ALL = True

    def get_action_type_mask(self, inventory: dict, phase: int, lesson_index: int = 0) -> list[int]:
        """
        Return a binary mask (length == len(ActionType) == 9) indicating
        which action types are currently valid.
        """
        mask = [1] * len(ActionType)

        # PLACE requires at least one placeable entity in inventory
        has_placeable = any(
            item in self._entity_to_local for item in inventory
        )
        mask[ActionType.PLACE.value] = 1 if has_placeable else 0

        # REMOVE disabled in Phase 1-2
        if phase <= 2:
            mask[ActionType.REMOVE.value] = 0

        # Lesson-aware action restrictions for Phase 1
        if phase == 1:
            if lesson_index <= 2:
                # Lessons 0-2: PLACE focused — disable CRAFT, RESEARCH, SET_RECIPE
                # Agent has items, just needs to learn to PLACE and MOVE
                mask[ActionType.CRAFT.value] = 0
                mask[ActionType.RESEARCH.value] = 0
                mask[ActionType.SET_RECIPE.value] = 0
            elif lesson_index <= 4:
                # Lessons 3-4: INSERT + PLACE focused — add INSERT, keep CRAFT off
                mask[ActionType.RESEARCH.value] = 0
                mask[ActionType.SET_RECIPE.value] = 0
            # Lessons 5+: everything except REMOVE

        # Ensure the always-valid actions are set (defensive)
        mask[ActionType.WAIT.value] = 1
        mask[ActionType.MOVE.value] = 1
        mask[ActionType.MINE.value] = 1

        return mask

    def get_recipe_mask(self, phase: int, lesson_index: int = 0) -> list[int]:
        """Return binary mask over recipe vocabulary. 1 = allowed, 0 = blocked.

        In Phase 1, only lesson-relevant recipes are allowed.
        Phase 2+: all recipes unlocked.
        """
        if phase >= 2:
            return [1] * len(self._recipes)

        allowed = self._LESSON_RECIPES.get(lesson_index, self._LESSON_RECIPES.get(7, set()))
        mask = []
        for recipe_name in self._recipes:
            mask.append(1 if recipe_name in allowed else 0)
        # Ensure at least one recipe is allowed (fallback)
        if sum(mask) == 0:
            mask = [1] * len(self._recipes)
        return mask
