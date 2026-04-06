# BigEd CC — Rust Production Deployment Guide

## Overview

The Rust binary (`biged.exe`) is the **production deployment path** for BigEd CC. It provides a hardened, single-binary service with 50 REST endpoints, a 5-section operator GUI, Ollama management, and a PyO3 skill bridge. Python stays primary for development — see the main `CLAUDE.md` for the Python track.

This document covers the Rust track only.

## Quick Start

```bash
# From the repo root (Education/):
cd biged-rs

# Build release binary (11 MB, LTO + strip)
cargo build --release

# Run supervisor + server + thermal monitor
./target/release/biged supervisor

# Or run just the HTTP server
./target/release/biged serve

# Or launch the desktop operator GUI
./target/release/biged gui

# Migrate from an existing Python fleet
./target/release/biged migrate --from ../fleet
```

## Requirements

- **Rust toolchain**: 1.76+ (see `rust-toolchain.toml`)
- **Python 3.11+**: required for PyO3 skill execution (even in service-only mode)
- **Ollama**: managed by the Rust supervisor, auto-detected at startup

### Windows-specific

PyO3 needs to find the Python DLL at runtime. Create `.cargo/config.toml` (gitignored):

```toml
[env]
PYO3_PYTHON = "C:\\path\\to\\python.exe"
```

And ensure the Python DLL directory is on PATH:
```
set PATH=C:\path\to\python;%PATH%
```

## Architecture

```
biged.exe
├── Supervisor     — process lifecycle, thermal, health, backup, Ollama manager
├── HTTP Server    — 50 endpoints on :5555 (axum)
├── Worker         — PyO3 bridge to Python skills (spawn_blocking)
├── Operator GUI   — 5-section egui desktop app (or WASM in browser)
└── CLI            — supervisor | serve | worker | gui | thermal | migrate
```

### Service-only mode

On a production box, the Rust binary runs 24/7 as a service:
- Supervisor manages Ollama + worker lifecycle
- HTTP server serves the 50 production endpoints
- The operator GUI is optional (can use WASM in browser instead)
- Python launcher + modules are not needed for basic fleet management

**Service-only is Python-launcher-free, not Python-free.** Skills still execute via PyO3.

### UX Bridge (Phase G)

When both tracks are installed:
- The Rust GUI has a "Launch Modules" button (Fleet tab)
- Clicking it spawns `pythonw.exe launcher.py --connect-to http://localhost:5555`
- The Python launcher skips its own supervisor and connects to the Rust service
- Closing the launcher doesn't affect the Rust service or Ollama

## Endpoint Summary (50 endpoints)

### Status & Health
`GET /api/status` `/api/health` `/api/thermal` `/api/dashboard/batch` `/api/alerts` `/api/stream` (SSE) `/api/metrics`

### Fleet Operations
`GET /api/agents` `GET/POST /api/tasks` `GET /api/tasks/queue` `GET /api/tasks/recent`
`POST /api/tasks/dispatch` `DELETE /api/tasks/{id}` `PUT /api/tasks/{id}/priority`
`POST /api/tasks/{id}/requeue` `GET /api/queue/status` `POST /api/queue/pause` `/api/queue/resume`
`POST /api/fleet/worker/{name}/disable` `/enable`

### Activity & Skills
`GET /api/activity` `/api/activity/live` `/api/agent-cards` `/api/agents/performance`
`GET /api/skills` `/api/knowledge` `/api/timeline` `/api/discussions`

### Config & Settings
`GET /api/config` `/api/config/models` `/api/config/thermal`
`GET/PUT /api/settings` `GET/PUT/POST /api/settings/theme`

### Ollama Management
`GET /api/ollama/status` `/api/ollama/ps` `POST /api/ollama/start` `/api/ollama/stop`

### Backup & Recovery
`POST /api/backup/trigger` `GET /api/backup/list` `POST /api/backup/restore`

### Audit Trail
`GET /api/audit/scores` `/api/audit/history` `/api/audit/snapshot`

### Compliance Artifacts (CosmicTasha pattern)
`POST /api/compliance/profiles` `GET /api/compliance/profiles/{id}`
`GET /api/compliance/profiles/{id}/verify` `GET /api/compliance/audit_log`

### Version
`GET /api/version`

## Operator GUI (5 sections)

1. **Overview** — neural lanes, agent cards, task counts, thermal, alerts
2. **Fleet** — agent grid with enable/disable buttons, "Launch Modules" button
3. **Tasks** — queue table, dispatch form, cancel/requeue, pause/resume
4. **Config** — general, hardware, backup (production fields only)
5. **Logs** — supervisor/worker/fleet log viewer with level filter

## Build & Deploy

### Development build
```bash
cargo build          # debug, fast compile
cargo check          # type-check only
cargo test           # unit + integration tests
```

### Release build
```bash
cargo build --release   # optimized, LTO + strip, ~11 MB
```

### Production deployment bundle
```powershell
powershell -ExecutionPolicy Bypass -File install/build-release.ps1
```

Produces `install/dist/` containing:
- `biged.exe` — release binary
- `fleet/fleet.toml` — default config
- `fleet/skills/` — Python skills for PyO3
- `manifest.json` — SHA-256 hash + build metadata

### Contract tests
```bash
# Requires Python DLL on PATH
cargo test --test contract_test -- --nocapture
cargo test --test bridge_test -- --nocapture
```

## Shared Contracts

Three surfaces are the hard contract between Python and Rust tracks:

1. **DB schema** — both read/write `fleet.db` (SQLite, WAL mode). Schema in `biged-core/src/schema.sql`.
2. **Skill contract** — `run(task: dict, context: dict) -> dict`. Skills stay Python.
3. **Config format** — `fleet.toml`. Rust ignores unknown keys (Python-only sections).

See `SHARED_CONTRACTS.md` for enforcement rules.

## What Rust does NOT do

- Host Python modules (CRM, CosmicTasha, etc.)
- Replace the Python development dashboard
- Port skills to Rust
- Maintain feature parity with Python's 256+ endpoints
- Run without Python present (PyO3 needs an interpreter)
