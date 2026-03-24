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
- `claim_task()` uses atomic `UPDATE...WHERE(SELECT)` through pooled connections — eliminates competing raw connections that cause the current race
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
fleet/templates/
├── dashboard.html      # current file, works as-is against axum
├── view_graph.html     # graph view full-chrome template
├── view_embed.html     # graph view embed template
└── view_builder.html   # drag-and-drop view builder
fleet/static/
├── view_engine.js      # Cytoscape graph renderer
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

## 10. Additional Subsystems

### RAG Engine (rag.py → biged-core)

Separate `rag.db` with FTS5 full-text search + optional vector embeddings. biged-core manages a second `r2d2` pool for `rag.db`. FTS5 queries ported to `rusqlite` (FTS5 is a SQLite extension, works natively). Future option: replace FTS5 with `tantivy` crate for better performance. Vector search stays Python-side (sentence-transformers) called via bridge.

### Security (security.py → biged-server)

All security primitives migrate to axum tower middleware:
- RBAC token validation → custom `tower::Layer`
- CORS → `tower-http::cors::CorsLayer`
- Rate limiting → `tower_governor` or custom layer
- TLS cert generation → `rcgen` crate
- CSRF → `axum-extra` or custom header check

### Backup System (backup_manager.py → biged-supervisor)

Becomes an 8th tokio task in the supervisor: `backup_loop` (every 1200s, configurable). Snapshots `fleet.db`, `rag.db`, `fleet.toml`, `knowledge/` to backup directory.

### Messaging Bridges (Discord, OpenClaw, FleetBridge)

Stay as Python processes spawned by biged-supervisor via `tokio::process::Command`. They communicate through SQLite messages table (unchanged). Low priority for Rust port — they work fine as-is.

### MCP Manager (mcp_manager.py)

Stays Python, called via bridge when skills need MCP routing. The `.mcp.json` config is read by biged-core for probe/health checks.

### Enterprise Modules (SSO, billing, compliance, marketplace, geo-fleet)

Stay as Python modules called via bridge or served as separate Flask endpoints proxied through biged-server. Deep Python library dependencies (python-jose, pysaml2) make Rust ports impractical. Migrate to Rust only if/when Rust equivalents mature.

---

## 11. Skill Contract Normalization

The bridge must handle 4 observed `run()` signatures:

| Variant | Count | Example |
|---------|-------|---------|
| `run(payload, config)` | ~120 | Most skills |
| `run(payload, config, log)` | ~8 | Takes a logger |
| `run(task, context)` | ~3 | Different param names |
| Module-level imports | ~5 | `from skills._models import call_complex` at top |

**Strategy:** Bridge inspects `inspect.signature(module.run)` at load time and builds an adapter:
- 2-arg: call as `run(payload, config)`
- 3-arg: inject a Python `logging.Logger` as third arg
- Different names: positional dispatch (payload=first, config=second)
- Module-level imports: work as-is since `sys.path` includes `fleet/` and `fleet/skills/`

**Pre-Phase-0 audit:** Normalize all skills to canonical 2-arg `run(payload, config)` in the Python codebase before Rust bridge is built. Logger available via `logging.getLogger(__name__)` inside run() (current convention).

---

## 12. Testing Strategy

### Per-crate unit tests
- Rust `#[test]` modules in each crate
- `biged-core`: config parsing, DB operations, task queue semantics
- `biged-supervisor`: scaling decisions, event dispatch, thermal thresholds
- `biged-server`: endpoint response format, auth middleware
- `biged-bridge`: skill loading, signature detection, timeout behavior

### Integration tests
- Bridge + real Python skills (run all 130+ through PyO3)
- Server + DB (full endpoint test suite)
- Supervisor + bridge + server (end-to-end task lifecycle)

### Behavior parity tests
- Run identical inputs through Python fleet and Rust binary, compare outputs
- Automated regression suite that catches any behavior drift

### Smoke test parity
- Port existing 38-test smoke suite to Rust integration tests
- Must pass before any phase gate

### Performance benchmarks
- Baseline: Python fleet (task/s, latency, memory, startup time)
- Target: 10x task throughput, 50x startup, 5x memory reduction
- Measured at each phase gate

---

## 13. CI/CD Strategy

### GitHub Actions matrix
```yaml
strategy:
  matrix:
    os: [windows-latest, ubuntu-latest, macos-latest]
    include:
      - os: ubuntu-latest
        target: aarch64-unknown-linux-gnu  # ARM cross-compile
      - os: ubuntu-latest
        target: wasm32-unknown-unknown     # WASM build
```

### Build requirements per runner
- Rust toolchain (rustup)
- Python 3.12+ (for PyO3 bridge tests)
- ONNX Runtime shared libs (for `ort` crate)
- `wasm-bindgen-cli` + `wasm-opt` (WASM target only)

### Caching
- `actions/cache` for `~/.cargo/registry` and `target/` directory
- Separate cache keys per OS + Cargo.lock hash

### Artifacts
- Native binaries per platform (Windows .exe, Linux binary, macOS binary)
- WASM bundle (static/wasm/)
- Installers: MSI (Windows), .deb + .AppImage (Linux), .dmg (macOS)

---

## 14. Error Handling Strategy

### Crate-level error types
```rust
// biged-core
#[derive(thiserror::Error, Debug)]
pub enum CoreError {
    #[error("database: {0}")]
    Db(#[from] rusqlite::Error),
    #[error("config: {0}")]
    Config(#[from] toml::de::Error),
    #[error("task queue full")]
    QueueFull,
}

// biged-bridge
#[derive(thiserror::Error, Debug)]
pub enum BridgeError {
    #[error("skill not found: {0}")]
    SkillNotFound(String),
    #[error("skill timeout after {0:?}")]
    Timeout(Duration),
    #[error("python: {0}")]
    Python(#[from] pyo3::PyErr),
}
```

- Library crates (`biged-core`, `biged-bridge`): `thiserror` for typed errors
- Application code (`biged-supervisor`, `biged-server`): `anyhow::Result` for ergonomic propagation
- All errors logged via `tracing::error!` before propagation

### Logging/observability

- `tracing-subscriber` with JSON formatting (matches existing `_json_log`)
- Per-component log files in `fleet/logs/` (same layout as Python)
- Python skill logs captured by redirecting `logging.Logger` output to Rust's tracing via PyO3 handler
- Structured fields: `agent`, `skill`, `task_id`, `duration_ms` on every span

---

## 15. Rollback Plan

| Phase | Rollback |
|-------|----------|
| Phase 1 (Supervisor) | Kill Rust binary, restart `python supervisor.py`. Same DB, same config. |
| Phase 2 (Server) | Point nginx back to Flask on port 5556. Static assets unchanged. |
| Phase 3 (Bridge) | If PyO3 fails: fall back to subprocess JSON protocol. Rust spawns `python -c "import skills.X; print(json.dumps(skills.X.run(...)))"` per task. 10x slower but functional. |
| Phase 4 (GUI) | Keep customtkinter launcher alongside. Both can coexist pointing at same server. |
| Phase 5 (WASM) | Serve old `dashboard.html` at `/` instead of WASM app. |

**Decision point:** If Phase 3 bridge can't handle >90% of skills transparently after 2 weeks of effort, fall back to subprocess protocol and revisit PyO3 after skill normalization.

---

## 16. What Stays Python

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
| WASM binary size | `wasm-opt -Os` + tree shaking + lazy component loading. Baseline measured in Phase 5, target 8-12MB |
| Migration duration | Each phase is independently deployable. Can ship Phase 1-3 without GUI |
| Skill compatibility | Bridge intercepts `import db` transparently. Zero skill changes required |
| Cross-compilation | `cross` tool handles all targets. CI matrix: windows-latest, ubuntu-latest, macos-latest |
