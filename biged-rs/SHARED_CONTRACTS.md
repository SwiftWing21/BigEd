# Shared Contracts — Python + Rust Parity Rules

## Overview

Three surfaces are the hard contract between the Python track (`fleet/`) and the Rust track (`biged-rs/`). Breaking any of them breaks interoperability. These are the ONLY things that must stay in lockstep — everything else diverges by design.

## Contract 1: Database Schema

**Python source of truth:** `fleet/db.py` + `fleet/db_tasks.py`
**Rust source of truth:** `biged-rs/crates/biged-core/src/schema.sql`

### Rules
- Both tracks read/write the same `fleet.db` (SQLite, WAL mode)
- Schema changes happen in **Python first**, Rust follows
- Any PR touching `db.py` schema must also update `schema.sql`
- Rust `Db::open()` uses `CREATE TABLE IF NOT EXISTS` so it doesn't conflict
- Rust can ignore Python-only tables (audit, experiment) that are created lazily

### Validated by
```bash
cargo test --test contract_test contract_db -- --nocapture
```

Tests: `contract_db_schema_rust_reads_python_db`, `contract_db_schema_rust_writes_then_reads`, `contract_db_new_phase_b_methods`

### Core tables (must match)
- `agents` (name, role, status, current_task_id, last_heartbeat, pid)
- `tasks` (id, created_at, assigned_to, status, priority, type, payload_json, result_json, error, parent_id, depends_on, review_rounds, conditions, classification, intelligence_score, trace_id)
- `messages` (id, from_agent, to_agent, created_at, read_at, body_json, channel)
- `notes` (id, channel, from_agent, created_at, body_json)
- `locks` (name, holder, acquired_at)
- `usage` (id, created_at, skill, model, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd, task_id)

## Contract 2: Skill Execution

**Python source of truth:** `fleet/skills/*.py`
**Rust bridge:** `biged-rs/crates/biged-bridge/src/runner.rs`

### Rules
- Skills are Python, signature: `run(task: dict, context: dict) -> dict`
- Some skills accept 3 args: `run(task, context, log)` — the bridge auto-detects arity
- Never change the skill contract without updating both the Python worker and `biged-bridge`
- Skills are NOT ported to Rust. Ever. The bridge calls Python via PyO3.
- Skill timeouts are configured in `BridgeConfig` (default 600s, overrides for code_write/security)

### Validated by
```bash
cargo test --test contract_test contract_skill -- --nocapture
cargo test --test bridge_test -- --nocapture
```

Tests: `contract_skill_smoke_echo`, `contract_skill_missing_graceful`, plus 9 bridge tests

### The smoke_echo skill
`fleet/skills/smoke_echo.py` exists specifically for contract testing. It echoes its input back with `bridge: "rust-pyo3"`. Both tracks can verify bridge parity by calling it.

## Contract 3: Config Format

**Shared file:** `fleet/fleet.toml`
**Python parser:** `fleet/config.py`
**Rust parser:** `biged-rs/crates/biged-core/src/config.rs`

### Rules
- `fleet.toml` is the single source of truth for both tracks
- Both parsers handle the same core keys (dashboard, models, thermal, workers, fleet)
- Rust uses `#[serde(flatten)]` + `toml::Table` extras to capture unknown keys without erroring
- Python-only keys (e.g., `[dashboard.experimental]`) are silently ignored by Rust
- New config keys are added to Python first; Rust adds support only if the key is production-relevant

### Validated by
```bash
cargo test --test contract_test contract_config -- --nocapture
```

Tests: `contract_config_parses_python_fleet_toml`, `contract_config_unknown_keys_ignored`, `contract_config_defaults_when_minimal`

## What is NOT shared (deliberately)

- REST endpoint set (Rust is a strict subset — 50 vs 228+)
- UI layer (Jinja+JS vs egui)
- Test suites (different concerns)
- Deployment / packaging (pip vs single binary)
- Module system (Python-only)
- Feature velocity (Python moves fast, Rust is stable)

## Enforcement

The contract test suite (`cargo test --test contract_test`) runs 8 tests across all 3 contracts. It should run:
- Before any release build
- Before any PR that touches `db.py`, `schema.sql`, `config.py`, `config.rs`, or the skill contract
- In CI when CI is enabled

If any contract test fails, the tracks have diverged and interoperability is broken. Fix the contract before merging.
