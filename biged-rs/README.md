# BigEd CC -- Rust Runtime

Single-binary fleet management system. Replaces the Python infrastructure with native performance.

## Quick Start

```bash
# Build
cargo build --release

# Run supervisor (process lifecycle + thermal + health)
./target/release/biged supervisor

# Run API server (REST + SSE on :5555)
./target/release/biged serve

# Launch desktop GUI
./target/release/biged gui
```

## Crates

| Crate | Purpose |
|-------|---------|
| `biged-core` | Config, DB pool, task queue, shared types |
| `biged-supervisor` | Tokio supervisor, thermal, health, backup, scaler |
| `biged-server` | Axum REST API, SSE, 27+ endpoints |
| `biged-bridge` | PyO3 skill execution (130+ Python skills) |
| `biged-gui` | egui native desktop GUI |
| `biged-wasm` | Browser GUI (WASM target) |

## Subcommands

```
biged supervisor    Process lifecycle, scaling, health checks
biged serve         REST API + SSE on port 5555
biged worker        Single task worker
biged thermal       GPU/CPU thermal monitor
biged gui           Native desktop GUI
biged migrate       Database migration check
```

## Requirements

- Rust 1.76+ (stable)
- Python 3.12+ (for skill execution via PyO3)
- `fleet.toml` config in parent `fleet/` directory

## Tests

```bash
cargo test --workspace --exclude biged-wasm
```

## Benchmarks

```bash
cargo bench --bench task_throughput
```
