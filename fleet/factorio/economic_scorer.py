# fleet/factorio/economic_scorer.py
"""Recursive economic scoring model for Factorio RL reward shaping.

Assigns a price to every item based on recipe ingredient costs.
Raw ores get base prices; complex items accumulate ingredient costs
with a complexity multiplier. Production score = sum(price * count)
across all items produced.

Inspired by FLE's recursive price model (NeurIPS 2025).
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Base prices for raw resources (cannot be crafted)
_BASE_PRICES: dict[str, float] = {
    "iron-ore": 1.0,
    "copper-ore": 1.0,
    "coal": 0.5,
    "stone": 0.8,
    "wood": 0.5,
    "water": 0.01,
    "crude-oil": 2.0,
    "uranium-ore": 5.0,
    "raw-fish": 0.5,
}

# Complexity exponent — ingredients cost slightly more than their sum
# to reward building complex items disproportionately
_COMPLEXITY_EXPONENT = 1.1

# Bonus multiplier for items that represent automation milestones
_MILESTONE_MULTIPLIERS: dict[str, float] = {
    "iron-plate": 3.0,                # smelting is THE key behavior to learn
    "copper-plate": 3.0,              # smelting is THE key behavior to learn
    "steel-plate": 5.0,               # advanced smelting
    "stone-brick": 2.0,               # smelting
    "automation-science-pack": 5.0,   # red science — first automation goal
    "logistic-science-pack": 8.0,     # green science — target milestone
    "lab": 10.0,                      # labs are critical infrastructure
    "assembling-machine-1": 3.0,      # automation building block
    "electric-mining-drill": 3.0,     # automation building block
    "steam-engine": 3.0,              # power generation
    "boiler": 2.0,                    # power generation
    "iron-gear-wheel": 2.0,           # key intermediate
    "electronic-circuit": 3.0,        # key intermediate
}


class EconomicScorer:
    """Compute production score using recursive item prices.

    Usage:
        scorer = EconomicScorer("fleet/factorio/data/recipes.json")
        score = scorer.score_production({"iron-plate": 50, "iron-gear-wheel": 10})
        delta = scorer.score_delta(prev_produced, curr_produced)
    """

    def __init__(self, recipes_path: str | None = None) -> None:
        self._prices: dict[str, float] = dict(_BASE_PRICES)
        self._recipes: dict[str, dict] = {}

        if recipes_path is None:
            recipes_path = str(Path(__file__).parent / "data" / "recipes.json")

        try:
            with open(recipes_path, encoding="utf-8") as f:
                self._recipes = json.load(f)
            self._compute_prices()
            log.info("EconomicScorer: %d items priced (from %d recipes)",
                     len(self._prices), len(self._recipes))
        except Exception:
            log.warning("EconomicScorer: failed to load recipes, using base prices only",
                        exc_info=True)

    def _compute_prices(self) -> None:
        """Recursively compute prices for all recipe outputs."""
        # Iterate until no new prices are computed (handles dependency chains)
        changed = True
        iterations = 0
        while changed and iterations < 20:
            changed = False
            iterations += 1
            for recipe_name, recipe in self._recipes.items():
                products = recipe.get("products", [])
                ingredients = recipe.get("ingredients", [])

                for product in products:
                    pname = product["name"]
                    pamount = product.get("amount", 1)
                    if pamount <= 0:
                        continue

                    # Check if all ingredients have prices
                    all_priced = all(
                        ing["name"] in self._prices for ing in ingredients
                    )
                    if not all_priced:
                        continue

                    # Compute cost from ingredients
                    ingredient_cost = sum(
                        self._prices[ing["name"]] * ing["amount"]
                        for ing in ingredients
                    )

                    # Apply complexity exponent — more ingredients = disproportionately valuable
                    num_ingredients = len(ingredients)
                    if num_ingredients > 1:
                        ingredient_cost *= _COMPLEXITY_EXPONENT ** (num_ingredients - 1)

                    # Add energy/time cost (small bonus for slow recipes)
                    energy = recipe.get("energy", 0.5)
                    time_bonus = energy * 0.1

                    # Per-unit price
                    unit_price = (ingredient_cost + time_bonus) / pamount

                    # Milestone multiplier
                    unit_price *= _MILESTONE_MULTIPLIERS.get(pname, 1.0)

                    # Only update if this gives a higher price (some items have multiple recipes)
                    if pname not in self._prices or unit_price > self._prices[pname]:
                        self._prices[pname] = unit_price
                        changed = True

        log.debug("Price computation converged in %d iterations", iterations)

    def get_price(self, item: str) -> float:
        """Get the economic price of an item. Unknown items return 0.1."""
        return self._prices.get(item, 0.1)

    def get_all_prices(self) -> dict[str, float]:
        """Return a copy of all computed prices."""
        return dict(self._prices)

    def score_production(self, produced: dict[str, int | float]) -> float:
        """Compute total production score for a dict of {item: count}."""
        return sum(
            self.get_price(item) * count
            for item, count in produced.items()
        )

    def score_delta(
        self,
        prev_produced: dict[str, int | float],
        curr_produced: dict[str, int | float],
    ) -> float:
        """Compute the production score delta between two snapshots.

        Returns the increase in total production value — always >= 0
        (we don't penalize consumption, only reward production).
        """
        delta = 0.0
        for item, curr_count in curr_produced.items():
            prev_count = prev_produced.get(item, 0)
            if curr_count > prev_count:
                delta += self.get_price(item) * (curr_count - prev_count)
        return delta

    def score_inventory_value(self, inventory: dict[str, int]) -> float:
        """Score the total value of items in inventory."""
        return sum(
            self.get_price(item) * count
            for item, count in inventory.items()
        )
