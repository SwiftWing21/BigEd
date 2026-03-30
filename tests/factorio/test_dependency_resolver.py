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


# ---------------------------------------------------------------------------
# Advanced tests
# ---------------------------------------------------------------------------


def test_resolve_yield_awareness(dag):
    """Copper-cable yields 2 per craft; needing 6 → deficit=6 items (3 crafts)."""
    inventory = {"copper-plate": 10}
    plan = resolve("copper-cable", 6, inventory, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Should craft copper-cable; deficit in the step is crafts_needed = ceil(6/2) = 3
    craft_actions = [a for a in actions if a["action"] == "craft"]
    assert len(craft_actions) == 1
    assert craft_actions[0]["recipe"] == "copper-cable"
    assert craft_actions[0]["count"] == 3  # 3 crafts to get 6 cables


def test_resolve_smelting_chain(dag):
    """iron-gear-wheel → iron-plate (smelt) → iron-ore (mine).
    With stone-furnace in entities, actions include acquire, smelt, and craft."""
    inventory = {}
    entities = {"stone-furnace": 1}
    plan = resolve("iron-gear-wheel", 1, inventory, entities, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    action_types = {a["action"] for a in actions}
    assert "acquire" in action_types  # iron-ore (and coal for fuel)
    assert "smelt" in action_types    # iron-plate
    assert "craft" in action_types    # iron-gear-wheel


def test_resolve_builds_infrastructure(dag):
    """iron-plate with no entities → resolver should build a stone-furnace."""
    inventory = {"iron-ore": 10, "stone": 10, "coal": 10}
    entities = {}
    plan = resolve("iron-plate", 1, inventory, entities, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Should contain a craft action for stone-furnace (build infrastructure)
    build_furnace = [a for a in actions if a.get("recipe") == "stone-furnace"]
    assert len(build_furnace) >= 1


def test_resolve_uses_existing_infrastructure(dag):
    """iron-plate with steel-furnace → should NOT build stone-furnace."""
    inventory = {"iron-ore": 10, "coal": 10}
    entities = {"steel-furnace": 1}
    plan = resolve("iron-plate", 1, inventory, entities, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # No stone-furnace craft action
    build_furnace = [a for a in actions if a.get("recipe") == "stone-furnace"]
    assert len(build_furnace) == 0


def test_resolve_cross_goal_dedup(dag):
    """Two goals sharing iron-plate: with plenty of ore + coal + furnace, no double-resolve."""
    inventory = {"iron-ore": 20, "coal": 20}
    entities = {"stone-furnace": 1}
    plan1 = resolve("iron-plate", 5, inventory, entities, dag)
    plan2 = resolve("iron-plate", 3, dict(inventory), entities, dag)
    # Each plan independently should be complete
    assert plan1.is_complete()
    assert plan2.is_complete()
    # Actions from each plan should only smelt the requested amount, not double
    actions1 = plan1.to_actions()
    smelt1 = [a for a in actions1 if a["action"] == "smelt"]
    assert len(smelt1) == 1
    assert smelt1[0]["count"] == 5


def test_resolve_max_depth_exceeded(dag):
    """electronic-circuit with max_depth=1 → plan.is_complete() should be False."""
    inventory = {}
    plan = resolve("electronic-circuit", 1, inventory, {}, dag, max_depth=1)
    assert not plan.is_complete()


def test_resolve_empty_goal(dag):
    """Empty string goal with amount 0 — no meaningful actions."""
    # Passing a non-existent item with 0 need: satisfied trivially
    inventory = {}
    plan = resolve("", 0, inventory, {}, dag)
    actions = plan.to_actions()
    assert actions == []


def test_plan_summary(dag):
    """stone-furnace with partial stone → summary contains item names."""
    inventory = {"stone": 3}
    plan = resolve("stone-furnace", 1, inventory, {}, dag)
    summary = plan.summary()
    assert "stone" in summary
    assert "stone-furnace" in summary


def test_resolve_zero_count_goal(dag):
    """{"stone-furnace": 0} → no actions, is_complete=True."""
    inventory = {}
    plan = resolve("stone-furnace", 0, inventory, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    assert actions == []


def test_resolve_unknown_item(dag):
    """{"mystery-widget": 3} → acquire step (terminal item)."""
    inventory = {}
    plan = resolve("mystery-widget", 3, inventory, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    assert len(actions) == 1
    assert actions[0]["action"] == "acquire"
    assert actions[0]["item"] == "mystery-widget"
    assert actions[0]["count"] == 3


def test_resolve_multi_result_recipe(tmp_path):
    """Multi-result recipe: advanced-oil-processing produces multiple outputs.
    Resolving petroleum-gas with oil-refinery should complete."""
    recipes = {
        "advanced-oil-processing": {
            "category": "oil-processing",
            "energy": 5.0,
            "ingredients": [
                {"name": "crude-oil", "amount": 100},
                {"name": "water", "amount": 50},
            ],
            "result": "petroleum-gas",
            "result_count": 55,
        },
    }
    path = tmp_path / "oil_recipes.json"
    path.write_text(json.dumps(recipes))
    oil_dag = RecipeDAG(str(path))

    inventory = {"crude-oil": 500, "water": 250}
    entities = {"oil-refinery": 1}
    plan = resolve("petroleum-gas", 50, inventory, entities, oil_dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Should craft/process petroleum-gas, 1 craft yields 55 which covers 50
    assert len(actions) >= 1
