"""Dependency resolver — backwards-chains from item goals to raw resources.

Produces a ResolutionPlan tree that can be flattened into ordered action lists.
Uses RecipeDAG for recipe lookups and graph traversal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INFRASTRUCTURE_PRIORITY: dict[str, list[str]] = {
    "smelting": ["electric-furnace", "steel-furnace", "stone-furnace"],
    "chemistry": ["chemical-plant"],
    "centrifuging": ["centrifuge"],
    "oil-processing": ["oil-refinery"],
    "crafting-with-fluid": ["assembling-machine-3", "assembling-machine-2"],
}

FUEL_ENERGY: dict[str, float] = {
    "coal": 8.0,
    "wood": 4.0,
    "solid-fuel": 25.0,
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ResolutionStep:
    """One node in the resolution tree."""

    type: str  # acquire / craft / smelt / build
    item: str
    need: int
    have: int
    deficit: int
    method: str | None = None  # mine, hand-craft, machine, etc.
    via_recipe: str | None = None
    infrastructure: str | None = None
    fuel: str | None = None
    fuel_count: int = 0
    unresolved: bool = False
    unresolved_reason: str | None = None
    children: list[ResolutionStep] = field(default_factory=list)


@dataclass
class ResolutionPlan:
    """Top-level plan wrapping the goal and its resolution tree."""

    goal: str
    steps: list[ResolutionStep] = field(default_factory=list)

    def is_complete(self) -> bool:
        """True if no steps are unresolved."""
        return not _has_unresolved(self.steps)

    def to_actions(self) -> list[dict]:
        """Post-order traversal producing an ordered action list."""
        actions: list[dict] = []
        for step in self.steps:
            _collect_actions(step, actions)
        return actions

    def summary(self) -> str:
        """Human-readable summary of the plan."""
        actions = self.to_actions()
        if not actions:
            return f"{self.goal}: already satisfied"
        lines = [f"{self.goal}: {len(actions)} action(s)"]
        for a in actions:
            lines.append(f"  - {a['action']}: {a.get('item') or a.get('recipe')} x{a['count']}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_unresolved(steps: list[ResolutionStep]) -> bool:
    for s in steps:
        if s.unresolved:
            return True
        if _has_unresolved(s.children):
            return True
    return False


def _collect_actions(step: ResolutionStep, actions: list[dict]) -> None:
    """Post-order: children first, then self."""
    for child in step.children:
        _collect_actions(child, actions)

    # Skip satisfied (deficit <= 0) and unresolved steps
    if step.deficit <= 0 or step.unresolved:
        return

    if step.type == "acquire":
        actions.append({
            "action": "acquire",
            "item": step.item,
            "count": step.deficit,
            "method": step.method or "mine",
        })
    elif step.type == "craft" or step.type == "build":
        actions.append({
            "action": "craft",
            "recipe": step.via_recipe or step.item,
            "count": step.deficit,
        })
    elif step.type == "smelt":
        action: dict = {
            "action": "smelt",
            "item": step.item,
            "count": step.deficit,
        }
        if step.fuel:
            action["fuel"] = step.fuel
            action["fuel_count"] = step.fuel_count
        actions.append(action)


def entities_to_counts(entities) -> dict[str, int]:
    """Convert a list of Entity objects/dicts/already-counts to {name: count}.

    Accepts:
        - list of dicts with 'name' key
        - list of objects with .name attribute
        - dict already in {name: count} form
    """
    if isinstance(entities, dict):
        return dict(entities)
    counts: dict[str, int] = {}
    for e in entities:
        if isinstance(e, dict):
            name = e.get("name", "")
        else:
            name = getattr(e, "name", str(e))
        counts[name] = counts.get(name, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(
    goal: str,
    amount: int,
    inventory: dict[str, int],
    entities,
    dag,
    max_depth: int = 20,
) -> ResolutionPlan:
    """Resolve a goal item+amount into a ResolutionPlan.

    Args:
        goal: Item name to produce.
        amount: How many needed.
        inventory: Current {item: count} inventory (will not be mutated).
        entities: Current placed entities (list or dict).
        dag: RecipeDAG instance.
        max_depth: Recursion limit.

    Returns:
        ResolutionPlan with the resolution tree.
    """
    running_inv = dict(inventory)
    entity_counts = entities_to_counts(entities) if entities else {}
    visited: set[str] = set()
    root = _resolve_item(goal, amount, running_inv, entity_counts, dag, max_depth, visited)
    return ResolutionPlan(goal=goal, steps=[root])


def _resolve_item(
    item: str,
    need: int,
    running_inv: dict[str, int],
    entities: dict[str, int],
    dag,
    depth: int,
    visited: set[str],
) -> ResolutionStep:
    """Recursively resolve one item."""
    have = running_inv.get(item, 0)

    # 1. Already satisfied
    if have >= need:
        running_inv[item] = have - need
        return ResolutionStep(
            type="craft", item=item, need=need, have=have, deficit=0,
            method="inventory",
        )

    deficit = need - have
    # Consume what we have
    running_inv[item] = 0

    # 3. Depth limit
    if depth <= 0:
        return ResolutionStep(
            type="craft", item=item, need=need, have=have, deficit=deficit,
            unresolved=True, unresolved_reason="max depth exceeded",
        )

    # 4. Cycle detection
    if item in visited:
        return ResolutionStep(
            type="craft", item=item, need=need, have=have, deficit=deficit,
            unresolved=True, unresolved_reason="cycle detected",
        )

    # 5. Terminal — must acquire (mine/harvest)
    if dag.is_terminal(item):
        running_inv[item] = running_inv.get(item, 0)  # ensure key exists
        return ResolutionStep(
            type="acquire", item=item, need=need, have=have, deficit=deficit,
            method="mine",
        )

    # 6. Look up recipe
    visited.add(item)
    category = dag.category(item)
    yield_count = dag.yield_for(item)
    ingredients = dag.ingredients_for(item)
    energy = dag.energy_for(item)
    crafts_needed = math.ceil(deficit / yield_count)

    children: list[ResolutionStep] = []

    # 8. Non-hand-craftable — check infrastructure
    if category and category != "crafting":
        _check_infrastructure(category, entities, dag, running_inv, depth, visited, children)

    # 9. Smelting with non-electric furnace — resolve fuel
    fuel_name: str | None = None
    fuel_count: int = 0
    if category == "smelting":
        # Check if we have electric furnace
        has_electric = entities.get("electric-furnace", 0) > 0
        if not has_electric:
            fuel_name, fuel_count = _resolve_fuel(
                crafts_needed, energy, running_inv, dag, depth, visited, children,
            )

    # 10. Recurse on each ingredient
    if ingredients:
        for ing in ingredients:
            ing_name = ing["name"]
            ing_amount = ing["amount"] * crafts_needed
            child = _resolve_item(ing_name, ing_amount, running_inv, entities, dag, depth - 1, visited)
            children.append(child)

    # 11. Determine step type
    if category == "smelting":
        step_type = "smelt"
    elif category and category != "crafting":
        step_type = "craft"
    else:
        step_type = "craft"

    # Update running inventory with produced items
    produced = crafts_needed * yield_count
    surplus = produced - deficit
    running_inv[item] = running_inv.get(item, 0) + surplus

    visited.discard(item)

    return ResolutionStep(
        type=step_type,
        item=item,
        need=need,
        have=have,
        deficit=crafts_needed,  # crafts needed (count for action)
        method="machine" if category and category != "crafting" else "hand-craft",
        via_recipe=item,
        infrastructure=_best_entity(category, entities) if category and category != "crafting" else None,
        fuel=fuel_name,
        fuel_count=fuel_count,
        children=children,
    )


def _check_infrastructure(
    category: str,
    entities: dict[str, int],
    dag,
    running_inv: dict[str, int],
    depth: int,
    visited: set[str],
    children: list[ResolutionStep],
) -> None:
    """Check that required infrastructure exists; if not, build the simplest option."""
    priority = INFRASTRUCTURE_PRIORITY.get(category, [])
    for entity_name in priority:
        if entities.get(entity_name, 0) > 0:
            return  # Already have it

    # None found — build the simplest (last in priority list)
    if not priority:
        return
    simplest = priority[-1]

    # Only build if we have a recipe for it
    if not dag.is_terminal(simplest) and dag.resolve(simplest) is not None:
        child = _resolve_item(simplest, 1, running_inv, entities, dag, depth - 1, visited)
        child.type = "build"
        children.append(child)
        entities[simplest] = 1  # Mark as now available


def _resolve_fuel(
    crafts: int,
    energy_per: float,
    running_inv: dict[str, int],
    dag,
    depth: int,
    visited: set[str],
    children: list[ResolutionStep],
) -> tuple[str | None, int]:
    """Calculate and resolve fuel needs for smelting.

    Returns (fuel_name, fuel_count).
    """
    total_energy = crafts * energy_per
    if total_energy <= 0:
        return None, 0

    # Pick the best fuel we already have, or default to coal
    best_fuel = "coal"
    for fuel_name in ["solid-fuel", "coal", "wood"]:
        if running_inv.get(fuel_name, 0) > 0:
            best_fuel = fuel_name
            break

    fuel_energy = FUEL_ENERGY.get(best_fuel, 8.0)
    fuel_count = math.ceil(total_energy / fuel_energy)

    # Resolve the fuel itself
    child = _resolve_item(best_fuel, fuel_count, running_inv, {}, dag, depth - 1, visited)
    children.append(child)

    return best_fuel, fuel_count


def _best_entity(category: str, entities: dict[str, int]) -> str | None:
    """Return the best available entity for a category."""
    priority = INFRASTRUCTURE_PRIORITY.get(category, [])
    for entity_name in priority:
        if entities.get(entity_name, 0) > 0:
            return entity_name
    return priority[-1] if priority else None
