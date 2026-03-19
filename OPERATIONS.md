# BigEd CC — Operations Guide

> Covers skill/module authoring, deployment, operations, and troubleshooting.
> Companion to `FRAMEWORK_BLUEPRINT.md` (architecture) and `ROADMAP_v030_v040.md` (future work).

---

## 1. Skill Authoring

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

### Using LLM providers

Import the routing layer — never call Ollama/Claude/Gemini directly:

```python
from skills._models import call_model

result = call_model(prompt, config, provider="local")   # Ollama (default)
result = call_model(prompt, config, provider="claude")  # Claude API
result = call_model(prompt, config, provider="gemini")  # Gemini API
```

### Registration

1. Save file as `fleet/skills/<skill_name>.py`
2. Add to `lead_client.py` intent parser prompt if it should be dispatchable by natural language
3. Add to `[affinity]` section in `fleet.toml` if it should route to specific worker roles
4. Add custom timeout in `worker.py:SKILL_TIMEOUTS` if >600s is needed

### Testing

```bash
cd fleet
python smoke_test.py          # verifies all skills import cleanly
python -c "from skills.my_skill import run; print(run({'query': 'test'}, {}))"
python lead_client.py dispatch my_skill '{"query": "test"}' --wait
```

---

## 2. Module Authoring

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

## 3. Deployment (Fresh Machine)

### 3.1 Windows (Current — Production)

**Prerequisites:** Python 3.11+, Git, WSL2 (fleet runs inside WSL), Ollama

```bash
# 1. Clone
git clone <repo-url> Education && cd Education

# 2. Install launcher deps (Windows)
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

### 3.2 Linux (Planned)

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

### 3.3 macOS (Planned)

**Prerequisites:** Python 3.11+ (Homebrew), `python-tk` (brew), Git, Ollama

Same steps as Linux. Additional notes:
- `brew install python-tk@3.11` if tkinter import fails
- First launch may trigger Gatekeeper — right-click → Open to bypass
- No NVIDIA GPU support. CPU-only Ollama or Apple Silicon–optimized models
- If using `.app` bundle: drag to `/Applications/`, launch normally

### Hardware Sizing

| GPU VRAM | Recommended `local` model | Notes |
|----------|--------------------------|-------|
| None     | CPU-only (`qwen3:4b`, `num_gpu: 0`) | Slow but functional |
| 6-8 GB   | `qwen3:4b` (GPU) | Mid-tier, reliable |
| 10-12 GB | `qwen3:8b` (GPU) | Full quality, thermal management recommended |
| 16+ GB   | `qwen3:8b` + headroom for training | Can run Ollama during autoresearch |

---

## 4. Operations

### Start / Stop

**Windows:**
```bash
# Start fleet (WSL)
wsl -d Ubuntu -- bash -c "cd /mnt/c/.../fleet && nohup python supervisor.py >> logs/supervisor.log 2>&1 &"

# Start hardware supervisor (WSL, optional — GPU systems only)
wsl -d Ubuntu -- bash -c "cd /mnt/c/.../fleet && nohup python hw_supervisor.py >> logs/hw_supervisor.log 2>&1 &"

# Start launcher (Windows native)
python BigEd/launcher/launcher.py

# Stop fleet
wsl -d Ubuntu -- pkill -f supervisor.py
# Or gracefully:
wsl -d Ubuntu -- python lead_client.py broadcast '{"type": "pause"}'
```

**Linux / macOS:**
```bash
# Start fleet (native — no WSL)
cd fleet
nohup python supervisor.py >> logs/supervisor.log 2>&1 &
nohup python hw_supervisor.py >> logs/hw_supervisor.log 2>&1 &  # optional, GPU only

# Start launcher
python BigEd/launcher/launcher.py

# Stop fleet
pkill -f supervisor.py
# Or gracefully:
python lead_client.py broadcast '{"type": "pause"}'
```

### Health Checks

```bash
# Fleet status
python lead_client.py status

# Check specific agent log
python lead_client.py logs researcher --tail 50

# Verify DB integrity
python -c "import db; db.init_db(); print(db.get_fleet_status())"

# Smoke test
python smoke_test.py --fast

# Soak test (extended)
python soak_test.py
```

### Log Locations

| Component | Log Path |
|-----------|----------|
| Supervisor | `fleet/logs/supervisor.log` |
| HW Supervisor | `fleet/logs/hw_supervisor.log` |
| Workers | `fleet/logs/<role>.log` (e.g., `researcher.log`) |
| Dashboard | stdout (Flask) |

### Key Files (Runtime)

| File | Purpose | Managed By |
|------|---------|-----------|
| `fleet/fleet.db` | Task queue, agents, messages | `db.py` (SQLite WAL) |
| `fleet/hw_state.json` | GPU temps, VRAM, model tier state | `hw_supervisor.py` |
| `fleet/STATUS.md` | Human-readable fleet snapshot | `supervisor.py` |
| `~/.secrets` | API keys (export format) | `lead_client.py secret` |

---

## 5. Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| Workers not claiming tasks | Check `lead_client.py status` — agents may show OFFLINE | Restart supervisor: `python supervisor.py` |
| "Ollama not reachable" in worker logs | Ollama process not running or wrong host | Start Ollama: `ollama serve &`. Check `fleet.toml [models] ollama_host` |
| DB locked / busy timeout | Long-running write or crashed process holding WAL | Kill stale processes: `pkill -f worker.py`, then restart supervisor |
| Worker stuck on single task | Skill timeout not triggering (>600s default) | Check `SKILL_TIMEOUTS` in `worker.py`. Add entry for slow skills |
| Launcher can't find fleet | `FLEET_DIR` resolution failing | Set env: `BIGED_FLEET_DIR=C:\Users\...\fleet` or verify `fleet/fleet.toml` exists |
| GPU OOM during task | Model too large for available VRAM | Lower `[models] local` tier. Enable `hw_supervisor.py` for auto-scaling |
| Console dispatch hangs | WSL not running or `lead_client.py` not found | Verify WSL: `wsl echo ok`. Check `_find_fleet_dir()` resolves correctly |
| Module tab not appearing | Module not in profile or disabled in `[launcher.tabs]` | Check `fleet.toml [launcher] profile` and `[launcher.tabs]` |
| Stale tasks stuck in RUNNING | Worker died mid-task, supervisor recovery not running | Run `python -c "import db; db.init_db(); print(db.recover_stale_tasks())"` |
| Training lock stuck | Training process crashed without releasing | Check: `python -c "import db; db.init_db(); db.release_lock('training')"` |
| Agent flicker in UI | Widget destroy/recreate pattern (pre-v0.32) | Update to v0.32+ (widget cache + configure pattern) |
| **Platform-specific** | | |
| WSL not found (Windows) | WSL2 not installed or distro missing | `wsl --install` or `wsl --install -d Ubuntu` |
| `tkinter` import error (Linux) | `python3-tk` package missing | `sudo apt install python3-tk` (Ubuntu/Debian) or `sudo pacman -S tk` (Arch/SteamOS) |
| `tkinter` import error (macOS) | Homebrew Python missing tk | `brew install python-tk@3.11` |
| Gatekeeper blocks launch (macOS) | Unsigned `.app` bundle | Right-click → Open, or `xattr -d com.apple.quarantine BigEdCC.app` |
| No GPU detected (macOS) | NVIDIA GPU not available on Mac | Expected — use CPU-only Ollama. Apple Silicon models via Metal |
| ROCm not found (Linux/AMD) | AMD GPU driver or ROCm not installed | Install ROCm per AMD docs. Ollama uses ROCm automatically when available |

---

## 6. Issue Reporting

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
- User paths are anonymized (`C:\Users\max\...` → `~\...`)
- No file contents are included — only log tails and metadata
- Review the JSON before submitting if in doubt

See `FRAMEWORK_BLUEPRINT.md` S10-S11 for full debug report schema and resolution tracking lifecycle.

---

## Maintenance Protocol

This document is updated alongside code changes:

1. **Version bumps** — Review all sections when completing a roadmap phase
2. **New skill/module** — Add to relevant section in the same commit
3. **New failure mode** — Add to troubleshooting table when discovered
4. **Deployment changes** — Update Section 3 if install steps change
