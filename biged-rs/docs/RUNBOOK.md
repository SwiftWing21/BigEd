# BigEd Rust -- Operator Runbook

## Starting the System

### Full Stack (single terminal)

```bash
biged supervisor &
biged serve &
biged gui --server-url http://localhost:5555
```

### Individual Components

```bash
biged supervisor          # Process lifecycle + thermal + health (default if no subcommand)
biged serve               # REST API + SSE on :5555
biged worker              # Single worker (claims tasks from fleet.db, runs skills via PyO3)
biged thermal             # Thermal monitor only (GPU/CPU)
biged gui                 # Desktop GUI (connects to --server-url, default localhost:5555)
biged migrate             # Verify DB compatibility with existing fleet.db
```

### Headless Mode

```bash
biged --headless          # Supervisor without GUI capability
```

## Health Checks

```bash
curl http://localhost:5555/api/health     # Server health
curl http://localhost:5555/api/status     # Fleet status (agents, tasks, workers)
curl http://localhost:5555/api/thermal    # GPU/CPU temperatures
curl http://localhost:5555/api/metrics    # System metrics (Prometheus-compatible)
```

## Configuration Reference

The Rust binary reads `fleet/fleet.toml` from the current working directory.
All defaults are shown below -- override only what you need.

```toml
[fleet]
offline_mode = false
air_gap_mode = false
eco_mode = false
idle_enabled = true
idle_timeout_secs = 10
max_workers = 10

[models]
local = "qwen3:8b"
conductor_model = "qwen3:4b"
ollama_host = "http://localhost:11434"
keep_alive_mins = 30

[dashboard]
enabled = true
port = 5555
bind_address = "127.0.0.1"
auto_open = true
theme = "figma"

[workers]
max_workers = 6
coder_count = 3
memory_limit_mb = 384
cpu_limit_percent = 10
nice_level = 15

[thermal]
gpu_target_c = 75
gpu_max_sustained_c = 82
gpu_max_burst_c = 85
cpu_max_sustained_c = 85
poll_interval_secs = 5
cooldown_window_secs = 120

[backup]
enabled = true
interval_secs = 1200
location = "~/BigEd-backups"
depth = 10
prune_enabled = true
warn_disk_usage_pct = 80
```

## Troubleshooting

### "Cannot find Python"

The worker subcommand needs Python 3.12+ for PyO3 skill execution. Set
`PYO3_PYTHON` to your Python path:

```bash
export PYO3_PYTHON=/usr/bin/python3.12    # Linux/macOS
set PYO3_PYTHON=C:\Python312\python.exe   # Windows
```

### "Database locked"

Both Python and Rust fleets use WAL mode, so they can coexist. If you see lock
errors, ensure only one supervisor is running at a time. Multiple workers and
readers are fine.

### "GUI won't start"

The GUI needs a running server. Start `biged serve` first, then launch the GUI:

```bash
biged serve &
biged gui --server-url http://localhost:5555
```

If the GUI crashes immediately, check that your GPU drivers support OpenGL 3.3+
(required by egui).

### "Skill execution timeout"

The bridge enforces per-skill timeouts:

| Skill | Timeout |
|-------|---------|
| `code_write` | 900s |
| `code_write_review` | 900s |
| `fma_review` | 900s |
| `pen_test` | 600s |
| `security_audit` | 600s |
| All others | 600s (default) |

If a skill consistently times out, check whether it is waiting on an Ollama
model that is not loaded. Run `curl http://localhost:11434/api/tags` to verify.

### Log verbosity

Structured JSON logs are emitted to stdout. Adjust with `RUST_LOG`:

```bash
RUST_LOG=biged=debug biged supervisor    # Verbose
RUST_LOG=biged=warn biged serve          # Quiet
RUST_LOG=biged_bridge=trace biged worker # Trace skill execution only
```

## Backup & Recovery

Backups are configured in the `[backup]` section of `fleet.toml`:

- **Interval:** `interval_secs` (default 1200 = 20 minutes)
- **Location:** `location` (default `~/BigEd-backups`)
- **Retention:** `depth` snapshots kept (default 10), oldest pruned when `prune_enabled = true`
- **Disk warning:** alerts at `warn_disk_usage_pct` (default 80%)

Manual backup:

```bash
cp fleet/fleet.db fleet/fleet.db.bak
```

## Monitoring

### SSE Event Stream

```bash
curl -N http://localhost:5555/api/stream
```

Events are broadcast via an in-process channel (256-slot buffer). The event bus
is created when `biged serve` starts.

### Prometheus Metrics

```bash
curl http://localhost:5555/api/metrics
```

Scrape this endpoint with your Prometheus instance at 15-30s intervals.

## Stopping the System

Send SIGTERM (or Ctrl+C on the foreground process). The supervisor will:

1. Drain in-flight tasks (wait up to 30s)
2. Unload Ollama models (`keep_alive=0`)
3. Close the SQLite WAL checkpoint
4. Exit cleanly

For a forceful stop: `pkill -9 biged` (not recommended -- may leave WAL uncheckpointed).
