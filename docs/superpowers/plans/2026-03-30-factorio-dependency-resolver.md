# Factorio Dependency Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backwards-chaining dependency resolver that converts lesson goals into ordered action sequences by traversing a Factorio recipe DAG.

**Architecture:** Standalone `recipe_dag.py` (loads recipes.json, graph traversal, cycle detection) + `dependency_resolver.py` (backwards chaining, inventory math, abstract action output). Plugs into the existing hybrid teacher in `bridge.py` — resolver runs before LLM, bypasses LLM for pure-craft goals.

**Tech Stack:** Python 3.12, pytest, TOML curricula, JSON recipe data, Lua data-stage dump

**Spec:** `docs/superpowers/specs/2026-03-30-factorio-dependency-resolver-design.md`

---

### Task 1: Recipe DAG — Data Structures and JSON Loader

**Files:**
- Create: `fleet/factorio/recipe_dag.py`
- Create: `fleet/factorio/recipes.json` (minimal Phase 1 starter — ~30 recipes)
- Test: `tests/factorio/test_recipe_dag.py`

- [ ] **Step 1: Write test for JSON loading and single-hop resolve**

```python
# tests/factorio/test_recipe_dag.py
"""Tests for RecipeDAG — Factorio recipe graph."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fleet"))

import pytest

FIXTURE = {
    "stone-furnace": {
        "category": "crafting",
        "ingredients": [{"name": "stone", "amount": 5}],
        "results": [{"name": "stone-furnace", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
    "iron-plate": {
        "category": "smelting",
        "ingredients": [{"name": "iron-ore", "amount": 1}],
        "results": [{"name": "iron-plate", "amount": 1}],
        "energy": 3.2,
        "enabled": True
    },
    "iron-gear-wheel": {
        "category": "crafting",
        "ingredients": [{"name": "iron-plate", "amount": 2}],
        "results": [{"name": "iron-gear-wheel", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
    "copper-cable": {
        "category": "crafting",
        "ingredients": [{"name": "copper-plate", "amount": 1}],
        "results": [{"name": "copper-cable", "amount": 2}],
        "energy": 0.5,
        "enabled": True
    },
    "electronic-circuit": {
        "category": "crafting",
        "ingredients": [
            {"name": "iron-plate", "amount": 1},
            {"name": "copper-cable", "amount": 3}
        ],
        "results": [{"name": "electronic-circuit", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
    "copper-plate": {
        "category": "smelting",
        "ingredients": [{"name": "copper-ore", "amount": 1}],
        "results": [{"name": "copper-plate", "amount": 1}],
        "energy": 3.2,
        "enabled": True
    },
}


@pytest.fixture
def fixture_path(tmp_path):
    p = tmp_path / "recipes.json"
    p.write_text(json.dumps(FIXTURE))
    return str(p)


def test_load_from_json(fixture_path):
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    assert len(dag._recipes) == 6


def test_resolve_single_hop(fixture_path):
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    recipe = dag.resolve("stone-furnace")
    assert recipe is not None
    assert recipe["category"] == "crafting"
    assert recipe["ingredients"][0]["name"] == "stone"


def test_resolve_terminal_returns_none(fixture_path):
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    assert dag.resolve("iron-ore") is None
    assert dag.resolve("stone") is None


def test_is_terminal(fixture_path):
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    assert dag.is_terminal("iron-ore") is True
    assert dag.is_terminal("stone-furnace") is False


def test_category(fixture_path):
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    assert dag.category("iron-plate") == "smelting"
    assert dag.category("stone-furnace") == "crafting"
    assert dag.category("unknown-item") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_recipe_dag.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'factorio.recipe_dag'`

- [ ] **Step 3: Write RecipeDAG class**

```python
# fleet/factorio/recipe_dag.py
"""Factorio recipe DAG — load recipes, resolve dependencies, detect cycles."""
import json
import logging
import math
from pathlib import Path

log = logging.getLogger("biged.factorio.recipe_dag")


class RecipeDAG:
    """Directed acyclic graph of Factorio recipes.

    Loads from a JSON file (dumped by Lua data-stage script).
    Each key is a recipe name mapping to category, ingredients, results, energy.
    """

    def __init__(self, recipes_path: str | None = None):
        if recipes_path is None:
            recipes_path = str(Path(__file__).parent / "recipes.json")
        with open(recipes_path) as f:
            self._recipes: dict[str, dict] = json.load(f)
        # Build item→recipe reverse index (which recipe produces this item?)
        self._item_to_recipe: dict[str, str] = {}
        for recipe_name, recipe in self._recipes.items():
            for result in recipe.get("results", []):
                # First recipe wins for an item (can be overridden by RCON sync)
                if result["name"] not in self._item_to_recipe:
                    self._item_to_recipe[result["name"]] = recipe_name

    def resolve(self, item: str) -> dict | None:
        """Single-hop: return recipe that produces item, or None if terminal."""
        recipe_name = self._item_to_recipe.get(item)
        if recipe_name is None:
            return None
        return self._recipes.get(recipe_name)

    def is_terminal(self, item: str) -> bool:
        """True if item has no recipe (must be mined/gathered)."""
        return item not in self._item_to_recipe

    def category(self, item: str) -> str | None:
        """Return crafting category for the recipe that produces item."""
        recipe = self.resolve(item)
        if recipe is None:
            return None
        return recipe.get("category")

    def yield_for(self, item: str) -> int:
        """How many of item does one craft produce? Default 1."""
        recipe = self.resolve(item)
        if recipe is None:
            return 1
        for result in recipe.get("results", []):
            if result["name"] == item:
                return result.get("amount", 1)
        return 1

    def ingredients_for(self, item: str) -> list[dict] | None:
        """Return ingredient list for the recipe that produces item."""
        recipe = self.resolve(item)
        if recipe is None:
            return None
        return recipe.get("ingredients", [])

    def energy_for(self, item: str) -> float:
        """Return crafting energy (seconds) for recipe producing item."""
        recipe = self.resolve(item)
        if recipe is None:
            return 0.0
        return recipe.get("energy", 0.0)

    def raw_resources(self, item: str, amount: int) -> dict[str, int]:
        """Flatten: total raw materials needed for N of item."""
        result: dict[str, int] = {}
        visited: set[str] = set()
        self._flatten(item, amount, result, visited)
        return result

    def _flatten(self, item: str, amount: int, out: dict[str, int],
                 visited: set[str]) -> None:
        if item in visited:
            return
        if self.is_terminal(item):
            out[item] = out.get(item, 0) + amount
            return
        visited.add(item)
        recipe_yield = self.yield_for(item)
        crafts = math.ceil(amount / recipe_yield)
        ingredients = self.ingredients_for(item)
        if ingredients:
            for ing in ingredients:
                self._flatten(ing["name"], ing["amount"] * crafts, out,
                              visited.copy())  # copy to allow shared ingredients
        visited.discard(item)

    def update_recipe(self, recipe_name: str, recipe_data: dict) -> None:
        """Update an in-memory recipe (used by RCON sync). Does not write to disk."""
        self._recipes[recipe_name] = recipe_data
        for result in recipe_data.get("results", []):
            self._item_to_recipe[result["name"]] = recipe_name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_recipe_dag.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/recipe_dag.py tests/factorio/test_recipe_dag.py
git commit -m "feat(factorio): RecipeDAG — JSON loader, single-hop resolve, terminal detection"
```

---

### Task 2: Recipe DAG — Yield, Raw Resources, Cycle Detection

**Files:**
- Modify: `fleet/factorio/recipe_dag.py`
- Modify: `tests/factorio/test_recipe_dag.py`

- [ ] **Step 1: Write tests for yield, raw_resources, and cycle detection**

Add to `tests/factorio/test_recipe_dag.py`:

```python
def test_yield_for_copper_cable(fixture_path):
    """Copper cable yields 2 per craft."""
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    assert dag.yield_for("copper-cable") == 2


def test_yield_for_terminal(fixture_path):
    """Terminal items have yield 1 (default)."""
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    assert dag.yield_for("iron-ore") == 1


def test_raw_resources_stone_furnace(fixture_path):
    """Stone furnace needs 5 stone."""
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    raw = dag.raw_resources("stone-furnace", 1)
    assert raw == {"stone": 5}


def test_raw_resources_iron_gear_wheel(fixture_path):
    """4 iron gear wheels need 8 iron-ore (4*2 iron-plate, each from 1 iron-ore)."""
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    raw = dag.raw_resources("iron-gear-wheel", 4)
    assert raw == {"iron-ore": 8}


def test_raw_resources_electronic_circuit(fixture_path):
    """1 electronic circuit = 1 iron-plate + 3 copper-cable (2 copper-plate).
    Raw: 1 iron-ore + 2 copper-ore."""
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(fixture_path)
    raw = dag.raw_resources("electronic-circuit", 1)
    assert raw == {"iron-ore": 1, "copper-ore": 2}


CYCLE_FIXTURE = {
    "kovarex-enrichment": {
        "category": "centrifuging",
        "ingredients": [
            {"name": "uranium-235", "amount": 40},
            {"name": "uranium-238", "amount": 5}
        ],
        "results": [
            {"name": "uranium-235", "amount": 41},
            {"name": "uranium-238", "amount": 2}
        ],
        "energy": 60.0,
        "enabled": True
    },
}


@pytest.fixture
def cycle_path(tmp_path):
    p = tmp_path / "cycle_recipes.json"
    p.write_text(json.dumps(CYCLE_FIXTURE))
    return str(p)


def test_cycle_detection_no_infinite_loop(cycle_path):
    """Kovarex creates a cycle (u-235 input and output). Must not infinite loop."""
    from factorio.recipe_dag import RecipeDAG
    dag = RecipeDAG(cycle_path)
    # Should return without hanging — cycle breaks at visited check
    raw = dag.raw_resources("uranium-235", 1)
    # uranium-235 is both input and output — cycle detected, treated as terminal
    assert isinstance(raw, dict)
```

- [ ] **Step 2: Run tests to verify new tests pass (implementation from Task 1 covers these)**

Run: `cd fleet && python -m pytest ../tests/factorio/test_recipe_dag.py -v`
Expected: All 11 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/factorio/test_recipe_dag.py
git commit -m "test(factorio): RecipeDAG yield, raw resources, cycle detection tests"
```

---

### Task 3: Dependency Resolver — Core Data Structures

**Files:**
- Create: `fleet/factorio/dependency_resolver.py`
- Create: `tests/factorio/test_dependency_resolver.py`

- [ ] **Step 1: Write tests for ResolutionStep and ResolutionPlan basics**

```python
# tests/factorio/test_dependency_resolver.py
"""Tests for Factorio dependency resolver."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fleet"))

import pytest

# Reuse the fixture from test_recipe_dag
FIXTURE = {
    "stone-furnace": {
        "category": "crafting",
        "ingredients": [{"name": "stone", "amount": 5}],
        "results": [{"name": "stone-furnace", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
    "iron-plate": {
        "category": "smelting",
        "ingredients": [{"name": "iron-ore", "amount": 1}],
        "results": [{"name": "iron-plate", "amount": 1}],
        "energy": 3.2,
        "enabled": True
    },
    "iron-gear-wheel": {
        "category": "crafting",
        "ingredients": [{"name": "iron-plate", "amount": 2}],
        "results": [{"name": "iron-gear-wheel", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
    "copper-cable": {
        "category": "crafting",
        "ingredients": [{"name": "copper-plate", "amount": 1}],
        "results": [{"name": "copper-cable", "amount": 2}],
        "energy": 0.5,
        "enabled": True
    },
    "copper-plate": {
        "category": "smelting",
        "ingredients": [{"name": "copper-ore", "amount": 1}],
        "results": [{"name": "copper-plate", "amount": 1}],
        "energy": 3.2,
        "enabled": True
    },
    "electronic-circuit": {
        "category": "crafting",
        "ingredients": [
            {"name": "iron-plate", "amount": 1},
            {"name": "copper-cable", "amount": 3}
        ],
        "results": [{"name": "electronic-circuit", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
}


@pytest.fixture
def dag(tmp_path):
    p = tmp_path / "recipes.json"
    p.write_text(json.dumps(FIXTURE))
    from factorio.recipe_dag import RecipeDAG
    return RecipeDAG(str(p))


def test_resolve_already_satisfied(dag):
    """Goal already in inventory — no steps needed."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"stone-furnace": 1}, {"stone-furnace": 1}, {}, dag)
    assert plan.is_complete()
    assert plan.to_actions() == []


def test_resolve_simple_craft(dag):
    """Craft stone-furnace when we have enough stone."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"stone-furnace": 1}, {"stone": 5}, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    assert len(actions) >= 1
    craft = [a for a in actions if a["action"] == "craft"]
    assert any(a["recipe"] == "stone-furnace" for a in craft)


def test_resolve_mine_then_craft(dag):
    """Need stone-furnace with no stone — must mine first."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"stone-furnace": 1}, {}, {}, dag)
    assert plan.is_complete()
    actions = plan.to_actions()
    # Mine must come before craft
    acquire_idx = next(i for i, a in enumerate(actions) if a["action"] == "acquire")
    craft_idx = next(i for i, a in enumerate(actions) if a["action"] == "craft")
    assert acquire_idx < craft_idx


def test_resolve_partial_inventory(dag):
    """Have 3 stone, need 5 for furnace — mine only 2."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"stone-furnace": 1}, {"stone": 3}, {}, dag)
    actions = plan.to_actions()
    acquire = [a for a in actions if a["action"] == "acquire" and a["item"] == "stone"]
    assert acquire[0]["count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_dependency_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'factorio.dependency_resolver'`

- [ ] **Step 3: Write dependency_resolver.py with core resolve logic**

```python
# fleet/factorio/dependency_resolver.py
"""Factorio dependency resolver — backwards-chaining from goals to raw resources."""
import logging
import math
from dataclasses import dataclass, field

log = logging.getLogger("biged.factorio.dependency_resolver")

# Infrastructure priority: check entities for existing, build simplest if missing
INFRASTRUCTURE_PRIORITY = {
    "smelting": ["electric-furnace", "steel-furnace", "stone-furnace"],
    "chemistry": ["chemical-plant"],
    "centrifuging": ["centrifuge"],
    "oil-processing": ["oil-refinery"],
    "crafting-with-fluid": ["assembling-machine-3", "assembling-machine-2"],
}

# Fuel energy values (MJ)
FUEL_ENERGY = {
    "coal": 8.0,
    "wood": 4.0,
    "solid-fuel": 25.0,
}


@dataclass
class ResolutionStep:
    type: str                              # "acquire", "craft", "smelt", "build"
    item: str
    need: int
    have: int
    deficit: int
    method: str | None = None              # "mine" for acquire steps
    via_recipe: str | None = None
    infrastructure: str | None = None
    fuel: str | None = None
    fuel_count: int = 0
    unresolved: bool = False
    unresolved_reason: str | None = None
    children: list["ResolutionStep"] = field(default_factory=list)


@dataclass
class ResolutionPlan:
    goal: dict[str, int]
    steps: list[ResolutionStep] = field(default_factory=list)

    def is_complete(self) -> bool:
        """True if no steps are unresolved."""
        return not any(self._walk_unresolved(self.steps))

    def _walk_unresolved(self, steps):
        for s in steps:
            if s.unresolved:
                yield s
            yield from self._walk_unresolved(s.children)

    def to_actions(self) -> list[dict]:
        """Flatten tree into ordered abstract action list (post-order)."""
        actions: list[dict] = []
        for step in self.steps:
            self._flatten_step(step, actions)
        return actions

    def _flatten_step(self, step: ResolutionStep, out: list[dict]) -> None:
        # Children first (post-order)
        for child in step.children:
            self._flatten_step(child, out)

        if step.deficit <= 0:
            return
        if step.unresolved:
            return

        if step.type == "acquire":
            out.append({
                "action": "acquire",
                "item": step.item,
                "count": step.deficit,
                "method": step.method or "mine",
            })
        elif step.type == "craft":
            out.append({
                "action": "craft",
                "recipe": step.item,
                "count": step.deficit,
            })
        elif step.type == "smelt":
            out.append({
                "action": "smelt",
                "item": step.item,
                "count": step.deficit,
                "fuel": step.fuel,
                "fuel_count": step.fuel_count,
            })
        elif step.type == "build":
            out.append({
                "action": "craft",
                "recipe": step.item,
                "count": step.deficit,
            })

    def summary(self) -> str:
        """Human-readable summary for LLM prompt injection."""
        lines = []
        for item, count in self.goal.items():
            lines.append(f"Goal: {count}x {item}")
        for step in self.steps:
            self._summarize_step(step, lines, indent=0)
        return "\n".join(lines)

    def _summarize_step(self, step: ResolutionStep, lines: list[str],
                        indent: int) -> None:
        prefix = "  " * indent
        if step.deficit <= 0:
            lines.append(f"{prefix}OK {step.item}: have {step.have} (need {step.need})")
        elif step.unresolved:
            lines.append(f"{prefix}UNRESOLVED {step.item}: {step.unresolved_reason}")
        else:
            lines.append(
                f"{prefix}NEED {step.type} {step.deficit}x {step.item}"
                f" (have {step.have}, need {step.need})"
            )
        for child in step.children:
            self._summarize_step(child, lines, indent + 1)


def entities_to_counts(entities) -> dict[str, int]:
    """Convert a list of Entity objects to {name: count} dict.

    Accepts either a list of objects with a .name attribute, or a list of dicts
    with a 'name' key, or an already-converted dict.
    """
    if isinstance(entities, dict):
        return entities
    counts: dict[str, int] = {}
    for e in entities:
        name = e.name if hasattr(e, "name") else e.get("name", "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts


def resolve(
    goal: dict[str, int],
    inventory: dict[str, int],
    entities: dict[str, int],
    dag,
    max_depth: int = 20,
) -> ResolutionPlan:
    """Resolve item goals into a dependency tree.

    Args:
        goal: Items needed, e.g. {"stone-furnace": 1}
        inventory: Current inventory snapshot
        entities: Placed entities {name: count}
        dag: RecipeDAG instance
        max_depth: Max recursion depth (safety cap)

    Returns:
        ResolutionPlan with tree structure + to_actions() for flat list.
    """
    plan = ResolutionPlan(goal=goal)
    # Running inventory tracks what the agent will have after planned steps
    running_inv = dict(inventory)

    for item, count in goal.items():
        step = _resolve_item(item, count, running_inv, entities, dag,
                             max_depth, visited=set())
        plan.steps.append(step)
        # Update running inventory after resolving this goal
        if step.deficit > 0 and not step.unresolved:
            running_inv[item] = running_inv.get(item, 0) + step.deficit

    return plan


def _resolve_item(
    item: str,
    need: int,
    running_inv: dict[str, int],
    entities: dict[str, int],
    dag,
    depth: int,
    visited: set[str],
) -> ResolutionStep:
    have = running_inv.get(item, 0)

    # Already satisfied
    if have >= need:
        return ResolutionStep(type="craft", item=item, need=need,
                              have=have, deficit=0)

    deficit = need - have

    # Depth exceeded
    if depth <= 0:
        return ResolutionStep(
            type="acquire", item=item, need=need, have=have, deficit=deficit,
            unresolved=True, unresolved_reason=f"max depth exceeded for {item}",
        )

    # Cycle detection
    if item in visited:
        return ResolutionStep(
            type="acquire", item=item, need=need, have=have, deficit=deficit,
            unresolved=True,
            unresolved_reason=f"cycle detected: {item} (must already have)",
        )

    # Terminal item — must mine
    if dag.is_terminal(item):
        step = ResolutionStep(
            type="acquire", item=item, need=need, have=have,
            deficit=deficit, method="mine",
        )
        # Update running inventory
        running_inv[item] = running_inv.get(item, 0) + deficit
        return step

    visited.add(item)
    category = dag.category(item)
    recipe_yield = dag.yield_for(item)
    crafts_needed = math.ceil(deficit / recipe_yield)
    ingredients = dag.ingredients_for(item) or []

    children = []

    # Infrastructure check for non-hand-craftable recipes
    infra_entity = None
    if category and category != "crafting":
        infra_entity = _check_infrastructure(category, entities, dag,
                                             running_inv, depth - 1,
                                             visited.copy(), children)

    # Fuel check for smelting
    fuel_item = None
    fuel_count = 0
    if category == "smelting" and infra_entity != "electric-furnace":
        fuel_item, fuel_count = _resolve_fuel(
            crafts_needed, dag.energy_for(item), running_inv, dag,
            depth - 1, visited.copy(), children)

    # Resolve each ingredient
    for ing in ingredients:
        ing_need = ing["amount"] * crafts_needed
        child = _resolve_item(ing["name"], ing_need, running_inv, entities,
                              dag, depth - 1, visited.copy())
        children.append(child)

    # Determine step type
    if category == "smelting":
        step_type = "smelt"
    elif category and category != "crafting":
        step_type = "smelt"  # chemistry, centrifuging — similar process
    else:
        step_type = "craft"

    step = ResolutionStep(
        type=step_type,
        item=item,
        need=need,
        have=have,
        deficit=deficit,
        via_recipe=item,
        infrastructure=infra_entity,
        fuel=fuel_item,
        fuel_count=fuel_count,
        children=children,
    )

    # Update running inventory with crafted items
    running_inv[item] = running_inv.get(item, 0) + deficit
    # Track byproducts for multi-result recipes
    recipe = dag.resolve(item)
    if recipe:
        for result in recipe.get("results", []):
            if result["name"] != item:
                byproduct_count = result.get("amount", 1) * crafts_needed
                running_inv[result["name"]] = (
                    running_inv.get(result["name"], 0) + byproduct_count
                )

    visited.discard(item)
    return step


def _check_infrastructure(category, entities, dag, running_inv, depth,
                          visited, children):
    """Check if required infrastructure exists, resolve if not."""
    priority = INFRASTRUCTURE_PRIORITY.get(category, [])
    # Check existing entities
    for entity_name in priority:
        if entities.get(entity_name, 0) > 0:
            return entity_name
    # Need to build the simplest one (last in priority list)
    if priority:
        build_target = priority[-1]
        build_step = _resolve_item(build_target, 1, running_inv, entities,
                                   dag, depth, visited)
        build_step.type = "build"
        children.append(build_step)
        return build_target
    return None


def _resolve_fuel(crafts, energy_per, running_inv, dag, depth, visited,
                  children):
    """Resolve fuel for smelting. Returns (fuel_item, fuel_count)."""
    # Pick fuel the agent has most of; default to coal
    best_fuel = "coal"
    best_count = 0
    for fuel, _ in FUEL_ENERGY.items():
        have = running_inv.get(fuel, 0)
        if have > best_count:
            best_fuel = fuel
            best_count = have

    fuel_energy = FUEL_ENERGY.get(best_fuel, 8.0)
    total_energy = crafts * energy_per
    fuel_needed = math.ceil(total_energy / fuel_energy)

    have_fuel = running_inv.get(best_fuel, 0)
    if have_fuel < fuel_needed:
        fuel_deficit = fuel_needed - have_fuel
        fuel_step = _resolve_item(best_fuel, fuel_needed, running_inv, {},
                                  dag, depth, visited)
        children.append(fuel_step)

    return best_fuel, fuel_needed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_dependency_resolver.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/dependency_resolver.py tests/factorio/test_dependency_resolver.py
git commit -m "feat(factorio): dependency resolver — backwards chaining, inventory math, abstract actions"
```

---

### Task 4: Resolver — Advanced Tests (Yield, Smelting, Infrastructure, Cross-Goal)

**Files:**
- Modify: `tests/factorio/test_dependency_resolver.py`

- [ ] **Step 1: Write advanced tests**

Add to `tests/factorio/test_dependency_resolver.py`:

```python
def test_resolve_yield_awareness(dag):
    """Copper cable yields 2 — needing 6 should produce 3 crafts."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"copper-cable": 6}, {"copper-plate": 10}, {}, dag)
    actions = plan.to_actions()
    craft = [a for a in actions if a["action"] == "craft" and a["recipe"] == "copper-cable"]
    assert len(craft) == 1
    assert craft[0]["count"] == 6  # deficit is 6 items


def test_resolve_smelting_chain(dag):
    """Iron gear wheel needs iron-plate (smelting) needs iron-ore (mining)."""
    from factorio.dependency_resolver import resolve
    plan = resolve(
        {"iron-gear-wheel": 2},
        {},
        {"stone-furnace": 1},  # furnace exists
        dag,
    )
    actions = plan.to_actions()
    action_types = [a["action"] for a in actions]
    # Should have: acquire iron-ore, smelt iron-plate, craft gear
    assert "acquire" in action_types
    assert "smelt" in action_types
    assert "craft" in action_types


def test_resolve_builds_infrastructure(dag):
    """If no furnace exists, resolver adds build step for stone-furnace."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"iron-plate": 1}, {}, {}, dag)
    actions = plan.to_actions()
    # Should include crafting a stone-furnace (build step becomes craft action)
    craft_recipes = [a["recipe"] for a in actions if a["action"] == "craft"]
    assert "stone-furnace" in craft_recipes


def test_resolve_uses_existing_infrastructure(dag):
    """If steel-furnace exists, don't build stone-furnace."""
    from factorio.dependency_resolver import resolve
    plan = resolve(
        {"iron-plate": 1},
        {"iron-ore": 10, "coal": 10},
        {"steel-furnace": 1},
        dag,
    )
    actions = plan.to_actions()
    craft_recipes = [a.get("recipe") for a in actions if a["action"] == "craft"]
    assert "stone-furnace" not in craft_recipes


def test_resolve_cross_goal_dedup(dag):
    """Two goals sharing iron-plate: second goal sees first's resolution."""
    from factorio.dependency_resolver import resolve
    plan = resolve(
        {"iron-gear-wheel": 1, "electronic-circuit": 1},
        {"iron-ore": 20, "copper-ore": 20, "coal": 20},
        {"stone-furnace": 1},
        dag,
    )
    actions = plan.to_actions()
    # Should not double-resolve iron-ore mining
    acquire_iron = [a for a in actions
                    if a["action"] == "acquire" and a["item"] == "iron-ore"]
    # Total iron-ore needed: 2 (gear) + 1 (circuit) = 3, but we have 20
    # So no acquire needed at all
    assert len(acquire_iron) == 0


def test_resolve_max_depth_exceeded(dag):
    """Hitting max_depth marks steps as unresolved."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"electronic-circuit": 1}, {}, {}, dag, max_depth=1)
    assert not plan.is_complete()


def test_resolve_empty_goal(dag):
    """Empty goal produces empty plan."""
    from factorio.dependency_resolver import resolve
    plan = resolve({}, {}, {}, dag)
    assert plan.is_complete()
    assert plan.to_actions() == []


def test_plan_summary(dag):
    """Summary produces readable text."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"stone-furnace": 1}, {"stone": 3}, {}, dag)
    summary = plan.summary()
    assert "stone-furnace" in summary
    assert "stone" in summary


def test_resolve_zero_count_goal(dag):
    """Zero-count goal produces no actions."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"stone-furnace": 0}, {}, {}, dag)
    assert plan.is_complete()
    assert plan.to_actions() == []


def test_resolve_unknown_item(dag):
    """Unknown item (not in any recipe, not in inventory) becomes an acquire step."""
    from factorio.dependency_resolver import resolve
    plan = resolve({"mystery-widget": 3}, {}, {}, dag)
    actions = plan.to_actions()
    assert len(actions) == 1
    assert actions[0]["action"] == "acquire"
    assert actions[0]["item"] == "mystery-widget"
    assert actions[0]["count"] == 3


def test_resolve_multi_result_recipe():
    """Multi-result recipe: byproducts tracked in running inventory."""
    import json
    import tempfile
    from pathlib import Path

    multi_fixture = {
        "advanced-oil-processing": {
            "category": "oil-processing",
            "ingredients": [{"name": "crude-oil", "amount": 100}, {"name": "water", "amount": 50}],
            "results": [
                {"name": "heavy-oil", "amount": 25},
                {"name": "light-oil", "amount": 45},
                {"name": "petroleum-gas", "amount": 55}
            ],
            "energy": 5.0,
            "enabled": True
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(multi_fixture, f)
        path = f.name

    from factorio.recipe_dag import RecipeDAG
    from factorio.dependency_resolver import resolve
    dag = RecipeDAG(path)
    plan = resolve(
        {"petroleum-gas": 55},
        {"crude-oil": 1000, "water": 1000},
        {"oil-refinery": 1},
        dag,
    )
    assert plan.is_complete()
    Path(path).unlink()
```

- [ ] **Step 2: Run all tests**

Run: `cd fleet && python -m pytest ../tests/factorio/test_dependency_resolver.py -v`
Expected: All 12 tests PASS

- [ ] **Step 3: Fix any failures, then commit**

```bash
git add tests/factorio/test_dependency_resolver.py
git commit -m "test(factorio): advanced resolver tests — yield, smelting, infrastructure, cross-goal dedup"
```

---

### Task 5: Criteria Parser + Entity Converter

**Files:**
- Modify: `fleet/factorio/dependency_resolver.py`
- Create: `tests/factorio/test_criteria_parser.py`

- [ ] **Step 1: Write tests for parse_criteria_to_items**

```python
# tests/factorio/test_criteria_parser.py
"""Tests for curriculum criteria → item goal parser."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fleet"))


def test_parse_single_entity():
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items("entities.stone-furnace >= 1")
    assert result == {"stone-furnace": 1}


def test_parse_single_inventory():
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items("inventory.iron-gear-wheel >= 4")
    assert result == {"iron-gear-wheel": 4}


def test_parse_compound_and():
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items(
        "inventory.iron-gear-wheel >= 4 AND inventory.iron-plate >= 2"
    )
    assert result == {"iron-gear-wheel": 4, "iron-plate": 2}


def test_parse_non_item_criteria():
    """player.health and flow criteria are not item goals."""
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items("player.health > 0")
    assert result == {}


def test_parse_mixed_criteria():
    """Mix of item and non-item criteria."""
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items(
        "player.alive >= 1 AND inventory.iron-plate >= 30"
    )
    assert result == {"iron-plate": 30}


def test_parse_or_criteria():
    """OR criteria: take all branches (resolver handles which to pursue)."""
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items(
        "inventory.iron-ore >= 5 OR inventory.copper-ore >= 5"
    )
    # OR: take the first branch for goal extraction
    assert "iron-ore" in result or "copper-ore" in result


def test_parse_resources_criteria():
    """resources.* criteria are not directly craftable — skip."""
    from factorio.dependency_resolver import parse_criteria_to_items
    result = parse_criteria_to_items("resources.iron-ore > 0")
    assert result == {}


def test_entities_to_counts_dict_passthrough():
    """Already a dict — pass through."""
    from factorio.dependency_resolver import entities_to_counts
    d = {"stone-furnace": 1}
    assert entities_to_counts(d) == d


def test_entities_to_counts_list_of_dicts():
    """List of dicts with 'name' key."""
    from factorio.dependency_resolver import entities_to_counts
    entities = [{"name": "stone-furnace"}, {"name": "stone-furnace"}, {"name": "inserter"}]
    result = entities_to_counts(entities)
    assert result == {"stone-furnace": 2, "inserter": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_criteria_parser.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_criteria_to_items'`

- [ ] **Step 3: Add parse_criteria_to_items to dependency_resolver.py**

Add to end of `fleet/factorio/dependency_resolver.py`:

```python
import re

_ITEM_CRITERIA_RE = re.compile(
    r"(inventory|entities)\.([\w\-]+)\s*>=\s*(\d+)"
)


def parse_criteria_to_items(criteria: str) -> dict[str, int]:
    """Extract item goals from curriculum criteria strings.

    Recognizes inventory.* and entities.* with >= operator.
    Other criteria (player.*, resources.*, flow.*) are ignored.
    For OR criteria, extracts from the first branch only.
    """
    # For OR, take first branch
    first_branch = criteria.split(" OR ")[0].strip()

    result: dict[str, int] = {}
    for match in _ITEM_CRITERIA_RE.finditer(first_branch):
        _section, item_name, amount_str = match.groups()
        result[item_name] = int(amount_str)

    # Also check remaining OR branches for AND clauses
    # (but first branch is primary for goal extraction)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_criteria_parser.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/dependency_resolver.py tests/factorio/test_criteria_parser.py
git commit -m "feat(factorio): criteria parser + entities_to_counts helper"
```

---

### Task 6: Starter recipes.json

**Files:**
- Create: `fleet/factorio/recipes.json`

- [ ] **Step 1: Write Phase 1-2 recipes JSON**

Create `fleet/factorio/recipes.json` with the ~30 most common vanilla Factorio recipes needed for Phase 1-2 (bootstrap + automation). This is a starter file — the full dump from Lua comes in Task 8.

```json
{
  "stone-furnace": {
    "category": "crafting",
    "ingredients": [{"name": "stone", "amount": 5}],
    "results": [{"name": "stone-furnace", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "iron-plate": {
    "category": "smelting",
    "ingredients": [{"name": "iron-ore", "amount": 1}],
    "results": [{"name": "iron-plate", "amount": 1}],
    "energy": 3.2,
    "enabled": true
  },
  "copper-plate": {
    "category": "smelting",
    "ingredients": [{"name": "copper-ore", "amount": 1}],
    "results": [{"name": "copper-plate", "amount": 1}],
    "energy": 3.2,
    "enabled": true
  },
  "steel-plate": {
    "category": "smelting",
    "ingredients": [{"name": "iron-plate", "amount": 5}],
    "results": [{"name": "steel-plate", "amount": 1}],
    "energy": 16.0,
    "enabled": true
  },
  "iron-gear-wheel": {
    "category": "crafting",
    "ingredients": [{"name": "iron-plate", "amount": 2}],
    "results": [{"name": "iron-gear-wheel", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "copper-cable": {
    "category": "crafting",
    "ingredients": [{"name": "copper-plate", "amount": 1}],
    "results": [{"name": "copper-cable", "amount": 2}],
    "energy": 0.5,
    "enabled": true
  },
  "electronic-circuit": {
    "category": "crafting",
    "ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}],
    "results": [{"name": "electronic-circuit", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "transport-belt": {
    "category": "crafting",
    "ingredients": [{"name": "iron-gear-wheel", "amount": 1}, {"name": "iron-plate", "amount": 1}],
    "results": [{"name": "transport-belt", "amount": 2}],
    "energy": 0.5,
    "enabled": true
  },
  "inserter": {
    "category": "crafting",
    "ingredients": [{"name": "electronic-circuit", "amount": 1}, {"name": "iron-gear-wheel", "amount": 1}, {"name": "iron-plate", "amount": 1}],
    "results": [{"name": "inserter", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "burner-inserter": {
    "category": "crafting",
    "ingredients": [{"name": "iron-gear-wheel", "amount": 1}, {"name": "iron-plate", "amount": 1}],
    "results": [{"name": "burner-inserter", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "burner-mining-drill": {
    "category": "crafting",
    "ingredients": [{"name": "iron-gear-wheel", "amount": 3}, {"name": "iron-plate", "amount": 3}, {"name": "stone-furnace", "amount": 1}],
    "results": [{"name": "burner-mining-drill", "amount": 1}],
    "energy": 2.0,
    "enabled": true
  },
  "electric-mining-drill": {
    "category": "crafting",
    "ingredients": [{"name": "electronic-circuit", "amount": 3}, {"name": "iron-gear-wheel", "amount": 5}, {"name": "iron-plate", "amount": 10}],
    "results": [{"name": "electric-mining-drill", "amount": 1}],
    "energy": 2.0,
    "enabled": true
  },
  "assembling-machine-1": {
    "category": "crafting",
    "ingredients": [{"name": "electronic-circuit", "amount": 3}, {"name": "iron-gear-wheel", "amount": 5}, {"name": "iron-plate", "amount": 9}],
    "results": [{"name": "assembling-machine-1", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "small-electric-pole": {
    "category": "crafting",
    "ingredients": [{"name": "wood", "amount": 1}, {"name": "copper-cable", "amount": 2}],
    "results": [{"name": "small-electric-pole", "amount": 2}],
    "energy": 0.5,
    "enabled": true
  },
  "pipe": {
    "category": "crafting",
    "ingredients": [{"name": "iron-plate", "amount": 1}],
    "results": [{"name": "pipe", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "offshore-pump": {
    "category": "crafting",
    "ingredients": [{"name": "electronic-circuit", "amount": 2}, {"name": "iron-gear-wheel", "amount": 1}, {"name": "pipe", "amount": 1}],
    "results": [{"name": "offshore-pump", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "boiler": {
    "category": "crafting",
    "ingredients": [{"name": "stone-furnace", "amount": 1}, {"name": "pipe", "amount": 4}],
    "results": [{"name": "boiler", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "steam-engine": {
    "category": "crafting",
    "ingredients": [{"name": "iron-gear-wheel", "amount": 8}, {"name": "iron-plate", "amount": 10}, {"name": "pipe", "amount": 5}],
    "results": [{"name": "steam-engine", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "wooden-chest": {
    "category": "crafting",
    "ingredients": [{"name": "wood", "amount": 2}],
    "results": [{"name": "wooden-chest", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "iron-chest": {
    "category": "crafting",
    "ingredients": [{"name": "iron-plate", "amount": 8}],
    "results": [{"name": "iron-chest", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "automation-science-pack": {
    "category": "crafting",
    "ingredients": [{"name": "copper-plate", "amount": 1}, {"name": "iron-gear-wheel", "amount": 1}],
    "results": [{"name": "automation-science-pack", "amount": 1}],
    "energy": 5.0,
    "enabled": true
  },
  "logistic-science-pack": {
    "category": "crafting",
    "ingredients": [{"name": "inserter", "amount": 1}, {"name": "transport-belt", "amount": 1}],
    "results": [{"name": "logistic-science-pack", "amount": 1}],
    "energy": 6.0,
    "enabled": true
  },
  "lab": {
    "category": "crafting",
    "ingredients": [{"name": "electronic-circuit", "amount": 10}, {"name": "iron-gear-wheel", "amount": 10}, {"name": "transport-belt", "amount": 4}],
    "results": [{"name": "lab", "amount": 1}],
    "energy": 2.0,
    "enabled": true
  },
  "firearm-magazine": {
    "category": "crafting",
    "ingredients": [{"name": "iron-plate", "amount": 4}],
    "results": [{"name": "firearm-magazine", "amount": 1}],
    "energy": 1.0,
    "enabled": true
  },
  "gun-turret": {
    "category": "crafting",
    "ingredients": [{"name": "copper-plate", "amount": 10}, {"name": "iron-gear-wheel", "amount": 10}, {"name": "iron-plate", "amount": 20}],
    "results": [{"name": "gun-turret", "amount": 1}],
    "energy": 8.0,
    "enabled": true
  },
  "stone-wall": {
    "category": "crafting",
    "ingredients": [{"name": "stone-brick", "amount": 5}],
    "results": [{"name": "stone-wall", "amount": 1}],
    "energy": 0.5,
    "enabled": true
  },
  "stone-brick": {
    "category": "smelting",
    "ingredients": [{"name": "stone", "amount": 2}],
    "results": [{"name": "stone-brick", "amount": 1}],
    "energy": 3.2,
    "enabled": true
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/recipes.json
git commit -m "data(factorio): starter recipes.json — 27 vanilla Phase 1-2 recipes"
```

---

### Task 7: Lua Dump Script

**Files:**
- Create: `fleet/factorio/lua_mod/dump_recipes.lua`

- [ ] **Step 1: Write the Lua data-stage dump script**

```lua
-- fleet/factorio/lua_mod/dump_recipes.lua
-- Run during Factorio data-final-fixes to dump all recipes to JSON.
-- Usage: Copy to mods/<your-mod>/data-final-fixes.lua (or require from it).
-- Output: recipes_dump.json in Factorio's script-output directory.
--
-- After running, copy the output to fleet/factorio/recipes.json.

local function dump_recipes()
    local out = {}
    for name, recipe in pairs(data.raw.recipe) do
        local entry = {
            category = recipe.category or "crafting",
            energy = recipe.energy_required or 0.5,
            enabled = recipe.enabled ~= false,  -- default true
            ingredients = {},
            results = {},
        }

        -- Normalize ingredients (handle both old and new format)
        local ingredients = recipe.ingredients or {}
        for _, ing in ipairs(ingredients) do
            if ing.name then
                -- New format: {name="iron-plate", amount=1, type="item"}
                table.insert(entry.ingredients, {
                    name = ing.name,
                    amount = ing.amount or 1,
                    type = ing.type or "item",
                })
            elseif ing[1] then
                -- Old format: {"iron-plate", 1}
                table.insert(entry.ingredients, {
                    name = ing[1],
                    amount = ing[2] or 1,
                    type = "item",
                })
            end
        end

        -- Normalize results
        if recipe.results then
            for _, res in ipairs(recipe.results) do
                if res.name then
                    table.insert(entry.results, {
                        name = res.name,
                        amount = res.amount or 1,
                        type = res.type or "item",
                    })
                elseif res[1] then
                    table.insert(entry.results, {
                        name = res[1],
                        amount = res[2] or 1,
                        type = "item",
                    })
                end
            end
        elseif recipe.result then
            -- Single-result shorthand
            table.insert(entry.results, {
                name = recipe.result,
                amount = recipe.result_count or 1,
                type = "item",
            })
        else
            -- Recipe name is the result
            table.insert(entry.results, {
                name = name,
                amount = 1,
                type = "item",
            })
        end

        out[name] = entry
    end

    -- Write to script-output (accessible after game loads)
    -- Note: In data stage we can't write files, so log it instead
    -- The actual file write happens via a control-stage script
    log("BIGED_RECIPE_DUMP: " .. serpent.line(out))
end

dump_recipes()
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/lua_mod/dump_recipes.lua
git commit -m "feat(factorio): Lua data-stage recipe dump script"
```

---

### Task 8: RCON Sync — Validate Static Recipes Against Live Game

**Files:**
- Modify: `fleet/factorio/recipe_dag.py`
- Create: `tests/factorio/test_rcon_sync.py`

- [ ] **Step 1: Write tests for sync_recipes**

```python
# tests/factorio/test_rcon_sync.py
"""Tests for RCON recipe sync."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fleet"))

import pytest


FIXTURE = {
    "stone-furnace": {
        "category": "crafting",
        "ingredients": [{"name": "stone", "amount": 5}],
        "results": [{"name": "stone-furnace", "amount": 1}],
        "energy": 0.5,
        "enabled": True
    },
}


@pytest.fixture
def dag(tmp_path):
    p = tmp_path / "recipes.json"
    p.write_text(json.dumps(FIXTURE))
    from factorio.recipe_dag import RecipeDAG
    return RecipeDAG(str(p))


def test_sync_no_changes(dag):
    """When live recipes match static, no updates."""
    from factorio.recipe_dag import sync_recipes
    live = {
        "stone-furnace": {
            "category": "crafting",
            "ingredients": [{"name": "stone", "amount": 5}],
            "results": [{"name": "stone-furnace", "amount": 1}],
            "energy": 0.5,
            "enabled": True,
        }
    }
    changes = sync_recipes(live, dag)
    assert changes["updated"] == 0
    assert changes["added"] == 0


def test_sync_ingredient_change(dag):
    """Changed ingredient amount updates in-memory DAG."""
    from factorio.recipe_dag import sync_recipes
    live = {
        "stone-furnace": {
            "category": "crafting",
            "ingredients": [{"name": "stone", "amount": 10}],  # changed from 5 to 10
            "results": [{"name": "stone-furnace", "amount": 1}],
            "energy": 0.5,
            "enabled": True,
        }
    }
    changes = sync_recipes(live, dag)
    assert changes["updated"] == 1
    # In-memory DAG should reflect the change
    recipe = dag.resolve("stone-furnace")
    assert recipe["ingredients"][0]["amount"] == 10


def test_sync_new_recipe(dag):
    """New recipe from live game is added to in-memory DAG."""
    from factorio.recipe_dag import sync_recipes
    live = {
        "stone-furnace": FIXTURE["stone-furnace"],
        "iron-plate": {
            "category": "smelting",
            "ingredients": [{"name": "iron-ore", "amount": 1}],
            "results": [{"name": "iron-plate", "amount": 1}],
            "energy": 3.2,
            "enabled": True,
        },
    }
    changes = sync_recipes(live, dag)
    assert changes["added"] == 1
    assert dag.resolve("iron-plate") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/factorio/test_rcon_sync.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync_recipes'`

- [ ] **Step 3: Implement sync_recipes in recipe_dag.py**

Add to `fleet/factorio/recipe_dag.py`:

```python
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
        static = dag._recipes.get(name)
        if static is None:
            log.info("RCON sync: new recipe '%s' — adding to in-memory DAG", name)
            dag.update_recipe(name, live)
            added += 1
        elif live != static:
            log.warning("RCON sync: recipe '%s' differs from static JSON — "
                        "updating in-memory DAG", name)
            dag.update_recipe(name, live)
            updated += 1

    for name in dag._recipes:
        if name not in live_recipes:
            log.warning("RCON sync: static recipe '%s' not found in live game", name)
            missing += 1

    return {"updated": updated, "added": added, "missing": missing}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/factorio/test_rcon_sync.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/recipe_dag.py tests/factorio/test_rcon_sync.py
git commit -m "feat(factorio): RCON recipe sync — diff live game against static DAG"
```

---

### Task 9: Bridge Integration — Teacher + Brain Context

**Files:**
- Modify: `fleet/factorio/bridge.py` (lines ~229-257)
- Modify: `fleet/factorio/agent_brain.py` (add `add_context` method)

- [ ] **Step 1: Write integration test**

Add to `tests/factorio/test_dependency_resolver.py`:

```python
def test_brain_add_context():
    """AgentBrain.add_context stores context for prompt injection."""
    from unittest.mock import MagicMock
    from factorio.agent_brain import AgentBrain
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel

    config = BridgeConfig()
    wm = WorldModel()
    brain = AgentBrain(config, wm, curricula_dir="fleet/factorio/curricula")
    brain.add_context("dependency_plan", "test summary")
    assert brain._extra_context.get("dependency_plan") == "test summary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/factorio/test_dependency_resolver.py::test_brain_add_context -v`
Expected: FAIL — `AttributeError: 'AgentBrain' object has no attribute 'add_context'`

- [ ] **Step 3: Add add_context method to AgentBrain**

Add to `fleet/factorio/agent_brain.py` in `AgentBrain.__init__`:

```python
self._extra_context: dict[str, str] = {}
```

Add method to `AgentBrain`:

```python
def add_context(self, key: str, value: str) -> None:
    """Store extra context to inject into the next plan generation prompt."""
    self._extra_context[key] = value

def pop_context(self, key: str) -> str | None:
    """Pop extra context (consumed after one use)."""
    return self._extra_context.pop(key, None)
```

- [ ] **Step 4: Inject context into _generate_plan prompt**

In `fleet/factorio/agent_brain.py`, find the `_generate_plan` method where it builds the prompt. After the objective/hint section, inject extra context:

```python
# After objective section in prompt building:
extra = self.pop_context("dependency_plan")
if extra:
    prompt_parts.append(f"\n# Dependency Analysis\n{extra}\n")
```

The exact location depends on the current prompt construction in `_generate_plan`. Read the method to find where to inject.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/factorio/test_dependency_resolver.py::test_brain_add_context -v`
Expected: PASS

- [ ] **Step 6: Add resolver call to bridge.py _teacher_generate_plan**

In `fleet/factorio/bridge.py`, modify `_teacher_generate_plan` (line ~229) to call the resolver before LLM generation:

```python
async def _teacher_generate_plan(self, state) -> list[dict]:
    """Background task: ask LLM to generate an action plan."""
    objective = self._curriculum.get_current_objective()
    hint = objective.get("hint", "")
    lesson = objective.get("lesson_name", "?")
    log.info("Teacher thinking about lesson '%s' — hint: %s", lesson, hint)

    try:
        # NEW: resolve dependencies before LLM
        from factorio.dependency_resolver import (
            resolve, parse_criteria_to_items, entities_to_counts,
        )
        from factorio.recipe_dag import RecipeDAG

        criteria = objective.get("criteria", "")
        goal_items = parse_criteria_to_items(criteria)

        if goal_items:
            dag = RecipeDAG()  # loads fleet/factorio/recipes.json
            flat_entities = entities_to_counts(
                state.entities if hasattr(state, "entities") else {}
            )
            plan = resolve(goal_items, dict(state.inventory), flat_entities, dag)

            if plan.is_complete():
                # Execute craft actions directly (no spatial coords needed)
                actions = plan.to_actions()
                craft_actions = [a for a in actions if a["action"] == "craft"]
                if craft_actions and not any(
                    a["action"] != "craft" for a in actions
                ):
                    log.info("Resolver fully resolved %d craft actions, skipping LLM",
                             len(craft_actions))
                    return craft_actions

            # Inject summary for LLM context
            self.brain.add_context("dependency_plan", plan.summary())

        # Existing LLM plan generation
        self.brain.curriculum._phase = self._curriculum._phase
        self.brain.curriculum._tracker = self._curriculum._tracker
        self.brain.curriculum._lessons = self._curriculum._lessons
        self.brain.curriculum._meta = self._curriculum._meta

        plan_actions = await asyncio.get_event_loop().run_in_executor(
            None, self.brain._generate_plan, state)
        if plan_actions:
            log.info("Teacher generated %d actions for '%s'", len(plan_actions), lesson)
        else:
            log.warning("Teacher produced no plan for '%s'", lesson)
        return plan_actions or []
    except Exception:
        log.warning("Teacher plan generation failed", exc_info=True)
        return []
```

- [ ] **Step 7: Run full Factorio test suite**

Run: `cd fleet && python -m pytest ../tests/factorio/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add fleet/factorio/bridge.py fleet/factorio/agent_brain.py tests/factorio/test_dependency_resolver.py
git commit -m "feat(factorio): wire dependency resolver into hybrid teacher

Resolver runs before LLM in _teacher_generate_plan. Pure-craft goals
bypass LLM entirely. Partial resolutions inject summary into brain
context for better LLM planning."
```

---

### Task 10: Run Full Test Suite + Smoke Test

**Files:** None (verification only)

- [ ] **Step 1: Run Factorio tests**

Run: `cd fleet && python -m pytest ../tests/factorio/ -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 2: Run fleet smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All smoke tests PASS (no regressions)

- [ ] **Step 3: Run the root-level Factorio tests too**

Run: `cd fleet && python -m pytest ../tests/ -v -k "factorio or recipe_dag or dependency_resolver or criteria_parser"`
Expected: All tests PASS

- [ ] **Step 4: Commit any fixes if needed, then final commit**

```bash
git add fleet/factorio/recipe_dag.py fleet/factorio/dependency_resolver.py fleet/factorio/bridge.py fleet/factorio/agent_brain.py tests/factorio/
git commit -m "test(factorio): all dependency resolver tests passing, no regressions"
```

---

**Note on test files:** The existing `tests/factorio/conftest.py` adds `fleet/` to `sys.path` automatically. The `sys.path.insert` lines in each test file are technically redundant but included for standalone execution (`python -m pytest tests/factorio/test_recipe_dag.py` from repo root).
