# BigEd -- Python to Rust Migration Guide

## Overview

The `biged` binary replaces multiple Python processes with a single executable.
It is built as a Cargo workspace (`biged-rs/`) comprising six crates:

| Crate | Role |
|-------|------|
| `biged-core` | Config (`FleetConfig`), DB, error types |
| `biged-supervisor` | Process lifecycle, event bus |
| `biged-server` | Axum REST API + SSE (port 5555) |
| `biged-bridge` | PyO3 skill runner + worker loop |
| `biged-gui` | egui desktop GUI |
| `biged` (root) | CLI entry point, subcommand dispatch |

## What Changes

| Before (Python) | After (Rust) | Command |
|-----------------|--------------|---------|
| `python supervisor.py` | `biged supervisor` | Process lifecycle, scaling, health |
| `python hw_supervisor.py` | `biged thermal` | GPU/CPU monitoring, model management |
| `python worker.py --role X` | `biged worker` | Task execution via PyO3 bridge |
| `python dashboard.py` | `biged serve` | REST API + SSE on port 5555 |
| `python launcher.py` | `biged gui` | Native desktop GUI (egui) |
| Flask (port 5556) | `biged serve` | Unified server (no separate Flask) |

Running `biged` with no subcommand defaults to `supervisor`.

## What Stays Python

- All 130+ skills in `fleet/skills/` (executed via PyO3 bridge)
- `skills/_models.py` (LLM provider wrappers)
- Enterprise modules (SSO, billing, compliance)
- Messaging bridges (Discord, OpenClaw)
- `autoresearch/` ML training pipeline
- `fleet.toml` config format (unchanged)

The Rust worker (`biged worker`) calls into Python skills via PyO3. Each skill's
`run(payload, config)` function is invoked directly -- no subprocess overhead.
Skill-specific timeouts are enforced by the bridge (default 600s, with overrides
for long-running skills like `code_write` at 900s).

## Migration Steps

1. **Install the `biged` binary** (see install guide or `cargo install --path biged-rs`)
2. **Ensure Python 3.12+ is installed** -- required for skill execution via PyO3
3. **Run `biged migrate`** to verify DB compatibility with the existing `fleet.db`
4. **Start the supervisor:** `biged supervisor`
   - This replaces both `supervisor.py` and `hw_supervisor.py`
5. **Start the server:** `biged serve`
   - Reads `fleet/fleet.toml` and opens `fleet/fleet.db` (WAL mode)
   - Binds to the address/port from `[dashboard]` config (default `127.0.0.1:5555`)
6. **Optional -- launch the GUI:** `biged gui --server-url http://localhost:5555`
   - The `--server-url` flag defaults to `http://localhost:5555` if omitted

## Rollback

Both Python and Rust can coexist -- they share the same `fleet.db` (WAL mode).
To rollback: stop `biged`, then start `python supervisor.py` + `python dashboard.py`.

There is no schema difference; the Rust binary reads and writes the same SQLite
tables. You can switch between Python and Rust freely.

## Configuration

Same `fleet.toml` -- no config changes needed. The Rust binary reads all sections:

| Section | Key fields |
|---------|-----------|
| `[fleet]` | `offline_mode`, `max_workers`, `idle_timeout_secs`, `disabled_agents` |
| `[models]` | `local`, `complex`, `ollama_host`, `keep_alive_mins` |
| `[dashboard]` | `port`, `bind_address`, `cors_origins`, `theme` |
| `[workers]` | `max_workers`, `coder_count`, `memory_limit_mb`, `cpu_limit_percent` |
| `[thermal]` | `gpu_target_c`, `gpu_max_sustained_c`, `poll_interval_secs` |
| `[budgets]` | `period`, `enforcement`, per-skill overrides |
| `[backup]` | `enabled`, `interval_secs`, `location`, `depth` |

Unknown sections (e.g. `[naming]`, `[ditl]`) are preserved via `serde(flatten)` --
they round-trip through the Rust binary without data loss.

Settings changed via the REST API persist back to `fleet.toml`.

## Known Differences

- **Startup:** ~50x faster (no Python interpreter boot)
- **Task throughput:** ~10x higher (native SQLite, no GIL contention)
- **Memory usage:** ~5x lower (no Python runtime overhead per-process)
- **GUI:** runs at 60fps (egui GPU-accelerated vs customtkinter)
- **Logging:** structured JSON via `tracing` (set `RUST_LOG=biged=debug` for verbose)
- **Event bus:** in-process broadcast channel (256 slots) replaces the Python SSE polling loop
