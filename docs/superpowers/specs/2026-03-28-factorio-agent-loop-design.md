# Factorio Agent Loop — Design Spec

**Date:** 2026-03-28
**Status:** Draft
**Scope:** End-to-end autonomous agent loop + curriculum progression (no human takeover UI this session)

## Summary

Wire the existing Factorio bridge (RCON, state parsing, world model, action translator) to a local Ollama LLM that reasons about game state and produces action plans. The bridge drains plans action-by-action across ticks, re-planning when exhausted or invalidated by world events. A curriculum manager tracks progress through 4 training phases with auto-advance.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Autonomy level | Fully autonomous | Human takeover (pause + directives) deferred to next session |
| LLM backend | Local Ollama (qwen3:8b) | Free, no API cost, already running, sufficient for Factorio reasoning |
| Architecture | Hybrid plan-and-drain | LLM called infrequently (every 5-20 ticks), fast execution between plans, single process |
| Scope | Loop + curriculum | Brain + phase tracking + auto-advance. No takeover UI. |
| Ollama timeout | 60s, retry once | qwen3:8b can be slow on large state prompts |

## Architecture

### Plan-and-Drain Loop

```
Bridge Tick:
  1. RCON → get_state() → GameState
  2. WorldModel.update(state) → events
  3. Has pending plan?
     YES → execute next action from plan
     NO  → call Ollama with state + curriculum context
           → parse JSON action array as new plan
  4. Execute action via RCON → record result
  5. CurriculumManager.check_progress(state)
     → lesson passed? advance lesson
     → phase complete? advance phase (or pause if auto_advance=false)
  6. Update bridge status
  sleep(cadence)
```

### Plan Invalidation

The current plan is discarded and a re-plan triggered when:
- Plan exhausted (all actions executed)
- 3+ consecutive action failures
- World model event: `entity_destroyed`, `power_outage`, `resource_depleted`, `research_complete`

Soft re-plan hint (re-plan after current action, not immediate discard):
- `idle_assemblers` — after 3 consecutive occurrences

On invalidation, the brain includes failure context in the next LLM prompt so it can adjust.

### Command Queue Priority

Human commands injected via the bridge API (`POST /api/command`) always execute before brain actions. This is the foundation for the future takeover feature.

## Components

### AgentBrain (`fleet/factorio/agent_brain.py`)

Core reasoning coordinator. Single class, no threads.

```python
class AgentBrain:
    def __init__(self, config: BridgeConfig, world_model: WorldModel):
        # Loads strategy guide + curriculum
        # Initializes CurriculumManager
        # Tracks current plan, plan index, consecutive failures
        # _ollama_cooldown_until: float = 0 (monotonic timestamp)

    def next_action(self, state: GameState, events: list[GameEvent]) -> TranslatedAction | None:
        # If events trigger invalidation → discard plan
        # If plan has actions remaining → translate_action(raw_dict) and return
        # If plan empty and not in cooldown → call _generate_plan(state)
        # translate_action() converts raw LLM dict → TranslatedAction
        # Return TranslatedAction or None on failure/cooldown

    def report_result(self, action: TranslatedAction, result: dict) -> None:
        # Track success/failure
        # On 3+ consecutive failures → invalidate plan

    def check_progress(self, state: GameState) -> dict:
        # Converts GameState → flat eval dict via flatten_state()
        # Delegates to CurriculumManager.check_progress(eval_dict)
        # Returns {lesson_passed, phase_complete, progress}

    def get_plan_status(self) -> dict:
        # Returns {plan: [...], plan_index, planning: bool}
        # Used by bridge_api /api/plan endpoint

    def _generate_plan(self, state: GameState) -> list[dict]:
        # Assemble prompt (system + state + instruction)
        # POST to Ollama /api/generate (timeout=60s)
        # Parse JSON from response, strip markdown fences
        # Retry once on parse failure
        # On connection failure: set _ollama_cooldown_until = now + 30s
        # Return action list or empty list

    def _build_prompt(self, state: GameState) -> tuple[str, str]:
        # Returns (system_prompt, user_prompt)
        # system: action schema, decision framework, common mistakes
        # user: state markdown, current objective, last plan results
```

**State flattening for curriculum evaluation:**
`flatten_state(state: GameState) -> dict` converts the typed GameState into the dict shape `evaluate_criteria` expects:
```python
def flatten_state(state: GameState) -> dict:
    # Count entities by name: {"stone-furnace": 3, "inserter": 5, ...}
    entity_counts = {}
    for e in state.entities:
        entity_counts[e.name] = entity_counts.get(e.name, 0) + 1
    return {
        "inventory": dict(state.inventory),
        "entities": entity_counts,
        "research": {"name": state.research_name, "progress": state.research_progress},
    }
```
This is a module-level function in `agent_brain.py`, used by both `check_progress` and `_build_prompt`.

**Ollama HTTP call:**
- `POST http://localhost:11434/api/generate`
- Body: `{"model": config.ollama_model, "prompt": user_prompt, "system": system_prompt, "stream": false}`
- Uses `urllib.request.urlopen()` with `timeout=60`
- Parses `response["response"]` field for the JSON action array

**Ollama error handling:**
- `ConnectionRefusedError` / `URLError`: log warning, set cooldown (30s), return empty plan
- During cooldown: `next_action()` returns `None` without calling Ollama
- On startup: optionally check `GET /api/tags` to verify model is available (non-blocking, warn-only)

### CurriculumManager (`fleet/factorio/curriculum_manager.py`)

Wraps the existing `LessonTracker` and `evaluate_criteria` with phase lifecycle.

```python
class CurriculumManager:
    def __init__(self, config: BridgeConfig):
        # Load curriculum TOML for current_phase
        # Initialize LessonTracker with lesson count

    def get_current_objective(self) -> dict:
        # Returns {phase, lesson_name, criteria, description, hint}
        # Used by AgentBrain to build the LLM prompt

    def check_progress(self, state_dict: dict) -> dict:
        # Evaluate current lesson criteria against state
        # If passed: mark lesson, advance
        # If all lessons passed: phase_complete=True
        # Returns {lesson_passed, phase_complete, progress}

    def advance_phase(self) -> bool:
        # Increment phase, load next TOML
        # Returns False if already at phase 4 (done)

    def get_progress(self) -> dict:
        # Full progress snapshot for dashboard/logging
        # {phase, lesson, total_lessons, attempts, completed_phases}
```

### Curriculum TOMLs (`fleet/factorio/curricula/`)

**Directory:** `fleet/factorio/curricula/` (new directory, separate from `fleet/idle_curricula/` which is for fleet worker idle tasks). `CurriculumManager` has its own TOML loader — does NOT reuse `curriculum.py:load_curriculum()` since the TOML schema differs (meta section + lessons array vs. flat task lists). It does reuse `evaluate_criteria()` and `LessonTracker` from `curriculum.py`.

**File naming:** `phase{N}_{name}.toml` — e.g., `phase1_bootstrap.toml`. `CurriculumManager` locates the file by scanning for `phase{N}_*.toml` in the curricula directory.

Each phase is a TOML file with ordered lessons:

```toml
[meta]
phase = 1
name = "Bootstrap"
description = "Hand-craft basics, place first furnaces, establish smelting"

[[lessons]]
name = "Craft iron gear wheels"
description = "Craft 10 iron gear wheels from starting materials"
criteria = "inventory.iron-gear-wheel >= 10"
hint = "Use craft action with recipe=iron-gear-wheel, count=10"
max_attempts = 20

[[lessons]]
name = "Place stone furnaces"
description = "Place at least 3 stone furnaces near iron ore"
criteria = "entities.stone-furnace >= 3"
hint = "Place furnaces on empty ground near iron ore patches"
max_attempts = 30
```

**Phase 1 — Bootstrap:** Hand-craft gear wheels, place furnaces, start smelting iron
**Phase 2 — Automate Smelting:** Build power (boiler chain), electric miners, belt-fed smelting line
**Phase 3 — First Science:** Assemblers for gears + red science, labs, start automation research
**Phase 4 — Expand:** Electronic circuits, green science, scale production

Exact lesson criteria will be defined during implementation based on what's achievable by the LLM.

### Bridge Modifications (`fleet/factorio/bridge.py`)

**`__init__`:** Add `self.brain = AgentBrain(config, self.world_model)`

**`tick()` step 5 restructure:**
```python
# 5a. Drain human command queue first (priority)
while not self.command_queue.empty():
    # ... existing command execution logic (unchanged) ...

# 5b. Ask brain for next autonomous action
# brain.next_action() may call Ollama (blocking, up to 60s).
# Use asyncio.to_thread() to avoid blocking the event loop.
if self.command_queue.empty():
    action = await asyncio.to_thread(self.brain.next_action, state, events)
    if action and action.rcon_command:
        try:
            cmd_json = action.rcon_command.split(" ", 1)[1]
            resp = await self.rcon.remote_call("exec_cmd", cmd_json)
            result = json.loads(resp)
        except Exception as e:
            result = {"error": str(e), "success": False}
        self.brain.report_result(action, result)

# 5c. Check curriculum progress
progress = self.brain.check_progress(state)
if progress.get("lesson_passed"):
    log.info("Lesson passed: %s", progress.get("lesson_name"))
if progress.get("phase_complete"):
    log.info("Phase %d complete!", progress.get("phase"))
    if self.config.auto_advance:
        self.brain.curriculum.advance_phase()
```

Note: `asyncio.to_thread()` runs the synchronous `next_action` (which may block on `urllib.request.urlopen`) in a thread pool, keeping the event loop responsive for RCON and cadence timing.

### Config Additions (`fleet/factorio/bridge_config.py`)

New fields on `BridgeConfig`:
```python
ollama_url: str = "http://localhost:11434"
ollama_model: str = "qwen3:8b"
ollama_timeout: int = 60
plan_max_actions: int = 20
plan_invalidation_failures: int = 3
ollama_cooldown_secs: int = 30
```

**Existing fields used** (already in BridgeConfig, no changes needed):
- `current_phase: int = 1` — which curriculum phase to start on
- `auto_advance: bool = True` — auto-advance between phases
- `curriculum_dir: str = "fleet/idle_curricula"` — NOT used by CurriculumManager (it uses `fleet/factorio/curricula/` hardcoded)

### API Addition (`fleet/factorio/bridge_api.py`)

**Signature change:** `create_api(world_model, command_queue, brain)` — adds `brain` parameter (AgentBrain instance).

New endpoint:
```python
@app.route("/api/plan")
def api_plan():
    # Calls brain.get_plan_status() + brain.curriculum.get_progress()
    # Returns {plan: [...actions], plan_index, phase, lesson, progress}
```

## LLM Prompt Design

### System Prompt (~500 tokens, static)

```
You are a Factorio automation agent controlling a factory through commands.
Respond with ONLY a valid JSON array of action objects. No markdown, no explanation.

Available actions:
- {"action": "place", "entity": "<name>", "position": {"x": N, "y": N}, "direction": "north|east|south|west"}  // Tries to place at an exact spot. May fail if blocked.
- {"action": "place_near", "entity": "<name>", "near_position": {"x": N, "y": N}, "direction": "north|east|south|west"} // Finds a valid empty spot near the position and places the entity there. More reliable.
- {"action": "craft", "recipe": "<name>", "count": N}
- {"action": "research", "technology": "<name>"}
- {"action": "move", "position": {"x": N, "y": N}}
- {"action": "set_recipe", "unit_number": N, "recipe": "<name>"}
- {"action": "connect", "entity": "transport-belt", "from": {"x": N, "y": N}, "to": {"x": N, "y": N}}
- {"action": "remove", "unit_number": N}
- {"action": "wait", "ticks": N}

Decision priority:
1. Fix bottlenecks (idle assemblers, full outputs)
2. Maintain power (build power if none or low)
3. Advance toward current objective
- Use `place_near` for building, as it is more reliable than `place`.
4. Optimize layout

Rules:
- Inserters pick from BEHIND, drop in FRONT (direction matters!)
- Always set_recipe on assemblers after placing
- Check inventory before placing — you can't place what you don't have
- Keep builds compact to minimize belt length
- Electric miners/assemblers need power to work
```

### User Prompt (dynamic, per plan)

```
# Current Factory State
{state_to_markdown output}

# Current Objective
Phase {N}: {phase_name}
Lesson: {lesson_name} — {lesson_description}
Success criteria: {criteria}
Hint: {hint}

# Previous Plan Results
{action results summary or "First plan — no previous results"}

Generate 5-20 actions to work toward the objective.
```

### Response Parsing

1. Strip leading/trailing whitespace
2. Remove markdown fences (` ```json ... ``` `)
3. `json.loads()` → expect list of dicts
4. Validate each dict has `"action"` key in KNOWN_ACTIONS
5. Cap at `config.plan_max_actions`
6. On parse failure: retry once with a shorter prompt ("Respond with ONLY a JSON array")

## File Summary

| File | Status | Purpose |
|------|--------|---------|
| `fleet/factorio/agent_brain.py` | **New** | Plan-and-drain loop, Ollama calls, prompt assembly |
| `fleet/factorio/curriculum_manager.py` | **New** | Phase lifecycle, lesson tracking, criteria evaluation |
| `fleet/factorio/curricula/phase1_bootstrap.toml` | **New** | Bootstrap lessons |
| `fleet/factorio/curricula/phase2_automate.toml` | **New** | Automation lessons |
| `fleet/factorio/curricula/phase3_science.toml` | **New** | Red science lessons |
| `fleet/factorio/curricula/phase4_expand.toml` | **New** | Expansion lessons |
| `fleet/factorio/bridge.py` | **Modify** | Integrate AgentBrain into tick loop |
| `fleet/factorio/bridge_config.py` | **Modify** | Add Ollama + plan config fields |
| `fleet/factorio/bridge_api.py` | **Modify** | Add /api/plan endpoint |

## Out of Scope (Future Sessions)

- **Human takeover UI** — pause/resume, direct commands, directive nudges (D mode)
- **Dashboard enrichment** — plan visualization, curriculum progress panel
- **Fleet task dispatch** — routing Factorio reasoning through fleet workers
- **API model escalation** — tiered routing (local for routine, API for strategy)
- **Multi-agent** — multiple bots in one world
