# BigEd CC — Operations Manual

> Complete ops runbook: quick start, CLI reference, platform troubleshooting,
> skill/module authoring, deployment, monitoring, backup, and recovery.
> Companion to `FRAMEWORK_BLUEPRINT.md` (architecture) and `ROADMAP_v030_v040.md` (future work).

---

## Quick Start

```bash
# 1. Start fleet
uv run python fleet/supervisor.py              # Linux/macOS — direct
# Windows: either run inside WSL, or set BIGED_NATIVE_WINDOWS=1

# 2. Start dashboard (web UI on http://localhost:5555)
uv run python fleet/dashboard.py

# 3. Start launcher (tkinter desktop app)
python BigEd/launcher/launcher.py

# 4. Check fleet health
uv run python fleet/lead_client.py status
```

---

## CLI Reference

All commands below are run as `uv run python fleet/lead_client.py <command>`.

### Fleet Status & Control

| Command | Description |
|---------|-------------|
| `status` | Show all agents (name, role, status, last heartbeat) and task counts |
| `detect-cli` | Detect best local CLI, shell, network tools, and bridge for this platform |
| `install-service` | Install fleet as auto-start service (Task Scheduler / systemd / launchd) |
| `uninstall-service` | Remove the auto-start service |

### Task Management

| Command | Description |
|---------|-------------|
| `task "instruction"` | Submit natural language task (parsed by conductor model). Add `--wait` to block until complete. `--priority N` (1-10, default 5) |
| `task '{"skill":"web_search","query":"..."}' ` | Submit raw JSON task (bypasses intent parser) |
| `dispatch skill payload` | Dispatch explicit skill + JSON payload. `--priority N` (default 9), `--assigned-to agent`, `--b64` for base64 payload |
| `result <task_id>` | Fetch status and result of a specific task |

### Messaging

| Command | Description |
|---------|-------------|
| `send agent "msg"` | Direct message to a specific agent. `--channel fleet\|sup\|agent\|pool` |
| `broadcast "msg"` | Broadcast message to all registered agents. `--channel` same as above |
| `inbox agent` | Check agent inbox (unread by default). `--all` for all, `--limit N`, `--channel` filter |
| `notes channel` | Read channel scratchpad. `--post "json"` to add, `--since ISO`, `--limit N` |

### Cost Intelligence

| Command | Description |
|---------|-------------|
| `usage --period day\|week\|month` | Token usage breakdown by skill (calls, input/output tokens, cost USD, cache savings) |
| `usage-delta from_start from_end to_start to_end` | Compare usage between two ISO date ranges (delta %, direction arrows) |
| `budget` | Token budget status per skill (from `[budgets]` in fleet.toml) |

### Marathon / Training

| Command | Description |
|---------|-------------|
| `marathon [session]` | List marathon sessions (last 5). Pass session ID for last 3 snapshots |
| `marathon-checkpoint` | Show autoresearch training checkpoints (last 10 `.pt` files, size, modified) |

### Logs

| Command | Description |
|---------|-------------|
| `logs agent --tail N` | Tail the log file for a specific agent (default 30 lines) |

### Secrets

| Command | Description |
|---------|-------------|
| `secret set KEY value` | Set an API key in `~/.secrets` (atomic write). `--b64` for base64-encoded value |
| `secret get KEY` | Retrieve a secret value |
| `secret list` | List all secret keys (values masked) |

---

## Platform Troubleshooting Matrix (PT-4)

### Startup Issues

| Issue | Windows | Linux | macOS |
|-------|---------|-------|-------|
| **Ollama not starting** | Check Windows Ollama installer; verify `http://localhost:11434` responds. Restart: `taskkill /f /im ollama.exe && ollama serve` | `systemctl status ollama` or `ollama serve &`. Check `journalctl -u ollama` | `brew services start ollama`. Check `brew services list` |
| **Fleet not starting** | Run inside WSL: `wsl -d Ubuntu -- bash -c "cd /mnt/c/.../fleet && uv run python supervisor.py"`. Or set `BIGED_NATIVE_WINDOWS=1` for native mode | Direct: `uv run python supervisor.py` | Direct: `uv run python supervisor.py` |
| **Dashboard won't launch** | Ensure Flask installed: `uv pip install flask`. Check port 5555: `netstat -an \| findstr 5555` | `ss -tlnp \| grep 5555`. Kill squatter: `fuser -k 5555/tcp` | `lsof -i :5555`. Kill: `kill $(lsof -t -i :5555)` |
| **Launcher won't start** | Check Python has tkinter: `python -c "import tkinter"`. Usually bundled on Windows | `sudo apt install python3-tk` (Debian/Ubuntu) or `sudo pacman -S tk` (Arch) | `brew install python-tk@3.11` |
| **Auto-boot not working** | Check Task Scheduler: `schtasks /query /tn BigEdFleet`. Re-run `install-service` if missing | `systemctl --user status biged-fleet`. Check `~/.config/systemd/user/biged-fleet.service` | `launchctl list \| grep biged`. Check `~/Library/LaunchAgents/com.biged.fleet.plist` |

### GPU / Hardware Issues

| Issue | Windows | Linux | macOS |
|-------|---------|-------|-------|
| **GPU not detected** | Install `nvidia-ml-py`: `pip install nvidia-ml-py`. Verify NVIDIA driver: `nvidia-smi` | Install `nvidia-ml-py` + CUDA toolkit. Verify: `nvidia-smi` | No NVIDIA GPU on macOS — CPU-only mode. Apple Silicon uses Metal via Ollama automatically |
| **pynvml ImportError** | `pip install nvidia-ml-py` (not `pynvml`) | `pip install nvidia-ml-py` | N/A — skip pynvml. hw_supervisor detects absence and runs CPU-only |
| **Training OOM** | Ensure Ollama models fully evicted before training. Check `DEPTH` in `MACHINE_PROFILE.md` (max DEPTH=6 for 12GB VRAM). Run Ollama on CPU during training: `CUDA_VISIBLE_DEVICES=-1 ollama serve &` | Same approach. Use `nvidia-smi` to verify GPU memory freed before `train.py` | N/A — no GPU training on macOS. CPU-only autoresearch works but is slow |
| **Thermal throttling** | Check `hw_state.json` — if `thermal.gpu_temp_c > 75`, hw_supervisor auto-downscales model tier. Increase fan curve or lower `[thermal] gpu_max_sustained_c` in fleet.toml | Same. Also: `sensors` for CPU temps, `nvidia-smi -l 1` for live GPU temp | N/A — no NVIDIA thermal management. Ollama handles Apple Silicon throttling internally |
| **ROCm not found (AMD)** | AMD GPUs not supported on Windows for Ollama | Install ROCm per AMD docs. Ollama uses ROCm automatically when available | N/A |

### Networking Issues

| Issue | Windows | Linux | macOS |
|-------|---------|-------|-------|
| **WSL networking** | Enable mirrored networking in `%USERPROFILE%\.wslconfig`: `[wsl2]` / `networkingMode=mirrored`. Restart WSL: `wsl --shutdown && wsl` | N/A | N/A |
| **Dashboard unreachable from browser** | Check firewall allows port 5555. If running in WSL, use `localhost:5555` with mirrored networking, or WSL IP from `wsl hostname -I` | Check `ufw status` — allow 5555 if needed | Check System Preferences > Security > Firewall |
| **Ollama unreachable from fleet** | Verify `fleet.toml [models] ollama_host` matches. Default: `http://localhost:11434`. In WSL, may need `http://host.docker.internal:11434` or mirrored networking | Verify host setting. If Docker: expose port 11434 | Same — check host setting |
| **API calls failing (429s)** | Fleet auto-throttles at 20% of rate limits with 300ms min between requests and exponential backoff. Check `usage --period day` for budget overruns | Same | Same |

### Database Issues

| Issue | Windows | Linux | macOS |
|-------|---------|-------|-------|
| **DB locked / busy timeout** | Long-running write or crashed process holding WAL. Kill stale processes: `wsl -- pkill -f worker.py`, then restart supervisor | `pkill -f worker.py` then restart supervisor | `pkill -f worker.py` then restart supervisor |
| **Stale tasks stuck in RUNNING** | Worker died mid-task. Run: `python -c "import db; db.init_db(); print(db.recover_stale_tasks())"` | Same | Same |
| **Training lock stuck** | Training process crashed without releasing. Run: `python -c "import db; db.init_db(); db.release_lock('training')"` | Same | Same |
| **fleet.db corrupted** | Restore from backup: `~/BigEd-backups/<latest>/fleet.db`. Or delete and restart — supervisor recreates tables on boot | Same | Same |

### Skill / Module Issues

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| **Workers not claiming tasks** | `lead_client.py status` — agents show OFFLINE | Restart supervisor. Check worker logs in `fleet/logs/` |
| **Worker stuck on single task** | Skill timeout not triggering (default 600s) | Add entry in `worker.py:SKILL_TIMEOUTS` for slow skills |
| **Skill dispatch hangs** | Intent parser model not loaded | Ensure conductor model loaded: `ollama list`. Check `fleet.toml [models] conductor_model` |
| **Module tab not appearing** | Module not in profile or disabled | Check `fleet.toml [launcher] profile` and `[launcher.tabs]` section |
| **Launcher can't find fleet** | `FLEET_DIR` resolution failing | Set env: `BIGED_FLEET_DIR=/path/to/fleet` or verify `fleet/fleet.toml` exists |
| **Agent flicker in UI** | Widget destroy/recreate pattern (pre-v0.32) | Update to v0.32+ which uses widget cache + configure pattern |

### Platform-Specific Edge Cases

| Issue | Platform | Fix |
|-------|----------|-----|
| **WSL not found** | Windows | `wsl --install` or `wsl --install -d Ubuntu`. Reboot after install |
| **Gatekeeper blocks launch** | macOS | Right-click the app > Open. Or: `xattr -d com.apple.quarantine BigEdCC.app` |
| **`python3` not `python`** | Linux | Alias in shell profile, or install `python-is-python3` package (Debian/Ubuntu) |
| **Permission denied on scripts** | Linux/macOS | `chmod +x scripts/backup.sh fleet/supervisor.py` |
| **Git line endings break scripts** | Windows | Set `core.autocrlf=input` in `.gitattributes`. Re-clone or `git checkout -- scripts/` |

---

## Skill Authoring

### Interface

Every skill is a Python file in `fleet/skills/` with a single `run()` function:

```python
# fleet/skills/my_skill.py
def run(payload: dict, config: dict) -> dict:
    """
    payload — JSON parsed from task's payload_json
    config  — fleet.toml parsed via config.load_config()
    Returns a dict (serialized to result_json by the worker)
    """
    query = payload.get("query", "")
    # ... do work ...
    return {"summary": "...", "source": "..."}
```

### Using LLM Providers

Import the routing layer — never call Ollama/Claude/Gemini directly:

```python
from skills._models import call_model

result = call_model(prompt, config, provider="local")   # Ollama (default)
result = call_model(prompt, config, provider="claude")  # Claude API
result = call_model(prompt, config, provider="gemini")  # Gemini API
```

### Network Requirements

If a skill requires internet access, declare it at module level:

```python
REQUIRES_NETWORK = True  # Worker checks before dispatch; rejected in offline/air-gap mode
```

Skills without this flag (or with `REQUIRES_NETWORK = False`) are allowed in all modes.

### Registration

1. Save file as `fleet/skills/<skill_name>.py`
2. Add to `lead_client.py` intent parser prompt if it should be dispatchable by natural language
3. Add to `[affinity]` section in `fleet.toml` if it should route to specific worker roles
4. Add custom timeout in `worker.py:SKILL_TIMEOUTS` if >600s is needed

### Testing

```bash
cd fleet

# Verify all skills import cleanly
uv run python smoke_test.py --fast

# Test a single skill directly
uv run python -c "from skills.my_skill import run; print(run({'query': 'test'}, {}))"

# Test via fleet dispatch (requires running supervisor)
uv run python lead_client.py dispatch my_skill '{"query": "test"}' --wait
```

---

## Module Authoring

### Interface Contract

Create `BigEd/launcher/modules/mod_<name>.py` with a `Module` class:

```python
class Module:
    NAME = "my_module"          # matches fleet.toml key
    LABEL = "My Module"         # tab label in UI
    VERSION = "0.31"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []             # other module NAMEs required

    DATA_SCHEMA = {
        "table": "my_module_data",
        "fields": {
            "name": {"type": "text", "required": True},
            "status": {"type": "text", "enum": ["active", "inactive"]},
        },
        "retention_days": None,
    }

    def __init__(self, app):
        self.app = app

    def build_tab(self, parent):
        """Build UI widgets into parent frame."""
        pass

    def on_refresh(self):
        """Called periodically by launcher timer. Use _db_query_bg for DB work."""
        pass

    def on_close(self):
        """Cleanup on app exit."""
        pass

    def get_settings(self) -> dict:
        return {}

    def apply_settings(self, cfg):
        pass

    def export_data(self) -> list[dict]:
        """Return all records as list of dicts for data portability."""
        return []

    def validate_record(self, data) -> tuple[bool, str]:
        """Validate a record against DATA_SCHEMA."""
        return True, "OK"
```

### Registration

1. Save as `BigEd/launcher/modules/mod_<name>.py` — auto-discovered by `discover_modules()`
2. Add to `manifest.json` (auto-added on first load, but you can pre-populate)
3. Add to profile in `modules/__init__.py:DEPLOYMENT_PROFILES` if it belongs to a profile
4. Enable in `fleet.toml` under `[launcher.tabs]`: `my_module = true`

### Async DB Pattern

Never block the UI thread. Use the launcher's background query helper:

```python
def on_refresh(self):
    def _fetch(con):
        return con.execute("SELECT * FROM my_table").fetchall()
    def _render(rows):
        if rows is None:
            return
        # update widgets here
    self.app._db_query_bg(_fetch, _render)
```

---

## Deployment (Fresh Machine)

### Windows (Current Production)

**Prerequisites:** Python 3.11+, Git, WSL2 (fleet runs inside WSL), Ollama

```bash
# 1. Clone
git clone <repo-url> Education && cd Education

# 2. Install launcher deps (Windows native Python)
cd BigEd/launcher
pip install -r requirements.txt

# 3. Install fleet deps (WSL)
wsl
cd /mnt/c/Users/<you>/Projects/Education/fleet
pip install httpx anthropic  # or: uv sync

# 4. Configure
cp fleet/fleet.toml.example fleet/fleet.toml   # if template exists
# Edit fleet.toml:
#   [launcher] profile = "consulting"    # or minimal/research/full
#   [models]   local = "qwen3:8b"        # adjust for your GPU VRAM
#   [thermal]  gpu_max_sustained_c = 75  # adjust for your card

# 5. Set API keys
python fleet/lead_client.py secret set ANTHROPIC_API_KEY <key>
python fleet/lead_client.py secret set GEMINI_API_KEY <key>

# 6. Pull Ollama models
ollama pull qwen3:8b     # GPU workers (skip if no GPU)
ollama pull qwen3:4b     # conductor (CPU)
ollama pull qwen3:0.6b   # maintainer (CPU, always loaded)

# 7. Verify
cd fleet
python smoke_test.py --fast   # should pass all checks

# 8. Start fleet (WSL)
nohup python supervisor.py >> logs/supervisor.log 2>&1 &

# 9. Start launcher (Windows)
cd BigEd/launcher
python launcher.py
```

### Linux

**Prerequisites:** Python 3.11+, `python3-tk` (system package), Git, Ollama

```bash
# 1. Clone
git clone <repo-url> Education && cd Education

# 2. Install deps (single environment — no WSL needed)
pip install -r BigEd/launcher/requirements.txt
cd fleet && pip install httpx anthropic  # or: uv sync

# 3. Configure (same as Windows)
cp fleet/fleet.toml.example fleet/fleet.toml
# Edit fleet.toml as needed

# 4. Set API keys
python fleet/lead_client.py secret set ANTHROPIC_API_KEY <key>

# 5. Pull Ollama models
ollama pull qwen3:8b

# 6. Verify
cd fleet && python smoke_test.py --fast

# 7. Start fleet (native — no WSL bridge)
nohup python supervisor.py >> logs/supervisor.log 2>&1 &

# 8. Start launcher (same machine)
python BigEd/launcher/launcher.py
```

**Key difference:** No WSL layer. Fleet and launcher run in the same OS. `DirectBridge` replaces `wsl()`/`wsl_bg()` calls.

### macOS

**Prerequisites:** Python 3.11+ (Homebrew), `python-tk` (brew), Git, Ollama

Same steps as Linux. Additional notes:
- `brew install python-tk@3.11` if tkinter import fails
- First launch may trigger Gatekeeper — right-click > Open to bypass
- No NVIDIA GPU support. CPU-only Ollama or Apple Silicon-optimized models
- If using `.app` bundle: drag to `/Applications/`, launch normally

### Hardware Sizing

| GPU VRAM | Recommended `local` model | Notes |
|----------|--------------------------|-------|
| None     | CPU-only (`qwen3:4b`, `num_gpu: 0`) | Slow but functional |
| 6-8 GB   | `qwen3:4b` (GPU) | Mid-tier, reliable |
| 10-12 GB | `qwen3:8b` (GPU) | Full quality, thermal management recommended |
| 16+ GB   | `qwen3:8b` + headroom for training | Can run Ollama during autoresearch |

### VRAM Safety Rules

- **Safe ceiling:** 10 GB for a 12 GB card (leaves headroom for OS/display)
- **Sweet spot:** DEPTH=6, ~26M params, ~6.9 GB VRAM
- **DEPTH=7+ OOMs** on 12 GB cards — never exceed DEPTH=6
- **Training + Ollama:** Run Ollama on CPU during training: `CUDA_VISIBLE_DEVICES=-1 ollama serve &`
- **Eco mode** (default): CPU-only, ~40% CPU utilization, 0 VRAM

---

## Operations

### Start / Stop

**Windows:**
```bash
# Start fleet (WSL)
wsl -d Ubuntu -- bash -c "cd /mnt/c/.../fleet && nohup uv run python supervisor.py >> logs/supervisor.log 2>&1 &"

# Start hardware supervisor (WSL, optional — GPU systems only)
wsl -d Ubuntu -- bash -c "cd /mnt/c/.../fleet && nohup uv run python hw_supervisor.py >> logs/hw_supervisor.log 2>&1 &"

# Start dashboard (WSL or Windows)
uv run python fleet/dashboard.py              # default http://localhost:5555
uv run python fleet/dashboard.py --port 8080  # custom port

# Start launcher (Windows native)
python BigEd/launcher/launcher.py

# Stop fleet — graceful
wsl -d Ubuntu -- uv run python lead_client.py broadcast '{"type": "pause"}'
# Stop fleet — force
wsl -d Ubuntu -- pkill -f supervisor.py
```

**Linux / macOS:**
```bash
# Start fleet (native — no WSL)
cd fleet
nohup uv run python supervisor.py >> logs/supervisor.log 2>&1 &
nohup uv run python hw_supervisor.py >> logs/hw_supervisor.log 2>&1 &  # optional, GPU only

# Start dashboard
uv run python dashboard.py

# Start launcher
python BigEd/launcher/launcher.py

# Stop fleet
pkill -f supervisor.py
# Or gracefully:
uv run python lead_client.py broadcast '{"type": "pause"}'
```

### Health Checks

```bash
# Fleet status (agents + task counts)
uv run python lead_client.py status

# Check specific agent log
uv run python lead_client.py logs researcher --tail 50

# Verify DB integrity
uv run python -c "import db; db.init_db(); print(db.get_fleet_status())"

# Quick smoke test (imports + connectivity)
uv run python smoke_test.py --fast

# Extended soak test
uv run python soak_test.py

# Platform detection (shell, network tools, bridge type)
uv run python lead_client.py detect-cli
```

### Log Locations

| Component | Log Path |
|-----------|----------|
| Supervisor | `fleet/logs/supervisor.log` |
| HW Supervisor | `fleet/logs/hw_supervisor.log` |
| Workers | `fleet/logs/<role>.log` (e.g., `researcher.log`, `coder_1.log`) |
| Dashboard | stdout (Flask dev server) |
| Launcher | stdout (tkinter) |

### Key Files (Runtime)

| File | Purpose | Managed By |
|------|---------|-----------|
| `fleet/fleet.db` | Task queue, agents, messages, usage tracking | `db.py` (SQLite WAL mode) |
| `fleet/rag.db` | RAG document embeddings and chunks | RAG skills |
| `fleet/hw_state.json` | GPU temps, VRAM, model tier state (updated every 5s) | `hw_supervisor.py` |
| `fleet/STATUS.md` | Human-readable fleet snapshot | `supervisor.py` |
| `fleet/fleet.toml` | Configuration (models, thermal, tabs, budgets, offline mode) | Manual / `config.py` |
| `fleet/keys_registry.toml` | API key registry metadata | `lead_client.py` |
| `~/.secrets` | API keys (`export KEY='value'` format) | `lead_client.py secret` |
| `BigEd/launcher/data/tools.db` | Launcher module data (tools, records) | Launcher modules |

### Operational Modes

| Mode | Config Flag | Behavior |
|------|------------|----------|
| **Normal** | (default) | All skills, APIs, Discord/OpenClaw active |
| **Eco** | `eco_mode = true` | CPU-only Ollama, ~40% CPU, 0 VRAM. Default mode |
| **Offline** | `offline_mode = true` | External API skills rejected, local Ollama works, Discord/OpenClaw skipped |
| **Air-Gap** | `air_gap_mode = true` | Implies offline + dashboard disabled, secrets not loaded, deny-by-default skill whitelist |

---

## Monitoring

### Dashboard Endpoints

The dashboard runs on `http://localhost:5555` (configurable with `--port`).

**Core Status:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard web UI (HTML) |
| `/api/status` | GET | Fleet agents and task counts |
| `/api/activity` | GET | Recent task activity log |
| `/api/skills` | GET | Skill execution stats |
| `/api/timeline` | GET | Task execution timeline |
| `/api/fleet/health` | GET | Fleet health summary (agents, uptime, errors) |
| `/api/fleet/uptime` | GET | Uptime and restart history |
| `/api/fleet/idle` | GET | Idle worker detection |
| `/api/fleet/workers` | GET | Individual worker process status |

**Knowledge & RAG:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/knowledge` | GET | Knowledge artifact inventory |
| `/api/code_stats` | GET | Code review and index statistics |
| `/api/reviews` | GET | Recent code/FMA reviews |
| `/api/discussions` | GET | Active code discussions |
| `/api/rag` | GET | RAG database stats (chunks, sources) |

**Hardware & Thermal:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/thermal` | GET | GPU temperature, VRAM usage, model tier, thermal state from `hw_state.json` |
| `/api/training` | GET | Training status (active, checkpoints, VRAM allocation) |

**Cost Intelligence:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/usage` | GET | Token usage summary (params: `period`, `group_by`) |
| `/api/usage/delta` | GET | Usage comparison between date ranges |
| `/api/usage/budgets` | GET | Budget status per skill |
| `/api/usage/regression` | GET | Cost regression analysis |

**Communications:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/comms` | GET | Inter-agent message history |
| `/api/alerts` | GET | Active alerts (info/warning/critical) |
| `/api/alerts/ack/<id>` | POST | Acknowledge an alert |
| `/api/resolutions` | GET | Issue resolution tracking |
| `/api/modules` | GET | Launcher module status |
| `/api/data_stats` | GET | Data/record statistics across modules |

**Live Streaming:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stream` | GET | Server-Sent Events (SSE) — live task updates, alerts, agent status changes |

**Process Control:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fleet/start` | POST | Start/restart fleet workers |
| `/api/fleet/stop` | POST | Stop fleet workers |
| `/api/fleet/worker/<name>/restart` | POST | Restart a specific worker |
| `/api/fleet/marathon` | GET | Marathon session data |
| `/api/fleet/checkpoints` | GET | Training checkpoint inventory |

### Monitoring Checklist

For routine health monitoring, check these in order:

1. **Fleet status:** `lead_client.py status` — all agents should show ONLINE
2. **Task queue:** PENDING count should not grow unbounded; RUNNING should be <= worker count
3. **Thermal:** `curl localhost:5555/api/thermal` — GPU temp below `gpu_max_sustained_c` (default 75C)
4. **Dashboard health:** `curl localhost:5555/api/fleet/health` — no critical errors
5. **Usage:** `lead_client.py usage --period day` — no unexpected cost spikes
6. **Logs:** Check `fleet/logs/supervisor.log` for repeated errors or restart loops

---

## Backup & Recovery

### Running a Backup

```bash
bash scripts/backup.sh
```

This creates a timestamped snapshot in `~/BigEd-backups/<YYYYMMDD_HHMMSS>/` containing:

| File / Directory | Source | Purpose |
|-----------------|--------|---------|
| `fleet.db` | `fleet/fleet.db` | Task queue, agents, messages, usage data |
| `rag.db` | `fleet/rag.db` | RAG document embeddings and chunks |
| `tools.db` | `BigEd/launcher/data/tools.db` | Launcher module data |
| `knowledge/` | `fleet/knowledge/` | All worker artifacts (reviews, discussions, research, drafts) |
| `keys_registry.toml` | `fleet/keys_registry.toml` | API key registry metadata |

The script automatically prunes old backups, keeping the last 10.

### Recovery Procedures

**Full fleet reset (clean restart):**
```bash
# Stop fleet
pkill -f supervisor.py

# Optionally restore DB from backup
cp ~/BigEd-backups/<latest>/fleet.db fleet/fleet.db

# Restart — supervisor recreates missing tables on boot
uv run python fleet/supervisor.py
```

**RAG re-ingestion (after rag.db loss):**
```bash
# Restore from backup if available
cp ~/BigEd-backups/<latest>/rag.db fleet/rag.db

# Or re-ingest documents (rebuilds the index)
uv run python lead_client.py task "re-index all knowledge documents"
```

**Secrets recovery:**
```bash
# Secrets live in ~/.secrets, not in the backup
# Re-set if lost:
uv run python fleet/lead_client.py secret set ANTHROPIC_API_KEY <key>
uv run python fleet/lead_client.py secret set GEMINI_API_KEY <key>
```

**Knowledge artifact recovery:**
```bash
# Restore knowledge directory from backup
cp -r ~/BigEd-backups/<latest>/knowledge/ fleet/knowledge/
```

### Backup Schedule Recommendation

| Environment | Frequency | Method |
|------------|-----------|--------|
| Development | Before major changes | Manual: `bash scripts/backup.sh` |
| Production (single user) | Daily | Cron: `0 2 * * * bash /path/to/scripts/backup.sh` |
| Production (team) | Every 6 hours | Cron + off-site copy |

---

## Issue Reporting

### Generating a Debug Report

Two methods:

- **GUI:** Click "Report Issue" in the launcher sidebar. Fill in description, optional reproduction steps, check "Include logs". Click Submit — report saved to `reports/debug/`.
- **CLI:** `python -m biged.debug_report` — generates a snapshot without the GUI. Useful for headless or crashed states.

On unhandled exception, a report is auto-generated and the user is notified.

### Reading a Debug Report

Reports are JSON files in `reports/debug/debug_<timestamp>.json`:

| Section | What it tells you |
|---------|-------------------|
| `platform` | OS, Python version, architecture — rules out platform-specific issues |
| `hardware` | GPU model/VRAM/temp, CPU, RAM — identifies resource constraints |
| `fleet_state` | Agent statuses, task counts, Ollama state, thermal — fleet health snapshot |
| `error` | Exception type/traceback, component, trigger — the actual problem |
| `logs` | Last 50 lines of supervisor/worker logs, last 100 lines of launcher output |
| `config_snapshot` | Active profile, model tier, thermal limits — reproduction context |
| `reproduction_steps` | User-provided steps to reproduce (if manual report) |

### Submitting a Report

- **GitHub Issues (automated):** If `gh` CLI is installed, the "Report Issue" dialog offers "Submit to GitHub". Creates an issue with report summary in the body and full report attached. Labels: `bug` or `user-report`.
- **File export (manual):** Report saved as `.json` to Desktop. Attach to an email or GitHub issue manually.

### Privacy

Reports are sanitized before submission:
- API keys are stripped from the config snapshot
- User paths are anonymized (`C:\Users\max\...` becomes `~\...`)
- No file contents are included — only log tails and metadata
- Review the JSON before submitting if in doubt

See `FRAMEWORK_BLUEPRINT.md` S10-S11 for full debug report schema and resolution tracking lifecycle.

---

## Worker Roles Reference

| Worker | Role | Primary Skills |
|--------|------|---------------|
| researcher | Papers, arxiv, web search | `web_search`, `arxiv_fetch`, `lead_research` |
| coder_1..N | Code review (architect/critic/perf) | `code_review`, `code_discuss`, `code_index`, `fma_review`, `skill_draft`, `code_quality` |
| archivist | Flashcards, knowledge org | `summarize`, `synthesize` |
| analyst | Autoresearch results analysis | Analysis skills |
| sales | SMB lead research + outreach | `lead_research` |
| onboarding | Client onboarding checklists | Onboarding skills |
| implementation | Local AI deployment specs | Implementation skills |
| security | Security audits, pen tests, advisories | `security_audit`, `pen_test`, `security_review` |
| planner | Workload planning (queues 5-500 tasks) | Planning/scheduling |
| legal | Legal document review | Legal skills |
| account_manager | Account management | Account skills |

Coder count is configurable via `fleet.toml [workers] coder_count` (default 1).

### Skill Output Locations

| Skill | Output Directory |
|-------|-----------------|
| `code_discuss` | `knowledge/code_discussion/` + messages table |
| `code_index` | `knowledge/code_index.jsonl` |
| `code_review` | `knowledge/code_reviews/<file>_review_<date>_<agent>.md` |
| `fma_review` | `knowledge/fma_reviews/<file>_review_<date>_<agent>.md` + discussion |
| `skill_draft` | `knowledge/code_drafts/<name>_draft_<date>_<agent>.py` |
| `security_review` | `knowledge/security/reviews/security_review_<date>.md` |
| `code_quality` | `knowledge/quality/reviews/quality_review_<date>.md` |

Drafts are **never auto-deployed** — review before copying to `skills/`.

---

## Messaging Bridges

| Bridge | Config Flag | Status |
|--------|------------|--------|
| Discord (`discord_bot.py`) | `discord_bot_enabled` | Active — routes `biged-fleetchat` to fleet |
| OpenClaw gateway | `openclaw_enabled` | Installed, disabled by default |

Discord commands: `/aider`, `/claude`, `/gemini`, `/local`, `/status`, `/task`, `/result`, `/help`

---

## Dual Supervisor Architecture

| Supervisor | Responsibility | Loop Interval |
|-----------|---------------|---------------|
| `supervisor.py` | Process lifecycle: Ollama start/stop, worker respawn, training detection, Discord/OpenClaw | Continuous |
| `hw_supervisor.py` | Model health: keepalive (~240s), conductor check (~60s), VRAM/thermal scaling, model tier transitions | 5s state writes |

State file: `hw_state.json` — written by hw_supervisor every 5s, read by supervisor, workers, dashboard, and launcher. Contains: status, model, thermal, models_loaded, conductor status.

---

## Maintenance Protocol

This document is updated alongside code changes:

1. **Version bumps** — Review all sections when completing a roadmap phase
2. **New skill/module** — Add to relevant section in the same commit
3. **New failure mode** — Add to troubleshooting table when discovered
4. **Deployment changes** — Update deployment section if install steps change
5. **New CLI command** — Add to CLI Reference when `lead_client.py` gains a subcommand
6. **New dashboard endpoint** — Add to Monitoring section when `dashboard.py` gains a route
