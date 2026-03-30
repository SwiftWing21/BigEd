# Factorio Agent Dependency Resolver — Design Spec

**Date:** 2026-03-30
**Status:** Approved
**Module:** `fleet/factorio/`

## Problem

The Factorio agent's LLM teacher generates action plans without understanding crafting dependencies. When a lesson requires "place a stone furnace" but the agent has insufficient materials, the teacher guesses — often wasting actions crafting wrong items or attempting to place items it doesn't have. The agent needs a backwards-chaining dependency resolver that works from goals to raw resources.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Recipe source | Hybrid: static JSON (Lua data-stage dump) + RCON sync on connect | Fast for planning, validated against live game |
| Resolution depth | Full recursive by default (mine → smelt → craft → place), configurable `max_depth` | Produces complete action chains; depth cap as safety valve |
| Recipe coverage | Full Factorio recipe graph (vanilla + Space Age + mods) | Lua dump captures everything in `data.raw.recipe` |
| Output format | Dependency tree (`ResolutionPlan`) + `.to_actions()` flat list | Tree for teacher reasoning/logging, flat list for execution |
| Architecture | Standalone module (`dependency_resolver.py` + `recipe_dag.py`) | Clean separation, pure functions, easy to test |

## Component 1: Recipe DAG

### Data Source — Lua Dump Script

`fleet/factorio/lua_mod/dump_recipes.lua` runs during Factorio's data-final-fixes stage. Iterates `data.raw.recipe` and writes `fleet/factorio/recipes.json`.

### JSON Schema

```json
{
  "<recipe-name>": {
    "category": "crafting | smelting | chemistry | centrifuging | ...",
    "ingredients": [{"name": "<item>", "amount": <int>, "type": "item | fluid"}],
    "results": [{"name": "<item>", "amount": <int>, "type": "item | fluid"}],
    "energy": <float>,
    "enabled": <bool>
  }
}
```

Example entries:

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
  "copper-cable": {
    "category": "crafting",
    "ingredients": [{"name": "copper-plate", "amount": 1}],
    "results": [{"name": "copper-cable", "amount": 2}],
    "energy": 0.5,
    "enabled": true
  }
}
```

### Terminal Nodes

Items with no recipe entry are terminal — they must be acquired by mining or gathering. Common terminals: `iron-ore`, `copper-ore`, `stone`, `coal`, `wood`, `water`, `crude-oil`. Space Age adds planet-specific terminals (calcite, tungsten-ore, etc.). The resolver treats any item absent from the recipe map as terminal.

### RecipeDAG Class (`recipe_dag.py`)

```python
class RecipeDAG:
    """Directed acyclic graph of Factorio recipes."""

    def __init__(self, recipes_path: str = "fleet/factorio/recipes.json"):
        """Load recipe graph from JSON."""

    def resolve(self, item: str) -> Recipe | None:
        """Single-hop: return recipe for item, or None if terminal."""

    def resolve_recursive(self, item: str, amount: int, max_depth: int = 20) -> DependencyNode:
        """Full chain: recursively resolve all dependencies."""

    def category(self, item: str) -> str | None:
        """Return crafting category (crafting, smelting, chemistry, etc.)."""

    def raw_resources(self, item: str, amount: int) -> dict[str, int]:
        """Flatten: total raw materials needed for N of item."""

    def is_terminal(self, item: str) -> bool:
        """True if item has no recipe (must be mined/gathered)."""

    def infrastructure_for(self, category: str) -> str | None:
        """Map category to required entity: smelting → stone-furnace, etc."""
```

### Cycle Detection

Catalytic recipes (Kovarex enrichment, some Space Age recipes) create cycles where an input item appears in the output. The resolver maintains a `visited: set[str]` during recursion. When a cycle is detected, the cyclic input is treated as a prerequisite ("must already have") rather than recursing further. This prevents infinite loops while correctly modeling that Kovarex needs U-235 to bootstrap.

### RCON Sync

On bridge connect, an optional `sync_recipes(rcon_client)` function:

1. Calls `game.forces["player"].recipes` via RCON
2. Diffs against static JSON (new recipes, missing recipes, changed ingredients)
3. Logs warnings for mismatches
4. Does NOT block or modify the static file — the JSON is authoritative for planning

## Component 2: Dependency Resolver

### Interface (`dependency_resolver.py`)

```python
def resolve(
    goal: dict[str, int],          # {"stone-furnace": 1}
    inventory: dict[str, int],     # {"stone": 3, "iron-plate": 8}
    entities: dict[str, int],      # {"stone-furnace": 1} (placed in world)
    dag: RecipeDAG,
    max_depth: int = 20,
) -> ResolutionPlan
```

### Algorithm — Backwards Chaining

For each goal item:

1. **Check inventory** — if `have >= need`, skip (already satisfied)
2. **Compute deficit** — `deficit = need - have`
3. **Look up recipe** — `dag.resolve(item)`
4. **If terminal** (no recipe) — emit `mine` step for deficit amount
5. **If craftable** (`category: crafting`) — compute ingredient amounts (respecting yield), recurse on each ingredient
6. **If smelting/chemistry** — check `entities` for required infrastructure. If missing, resolve the infrastructure item recursively. Then resolve raw ingredients. Emit `smelt` step.
7. **Fuel awareness** — smelting requires fuel. If no coal/wood in inventory, add to resolution.
8. **Yield awareness** — recipes like copper-cable (yield 2) require `ceil(deficit / yield)` crafts, not `deficit` crafts.
9. **Inventory deduction** — as the resolver plans crafts, it tracks a running inventory (what the agent will have after executing prior steps) to avoid double-counting.

### Data Structures

```python
@dataclass
class ResolutionStep:
    type: str                          # "mine", "craft", "smelt", "place"
    item: str                          # "stone", "stone-furnace"
    need: int                          # total needed
    have: int                          # already in inventory
    deficit: int                       # need - have
    via_recipe: str | None             # recipe name if crafting/smelting
    infrastructure: str | None         # required entity (e.g. "stone-furnace" for smelting)
    children: list[ResolutionStep]     # sub-dependencies

@dataclass
class ResolutionPlan:
    goal: dict[str, int]
    steps: list[ResolutionStep]        # tree roots (one per goal item)

    def is_complete(self) -> bool:
        """True if all steps are resolvable (no unknowns)."""

    def to_actions(self) -> list[dict]:
        """Flatten tree into ordered action list via post-order traversal.

        Order: mine → smelt → craft → place (deepest dependencies first).
        Output format matches existing action_translator.py expectations.
        """

    def summary(self) -> str:
        """Human-readable summary for LLM prompt injection.

        Example:
            Goal: place 1 stone-furnace
            . Can craft: stone-furnace (need 5 stone)
            x Missing: 2 stone (have 3, need 5) -- must mine
            Action: mine 2 stone, then craft and place furnace.
        """
```

### Infrastructure Category Map

```python
INFRASTRUCTURE_MAP = {
    "smelting": "stone-furnace",        # or steel-furnace, electric-furnace
    "chemistry": "chemical-plant",
    "centrifuging": "centrifuge",
    "oil-processing": "oil-refinery",
    "crafting-with-fluid": "assembling-machine-2",
}
```

When the resolver encounters a non-hand-craftable recipe, it checks `entities` for any entity matching the required category. If none exists, it recursively resolves the simplest entity for that category (e.g., `stone-furnace` for smelting, not `electric-furnace`).

## Component 3: Integration

### Teacher Integration (`bridge.py`)

The hybrid teacher calls the resolver before generating LLM plans:

```python
async def _teacher_intervention(state):
    # 1. Parse lesson criteria into item goals
    lesson = curriculum.get_current_lesson()
    goal_items = parse_criteria_to_items(lesson.criteria)

    # 2. Resolve dependencies
    plan = resolve(goal_items, state.inventory, state.entities, dag)

    # 3. If fully resolvable, execute directly (skip LLM)
    if plan.is_complete():
        actions = plan.to_actions()
        for action in actions:
            await execute_action(action)
        return

    # 4. If partial, inject summary into LLM prompt
    brain.add_context("dependency_plan", plan.summary())
    await brain.generate_plan(state)
```

When the plan is fully resolvable (all items can be traced to mineable resources), the teacher bypasses the LLM entirely and executes the action list directly. The LLM is only consulted when spatial decisions are needed (where to move, where to mine, where to place entities).

### Criteria Parser Helper

```python
def parse_criteria_to_items(criteria: str) -> dict[str, int]:
    """Extract item goals from curriculum criteria strings.

    "entities.stone-furnace >= 1"                          -> {"stone-furnace": 1}
    "inventory.iron-gear-wheel >= 4 AND inventory.iron-plate >= 2"
                                                           -> {"iron-gear-wheel": 4, "iron-plate": 2}
    "player.health > 0"                                    -> {}  (not an item goal)
    """
```

Recognizes `inventory.*` and `entities.*` prefixes. Other criteria (player stats, flow rates) are ignored — they require gameplay, not crafting.

### Brain Prompt Injection

When the resolver produces a partial plan, the summary is injected into the LLM prompt:

```
# Dependency Analysis
Goal: place 1 stone-furnace
OK Can craft: stone-furnace (need 5 stone)
MISSING 2 stone (have 3, need 5) — must mine
Recommended: mine 2 stone at nearest stone patch, then craft and place furnace.
```

This gives the LLM structured dependency context instead of forcing it to reason about recipes from memory.

## File Layout

```
fleet/factorio/
  dependency_resolver.py    # resolve(), ResolutionPlan, ResolutionStep, parse_criteria_to_items
  recipe_dag.py             # RecipeDAG class, JSON loader, cycle detection, RCON sync
  recipes.json              # Full Factorio recipe dump (committed, ~2000 recipes)
  lua_mod/
    dump_recipes.lua        # Data-stage script to generate recipes.json
```

## Testing Strategy

- **Unit tests for RecipeDAG:** Load from test fixture JSON, verify resolve/recursive/cycle detection
- **Unit tests for resolver:** Mock inventory + entities, verify correct action chains for known scenarios (craft from scratch, partial inventory, smelting chain, missing infrastructure)
- **Cycle test:** Kovarex enrichment recipe, verify no infinite recursion
- **Yield test:** Copper cable (yield 2), verify correct craft count
- **Integration test:** Wire resolver into teacher, verify it produces valid action sequences for Phase 1 lessons
- **Edge cases:** Empty inventory, goal already satisfied, missing recipe (unknown item), max_depth exceeded

## Future Extensions

- **Research gating:** Filter recipes by unlocked technologies (RCON sync provides this data)
- **Cost optimization:** When multiple recipes produce the same item, pick the cheapest path
- **Parallel crafting:** Identify independent subtrees that can be crafted simultaneously
- **Fluid handling:** Pipe routing for chemistry recipes (Phase 3+)
