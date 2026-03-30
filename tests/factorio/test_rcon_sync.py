"""Tests for sync_recipes — RCON live recipe diffing against static DAG."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fleet"))

import pytest
from factorio.recipe_dag import RecipeDAG, sync_recipes


STONE_FURNACE_RECIPE = {
    "stone-furnace": {
        "category": "crafting",
        "energy": 0.5,
        "ingredients": [{"name": "stone", "amount": 5}],
        "result": "stone-furnace",
        "result_count": 1,
    },
}


@pytest.fixture
def dag(tmp_path):
    """Create a RecipeDAG with a single stone-furnace recipe."""
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps(STONE_FURNACE_RECIPE))
    return RecipeDAG(str(path))


def test_sync_no_changes(dag):
    """Matching live recipes produce 0 updates, 0 added, 0 missing."""
    live = dict(STONE_FURNACE_RECIPE)
    result = sync_recipes(live, dag)
    assert result == {"updated": 0, "added": 0, "missing": 0}


def test_sync_ingredient_change(dag):
    """Changed ingredient amount triggers an in-memory DAG update."""
    live = {
        "stone-furnace": {
            "category": "crafting",
            "energy": 0.5,
            "ingredients": [{"name": "stone", "amount": 10}],  # changed from 5 to 10
            "result": "stone-furnace",
            "result_count": 1,
        },
    }
    result = sync_recipes(live, dag)
    assert result["updated"] == 1
    assert result["added"] == 0
    assert result["missing"] == 0
    # Verify the in-memory DAG was actually patched
    assert dag.recipes["stone-furnace"]["ingredients"][0]["amount"] == 10


def test_sync_new_recipe(dag):
    """New recipe in live data gets added to the in-memory DAG."""
    live = dict(STONE_FURNACE_RECIPE)
    live["iron-plate"] = {
        "category": "smelting",
        "energy": 3.2,
        "ingredients": [{"name": "iron-ore", "amount": 1}],
        "result": "iron-plate",
        "result_count": 1,
    }
    result = sync_recipes(live, dag)
    assert result["added"] == 1
    assert result["updated"] == 0
    assert result["missing"] == 0
    # Verify the new recipe is queryable
    assert dag.resolve("iron-plate") is not None
