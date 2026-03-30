"""Tests for dependency_resolver — backwards chaining, inventory math, abstract actions."""
import json
import pytest
from factorio.recipe_dag import RecipeDAG
from factorio.dependency_resolver import resolve, ResolutionPlan


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


def test_resolve_already_satisfied(dag):
    """Goal already in inventory — no actions needed."""
    inventory = {"stone-furnace": 3}
    plan = resolve("stone-furnace", 1, inventory, {}, dag)
    assert isinstance(plan, ResolutionPlan)
    assert plan.is_complete()
    actions = plan.to_actions()
    assert actions == []


def test_resolve_simple_craft(dag):
    """Have 5 stone in inventory — craft 1 stone-furnace, no mining needed."""
    inventory = {"stone": 5}
    plan = resolve("stone-furnace", 1, inventory, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Should only be a craft action, no acquire
    assert len(actions) == 1
    assert actions[0]["action"] == "craft"
    assert actions[0]["recipe"] == "stone-furnace"
    assert actions[0]["count"] == 1


def test_resolve_mine_then_craft(dag):
    """No stone in inventory — must acquire stone then craft furnace."""
    inventory = {}
    plan = resolve("stone-furnace", 1, inventory, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Must have acquire before craft (post-order: children first)
    assert len(actions) == 2
    assert actions[0]["action"] == "acquire"
    assert actions[0]["item"] == "stone"
    assert actions[0]["count"] == 5
    assert actions[1]["action"] == "craft"
    assert actions[1]["recipe"] == "stone-furnace"
    assert actions[1]["count"] == 1


def test_resolve_partial_inventory(dag):
    """Have 3 of 5 stone — only acquire the 2 deficit."""
    inventory = {"stone": 3}
    plan = resolve("stone-furnace", 1, inventory, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Acquire 2 stone (deficit), then craft
    assert len(actions) == 2
    acquire = actions[0]
    assert acquire["action"] == "acquire"
    assert acquire["item"] == "stone"
    assert acquire["count"] == 2
    craft = actions[1]
    assert craft["action"] == "craft"
    assert craft["recipe"] == "stone-furnace"
    assert craft["count"] == 1
