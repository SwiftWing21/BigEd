"""RecipeDAG — Factorio recipe dependency graph with JSON loading and flattening."""
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("biged.factorio.recipe_dag")


class RecipeDAG:
    """Loads Factorio recipes from JSON and provides graph traversal methods.

    The JSON format is a dict of recipe_name -> recipe_data, where each recipe has:
        category, energy, ingredients (list of {name, amount}), result, result_count.
    """

    def __init__(self, recipes_path: str) -> None:
        path = Path(recipes_path)
        with open(path, "r", encoding="utf-8") as f:
            self.recipes: dict[str, dict] = json.load(f)
        # Build reverse index: item_name -> recipe that produces it
        self._item_to_recipe: dict[str, dict] = {}
        for name, recipe in self.recipes.items():
            result_item = recipe.get("result", name)
            self._item_to_recipe[result_item] = recipe

    def resolve(self, item: str) -> dict | None:
        """Single-hop resolve: return the recipe that produces this item, or None."""
        return self._item_to_recipe.get(item)

    def is_terminal(self, item: str) -> bool:
        """True if item has no recipe (must be mined/harvested)."""
        return item not in self._item_to_recipe

    def category(self, item: str) -> str | None:
        """Return the crafting category for an item, or None if unknown."""
        recipe = self.resolve(item)
        if recipe is None:
            return None
        return recipe.get("category")

    def yield_for(self, item: str) -> int:
        """How many items produced per craft. Defaults to 1 for terminals."""
        recipe = self.resolve(item)
        if recipe is None:
            return 1
        return recipe.get("result_count", 1)

    def ingredients_for(self, item: str) -> list[dict] | None:
        """Return the ingredient list for an item, or None if terminal."""
        recipe = self.resolve(item)
        if recipe is None:
            return None
        return recipe.get("ingredients")

    def energy_for(self, item: str) -> float:
        """Craft time in seconds. Returns 0.0 for terminals."""
        recipe = self.resolve(item)
        if recipe is None:
            return 0.0
        return float(recipe.get("energy", 0))

    def raw_resources(self, item: str, amount: int) -> dict[str, int]:
        """Flatten item + amount to raw (terminal) materials.

        Uses _flatten with cycle detection to avoid infinite loops
        (e.g. Kovarex enrichment where uranium-235 is both input and output).
        """
        result: dict[str, float] = defaultdict(float)
        self._flatten(item, amount, result, set())
        # Round up fractional amounts
        return {k: math.ceil(v) for k, v in result.items() if v > 0}

    def _flatten(
        self,
        item: str,
        amount: float,
        accum: dict[str, float],
        visited: set[str],
    ) -> None:
        """Recursive helper: resolve item into raw resources.

        Args:
            item: The item to resolve.
            amount: How many of this item are needed.
            accum: Accumulator for raw resource totals.
            visited: Set of items currently being resolved (cycle detection).
        """
        # Cycle detection: if we're already resolving this item, treat as raw
        if item in visited:
            accum[item] += amount
            return

        recipe = self.resolve(item)
        if recipe is None:
            # Terminal / raw resource
            accum[item] += amount
            return

        visited.add(item)

        yield_count = self.yield_for(item)
        # How many crafts needed to produce `amount` items
        crafts = math.ceil(amount / yield_count)

        for ingredient in recipe.get("ingredients", []):
            ing_name = ingredient["name"]
            ing_amount = ingredient["amount"] * crafts
            self._flatten(ing_name, ing_amount, accum, visited)

        visited.discard(item)

    def update_recipe(self, name: str, data: dict) -> None:
        """In-memory update of a recipe (for RCON sync later).

        Updates both the recipes dict and the reverse index.
        """
        self.recipes[name] = data
        result_item = data.get("result", name)
        self._item_to_recipe[result_item] = data


def sync_recipes(live_recipes: dict[str, dict], dag: "RecipeDAG") -> dict[str, int]:
    """Diff live game recipes against static DAG; patch in-memory DAG.

    Args:
        live_recipes: Recipe dict from RCON (same schema as recipes.json)
        dag: RecipeDAG instance to update in-memory

    Returns:
        {"updated": N, "added": N, "missing": N} change summary
    """
    updated = 0
    added = 0
    missing = 0

    for name, live in live_recipes.items():
        static = dag.recipes.get(name)
        if static is None:
            log.info("RCON sync: new recipe '%s' — adding to in-memory DAG", name)
            dag.update_recipe(name, live)
            added += 1
        elif live != static:
            log.warning("RCON sync: recipe '%s' differs from static JSON — "
                        "updating in-memory DAG", name)
            dag.update_recipe(name, live)
            updated += 1

    for name in dag.recipes:
        if name not in live_recipes:
            log.warning("RCON sync: static recipe '%s' not found in live game", name)
            missing += 1

    return {"updated": updated, "added": added, "missing": missing}
