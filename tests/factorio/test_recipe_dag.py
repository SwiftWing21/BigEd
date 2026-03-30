"""Tests for RecipeDAG — JSON loader, resolve, yield, cycle detection."""
import json
import pytest
from factorio.recipe_dag import RecipeDAG


RECIPES = {
    "stone-furnace": {
        "category": "crafting",
        "energy": 0.5,
        "ingredients": [{"name": "stone", "amount": 5}],
        "result": "stone-furnace",
        "result_count": 1,
    },
    "iron-plate": {
        "category": "smelting",
        "energy": 3.2,
        "ingredients": [{"name": "iron-ore", "amount": 1}],
        "result": "iron-plate",
        "result_count": 1,
    },
    "iron-gear-wheel": {
        "category": "crafting",
        "energy": 0.5,
        "ingredients": [{"name": "iron-plate", "amount": 2}],
        "result": "iron-gear-wheel",
        "result_count": 1,
    },
    "copper-cable": {
        "category": "crafting",
        "energy": 0.5,
        "ingredients": [{"name": "copper-plate", "amount": 1}],
        "result": "copper-cable",
        "result_count": 2,
    },
    "electronic-circuit": {
        "category": "crafting",
        "energy": 0.5,
        "ingredients": [
            {"name": "iron-plate", "amount": 1},
            {"name": "copper-cable", "amount": 3},
        ],
        "result": "electronic-circuit",
        "result_count": 1,
    },
    "copper-plate": {
        "category": "smelting",
        "energy": 3.2,
        "ingredients": [{"name": "copper-ore", "amount": 1}],
        "result": "copper-plate",
        "result_count": 1,
    },
}


@pytest.fixture
def dag(tmp_path):
    """Create a RecipeDAG from a temp JSON file with 6 standard recipes."""
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps(RECIPES))
    return RecipeDAG(str(path))


KOVAREX_RECIPES = {
    "kovarex-enrichment-process": {
        "category": "centrifuging",
        "energy": 60,
        "ingredients": [
            {"name": "uranium-235", "amount": 40},
            {"name": "uranium-238", "amount": 5},
        ],
        "result": "uranium-235",
        "result_count": 41,
    },
}


@pytest.fixture
def kovarex_dag(tmp_path):
    """Create a RecipeDAG with a cyclic Kovarex enrichment recipe."""
    path = tmp_path / "kovarex.json"
    path.write_text(json.dumps(KOVAREX_RECIPES))
    return RecipeDAG(str(path))


# --- Tests ---


def test_load_from_json(dag):
    """Loads 6 recipes from JSON."""
    assert len(dag.recipes) == 6


def test_resolve_single_hop(dag):
    """stone-furnace recipe has stone as an ingredient."""
    recipe = dag.resolve("stone-furnace")
    assert recipe is not None
    names = [i["name"] for i in recipe["ingredients"]]
    assert "stone" in names


def test_resolve_terminal_returns_none(dag):
    """Terminal items (iron-ore, stone) have no recipe."""
    assert dag.resolve("iron-ore") is None
    assert dag.resolve("stone") is None


def test_is_terminal(dag):
    """iron-ore is terminal (must mine), stone-furnace is not."""
    assert dag.is_terminal("iron-ore") is True
    assert dag.is_terminal("stone-furnace") is False


def test_category(dag):
    """iron-plate is smelting, stone-furnace is crafting, unknown is None."""
    assert dag.category("iron-plate") == "smelting"
    assert dag.category("stone-furnace") == "crafting"
    assert dag.category("unknown-item") is None


def test_yield_for_copper_cable(dag):
    """copper-cable yields 2 per craft."""
    assert dag.yield_for("copper-cable") == 2


def test_yield_for_terminal(dag):
    """Terminal items default to yield of 1."""
    assert dag.yield_for("iron-ore") == 1


def test_raw_resources_stone_furnace(dag):
    """stone-furnace needs 5 stone (raw)."""
    raw = dag.raw_resources("stone-furnace", 1)
    assert raw == {"stone": 5}


def test_raw_resources_iron_gear_wheel(dag):
    """4 iron-gear-wheels = 8 iron-ore (each wheel = 2 iron-plate = 2 iron-ore)."""
    raw = dag.raw_resources("iron-gear-wheel", 4)
    assert raw == {"iron-ore": 8}


def test_raw_resources_electronic_circuit(dag):
    """1 electronic-circuit = 1 iron-plate + 3 copper-cable.
    1 iron-plate = 1 iron-ore.
    3 copper-cable = ceil(3/2) = 2 copper-plate = 2 copper-ore.
    Total: {iron-ore: 1, copper-ore: 2}.
    """
    raw = dag.raw_resources("electronic-circuit", 1)
    assert raw == {"iron-ore": 1, "copper-ore": 2}


def test_cycle_detection_no_infinite_loop(kovarex_dag):
    """Kovarex enrichment has uranium-235 as both input and output.
    Should not loop infinitely — uranium-235 and uranium-238 are treated as raw.
    """
    raw = kovarex_dag.raw_resources("uranium-235", 1)
    # uranium-235 is its own output, so flatten should treat ingredients as raw
    assert "uranium-235" in raw or "uranium-238" in raw
    # Just verify it terminates without hanging
