# BigEd CC — Rust Hybrid Architecture Design

> **Status:** Approved design, pending implementation plan
> **Date:** 2026-03-24
> **Author:** Claude Opus 4.6 + Max
> **Motivation:** Full platform play — cross-platform native app, WASM dashboard, edge deployment, single binary distribution

---

## 1. Overview

Replace BigEd CC's Python infrastructure with a Rust workspace while keeping 130+ Python skills via PyO3 embedded interpreter. One codebase compiles to native desktop (Windows/Linux/macOS), WASM browser dashboard, and edge binaries (ARM).

**Current state:** ~25,000 lines of Python fleet code, ~12,000 lines launcher UI, 130+ skills. Known architectural bugs: supervisor crash loops, task double-claiming race, Python 3.14 scoping issues, leaked daemon threads on skill timeout, raw SQLite connections bypassing WAL.

**Target state:** Single Rust binary (`biged`) that runs supervisor, workers, HTTP server, GPU monitor, and GUI. Skills remain `.py` files with unchanged contract. Same `fleet.toml` config, same `fleet.db` schema.

---

## 2. Workspace Structure

```
biged/
├── Cargo.toml                    # workspace manifest
├── crates/
│   ├── biged-core/               # shared types, config, DB pool, task queue
│   ├── biged-supervisor/         # process lifecycle, scaling, health checks
│   ├── biged-server/             # axum REST + SSE + WebSocket
│   ├── biged-thermal/            # GPU/CPU monitoring, model management
│   ├── biged-bridge/             # PyO3 embedded Python for skills
│   ├── biged-gui/                # egui native desktop app
│   └── biged-wasm/               # egui compiled to WASM (browser dashboard)
├── src/main.rs                   # CLI entrypoint — subcommands
├── skills/                       # Python .py files (unchanged contract)
├── fleet.toml                    # same config format
└── fleet.db                      # same SQLite schema
```

### Dependency graph

```
biged-core ← foundation (every crate depends on this)
  ├── biged-supervisor (core for DB, config, task queue)
  ├── biged-server (core for DB queries, serves REST)
  ├── biged-thermal (core for config, writes hw_state)
  ├── biged-bridge (core for task types, calls Python skills)
  ├── biged-gui (core for DB reads + server for API)
  └── biged-wasm (same as gui, compiled to wasm32)
```

### CLI modes

```bash
biged                    # full stack: supervisor + server + workers + GUI
biged --headless         # no GUI (server/CI deployment)
biged --worker-only      # just skill execution (edge node)
biged serve              # just the HTTP server (behind nginx)
biged thermal            # just GPU monitoring (standalone)
biged migrate            # DB migration from Python-era fleet.db
```

---

## 3. biged-core — Shared Foundation

### Config

- Reads existing `fleet.toml` unchanged via `toml` crate
- Parsed into strongly-typed `FleetConfig` struct at startup
- Hot-reloaded on SIGHUP or 300s timer (matches current behavior)
- Validated at parse time — no more `config.get("fleet", {}).get("x", default)` chains

### Database

- `r2d2::Pool<SqliteConnectionManager>` with 4-8 connections
- Same schema, same WAL mode, same 20 tables
- `claim_task()` uses atomic `UPDATE...RETURNING` — double-claiming race eliminated
- `rusqlite::busy_handler` with jittered backoff replaces Python's `_retry_write`
- Single pool instance — impossible to bypass WAL configuration

### Task queue

- In-memory `tokio::sync::mpsc` channel for hot path (zero-cost, lock-free)
- DB as durable backing store — restart recovers all pending tasks
- Workers `await` next task — no polling, no adaptive sleep loops

### Shared types

```rust
pub enum TaskStatus { Pending, Running, Done, Failed, WaitingHuman, Review, Forwarded }
pub enum AgentStatus { Idle, Busy, Quarantined, Offline }
pub struct Task { id: i64, skill: String, payload: serde_json::Value, ... }
pub struct Agent { name: String, role: String, status: AgentStatus, ... }
pub struct HwState { gpu_temp: f32, vram_used: f64, cpu_temp: f32, ... }
```

---

## 4. biged-supervisor — Unified Lifecycle

Merges `supervisor.py` (1,806 lines) and `hw_supervisor.py` (1,303 lines) into one async runtime. Dr. Ders becomes a subsystem, not a separate process.

### Tokio task tree

Each responsibility is an independent async task. One failing doesn't block others.

| Task | Interval | Replaces |
|------|----------|----------|
| `worker_health_loop` | 5s | supervisor proc.poll() checks |
| `thermal_loop` | 5s | entire hw_supervisor.py |
| `scaling_loop` | 30s | supervisor scaling logic |
| `stale_recovery_loop` | 300s | supervisor stale task recovery |
| `federation_heartbeat` | 60s | supervisor federation pings |
| `config_reload_watcher` | inotify | supervisor 300s config reload |
| `event_trigger_loop` | 30s | supervisor event triggers |

### Event bus

```rust
pub enum FleetEvent {
    TaskCompleted { id: i64, skill: String, agent: String },
    AgentStateChange { agent: String, from: AgentStatus, to: AgentStatus },
    ThermalAlert { gpu_temp: f32, action: ThermalAction },
    ModelTransition { from: String, to: String, reason: String },
    ConfigReloaded,
    ScaleUp { count: u32, reason: String },
    ScaleDown { count: u32 },
}
```

- `tokio::sync::broadcast` — zero-cost pub/sub for internal IPC
- Replaces SQLite `messages` table polling for real-time comms
- External clients subscribe via SSE/WebSocket from biged-server
- SQLite `messages` table kept only for durable cross-restart messages

### AutoScaler

- Current sklearn model exported to ONNX, loaded by `ort` crate
- Fallback to threshold-based scaling if no model available
- Runs in <1ms instead of 4 SQLite round-trips

### Bugs eliminated by design

| Python Bug | Rust Solution |
|---|---|
| Dr. Ders crash loop | Thermal is a tokio task — panics are caught and restarted without process overhead |
| `UnboundLocalError` on globals | No global mutable state. `Arc<RwLock<>>` enforced by compiler |
| Raw sqlite3 bypassing WAL | Single `Db` pool. No other way to get a connection |
| Main loop skip after crash | Independent tokio tasks. One failing doesn't block others |
| Leaked daemon threads on timeout | `tokio::time::timeout` cancels cleanly |

---

## 5. biged-bridge — Python Skill Execution via PyO3

### Execution flow

1. Rust worker claims task from TaskQueue (zero-copy, <1us)
2. Bridge checks `DashMap` skill cache for compiled module
3. If miss: `importlib.import_module("skills.{name}")` — cached for next call
4. Bridge calls `module.run(payload_dict, config_dict)` with GIL held
5. Python skill executes (LLM calls release GIL during I/O)
6. Bridge receives return dict, converts to `Result<serde_json::Value>`
7. Rust worker writes result to DB via pool

### GIL strategy

One interpreter, release GIL during I/O (PyO3 default). Skills spend 95%+ time in network I/O (waiting on Ollama/Claude/Gemini) which releases GIL automatically. Only the thin Python glue holds GIL — typically <10ms per skill invocation. Sub-interpreters (PEP 734) available as future upgrade path.

### Skill contract unchanged

```python
SKILL_NAME = "code_review"
DESCRIPTION = "Review code for quality issues"
REQUIRES_NETWORK = False

def run(payload: dict, config: dict) -> dict:
    import db  # lazy import — bridge injects fleet/ into sys.path
    from skills._models import call_complex
    return {"status": "ok", "result": "..."}
```

### db module interception

Bridge registers a Rust-backed `db` module in Python's `sys.modules`. When skills call `db.get_conn()`, `db.post_task()`, etc., these route through to Rust's `Db` connection pool. Skills don't change a single line but get proper connection management for free.

### Timeout enforcement

```rust
match tokio::time::timeout(duration, task).await {
    Ok(result) => result,
    Err(_) => {
        // Python interrupted via PyErr_SetInterrupt
        // GIL released, interpreter state clean
        Err(SkillError::Timeout(skill.to_string()))
    }
}
```

Eliminates the leaked daemon thread bug — Python's current approach spawns a daemon thread that runs forever if the skill exceeds timeout.

---

## 6. biged-server — axum REST API

### Design

- Same URL paths as current Flask API — `dashboard.html` frontend works unchanged
- `axum::Router` with shared `AppState` (Db pool + event bus + config)
- Fully async, multi-core via tokio
- Native SSE for backwards compatibility + WebSocket for new GUI

### Dual transport

- **SSE** (`/api/stream`): existing dashboard.html uses this, kept for compatibility
- **WebSocket** (`/ws`): new bidirectional channel for egui GUI, binary MessagePack encoding (10x smaller than JSON SSE)

### Migration strategy

| Phase | Endpoints | Coverage |
|-------|-----------|----------|
| 1 | Core 20 (status, agents, tasks, activity, thermal, settings) | Dashboard home + fleet |
| 2 | Analytics 15 (usage, activity/lanes, performance) | Analytics tab |
| 3 | ViewPort 16 (views, graphs, configs) | Graph views |
| 4 | Remaining 185 | Full parity |

During migration: Flask stays running on port 5556 as fallback. Rust serves on 5555. Unimplemented routes proxy to Flask.

### Static assets

```
static/
├── dashboard.html      # current file, works as-is against axum
├── view_engine.js      # Cytoscape graph renderer
├── view_graph.html     # graph view templates
└── tokens.css          # design tokens
```

Existing HTML/JS frontend doesn't know the backend switched. Same URLs, same JSON.

---

## 7. biged-gui + biged-wasm — Cross-Platform GUI

### One codebase, three targets

```bash
cargo build --bin biged-desktop                              # native (Win/Linux/macOS)
cargo build --target wasm32-unknown-unknown -p biged-wasm    # browser at /app
cross build --target aarch64-unknown-linux-gnu               # edge (RPi, ARM)
```

### egui app structure

```rust
pub struct BigEdApp {
    api: ApiClient,           // HTTP + WebSocket to biged-server
    state: AppState,          // cached fleet state
    neural_lanes: NeuralLanes, // GPU-accelerated lane graph
    theme: Theme,             // Figma dark theme ported to egui
}
```

### Tab mapping

| Current (customtkinter) | egui Equivalent |
|---|---|
| Command Center (tkinter Canvas) | `NeuralLanes` widget — 60fps GPU-accelerated |
| Fleet (agent cards) | `egui::Grid` with status indicators |
| Fleet Comm (chat) | `egui::TextEdit` + scrollable message list |
| Files (Import/Knowledge) | `egui_file_dialog` + preview panel |
| Settings (9 panels) | `egui::SidePanel` + form widgets |
| Graph View (pywebview) | Native egui graph renderer — no browser needed |

### Neural lane graph at 60fps

The `NeuralLanes` widget uses `egui::Painter` for GPU-accelerated rendering:
- Agent/model swim lanes with stacked status bars
- Animated pulses traversing active lanes every frame
- Bezier edge curves connecting agents sharing skills
- Right sidebar with skill/knowledge/channel indicators

### Theme

Figma dark theme from `ui/theme.py` ported to `egui::Visuals`:
- `BG` (#0a0e1a) → `window_fill`
- `GLASS_PANEL` (#1e2535) → `panel_fill`
- `ACCENT` (#3b82f6) → `selection.bg_fill`
- `GOLD` (#f59e0b) → section headers

### Advantages over customtkinter

- No `place()` breaking across Python versions
- No glass theme contrast issues (compile-time color validation)
- No `CREATE_NO_WINDOW` subprocess flags
- No tkinter threading restrictions
- Settings panels can't crash silently — compiler catches missing attributes

---

## 8. Migration Path

### Phase 0: Foundation (Week 1-2)

- Set up Rust workspace with all 7 crates
- Implement biged-core: config parser, DB pool, shared types
- `biged migrate` command that validates existing fleet.db

### Phase 1: Supervisor (Week 3-5)

- Implement biged-supervisor with all 7 tokio tasks
- Implement biged-thermal (merged Dr. Ders)
- Run alongside Python supervisor to validate behavior parity
- Gate: supervisor runs 24h with zero crashes

### Phase 2: Server (Week 5-7)

- Implement biged-server Phase 1 (core 20 endpoints)
- Proxy remaining endpoints to Flask
- Existing dashboard.html works against Rust server
- Gate: dashboard fully functional against axum

### Phase 3: Bridge (Week 7-9)

- Implement biged-bridge with PyO3
- Port worker.py task dispatch loop to Rust
- Skills execute via bridge, results flow through Rust DB pool
- Gate: all 130+ skills pass smoke test via bridge

### Phase 4: GUI (Week 9-13)

- Implement biged-gui with egui (all tabs)
- Port Figma theme
- Neural lane graph at 60fps
- Gate: feature parity with customtkinter launcher

### Phase 5: WASM + Polish (Week 13-16)

- Compile biged-wasm for browser dashboard
- Remaining server endpoints (Phase 2-4)
- Edge build (ARM cross-compilation)
- Remove Python Flask, supervisor.py, hw_supervisor.py, worker.py, launcher.py
- Gate: single `biged` binary runs everything

### Phase 6: Graduation (Week 16-18)

- Performance benchmarks vs Python baseline
- Cross-platform CI (Windows, Linux, macOS, ARM)
- Installer/packaging (MSI, .deb, .dmg, .AppImage)
- Version: 1.000.00 (beta graduation)

---

## 9. Rust Crate Dependencies

```toml
[workspace.dependencies]
tokio = { version = "1", features = ["full"] }
axum = "0.8"
rusqlite = { version = "0.32", features = ["bundled"] }
r2d2 = "0.8"
r2d2_sqlite = "0.25"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
toml = "0.8"
pyo3 = { version = "0.23", features = ["auto-initialize"] }
eframe = "0.30"
egui = "0.30"
nvml-wrapper = "0.10"
ort = "2"                    # ONNX Runtime for ML router
reqwest = { version = "0.12", features = ["json"] }
tracing = "0.1"
tracing-subscriber = "0.3"
dashmap = "6"
rmp-serde = "1"              # MessagePack for WebSocket
clap = { version = "4", features = ["derive"] }
```

---

## 10. What Stays Python

| Component | Reason |
|---|---|
| 130+ skills in `skills/` | AI SDK ecosystem is Python-first (anthropic, google-genai, httpx). Skills are the fast-iteration layer — changing a `.py` file doesn't require recompilation |
| `skills/_models.py` | Wraps anthropic/google-genai SDKs. PyO3 calls this directly |
| `autoresearch/` | ML training pipeline (PyTorch, transformers). Separate venv |
| `requirements.txt` | Still needed for skill dependencies |

---

## 11. Risk Mitigation

| Risk | Mitigation |
|---|---|
| PyO3 bridge complexity | Start with subprocess JSON protocol as fallback. PyO3 is the optimization |
| egui missing customtkinter widgets | egui has extensive widget library. Custom widgets easy via `Painter` |
| WASM binary size | `wasm-opt` + tree shaking. Target <5MB |
| Migration duration | Each phase is independently deployable. Can ship Phase 1-3 without GUI |
| Skill compatibility | Bridge intercepts `import db` transparently. Zero skill changes required |
| Cross-compilation | `cross` tool handles all targets. CI matrix: windows-latest, ubuntu-latest, macos-latest |
