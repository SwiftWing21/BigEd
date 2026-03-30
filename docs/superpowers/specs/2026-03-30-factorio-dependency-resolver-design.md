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

On bridge connect, an optional `sync_recipes(rcon_client, dag)` function:

1. Calls `game.forces["player"].recipes` via RCON
2. Diffs against static JSON (new recipes, missing recipes, changed ingredients)
3. Logs warnings for mismatches
4. Updates the **in-memory** DAG with any changed ingredient amounts or new recipes (does NOT modify the static JSON file)
5. The static JSON remains the committed baseline; in-memory patches handle mod/version drift

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

**Caller responsibility:** The `entities` parameter is a `dict[str, int]` (name → count). The bridge must convert `state.entities` (a `list[Entity]`) into this format before calling `resolve()`. A helper `entities_to_counts(state.entities) -> dict[str, int]` will be provided in `dependency_resolver.py`.

### Two-Layer Output: Abstract Actions vs Spatial Actions

The resolver produces **abstract actions** — item + count, no spatial coordinates. These are a distinct layer from what `action_translator.py` consumes (which requires positions for `mine` and `place`).

```python
# Resolver output (abstract — no positions):
{"action": "acquire", "item": "stone", "count": 2, "method": "mine"}
{"action": "craft", "recipe": "stone-furnace", "count": 1}
{"action": "smelt", "item": "iron-plate", "count": 4, "fuel": "coal", "fuel_count": 1}

# vs action_translator.py input (spatial — has positions):
{"action": "mine", "position": {"x": 16, "y": -5}}
{"action": "craft", "recipe": "stone-furnace", "count": 1}
{"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}}
```

**Spatial resolution is a separate step.** `to_actions()` produces abstract actions. The teacher/bridge converts `acquire` steps into spatial actions using game state (nearest resource patches for mining, open positions for placement). `craft` actions pass through unchanged since they don't need coordinates.

This separation is intentional: the resolver handles *what* to do (dependency math), while the teacher/bridge handles *where* to do it (spatial planning). The LLM teacher is specifically valuable for the spatial part.

### Algorithm — Backwards Chaining

For each goal item:

1. **Check inventory** — if `have >= need`, skip (already satisfied)
2. **Compute deficit** — `deficit = need - have`
3. **Look up recipe** — `dag.resolve(item)`
4. **If terminal** (no recipe) — emit `acquire` step for deficit amount (method: `mine`)
5. **If craftable** (`category: crafting`) — compute ingredient amounts (respecting yield), recurse on each ingredient
6. **If smelting/chemistry** — check `entities` for required infrastructure (see Infrastructure Resolution below). If missing, resolve the infrastructure item recursively. Then resolve raw ingredients. Emit `smelt` step.
7. **Fuel awareness** — smelting requires fuel. Fuel calculation: `fuel_count = ceil(smelt_count * recipe_energy / fuel_energy)`. Vanilla values: coal = 8 MJ, wood = 4 MJ, solid fuel = 25 MJ. Stone furnace has 1.0 fuel multiplier, steel furnace has 1.0, electric furnace uses electricity (no fuel). The resolver picks the fuel type with the highest count in the current inventory; if none, defaults to coal and adds it to the resolution.
8. **Yield awareness** — recipes like copper-cable (yield 2) require `ceil(deficit / yield)` crafts, not `deficit` crafts.
9. **Inventory deduction** — as the resolver plans crafts, it tracks a running inventory (what the agent will have after executing prior steps) to avoid double-counting.
10. **Cross-goal deduplication** — when multiple goal items share dependencies (e.g., both need iron-plate), the running inventory ensures the second goal sees what the first already resolved. The resolver processes goals sequentially, updating the running inventory after each.
11. **Max depth exceeded** — if recursion exceeds `max_depth`, the step is marked with `unresolved=True` and a reason string. `ResolutionPlan.is_complete()` returns `False` if any step is unresolved. The LLM teacher handles unresolved steps.
12. **Multi-result recipes** — some recipes produce multiple items (e.g., oil processing → heavy/light/petroleum). The resolver identifies which result matches the needed item and calculates craft count based on that result's yield. Byproducts are tracked in the running inventory.

### Data Structures

```python
@dataclass
class ResolutionStep:
    type: str                          # "acquire", "craft", "smelt", "build"
    item: str                          # "stone", "stone-furnace"
    need: int                          # total needed
    have: int                          # already in inventory
    deficit: int                       # need - have
    method: str | None                 # "mine", "gather" for acquire steps
    via_recipe: str | None             # recipe name if crafting/smelting
    infrastructure: str | None         # required entity (e.g. "stone-furnace" for smelting)
    fuel: str | None                   # fuel item needed (e.g. "coal" for smelting)
    fuel_count: int                    # fuel units needed
    unresolved: bool = False           # True if max_depth exceeded or unknown recipe
    unresolved_reason: str | None = None
    children: list[ResolutionStep]     # sub-dependencies

@dataclass
class ResolutionPlan:
    goal: dict[str, int]
    steps: list[ResolutionStep]        # tree roots (one per goal item)

    def is_complete(self) -> bool:
        """True if no steps are unresolved."""

    def to_actions(self) -> list[dict]:
        """Flatten tree into ordered abstract action list via post-order traversal.

        Order: acquire → smelt → craft → build (deepest dependencies first).
        Output is ABSTRACT (no positions). Caller must resolve spatial coords.

        Example output:
            [
                {"action": "acquire", "item": "stone", "count": 2, "method": "mine"},
                {"action": "craft", "recipe": "stone-furnace", "count": 1},
            ]
        """

    def summary(self) -> str:
        """Human-readable summary for LLM prompt injection.

        Example:
            Goal: place 1 stone-furnace
            OK Can craft: stone-furnace (need 5 stone)
            MISSING 2 stone (have 3, need 5) -- must mine
            Action: mine 2 stone, then craft and place furnace.
        """
```

### Infrastructure Resolution

The resolver uses a priority-ordered list per category, checking `entities` for existing infrastructure before building new:

```python
INFRASTRUCTURE_PRIORITY = {
    "smelting": ["electric-furnace", "steel-furnace", "stone-furnace"],
    "chemistry": ["chemical-plant"],
    "centrifuging": ["centrifuge"],
    "oil-processing": ["oil-refinery"],
    "crafting-with-fluid": ["assembling-machine-3", "assembling-machine-2"],
}
```

Resolution logic:
1. Check `entities` for any entity in the priority list — if found, use it (no build step needed)
2. If none found, resolve the **simplest** (last in list) entity for that category (e.g., `stone-furnace` for smelting)
3. The resolved entity becomes a `build` step with its own recursive dependency chain

### Concurrency Note

The resolver operates on a snapshot of inventory/entities. RL ticks may execute concurrently, so the inventory may change between resolution and execution. This is a known limitation — the bridge should re-check inventory before executing each action and re-resolve if state has diverged significantly.

## Component 3: Integration

### Teacher Integration (`bridge.py`)

The resolver plugs into the **existing** hybrid teacher machinery in `bridge.py` (lines 324-376). It does NOT replace the teacher's cooldown timers, lesson step tracking, or RL interleaving. Instead, it augments `_teacher_generate_plan()` — the function called when the stuck threshold is reached.

**Integration point:** Inside `_teacher_generate_plan()` (called at line ~229 when `_teacher_lesson_step_count >= 500`):

```python
async def _teacher_generate_plan(state):
    # NEW: resolve dependencies before calling LLM
    lesson = curriculum.get_current_lesson()
    goal_items = parse_criteria_to_items(lesson.criteria)

    if goal_items:
        entities_count = entities_to_counts(state.entities)
        plan = resolve(goal_items, state.inventory, entities_count, dag)

        if plan.is_complete():
            # All dependencies resolvable — produce abstract actions
            # craft actions execute directly; acquire/build actions
            # need spatial resolution, so pass to LLM with plan context
            abstract = plan.to_actions()
            craft_actions = [a for a in abstract if a["action"] == "craft"]
            spatial_actions = [a for a in abstract if a["action"] != "craft"]

            # Execute craft actions immediately (no spatial coords needed)
            for action in craft_actions:
                await execute_action(action)

            if not spatial_actions:
                return  # fully resolved via crafting alone

            # Spatial actions need LLM — inject plan summary for context
            brain.add_context("dependency_plan", plan.summary())
        else:
            # Partial resolution — inject what we know, let LLM handle rest
            brain.add_context("dependency_plan", plan.summary())

    # Existing LLM plan generation (unchanged)
    await brain.generate_plan(state)
```

The existing cooldown (`_teacher_cooldown = 50`) and step counter still apply. The resolver just makes the teacher smarter about *what* to plan when it fires.

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
- **Cycle test:** Kovarex enrichment recipe, verify no infinite recursion, cyclic input treated as prerequisite
- **Yield test:** Copper cable (yield 2), verify correct craft count (`ceil(need/yield)`)
- **Multi-result test:** Oil processing recipe, verify correct craft count for a specific product and byproduct tracking
- **Fuel calculation test:** Verify coal/wood amounts for N smelts with different furnace types
- **Infrastructure upgrade test:** Verify resolver uses existing steel-furnace instead of building stone-furnace
- **Cross-goal dedup test:** Two goals sharing iron-plate dependency, verify plates resolved once
- **Max depth test:** Deeply nested chain exceeding `max_depth`, verify `unresolved=True` and `is_complete()=False`
- **Criteria parser tests:** Valid criteria, compound AND/OR, non-item criteria (`player.health`), malformed strings
- **Entity conversion test:** `entities_to_counts()` with mixed entity list
- **Integration test:** Wire resolver into teacher, verify it produces valid action sequences for Phase 1 lessons
- **Edge cases:** Empty inventory, goal already satisfied, missing recipe (unknown item), zero-count goal

## Future Extensions

- **Research gating:** Filter recipes by unlocked technologies (RCON sync provides this data)
- **Cost optimization:** When multiple recipes produce the same item, pick the cheapest path
- **Parallel crafting:** Identify independent subtrees that can be crafted simultaneously
- **Fluid handling:** Pipe routing for chemistry recipes (Phase 3+)
