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

### Prerequisites

These features do not exist yet and must be built before or alongside this module:

1. **Multi-file Module Hub installs** — The existing `hub.py` downloads single files. This module spans ~15 files across 4 directories. Hub needs archive-based install support (zip package → extract to multiple destinations). **Est: S (3-5k tokens)**
2. **Supervisor sandbox mode** — The supervisor has no concept of draining queues, pausing idle skills, or dedicating workers to a single domain. Needs: drain API, idle skill pause, dynamic affinity override, dashboard banner. **Est: M (8-15k tokens)**

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

fleet/factorio/                                 # All fleet-side code (isolated)
├── __init__.py                                 # Module registration + sys.path setup
├── bridge.py                                   # Long-running bridge process
├── rcon_client.py                              # Async RCON protocol client
├── state_parser.py                             # Raw JSON → structured GameState
├── action_translator.py                        # Agent actions → RCON commands
├── config.py                                   # BridgeConfig dataclass
├── curriculum.py                               # Training progression engine
├── world_model.py                              # Persistent in-memory world state
├── lua_installer.py                            # Lua mod install modes (manual/assisted)
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
- `fleet/factorio/` is flat under `fleet/` — imports work without sys.path changes since workers already run with `fleet/` as root
- Lua mod ships inside the Python package — `lua_installer.py` knows where to find it
- Skills follow standard contract but delegate to bridge for state
- Curriculum files use existing `idle_curricula/` pattern

---

## Bridge Service

### Lifecycle

Managed by supervisor using the same spawn-and-poll pattern as `hw_supervisor.py` (Dr. Ders):
- Supervisor spawns `factorio/bridge.py` when `[factorio] enabled = true`
- Monitors via `subprocess.Popen.poll()` — restarts on crash
- Reports bridge status to dashboard via the existing process manager
- Uses `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` on Windows

No new heartbeat protocol — the existing poll-based monitoring is sufficient.

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
5. Write factory-state.md (debug/dashboard display only — not the canonical worker path)
6. Drain CommandQueue → RCON /biged-cmd for each action
7. Write results back to skill callers
```

### RCON Failure Handling

The bridge must handle RCON failures gracefully since Factorio may not be running, may crash, or may become unresponsive:

- **Connection failure on startup:** Retry with exponential backoff (1s, 2s, 4s, 8s, max 30s). Log warnings. Dashboard shows "Waiting for Factorio..."
- **Connection lost mid-game:** Pause tick loop, attempt reconnection with backoff. Keep WorldModel in memory (don't discard state). Resume from last known state on reconnect.
- **RCON command timeout:** 5s timeout per command. On timeout, skip that tick's actions, log warning, continue. After 3 consecutive timeouts, trigger circuit breaker — pause ticks for 30s, then retry.
- **Factorio crash detection:** If RCON connection drops AND the Factorio process (if bridge launched it) has exited, report to dashboard and stop tick loop. User must restart manually or bridge can auto-restart Factorio if configured.

### RCON Payload Size

Factorio's RCON implementation may have packet size limits for large state dumps (500 entities with full inventory data). Mitigations:

- **Primary:** The Lua mod's `max_entities` cap (default 500) and position rounding keep payloads reasonable. Test against actual Factorio to determine real limits.
- **Fallback if needed:** Split `/biged-state` into chunked responses — Lua mod writes full state to `script-output/biged-state.json`, RCON returns only a "state ready" signal, bridge reads the file. This hybrid approach uses RCON for signaling and files for bulk data.
- **Metrics are always small** — `/biged-metrics` payload is bounded by `tracked_items` list size (~20 items), no chunking needed.

### Sandbox Mode

When the Factorio module activates, the supervisor enters sandbox mode. **This is a new supervisor feature** (see Prerequisites) that requires:

1. **Queue drain API** — in-progress tasks finish, supervisor stops claiming new ones
2. **Idle skill pause** — temporarily disable background idle skills
3. **Dynamic affinity override** — route available workers to Factorio skills
4. **Dashboard banner** — show "Sandbox Mode: Factorio" with deactivation button
5. **Restore on exit** — deactivation resumes normal queue claiming and idle skills

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

### Bridge to Fleet Workers: Bridge API (canonical path)

Workers access game state through skills, which call the bridge's in-process API:
- `factorio_observe` → calls bridge's `get_world_state()` → returns structured GameState
- `factorio_act` → writes to bridge's CommandQueue → bridge executes via RCON

The bridge also writes `factory-state.md` each tick for **debug and dashboard display only**. This file is not the canonical worker path — on multi-PC setups, remote workers cannot read local files on the game host. Workers always go through skills → bridge API.

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

Adaptive boost behavior:
- On event: immediately switch to `adaptive_boost_ms` (default 1500ms) interval
- Hold boosted cadence for `adaptive_boost_hold_secs` (default 30s)
- Decay: step down through medium → slow (not instant drop). Decay curve: boost → 5s after hold expires → 30s after another 30s of no events

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

Each phase is a TOML file that adapts the existing `idle_curricula/` `[[tasks]]` format. Factorio lessons are dispatched as tasks with `type = "factorio"` so the existing idle curriculum machinery can process them:

```toml
[curriculum]
name = "factorio_bootstrap"
phase = 1
description = "Learn basic Factorio mechanics"
unlock_next = "factorio_02_goals"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "hand_craft"
instruction = "Craft 10 iron gear wheels from starting inventory"
success_criteria = "inventory.iron-gear-wheel >= 10"
max_steps = 20
complexity = "simple"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "place_and_smelt"
instruction = "Place a stone furnace and smelt 20 iron plates"
success_criteria = "production.iron-plate >= 20"
max_steps = 50
complexity = "simple"
```

### Success Criteria Evaluation

Criteria are evaluated against real game state from the WorldModel, not LLM self-report. Uses a safe expression parser with whitelisted accessors against WorldModel attributes — no arbitrary code execution. The parser supports comparison operators (`>=`, `<=`, `>`, `<`, `==`) and boolean connectors (`AND`, `OR`) against a fixed set of dotted paths (e.g., `inventory.iron-plate`, `flow.iron-plate`, `entities.furnace`).

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

### Node Pinning

The existing federation router dispatches by queue overflow and role affinity, but has no concept of "this skill can only run on this node." Factorio needs this for `factorio_observe` and `factorio_act` (which must talk to the local bridge).

**Approach:** The bridge exposes a localhost-only HTTP API (e.g., `http://localhost:{bridge_port}/api/state`, `/api/command`). Skills that need bridge access call this API. If the API isn't reachable (because the skill is running on a remote node), the skill returns an error and the federation router learns to route it locally. This naturally pins bridge-dependent skills to the game host without requiring new federation router features.

Planning-only skills (`factorio_plan`, `factorio_train`) receive a state snapshot as their task payload — they don't need bridge access and can run anywhere.

### Config

```toml
# Game Host
[factorio]
role = "host"
bridge_port = 27016              # localhost-only API for skills

# Compute nodes
[factorio]
role = "compute"
host_fleet_id = "..."            # federation ID of Game Host
```

---

## Viewing & Spectator Mode

Three server modes, controlled by two config keys:

| `server_mode` | `spectator_enabled` | Behavior |
|---------------|---------------------|----------|
| `headless` | `false` | Headless only. No window, lowest overhead (~200-400MB RAM). |
| `headless` | `true` (default) | Headless server + "Launch Spectator" button available in UI. Spectator connects as observer. +500MB-1GB when viewer open. |
| `client` | (ignored) | Single-process client mode. Simplest, but closing the window kills the game. |

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

Two modes for v0.1.0, selectable in the module settings panel:

| Mode | Behavior | Default |
|------|----------|---------|
| Manual | User copies `lua_mod/` to Factorio mods folder. README tells them how. | Yes (safest) |
| Assisted | Auto-detect `%APPDATA%/Factorio/mods/`, copy mod there. User restarts Factorio. | Opt-in |

Managed mode (full version sync, update warnings) deferred to v0.2.0 — unnecessary complexity before the module is proven to work.

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
4. Hub downloads zip package, verifies SHA-256 checksum
5. Files extracted to correct locations (requires multi-file hub install — see Prerequisites)
6. `manifest.json` updated, `fleet.toml [launcher.tabs] factorio = true` auto-set
7. New "Factorio" tab appears in launcher (hot-load, no restart)
8. First tab open triggers setup wizard

### First-Run Setup Wizard

Three steps:

**Step 1: Factorio Installation**
- Lua mod install mode selection (Manual / Assisted)
- Auto-detect Factorio install path with manual override
- If Assisted: copy mod and confirm

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

### Module Manager Panel

A generic Module Manager UI panel is needed for browsing/installing/managing modules (the hub backend exists but lacks a launcher UI). This is a **separate roadmap item** — not part of this spec's scope. The Factorio module can be installed via the existing hub CLI or a minimal install flow while the full UI is built independently.

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
  "archive": "factorio-sandbox-0.1.0.zip",
  "install_map": {
    "mod_factorio.py": "BigEd/launcher/modules/mod_factorio.py",
    "fleet_factorio/": "fleet/factorio/",
    "skills/": "fleet/skills/",
    "curricula/": "fleet/idle_curricula/"
  },
  "depends_on": [],
  "config_section": "factorio",
  "setup_wizard": true,
  "sha256": "..."
}
```

The `install_map` field is new — it tells the hub where to extract each path from the archive. This is part of the multi-file hub install prerequisite.

---

## Configuration

### fleet.toml `[factorio]` Section

```toml
[factorio]
enabled = false                     # master switch
role = "host"                       # host | compute
host_fleet_id = ""                  # federation ID (compute nodes only)
bridge_port = 27016                 # localhost-only API for skills

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
adaptive_boost_hold_secs = 30      # how long to hold boosted cadence after event
adaptive_events = [
    "resource_depleted",
    "entity_destroyed",
    "research_complete",
    "power_outage",
    "idle_assemblers",
]

# RCON resilience
rcon_timeout_secs = 5              # per-command timeout
rcon_max_retries = 3               # consecutive failures before circuit breaker
rcon_circuit_breaker_secs = 30     # pause duration when circuit breaker trips

# Agent
max_actions_per_step = 20
sandbox_mode = true                 # pause non-factorio work
reserved_workers = 0                # 0 = all workers dedicated

# Training
current_phase = 1                   # 1-4
auto_advance = true                 # graduate automatically

# Lua mod
lua_install_mode = "manual"         # manual | assisted

# Files
state_file = "fleet/factorio/factory-state.md"
log_dir = "fleet/factorio/logs"
curriculum_dir = "fleet/idle_curricula"
```

### Skill Affinity Addition

Factorio skills are assigned to a new `sandbox` agent role. This role is created dynamically when the Factorio module is enabled and removed when disabled:

```toml
[affinity]
sandbox = ["factorio_observe", "factorio_plan", "factorio_act", "factorio_train"]
```

Workers in sandbox mode are temporarily reassigned to the `sandbox` role. When sandbox mode exits, they revert to their original roles.

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

Reads the WorldModel state via the bridge's localhost API (`http://localhost:{bridge_port}/api/state`). Returns a structured observation as markdown. No LLM cost — pure parsing.

**Complexity:** simple | **Network:** false (localhost only)

### factorio_plan

Given current game state + curriculum phase + recent history, produces a strategic plan. This is the "brain" — it decides what to build next, where to expand, what to research. Receives state snapshot as task payload — does not need bridge access and can run on any node.

**Complexity:** complex | **Network:** true (LLM API call)

### factorio_act

Translates a plan into concrete actions and submits them to the bridge's CommandQueue via localhost API (`http://localhost:{bridge_port}/api/command`). Validates actions against inventory and game rules before submitting. Must run on Game Host.

**Complexity:** medium | **Network:** true (LLM for translation)

### factorio_train

Runs curriculum evaluation — checks success criteria against WorldModel, tracks lesson progress, handles phase graduation. Also generates feedback for failed attempts. Receives state snapshot as task payload — can run on any node.

**Complexity:** medium | **Network:** true (LLM for feedback generation)

---

## Future Work

### Managed Lua Install Mode (v0.2.0)

Full version sync — detect Factorio install, manage mod version, warn on Factorio updates that might break compatibility. Deferred until the module is proven to work with Manual + Assisted modes.

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

### Module Manager UI

A generic launcher panel for browsing, installing, enabling/disabling, and updating modules from the Module Hub. Backend exists (`hub.py`), UI is the gap. Separate roadmap item.

### Other Potential Extensions

- **Blueprint import/export** — allow agent to use community blueprints as templates
- **Replay analysis** — parse Factorio replay files for training data
- **GameEnvironment ABC** — abstract the bridge pattern when a second game integration arrives
- **Mod compatibility** — support popular Factorio mods (Krastorio, Space Exploration) as curriculum variants
