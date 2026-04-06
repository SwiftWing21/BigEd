# BigEd CC — Rust Production Track

**Two tracks, two jobs, no parity chase.**

The Rust binary is the **production deployment path** — a hardened, 11 MB single-binary service for machines that need to run BigEd without a Python development environment. Python stays primary for development. See the main `CLAUDE.md` for the Python track.

| Track | Purpose | Endpoints | UI |
|-------|---------|-----------|-----|
| **Python** (`fleet/`) | Development, full dashboard, skills, modules | 228+ | Jinja + vanilla JS |
| **Rust** (`biged-rs/`) | Production deployment, service tier | 50 | egui (5 sections) |

They share a database, a skill contract, and a config format. Nothing else.

## Quick Start

```bash
# Build
cargo build --release   # 11 MB binary, ~80s

# Run supervisor + server + thermal
./target/release/biged supervisor

# Or just the HTTP server
./target/release/biged serve

# Desktop operator GUI
./target/release/biged gui

# Migrate from Python fleet
./target/release/biged migrate --from ../fleet
```

## Crates

| Crate | Purpose |
|-------|---------|
| `biged-core` | Config, DB pool, task queue, shared types |
| `biged-supervisor` | Tokio supervisor, thermal, health, backup, scaler |
| `biged-server` | Axum REST API, SSE, 50 endpoints |
| `biged-bridge` | PyO3 skill execution (125+ Python skills) |
| `biged-gui` | egui native desktop GUI (5-section operator UI) |
| `biged-wasm` | Browser GUI (WASM target) |

## Subcommands

```
biged supervisor    Process lifecycle, Ollama, scaling, health
biged serve         REST API + SSE on :5555
biged worker        Single task worker (PyO3 bridge)
biged thermal       GPU/CPU thermal monitor
biged gui           Native desktop GUI
biged migrate       Import from Python fleet directory
```

## Tests

```bash
# Unit + integration tests
cargo test --workspace --exclude biged-wasm

# Shared contract tests (validates Python/Rust parity)
cargo test --test contract_test -- --nocapture

# PyO3 bridge tests (requires Python DLL on PATH)
cargo test --test bridge_test -- --nocapture
```

## Docs

- `DEPLOYMENT.md` — full deployment guide, endpoint list, operator GUI
- `SHARED_CONTRACTS.md` — the 3 hard contracts between Python and Rust

## Requirements

- Rust 1.76+ (stable)
- Python 3.11+ (for PyO3 skill execution — even in service-only mode)
- `fleet.toml` config in parent `fleet/` directory
- Ollama (auto-managed by supervisor)
