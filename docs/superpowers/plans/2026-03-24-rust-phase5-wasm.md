# BigEd Rust Rewrite — Phase 5: WASM + Polish (5 parallel agents)

> **For agentic workers:** 5 parallel agents, each owns one workstream. No file overlap.

**Goal:** Feature-gate GUI for WASM browser deployment, fill supervisor stubs, complete server endpoints, add CI/CD, and expand test coverage to hit graduation gate.

**Architecture:** eframe already supports WASM via `eframe::WebRunner`. Feature-gate desktop vs web in biged-gui. New biged-wasm crate wraps the web entry point. Supervisor stubs (backup, scaler) get real implementations. GitHub Actions CI covers 3 platforms + WASM.

**Tech Stack:** wasm-bindgen, web-sys (WASM), notify (file watcher), GitHub Actions (CI)

---

## Workstream Assignment (5 agents, no file overlap)

### Agent 1: WASM Feature Gates + Web Entry Point
**Owns:** `biged-rs/crates/biged-wasm/`, modifications to `biged-gui/Cargo.toml`
- Create biged-wasm crate with web entry point using `eframe::WebRunner`
- Feature-gate biged-gui: `desktop` (default, eframe native) vs `web` (eframe wasm)
- Add workspace deps: wasm-bindgen, web-sys
- Create `index.html` for WASM app serving
- Test: `cargo check --target wasm32-unknown-unknown -p biged-wasm`

### Agent 2: Supervisor Stubs → Real Implementations
**Owns:** `biged-rs/crates/biged-supervisor/src/backup.rs`, `scaler.rs`
- Implement backup.rs: periodic fleet.db + rag.db + fleet.toml snapshots
- Implement scaler.rs: dynamic worker count based on queue depth + thermal
- Wire both into supervisor.rs event loop as tokio tasks
- Tests in `biged-rs/tests/supervisor_backup_test.rs` and `supervisor_scaler_test.rs`

### Agent 3: Server Completeness + Settings Persistence
**Owns:** `biged-rs/crates/biged-server/src/handlers/settings.rs`, new handler files
- Settings persistence: read/write fleet.toml via API
- Add missing endpoints: `/api/metrics` (basic stats), `/api/config` (read-only config view)
- WebSocket upgrade prep: add tokio-tungstenite to deps, create ws.rs stub
- Tests in `biged-rs/tests/server_settings_test.rs`

### Agent 4: GitHub Actions CI Workflow
**Owns:** `.github/workflows/rust.yml`
- Matrix: windows-latest, ubuntu-latest, macos-latest
- Steps: cargo check, cargo test, cargo clippy, cargo fmt --check
- WASM target: cargo check --target wasm32-unknown-unknown (ubuntu only)
- Cargo cache via actions/cache
- PYO3_PYTHON setup for bridge tests

### Agent 5: Test Expansion + Benchmark Harness
**Owns:** `biged-rs/tests/integration_test.rs`, `biged-rs/benches/`
- End-to-end integration test: supervisor + server + bridge lifecycle
- Expand gui_test.rs and supervisor_test.rs beyond stubs
- Create benchmark harness: task throughput, startup time, memory baseline
- Smoke test parity check: verify all 38 Python smoke tests have Rust equivalents
