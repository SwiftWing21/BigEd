# Factorio Sandbox Module — Design Spec

**Date:** 2026-03-27
**Status:** Draft
**Version:** 0.1.0
**Module:** `factorio-sandbox`

## Overview

A BigEd module that lets fleet agents play Factorio autonomously through a training sandbox that graduates into full autonomous play. The module ships as a self-contained package via the Module Hub, keeping all Factorio-specific code isolated from the core fleet.

### Goals

1. Train BigEd agents in Factorio through a 4-phase curriculum (learn → complete goals → optimize KPIs → survive under pressure)
2. Ship as a module via Module Hub with proper install/enable/disable UX
3. Support multi-PC fleet distribution (bridge on game host, reasoning on any node)
4. Provide a spectator mode for watching agents play in real-time
5. Pause normal fleet operations when Factorio sandbox is active (dedicated focus)

### Non-Goals (Initial Scope)

- No `GameEnvironment` ABC — Factorio only, abstract when game #2 arrives
- No general Factorio mod manager — we install our one mod
- No replay viewer — watch live via spectator or read JSONL logs
- No blueprint string import/export — agent builds from scratch (that's the point)

---

## Architecture

### Approach: Fat Module with Bridge Service

A dedicated long-running bridge process maintains persistent game state, runs the perception-reason-act loop, and exposes an internal API for fleet workers. The launcher module provides UI (status, cadence slider, curriculum panel, Lua install settings). Skills are thin wrappers that call into the bridge.

```
┌─────────────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│  Fleet Workers      │     │  Factorio Bridge     │     │  Factorio Headless   │
│  (LLM reasoning)    │     │  (Python, long-run)  │     │  + Lua Mod           │
│                     │     │                      │     │                      │
│  factorio_observe ──┼────>│  WorldModel (mem)    │     │  /biged-state        │
│  factorio_plan   ──┼────>│  CadenceController   │<───>│  /biged-metrics      │
│  factorio_act    ──┼────>│  CommandQueue         │RCON │  /biged-cmd          │
│  factorio_train  ──┼────>│  EventDetector        │     │  /biged-observe      │
└─────────────────────┘     └─────────────────────┘     └──────────────────────┘
```

**Why this approach over alternatives:**

- **vs. Thin Module (skill suite only):** Skills are stateless — each invocation starts from a fresh state read. No persistent world model, no coherent multi-step strategy. Factorio is a deeply stateful game; stateless calls can't play it well.
- **vs. Game Environment ABC:** Premature abstraction. Factorio's tick-based deterministic sim is nothing like Minecraft's real-time voxel world. The ABC would be wrong on game #2 anyway. Build the concrete thing first.

---

## Module Structure & Packaging

```
BigEd/launcher/modules/mod_factorio.py         # Launcher tab UI

fleet/modules/factorio/                         # All fleet-side code (isolated)
├── __init__.py                                 # Module registration
├── bridge.py                                   # Long-running bridge process
├── rcon_client.py                              # Async RCON protocol client
├── state_parser.py                             # Raw JSON → structured GameState
├── action_translator.py                        # Agent actions → RCON commands
├── config.py                                   # BridgeConfig dataclass
├── curriculum.py                               # Training progression engine
├── world_model.py                              # Persistent in-memory world state
├── lua_installer.py                            # Lua mod install modes (manual/assisted/managed)
└── lua_mod/                                    # The Factorio Lua mod (verbatim)
    ├── info.json
    └── control.lua

fleet/skills/factorio_observe.py                # Skill: read world model, return markdown
fleet/skills/factorio_plan.py                   # Skill: strategic planning given state
fleet/skills/factorio_act.py                    # Skill: propose + execute actions
fleet/skills/factorio_train.py                  # Skill: curriculum evaluation + progression

fleet/idle_curricula/factorio_01_bootstrap.toml
fleet/idle_curricula/factorio_02_goals.toml
fleet/idle_curricula/factorio_03_kpis.toml
fleet/idle_curricula/factorio_04_survival.toml
```

**Design rationale:**
- `fleet/modules/factorio/` keeps all bridge code out of the top-level `fleet/` namespace
- Lua mod ships inside the Python package — `lua_installer.py` knows where to find it
- Skills follow standard contract but delegate to bridge for state
- Curriculum files use existing `idle_curricula/` pattern

---

## Bridge Service

### Lifecycle

Managed by supervisor, similar to `hw_supervisor.py` (Dr. Ders):
- Supervisor spawns `factorio_bridge.py` when `[factorio] enabled = true`
- Monitors health, restarts on crash, reports status to dashboard
- Bridge registers with supervisor on startup, sends heartbeats

### Components

**WorldModel** — Persistent in-memory game state. Diffs against previous state rather than re-parsing from scratch each tick. Tracks entity lifecycle (placed/removed/changed), resource depletion trends, production rates over time.

**CadenceController** — Manages the tick interval with four modes:
- **Fast:** 1 second — near-real-time for reactive play
- **Medium:** 5 seconds — good for factory building, logistics
- **Slow:** 30 seconds — strategic/planning pace, minimal token cost
- **Adaptive (default):** Slow baseline, automatically boosts to 1.5s when events fire

The launcher UI provides a slider to switch between modes or lock to a specific speed.

**EventDetector** — Compares current state to previous. Fires typed events:
- `ResourceDepleted` — an ore patch is running low
- `ResearchComplete` — a technology finished researching
- `EntityDestroyed` — something got destroyed (biters, accidents)
- `PowerOutage` — electric network satisfaction dropped
- `IdleAssemblers` — assemblers with no input, sitting idle

Events drive adaptive cadence AND feed into curriculum scoring.

**CommandQueue** — Fleet workers don't talk to RCON directly. They write action requests via the `factorio_act` skill. Bridge reads the queue, translates to RCON commands, executes, writes results back. Clean separation.

### Tick Loop

```
1. Wait for cadence interval
2. RCON /biged-state → parse → update WorldModel
3. RCON /biged-metrics (every Nth tick, configurable)
4. EventDetector: diff prev/curr, fire events, adjust cadence if adaptive
5. Write factory-state.md (for fleet workers to read)
6. Drain CommandQueue → RCON /biged-cmd for each action
7. Write results back to skill callers
```

### Sandbox Mode

When the Factorio module activates, the supervisor enters sandbox mode:

1. Drains the normal task queue (in-progress tasks finish, stops claiming new ones)
2. Pauses idle skills (no background `code_quality`, `benchmark`, etc.)
3. Reassigns all available workers to Factorio-affinity skills
4. Dashboard shows "Sandbox Mode: Factorio" banner
5. Deactivation exits sandbox mode and resumes normal operation

`reserved_workers` config allows keeping N workers for other tasks (default 0 = full dedication).

---

## Communication: RCON + File Handoff

### Factorio to Bridge: RCON

The bridge communicates with Factorio via RCON (Remote Console). The Lua mod (`control.lua`) registers four RCON commands:

| Command | Purpose | Returns |
|---------|---------|---------|
| `/biged-state` | Full state dump: entities, inventory, resources, research | JSON |
| `/biged-metrics` | Production stats, flow rates, electric network | JSON |
| `/biged-cmd <json>` | Execute an action (place, remove, craft, move, etc.) | JSON result |
| `/biged-observe <x> <y> <r>` | Focused observation around a specific point | JSON |

**Why RCON over file-based:**
- Request/response — no race conditions (vs. reading a file mid-write)
- Sub-100ms round trip on localhost
- Built into Factorio (just needs `--rcon-port` flag)
- Adaptive polling is trivial — change how often bridge calls RCON

### Bridge to Fleet Workers: File + Skill API

The bridge writes `factory-state.md` each tick. Fleet workers read this via the `factorio_observe` skill. Actions flow back through the `factorio_act` skill which writes to the bridge's CommandQueue.

This matches BigEd's existing file-based handoff pattern — workers don't need to know about RCON.

---

## Factorio Lua Mod

The mod (`biged-bridge`) runs inside Factorio and handles state serialization and command execution.

### State Serialization

`serialize_entity()` captures per-entity-type data:
- **Assemblers/Furnaces:** recipe, crafting progress, input/output inventory
- **Transport belts:** contents on each lane
- **Inserters:** held item, pickup/drop positions
- **Mining drills:** mining target, progress
- **Electric entities:** energy, buffer size
- **All entities:** name, type, position, direction, health, status

State dumps are bounded by `max_entities` (default 500) and `observation_radius` (default 64 tiles).

### Command Vocabulary

| Action | Parameters | Description |
|--------|-----------|-------------|
| `place` | entity, position, direction | Build an entity from inventory |
| `remove` | unit_number or position | Mine/deconstruct an entity |
| `set_recipe` | unit_number, recipe | Set assembler recipe |
| `craft` | recipe, count | Hand-craft items |
| `research` | technology | Start researching a tech |
| `move` | position | Teleport player (simplified movement) |
| `connect` | entity, from, to | Place a line of belts/pipes between two points |
| `observe` | position, radius | Focused area scan |
| `wait` | ticks | Do nothing for N ticks |

### Factorio Version Target

Factorio 2.0+ API. The mod manifest (`info.json`) declares minimum Factorio version.

---

## Cadence Control

Four modes, selectable via launcher UI slider:

| Mode | Interval | Use Case | Token Cost |
|------|----------|----------|------------|
| Fast | 1s | Debugging, watching real-time, combat | High |
| Medium | 5s | Factory building, logistics | Moderate |
| Slow | 30s | Strategic planning, early training | Low |
| Adaptive | 30s baseline, 1.5s on events | Production use (default) | Variable |

Adaptive mode mirrors the Dr. Ders event-driven wake-up pattern — idle until something interesting happens. Events that trigger a cadence boost:

- `resource_depleted` — ore patch running low
- `entity_destroyed` — biters or accidents
- `research_complete` — new tech available
- `power_outage` — electric network failing
- `idle_assemblers` — production stalled

Boost duration is configurable (default: hold fast cadence for 30 seconds after event, then decay back to slow).

---

## Training Progression

Four phases, progressing left to right as the agent gains competence:

### Phase 1: Curriculum (Learn the Basics)

Structured lessons teaching fundamental Factorio mechanics:
- Hand-crafting items
- Placing furnaces and smelting
- Building power (offshore pump → boiler → steam engine)
- Automating mining with electric drills and belts

**Complexity:** simple | **Model:** qwen3:8b | **Cadence:** slow

### Phase 2: Goals (Complete Objectives)

Open-ended objectives the agent must figure out how to accomplish:
- "Automate red science production"
- "Build a rail network to the iron patch at coordinates X,Y"
- "Set up green science (logistic science packs)"

**Complexity:** medium | **Model:** sonnet | **Cadence:** medium

### Phase 3: KPIs (Optimize Throughput)

Measurable performance targets:
- Achieve 30 iron plates/min throughput
- Maintain 15 science packs/min
- Keep assembler idle rate below 5%
- Minimize pollution per science pack produced

**Complexity:** medium | **Model:** sonnet | **Cadence:** medium

### Phase 4: Survival (Stay Alive Under Pressure)

Biters enabled, adversarial conditions:
- Maintain power stability under attack
- Prevent belt deadlocks
- Expand territory to access new resources
- Balance defense spending vs. production growth

**Complexity:** complex | **Model:** opus (planning) | **Cadence:** adaptive

### Curriculum File Format

Each phase is a TOML file using the existing `idle_curricula/` pattern:

```toml
[curriculum]
name = "factorio_bootstrap"
phase = 1
description = "Learn basic Factorio mechanics"
complexity = "simple"
cadence = "slow"
unlock_next = "factorio_02_goals"

[[lessons]]
name = "hand_craft"
task = "Craft 10 iron gear wheels from starting inventory"
success_criteria = "inventory.iron-gear-wheel >= 10"
max_steps = 20

[[lessons]]
name = "place_and_smelt"
task = "Place a stone furnace and smelt 20 iron plates"
success_criteria = "production.iron-plate >= 20"
max_steps = 50

[graduation]
criteria = "all lessons passed"
```

### Success Criteria Evaluation

Criteria are evaluated against real game state from the WorldModel, not LLM self-report. Uses a safe expression parser with whitelisted accessors against WorldModel attributes — no arbitrary code execution.

### IQ Scoring Integration

Each lesson attempt feeds into the existing reinforcement system. Factorio performance contributes to agent IQ score, affecting future task routing. Agents that consistently fail at specific tasks get routed to simpler work.

Phase transitions are automatic but overridable — the launcher UI shows current phase, lesson progress, and a "Skip to Phase X" dropdown.

---

## Multi-PC Distribution

When BigEd runs across multiple machines via federation:

```
┌──────────────────────────────┐
│  PC-A: "Game Host"           │
│  ├─ Factorio headless server │
│  ├─ FactorioBridge (RCON)    │
│  ├─ WorldModel (authoritative)│
│  └─ Workers: observe, act    │
└──────────────┬───────────────┘
               │ federation sync
        ┌──────┴──────┐
        │             │
┌───────▼────────┐ ┌──▼──────────────┐
│  PC-B          │ │  PC-C            │
│  Workers:      │ │  Workers:        │
│  plan, train,  │ │  plan, train,    │
│  evaluate      │ │  evaluate        │
└────────────────┘ └──────────────────┘
```

### Rules

- **Bridge runs on Game Host only** — zero network hop for RCON. Non-negotiable for latency.
- **WorldModel is authoritative on Game Host** — remote workers get read-only snapshots via federation sync.
- **Any PC can run reasoning/planning** — `factorio_plan` and `factorio_train` are compute-heavy (LLM calls), route to whichever node has GPU/API capacity.
- **Only Game Host executes actions** — remote workers propose actions via CommandQueue. Bridge on Game Host executes via RCON.
- **Federation router handles dispatch** — uses existing affinity system. `factorio_observe` and `factorio_act` have affinity to Game Host. `factorio_plan` and `factorio_train` go anywhere.

### Config

```toml
# Game Host
[factorio]
role = "host"

# Compute nodes
[factorio]
role = "compute"
host_fleet_id = "..."  # federation ID of Game Host
```

No special multi-PC code needed — falls out of federation routing + skill affinity.

---

## Viewing & Spectator Mode

Three server modes:

| Mode | Description | Overhead |
|------|-------------|----------|
| Headless (default) | No window, lowest overhead | ~200-400MB RAM, minimal CPU |
| Headless + Spectator | Headless server + client connects as observer | +500MB-1GB for client |
| Client-only | Single process, simplest, closing window kills game | Same as spectator |

**Headless + Spectator is recommended** because:
- Agent runs uninterrupted on headless server
- Spectator can be opened/closed anytime — zero impact on bridge
- Works across PCs — connect from PC-B to watch PC-A's agent
- The dashboard shows status/metrics even without spectator open

Launcher UI:
```
Factorio Server:  [Running]     [Stop]
Viewer:           [Launch Spectator]  [Not Running]
```

"Launch Spectator" detects Factorio install path, launches `factorio --mp-connect localhost:{port}`, tracks the process for status reporting.

---

## Lua Mod Installation

Three modes, selectable in the module settings panel:

| Mode | Behavior | Default |
|------|----------|---------|
| Manual | User copies `lua_mod/` to Factorio mods folder. README tells them how. | Yes (safest) |
| Assisted | Auto-detect `%APPDATA%/Factorio/mods/`, copy mod there. User restarts Factorio. | Opt-in |
| Managed | Full version sync — detect Factorio install, manage mod version, warn on updates. | Opt-in |

Each level adds automation on top of the previous. The module always works at Manual level — Assisted and Managed are convenience layers.

### Auto-Detection Paths

- **Steam (Windows):** `C:\Program Files (x86)\Steam\steamapps\common\Factorio\`
- **Standalone (Windows):** `C:\Program Files\Factorio\`
- **Mods (Windows):** `%APPDATA%\Factorio\mods\`
- **Linux:** `~/.factorio/` or `~/.steam/steam/steamapps/common/Factorio/`
- **macOS:** `~/Library/Application Support/factorio/`

---

## Module Hub & Install UX

### Module Hub Flow

1. User opens Module Hub tab in launcher
2. Browses available modules from `registry.json`
3. Clicks **Install** on "Factorio Sandbox"
4. Hub downloads package, verifies SHA-256 checksum
5. Files extracted to correct locations
6. `manifest.json` updated, `fleet.toml [launcher.tabs] factorio = true` auto-set
7. New "Factorio" tab appears in launcher (hot-load, no restart)
8. First tab open triggers setup wizard

### First-Run Setup Wizard

Three steps:

**Step 1: Factorio Installation**
- Lua mod install mode selection (Manual / Assisted / Managed)
- Auto-detect Factorio install path with manual override
- If Assisted/Managed: copy mod and confirm

**Step 2: Server Settings**
- RCON port, password (with random generation option)
- Save file selection
- Server mode (Headless / Headless + Spectator)

**Step 3: Agent Settings**
- Starting curriculum phase
- Cadence mode
- Sandbox mode toggle (pause other tasks)
- Reserved workers count

Wizard writes all settings to `fleet.toml [factorio]`.

### Module Manager Panel (Generic)

A new settings panel (similar to MCP settings) for managing all modules:

| Feature | Backend Status | UI Needed |
|---------|---------------|-----------|
| Browse available modules | `hub.py` fetches `registry.json` | List panel with install buttons |
| Install a module | `hub.download_module()` works | Progress indicator |
| Enable/disable installed | `hub.enable_module()` works | Toggle switch per module |
| Uninstall a module | `hub.uninstall_module()` works | Button with confirmation |
| Check for updates | `hub.check_updates()` works | Badge on outdated modules |
| View module details | Not implemented | Detail panel: description, version, author |

The hub backend is solid — the gap is a launcher UI panel that wires into it. This panel is generic and works for any module, not just Factorio.

### Module Hub Registry Entry

```json
{
  "name": "factorio-sandbox",
  "version": "0.1.0",
  "description": "Train BigEd agents in Factorio — curriculum-based progression from basics to autonomous play",
  "author": "BigEd",
  "category": "training",
  "min_biged_version": "0.400.00b",
  "default_enabled": false,
  "files": [
    "BigEd/launcher/modules/mod_factorio.py",
    "fleet/modules/factorio/*",
    "fleet/skills/factorio_*.py",
    "fleet/idle_curricula/factorio_*.toml"
  ],
  "depends_on": [],
  "config_section": "factorio",
  "setup_wizard": true,
  "sha256": "..."
}
```

---

## Configuration

### fleet.toml `[factorio]` Section

```toml
[factorio]
enabled = false                     # master switch
role = "host"                       # host | compute
host_fleet_id = ""                  # federation ID (compute nodes only)

# Server
rcon_host = "localhost"
rcon_port = 27015
rcon_password = ""
server_mode = "headless"            # headless | client
factorio_path = ""                  # auto-detect or manual
headless_path = ""                  # separate headless binary (Linux)
save_file = "sandbox.zip"
spectator_enabled = true

# Cadence
cadence = "adaptive"                # fast | medium | slow | adaptive
cadence_fast_ms = 1000
cadence_medium_ms = 5000
cadence_slow_ms = 30000
adaptive_boost_ms = 1500
adaptive_events = [
    "resource_depleted",
    "entity_destroyed",
    "research_complete",
    "power_outage",
    "idle_assemblers",
]

# Agent
max_actions_per_step = 20
sandbox_mode = true                 # pause non-factorio work
reserved_workers = 0                # 0 = all workers dedicated

# Training
current_phase = 1                   # 1-4
auto_advance = true                 # graduate automatically

# Lua mod
lua_install_mode = "manual"         # manual | assisted | managed

# Files
state_file = "fleet/modules/factorio/factory-state.md"
log_dir = "fleet/modules/factorio/logs"
curriculum_dir = "fleet/idle_curricula"
```

### Skill Affinity Addition

```toml
[affinity]
factorio = ["factorio_observe", "factorio_plan", "factorio_act", "factorio_train"]
```

### Skill Budget Addition

```toml
[budgets]
factorio_plan = 2.00      # planning uses sonnet/opus
factorio_train = 1.00     # evaluation is cheap
factorio_act = 0.50       # action translation is simple
factorio_observe = 0.00   # local parsing, no LLM cost
```

---

## Skills

All four skills follow the standard BigEd skill contract (`SKILL_NAME`, `DESCRIPTION`, `run()`).

### factorio_observe

Reads the WorldModel snapshot (or `factory-state.md`) and returns a structured observation. No LLM cost — pure parsing.

**Complexity:** simple | **Network:** false

### factorio_plan

Given current game state + curriculum phase + recent history, produces a strategic plan. This is the "brain" — it decides what to build next, where to expand, what to research.

**Complexity:** complex | **Network:** true (LLM API call)

### factorio_act

Translates a plan into concrete actions and writes them to the bridge's CommandQueue. Validates actions against inventory and game rules before submitting.

**Complexity:** medium | **Network:** true (LLM for translation)

### factorio_train

Runs curriculum evaluation — checks success criteria against WorldModel, tracks lesson progress, handles phase graduation. Also generates feedback for failed attempts.

**Complexity:** medium | **Network:** true (LLM for feedback generation)

---

## Future Work

### Multi-Character Agent Coordination (Phase 5)

Factorio's Lua API can create and control multiple characters without multiple client connections or Steam licenses. Each "agent character" is a scripted entity controlled through the single RCON connection.

Potential domain split:
- Character 1: Logistics (belts, trains, routing)
- Character 2: Construction (placing buildings, assemblers)
- Character 3: Defense (walls, turrets, biter response)
- Character 4: Exploration (scouting, revealing map, finding resources)

**Changes required:**
- `control.lua`: Replace `game.get_player(1)` with character registry, add `character_id` to `/biged-cmd`
- `WorldModel`: Per-character position, inventory, task assignment
- Bridge: Map fleet workers to characters
- New curriculum: Agents learn not to conflict (resource contention, spatial blocking)

Planned as Phase 5 of training progression, after single-character mastery.

### Other Potential Extensions

- **Blueprint import/export** — allow agent to use community blueprints as templates
- **Replay analysis** — parse Factorio replay files for training data
- **GameEnvironment ABC** — abstract the bridge pattern when a second game integration arrives
- **Mod compatibility** — support popular Factorio mods (Krastorio, Space Exploration) as curriculum variants
