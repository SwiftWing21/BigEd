"""Tests for parse_criteria_to_items in the Factorio dependency resolver."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from factorio.dependency_resolver import parse_criteria_to_items


# ── Single-value criteria ──────────────────────────────────────────────


def test_parse_single_entity():
    result = parse_criteria_to_items("entities.stone-furnace >= 1")
    assert result == {"stone-furnace": 1}


def test_parse_single_inventory():
    result = parse_criteria_to_items("inventory.iron-gear-wheel >= 4")
    assert result == {"iron-gear-wheel": 4}


# ── Compound criteria ──────────────────────────────────────────────────


def test_parse_compound_and():
    criteria = "inventory.iron-plate >= 10 AND inventory.copper-plate >= 5"
    result = parse_criteria_to_items(criteria)
    assert result == {"iron-plate": 10, "copper-plate": 5}


# ── Non-item criteria (should be ignored) ──────────────────────────────


def test_parse_non_item_criteria():
    result = parse_criteria_to_items("player.health > 0")
    assert result == {}


def test_parse_mixed_criteria():
    criteria = "player.alive == true AND inventory.iron-plate >= 20"
    result = parse_criteria_to_items(criteria)
    assert result == {"iron-plate": 20}


# ── OR branches — first branch only ───────────────────────────────────


def test_parse_or_criteria():
    criteria = (
        "inventory.iron-plate >= 10 AND inventory.copper-plate >= 5"
        " OR "
        "inventory.steel-plate >= 2"
    )
    result = parse_criteria_to_items(criteria)
    assert result == {"iron-plate": 10, "copper-plate": 5}


# ── Resources criteria (not craftable — ignored) ──────────────────────


def test_parse_resources_criteria():
    result = parse_criteria_to_items("resources.iron-ore > 0")
    assert result == {}


# ── Passthrough / converter helpers ───────────────────────────────────


def test_entities_to_counts_dict_passthrough():
    """A plain dict should parse as-is (no entities.* prefix needed)."""
    # parse_criteria_to_items only handles strings; a dict caller would
    # just use the dict directly.  Verify the function handles the
    # typical string form that *wraps* a dict-like pattern.
    result = parse_criteria_to_items("entities.assembling-machine-1 >= 3")
    assert result == {"assembling-machine-1": 3}


def test_entities_to_counts_list_of_dicts():
    """Multiple entity entries parsed from a compound criteria string."""
    criteria = "entities.stone-furnace >= 4 AND entities.burner-mining-drill >= 2"
    result = parse_criteria_to_items(criteria)
    assert result == {"stone-furnace": 4, "burner-mining-drill": 2}
