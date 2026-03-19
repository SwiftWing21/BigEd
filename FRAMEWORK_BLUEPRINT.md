# BigEd CC v0.41 — Framework Blueprint (All Milestones Complete)

> **Production-ready modular AI agent platform.** Customer-deployable, 24/7 capable, with safe deprecation, thermal management, and iterative skill training.

---

## 1. Architecture Overview

```
BigEd CC (v0.41)
├── Launcher (BigEd/launcher/)
│   ├── launcher.py          — Core app shell (~4700 lines)
│   │   ├── Header            — CPU/RAM/GPU/ETH stats (3s poll, hysteresis)
│   │   ├── Sidebar           — Fleet/Security/Research/Config/Consoles
│   │   ├── Core Tabs          — Command Center, Agents, Fleet Comm (always on)
│   │   ├── Module Tabs        — Loaded via modules/ system
│   │   ├── Taskbar            — Dispatch entry → fleet queue
│   │   └── Consoles           — Claude (API), Gemini (API), Local (Ollama)
│   └── modules/
│       ├── __init__.py        — Module loader, registry, lifecycle manager
│       ├── _version_check.py  — Deprecation version comparison
│       ├── manifest.json      — Module metadata & deprecation state
│       ├── mod_crm.py         — CRM (contacts, prospecting, lead import)
│       ├── mod_accounts.py    — Service account tracking
│       ├── mod_onboarding.py  — Customer onboarding checklists
│       ├── mod_customers.py   — Deployment tracking
│       ├── mod_ingestion.py   — File/folder import to RAG
│       └── mod_outputs.py     — Knowledge browser
│
├── Fleet (fleet/)
│   ├── supervisor.py          — Worker lifecycle, training detection, stale recovery
│   ├── hw_supervisor.py       — Thermal governor, VRAM scaling, ambient estimation
│   ├── worker.py              — Skill dispatch, timeouts, affinity routing, message handling
│   ├── db.py                  — SQLite data layer (WAL mode) — tasks, agents, messages, locks
│   ├── lead_client.py         — CLI entry point (status, task, broadcast, inbox)
│   ├── rag.py                 — FTS5/BM25 RAG engine
│   ├── config.py              — TOML config loader + is_offline/is_air_gap/AIR_GAP_SKILLS
│   ├── dashboard.py           — Flask web dashboard v2 (SSE, alerts, 40 endpoints)
│   ├── smoke_test.py          — 10-check startup verification (--fast mode)
│   ├── soak_test.py           — 10-check extended validation (concurrency, WAL stress)
│   ├── fleet.toml             — Master configuration
│   └── skills/                — 66 skill modules
│       ├── _models.py         — Provider routing (Claude/Gemini/Local)
│       ├── skill_train.py     — Iterative skill improvement (3 profiles, discovery logging)
│       ├── plan_workload.py   — Fleet-aware task planning
│       └── ...                — web_search, code_write, rag_index, etc.
│
└── autoresearch/              — ML training pipeline (separate venv, CUDA 12.8)
```

## 2. Module System

### Interface Contract

Every module implements:

```python
class Module:
    NAME = "module_name"        # matches fleet.toml key
    LABEL = "Display Name"      # tab label
    VERSION = "0.22"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []             # other module names required

    DATA_SCHEMA = {             # v0.25 data contract
        "table": "table_name",
        "fields": { ... },
        "retention_days": None,
    }

    def __init__(self, app):    # receives main app reference
    def build_tab(self, parent): ...
    def on_refresh(self): ...
    def on_close(self): ...
    def get_settings(self) -> dict: ...
    def apply_settings(self, cfg): ...
    def export_data(self) -> list[dict]: ...   # data portability
    def validate_record(self, data) -> tuple[bool, str]: ...
```

### Module Loading Flow

```
1. discover_modules()  — scan modules/ for mod_*.py
2. _load_manifest()    — read manifest.json for metadata
3. resolve_profile()   — merge deployment profile with tab config
4. Import + validate   — check Module class, required methods
5. _resolve_deps()     — ensure DEPENDS_ON are met
6. Instantiate         — cls(app) for each enabled module
7. build_tab()         — construct UI in tabview
```

### Deployment Profiles

```toml
[launcher]
profile = "research"  # minimal | research | consulting | full

# Profile definitions:
# minimal:    ingestion, outputs
# research:   ingestion, outputs
# consulting: crm, onboarding, customers, accounts, ingestion, outputs
# full:       all modules enabled
```

### Deprecation Lifecycle

```
ACTIVE  →  DEPRECATED  →  SUNSET  →  REMOVED
  │           │              │          │
  │       banner shown   auto-dis-   file deleted,
  │       in UI          abled at    data archived
  │                      version
  └─────── data always exportable ──────┘
```

Manifest metadata:
```json
{
  "name": "crm",
  "deprecated": true,
  "deprecated_since": "v0.26",
  "sunset_version": "v0.30",
  "migration_notes": "Export CRM data before v0.30."
}
```

## 3. Fleet Architecture

### Dual Supervisor System

| Component | Role | CPU/GPU |
|-----------|------|---------|
| `supervisor.py` | Worker lifecycle, queue management, training detection | CPU |
| `hw_supervisor.py` | VRAM monitoring, model tier scaling, thermal throttling | CPU |

### Model Tiers (Ollama)

| Tier | Model | VRAM | Purpose |
|------|-------|------|---------|
| Default | qwen3:8b | ~6GB | Fleet workers (GPU) |
| Mid | qwen3:4b | ~3GB | VRAM pressure fallback |
| Low | qwen3:1.7b | ~1GB | High pressure fallback |
| Conductor | qwen3:4b | 0 (CPU) | User chat, intent parsing (~2-3GB RAM) |
| Maintainer | qwen3:0.6b | 0 (CPU) | Always loaded, keepalive |

Default config: 2 CPU models + 1 GPU model

### Thermal Management

```toml
[thermal]
gpu_max_sustained_c = 75     # reduce workers above this
gpu_max_burst_c = 78         # pause GPU tasks above this
cooldown_target_c = 72       # resume when below for cooldown_window
cooldown_window_secs = 60    # consecutive seconds below target
ambient_estimation = true    # track cooldown rate for ambient temp
```

Thermal flow: `hw_supervisor.py` reads GPU/CPU temps → writes `hw_state.json` → workers check before claiming tasks → model tier auto-scales based on VRAM pressure.

### Worker Capabilities

- **Skill timeouts**: Default 600s, code_write gets 900s — prevents hung workers
- **Affinity routing**: `claim_task(affinity_skills=[...])` prefers matching skills
- **Actionable messages**: ping (pong), pause (stop claiming), resume, config_reload
- **Training lock**: Exclusive DB lock — only 1 training process at a time

### Database Schema (fleet.db)

```sql
agents:   id, name(UNIQUE), role, status, current_task_id, last_heartbeat, pid
tasks:    id, created_at, assigned_to, status, priority, type, payload_json, result_json, error, review_rounds,
          parent_id, depends_on
messages: id, from_agent, to_agent, created_at, read_at, body_json, channel(DEFAULT 'fleet')
notes:    id, channel, from_agent, created_at, body_json  — idx on (channel, created_at)
locks:    name(PK), holder, acquired_at
usage:    id, created_at, skill, model, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens,
          cost_usd(REAL), task_id, agent  — idx on (skill), (created_at)
```

Channel constants: `CH_SUP` (supervisor-to-supervisor), `CH_AGENT` (agent-to-agent), `CH_FLEET` (cross-layer, default), `CH_POOL` (supervisor-to-pool).

WAL mode, 30s busy timeout, retry writes with jittered backoff.

### Task DAG (Dependency Graph)

Tasks support dependencies via `depends_on` (JSON array of task IDs) and `parent_id`:

```
Status flow:  WAITING → PENDING → RUNNING → DONE/FAILED
              RUNNING → REVIEW → DONE (pass) or REVIEW → PENDING (reject, retry with critique)
              RUNNING → WAITING_HUMAN → PENDING (operator responds, _human_response in payload)
                                              ↓
                                    _promote_waiting_tasks()
                                    _cascade_fail_dependents()
```

- **WAITING**: Task has unmet dependencies — not claimable by workers
- When a task completes, `_promote_waiting_tasks()` checks all WAITING tasks and promotes those whose deps are all DONE
- When a task fails, `_cascade_fail_dependents()` fails all downstream WAITING tasks
- `post_task_chain([{type, payload}, ...])` creates a sequential pipeline (A → B → C)
- `post_task(..., depends_on=[id1, id2])` for fan-in patterns (C waits for both A and B)

### Input/Output Validation

- `post_task()` validates `payload_json` is valid JSON, clamps priority to 1-10
- `complete_task()` validates `result_json` is valid JSON, auto-wraps non-JSON in `{"raw": ...}`
- `complete_task()` accepts both str and dict (auto-serializes dicts)

## 4. Dashboard v2

### Endpoints (40 total: 27 data + 9 process/fleet + 4 A2A)

| Endpoint | Data Source | Purpose |
|----------|-----------|---------|
| `/api/status` | fleet.db | Agents + task counts |
| `/api/activity` | fleet.db | 30-day daily breakdown |
| `/api/skills` | fleet.db | Skill type aggregation |
| `/api/discussions` | fleet.db | Discussion topics from messages |
| `/api/knowledge` | filesystem | Knowledge files by category |
| `/api/code_stats` | git log | Lines added/deleted/commits |
| `/api/reviews` | filesystem | Code + FMA review summaries |
| `/api/timeline` | fleet.db | Merged task + discussion events |
| `/api/rag` | rag.db | RAG index statistics |
| `/api/thermal` | hw_state.json | GPU/CPU temps, fan, power, VRAM |
| `/api/training` | fleet.db + filesystem | Lock status, training logs |
| `/api/modules` | manifest.json | Module status, profiles |
| `/api/data_stats` | fleet.db + tools.db | Per-module data metrics (incl. notes) |
| `/api/comms` | fleet.db | Per-channel message/note counts + recent activity |
| `/api/alerts` | in-memory | Active alerts |
| `/api/resolutions` | data/resolutions.jsonl | Resolution tracking entries |
| `/api/usage` | fleet.db usage table | Token usage aggregates by skill/model/agent |
| `/api/usage/delta` | fleet.db usage table | Compare usage between two date ranges |
| `/api/usage/budgets` | fleet.db + fleet.toml | Per-skill budget status with pct_used |
| `/api/usage/regression` | fleet.db | Auto-flag skills with >20% token increase |
| `POST /api/fleet/start` | subprocess | Start fleet supervisor process |
| `POST /api/fleet/stop` | os.kill SIGTERM | Graceful fleet shutdown via PID |
| `/api/fleet/workers` | fleet.db + os.kill(0) | List workers with PID + alive check |
| `POST /api/fleet/worker/<name>/restart` | os.kill + DB | Restart specific worker by name |
| `/api/fleet/health` | fleet.db + Ollama + hw_state | Overall fleet health summary |
| `/api/fleet/uptime` | fleet.db agents table | Supervisor uptime since start |
| `/api/fleet/idle` | fleet.db idle_runs | Idle evolution statistics |
| `/api/fleet/marathon` | knowledge/marathon/ | Marathon session list + snapshot counts |
| `/api/fleet/checkpoints` | autoresearch/checkpoints/ | Training checkpoint status |
| `/api/stream` | SSE | Live push updates (5s interval) |

### Alert System

Monitors (30s check interval):
- GPU temp > burst limit (78C) → critical
- GPU temp > sustained limit (75C) → warning
- Worker no heartbeat > 5min → warning
- Disk space < 5GB → warning
- Training lock near timeout → warning

## 5. Skill Training Pipeline v2

### Training Profiles

| Profile | Iterations | Temperature | Approach |
|---------|-----------|-------------|----------|
| Conservative | 3 | 0.3 | Minimal, targeted — robustness focus |
| Aggressive | 10 | 0.7 | Restructuring, caching, algorithm optimization |
| Exploratory | 5 | 0.9 | Fundamentally different approaches |

### Discovery Logging

Every iteration produces a markdown discovery file:
```
knowledge/skill_training/discoveries/
├── summarize_20260318_143000_iter1.md
├── web_search_20260318_150000_iter2.md
└── ...
```

Discovery types: `improvement`, `breakthrough`, `neutral`, `regression`, `crash`

Negative results are logged — they narrow the search space.

### Cross-Skill Learning

- Training reads recent discoveries from other skills
- Successful patterns from one skill inform proposals for others
- Planner agent reviews discovery directory for strategic task planning

## 6. Testing Framework

### Smoke Test (17 fast / 20 full, ~2s fast / ~10s full)

```
1. Skill imports (54+ modules)    7. Channel message routing
2. DB health (task lifecycle)     8. Note round-trip
3. Config health (fleet.toml)     9. Backward-compat messages
4. Ollama reachable*              10. Usage tracking (CT-1)
5. Message round-trip             11. Budget check (CT-4)
6. Broadcast round-trip           12. Stale recovery
+ Training lock, Thermal readings* (* = skipped in --fast)
```

### Soak Test (25 checks, concurrent stress)

```
Core:       Task flood, claim/complete, concurrent claims, lock contention
Recovery:   Stale recovery under load, broadcast under load (20 agents)
Lifecycle:  Training lock, deprecation, module manifest, DB WAL stress
DAG:        Dependency chain, cascade fail
Modes:      Offline skill rejection, air-gap whitelist
Review:     Review status lifecycle, verdict parsing
Safety:     Quarantine lifecycle, DLP scrubbing, WAITING_HUMAN lifecycle
Comms:      Channel broadcast isolation, notes append+load
Config:     Security config, new skill imports (v0.39-v0.41), integration config
Validation: Post task validation
```

## 7. Configuration Reference (fleet.toml)

| Section | Key Fields |
|---------|-----------|
| `[fleet]` | eco_mode, idle_enabled, max_workers, discord_bot_enabled, offline_mode, air_gap_mode |
| `[models]` | local, complex, conductor_model, ollama_host |
| `[models.tiers]` | default, mid, low, critical |
| `[review]` | enabled, provider, claude_model, gemini_model |
| `[gpu]` | mode (eco/full) |
| `[thermal]` | sustained/burst/cooldown temps, poll interval |
| `[thermal.vram]` | emergency, high, restore thresholds |
| `[training]` | exclusive_lock, profiles (conservative/aggressive/exploratory) |
| `[dashboard]` | enabled, port |
| `[launcher]` | profile (minimal/research/consulting/full) |
| `[launcher.tabs]` | per-module enable/disable |
| `[workers]` | nice_level, cpu_limit_percent, coder_count |
| `[affinity]` | role → skill mapping |

## 8. Offline & Air-Gap Modes

### 8.1 Network Dependency Map

| Category | Components |
|----------|-----------|
| LOCAL (always works) | Ollama (localhost:11434), fleet.db, dashboard (localhost:5555), all filesystem ops |
| EXTERNAL API | Claude, Gemini, Brave/Tavily/Jina/DDG search, arXiv, Stability AI, Replicate |
| EXTERNAL SERVICE | Discord bot, GitHub (PAT/SSH for code_write, branch_manager) |
| TELEMETRY | None — zero phone-home code exists |

### 8.2 Offline Mode (`fleet.toml: offline_mode = true`)

Run without internet. All local capabilities work. External API skills gracefully degrade.

| Component | Behavior |
|-----------|----------|
| `_models.py` | Forces `complex_provider = "local"` (skip Claude/Gemini) |
| `supervisor.py` | Skips Discord bot + OpenClaw launch |
| `worker.py` | Skills with `REQUIRES_NETWORK = True` auto-rejected with clear error |
| Dashboard | Still serves on localhost |
| Ollama | Works normally (all local) |
| Launcher | Orange "OFFLINE" badge in header; Claude/Gemini console buttons disabled |

**Skill metadata:** Each skill declares `REQUIRES_NETWORK = True` if it needs internet. Worker checks this before dispatch.

### 8.3 Air-Gap Mode (`fleet.toml: air_gap_mode = true`)

Maximum isolation. No network interfaces used. Implies `offline_mode = true`.

| Component | Additional restriction |
|-----------|----------------------|
| Dashboard | Disabled entirely (no listening sockets) |
| Ollama keepalive | HTTP health checks skipped |
| `_load_secrets()` | Disabled (no API keys in memory) |
| Skills | Deny-by-default whitelist (`config.AIR_GAP_SKILLS`) |
| Launcher | Red "AIR-GAP" badge; Dashboard button disabled; API consoles disabled |

**Air-gap approved skills:** code_review, code_discuss, code_index, code_quality, summarize, discuss, flashcard, analyze_results, rag_index, rag_query, benchmark, ingest, security_review, security_audit.

### 8.4 Recovery & Backup

**Critical non-tracked files:**

| File | Loss impact | Recovery |
|------|-----------|----------|
| `fleet/fleet.db` | Task queue, agent state | Fleet restarts clean |
| `fleet/rag.db` | Document index, embeddings | Re-ingest documents |
| `BigEd/launcher/data/tools.db` | CRM/launcher data | Manual re-entry |
| `fleet/knowledge/` | Research, leads, reviews | Regenerable by re-running tasks |

**Backup:** `bash scripts/backup.sh` — copies all non-tracked runtime data to `~/BigEd-backups/<timestamp>/`. Keeps last 10. Run before milestone merges and schema migrations.

---

## 9. Portability & Cross-Platform Architecture

### 9.1 Platform Communication Layer

Fleet code (`fleet/`) is fully cross-platform. The WSL layer is a Windows-only bridge — on Linux/Mac, fleet runs natively in the same OS as the launcher.

**FleetBridge abstraction** replaces the current `wsl()`/`wsl_bg()` functions:

```python
class FleetBridge(ABC):
    @abstractmethod
    def run(self, cmd: str, capture=False, timeout=60) -> str: ...
    @abstractmethod
    def run_bg(self, cmd: str, callback=None, timeout=60): ...
    @abstractmethod
    def fleet_path(self) -> Path: ...

class WslBridge(FleetBridge):
    """Windows: shells commands into WSL Ubuntu."""
    # Converts FLEET_DIR to /mnt/c/... path
    # Uses subprocess with CREATE_NO_WINDOW

class DirectBridge(FleetBridge):
    """Linux/Mac: runs fleet commands natively."""
    # No path conversion needed
    # Standard subprocess.run()
```

Detection at startup:
```python
import sys
if sys.platform == "win32":
    bridge = WslBridge(fleet_dir)
else:
    bridge = DirectBridge(fleet_dir)
```

See `CROSS_PLATFORM.md` for full specification and current Windows-specific code inventory.

### 9.2 Dynamic Path Resolution

`launcher.py` computes `FLEET_DIR` dynamically at startup:
1. Check `BIGED_FLEET_DIR` environment variable (explicit override)
2. Walk up from `_SRC_DIR` (max 6 levels), looking for a directory containing `fleet/fleet.toml`
3. Fallback: `_SRC_DIR.parent.parent / "fleet"` (original relative assumption)

**Cross-platform rules:**
- Use `Path.home()` over `os.environ.get("USERPROFILE")` — works on all platforms
- WSL path conversion (`C:\...` → `/mnt/c/...`) only on Windows via `WslBridge`
- On Linux/Mac, `FLEET_DIR` is a native path — no conversion needed
- No hardcoded absolute paths exist in the codebase

### 9.3 Process Management

| Operation | Windows | Linux | macOS | Cross-Platform |
|-----------|---------|-------|-------|---------------|
| Kill process | `taskkill /F /PID` | `kill -9` / `pkill` | `kill -9` / `pkill` | `psutil.Process.kill()` |
| Background process | `CREATE_NO_WINDOW` flag | Standard fork | Standard fork | `subprocess` with platform flags |
| Process list | `tasklist` | `ps aux` / `pgrep` | `ps aux` / `pgrep` | `psutil.process_iter()` |
| Self-swap (updater) | `.bat` trampoline | `exec` replacement | `exec` replacement | Platform-conditional |

**Preferred approach:** Use `psutil` for all process management — it provides a unified API across platforms. Reserve platform-specific subprocess flags (`CREATE_NO_WINDOW`) for cases where `psutil` doesn't cover the need.

### 9.4 GPU Detection

| Data Point | Windows | Linux | macOS | Cross-Platform |
|-----------|---------|-------|-------|---------------|
| GPU name/VRAM/temp | `pynvml` | `pynvml` | N/A (no NVIDIA) | `pynvml` (NVIDIA only) |
| CPU name | `winreg` (HKLM) | `/proc/cpuinfo` | `sysctl -n machdep.cpu.brand_string` | `platform.processor()` fallback |
| CPU/RAM usage | `psutil` | `psutil` | `psutil` | `psutil` (cross-platform) |
| AMD GPU | N/A currently | `rocm-smi` | N/A | Future: `pyamdgpuinfo` |
| Apple Silicon | N/A | N/A | `Metal` framework | Future consideration |

Current `_cpu_name()` in `launcher.py:3944` uses `winreg` — needs platform branching:
```python
def _cpu_name():
    if sys.platform == "win32":
        import winreg
        # existing registry read
    elif sys.platform == "linux":
        # parse /proc/cpuinfo
    elif sys.platform == "darwin":
        # subprocess: sysctl -n machdep.cpu.brand_string
    else:
        return platform.processor() or "Unknown"
```

### 9.5 Secrets Management

Already cross-platform. `lead_client.py secret` stores keys in `~/.secrets` via `Path.home()`. No platform-specific code involved. Works identically on Windows, Linux, and macOS.

### Known Technical Debt

See `TECH_DEBT.md` for full tracking. Cross-platform items added as of v0.31.

## 10. Deployment & Packaging

### 10.1 Shared Package Structure

```
BigEdCC/
├── BigEd/launcher/launcher.py  — Main executable (or PyInstaller bundle)
├── BigEd/launcher/modules/     — Module files + manifest
├── fleet/                      — Fleet runtime
├── fleet.toml                  — Customer configuration
└── README.md                   — Deployment guide
```

### 10.2 Windows (Current — Production)

- **Packaging:** PyInstaller `.exe` (one-file, windowed) via `build.bat`
- **Installer:** `installer.py` → `Setup.exe` — writes `winreg` entries for Add/Remove Programs
- **Uninstaller:** `uninstaller.py` → `Uninstaller.exe` — removes registry keys, cleanup `.bat` trampoline
- **Updater:** `updater.py` → `Updater.exe` — git pull + rebuild, self-swap via `.bat` trampoline
- **Prerequisites:** Python 3.11+, WSL2 (fleet runs inside WSL), Ollama, Git
- **Fleet communication:** `wsl()` / `wsl_bg()` subprocess calls into WSL Ubuntu

Customer configuration:
1. Choose deployment profile: `fleet.toml → [launcher] profile = "consulting"`
2. Enable/disable modules: `[launcher.tabs]` section
3. Set model tier: `[models] local = "qwen3:8b"` (adjust for hardware)
4. Configure thermal limits: `[thermal]` section (RTX 3080 Ti defaults)
5. Set API keys: `~/.secrets` (Claude, Gemini, search APIs)
6. Start: `python supervisor.py` (WSL) + `python launcher.py` (Windows)

### 10.3 Linux (Planned)

- **Packaging:** AppImage (recommended) — single portable binary, no install required
- **Desktop integration:** `.desktop` file in `~/.local/share/applications/`
- **Dependencies:** `python3-tk` (system package for GUI), Ollama, Git
- **Fleet communication:** `DirectBridge` — native subprocess, no WSL layer
- **Install/uninstall:** Standard file copy + `.desktop` file. No registry equivalent needed
- **Notes:** Fleet runs natively — no WSL bridge overhead. Same filesystem, same Python runtime

### 10.4 macOS (Planned)

- **Packaging:** `.app` bundle via `py2app` or PyInstaller `--windowed`
- **Distribution:** DMG disk image for drag-and-drop install
- **Code signing:** Required for Gatekeeper — `codesign` + optional notarization via `xcrun notarytool`
- **Dependencies:** Python 3.11+ (Homebrew), Ollama, Git. `tkinter` may need `brew install python-tk`
- **Fleet communication:** `DirectBridge` — same as Linux
- **Notes:** No NVIDIA GPU support (Apple Silicon uses Metal). CPU-only Ollama or Apple Silicon–optimized models

### 10.5 Hardware Requirements

| Component | Minimum | Recommended (NVIDIA) | AMD GPU | Apple Silicon |
|-----------|---------|---------------------|---------|---------------|
| GPU | None (CPU mode) | RTX 3060+ (8GB+ VRAM) | RX 7600+ (ROCm) | M1+ (Metal, unified memory) |
| RAM | 8GB | 16GB+ | 16GB+ | 16GB+ (shared with GPU) |
| CPU | 4 cores | 8+ cores | 8+ cores | M1+ (4P+4E minimum) |
| Storage | 10GB | 50GB+ | 50GB+ | 50GB+ |
| OS | Win10+/Ubuntu 22.04+/macOS 13+ | Win11/Ubuntu 24.04 | Linux (ROCm support) | macOS 14+ |

**Steam Deck (SteamOS/Arch Linux):** Supported via Linux path. AMD APU (RDNA2, 16GB unified). CPU-only Ollama recommended — limited VRAM headroom for GPU inference alongside game workloads. Desktop Mode required for GUI.

---

## 11. Diagnostics & Issue Pipeline

### 11.1 Debug Report Format

A single structured diagnostic snapshot capturing system state at the moment of an issue. Generated on-demand (user clicks "Report Issue") or automatically on unhandled exception.

```
reports/debug/
├── debug_20260318_143000.json    — structured report
└── debug_20260318_143000.log     — raw log tail bundle
```

Report structure:
```json
{
  "report_id": "uuid",
  "timestamp": "ISO-8601",
  "version": "0.31",
  "platform": { "os": "win32/linux/darwin", "python": "3.11.x", "arch": "x86_64" },
  "hardware": {
    "gpu": { "name": "RTX 3080 Ti", "vram_total_gb": 12, "vram_used_gb": 8.2, "temp_c": 72 },
    "cpu": { "name": "...", "cores": 8, "usage_pct": 45 },
    "ram": { "total_gb": 32, "used_gb": 18 }
  },
  "fleet_state": {
    "agents": [{"name": "researcher", "status": "IDLE", "last_heartbeat": "..."}],
    "tasks": {"pending": 0, "running": 1, "done": 52, "failed": 3},
    "ollama": {"running": true, "models_loaded": ["qwen3:8b"]},
    "thermal_state": "ok|throttled|burst"
  },
  "error": {
    "type": "exception|hang|wrong_result|ui_issue|custom",
    "message": "...",
    "traceback": "...",
    "component": "launcher|fleet|worker|skill|module",
    "trigger": "user action or automated context"
  },
  "logs": {
    "supervisor_tail": "last 50 lines",
    "active_worker_tail": "last 50 lines of relevant worker",
    "launcher_output": "last 100 lines from _log_output buffer"
  },
  "user_description": "free text from reporter",
  "config_snapshot": {
    "profile": "research",
    "model_tier": "qwen3:8b",
    "thermal_limits": { "sustained": 75, "burst": 78 }
  },
  "reproduction_steps": []
}
```

### 11.2 Report Generation

Two trigger paths:
- **Manual:** User clicks "Report Issue" button (launcher sidebar or Config tab). Opens dialog with description field, optional reproduction steps, "Include logs" checkbox. Calls `generate_debug_report()` which snapshots all sources.
- **Automatic:** Global exception handler wraps `launcher.py` main loop. On unhandled exception, auto-generates report with traceback populated, saves to `reports/debug/`, shows notification.

Data sources:

| Field | Source | Method |
|-------|--------|--------|
| platform | `sys.platform`, `platform.python_version()`, `platform.machine()` | Direct |
| hardware | `psutil` (CPU/RAM), `pynvml` (GPU) | Same as header stats |
| fleet_state | `STATUS.md` parse or `fleet.db` query | Existing `parse_status()` |
| thermal | `hw_state.json` | Existing file read |
| logs | `fleet/logs/*.log` | `read_log_tail()` (launcher.py:399) |
| launcher output | `_log_output` ring buffer | New: `collections.deque(maxlen=200)` |
| config | `fleet.toml` | Existing `config.load_config()` |
| ollama state | HTTP GET `/api/tags` | Existing `_schedule_ollama_watch()` |

### 11.3 VS Code Dev Integration

For development use in VS Code:
- **Launch config** (`.vscode/launch.json`): Debug profile running `launcher.py` with `--debug` flag
- `--debug` enables: verbose logging to `fleet/logs/launcher_debug.log`, exception breakpoints, report auto-generation on crash
- **VS Code task** (`.vscode/tasks.json`): "Generate Debug Report" task running `python -m biged.debug_report`
- **Problem matcher**: Parse debug report JSON for errors, surface in VS Code Problems panel
- Reports saved to `reports/debug/` — viewable as JSON, structured for diff-ability

### 11.4 End-User Issue Submission

For production end users (non-developers):
- "Report Issue" button in launcher UI generates the debug report
- Report is sanitized: API keys stripped from config snapshot, paths anonymized (`C:\Users\max\...` → `~\...`)
- Two submission paths:
  - **GitHub Issues (automated):** If `gh` CLI is available and user opts in, create issue with report attached. Template: title from error type, body from report summary, label `bug` or `user-report`
  - **File export (manual):** Save `.json` report to Desktop. User can email or attach to issue manually
- Report includes `report_id` UUID for tracking through fix lifecycle (see S11)

### 11.5 _log_output Persistence

Currently `_log_output()` writes only to the GUI text widget — lost on close.

Fix:
- Add a ring buffer (`collections.deque(maxlen=200)`) mirroring every `_log_output()` call
- Debug report reads from this buffer for the `logs.launcher_output` field
- Optionally persist to `data/launcher_output.log` (rotate at 1MB)

---

## 12. Resolution Tracking

### 12.1 Issue-to-Fix Lifecycle

```
REPORTED → TRIAGED → REPRODUCING → FIX_IN_PROGRESS → FIX_VERIFIED → SHIPPED
```

| Stage | Who | What happens |
|-------|-----|-------------|
| REPORTED | User/auto | Debug report generated with `report_id` |
| TRIAGED | Dev | Report reviewed, severity assigned (P0-P3), component tagged |
| REPRODUCING | Dev | Reproduction confirmed using report's config/platform/steps |
| FIX_IN_PROGRESS | Dev | Branch created, linked to `report_id` in commit message |
| FIX_VERIFIED | Dev/CI | Fix verified against original report conditions (platform, config) |
| SHIPPED | Release | Version bumped, `report_id` referenced in changelog |

### 12.2 Resolution Database

A lightweight JSON-lines file mapping reports to fixes:

```
data/resolutions.jsonl
```

Each line:
```json
{
  "report_id": "uuid-from-debug-report",
  "issue_ref": "GH#42 or internal",
  "severity": "P0|P1|P2|P3",
  "component": "launcher|fleet|worker|skill|module",
  "platform": ["win32", "linux", "darwin"],
  "root_cause": "one-line description",
  "fix_commit": "abc1234",
  "fix_version": "0.33",
  "regression_test": "soak_test.py::test_name or manual steps",
  "status": "shipped|pending|wontfix",
  "resolved_at": "ISO-8601"
}
```

### 12.3 Regression Prevention

- **Commit convention:** `fix(component): description [report:uuid]` — links fix to original report
- **Regression test requirement:** Every P0/P1 fix must add or reference a test case (smoke or soak)
- **Resolution ingestion:** After a fix ships, append to `resolutions.jsonl` with report_id, fix_commit, and regression_test
- **Stability dashboard:** Dashboard endpoint `/api/resolutions` serves resolution stats: fixes per component, mean time to resolve, open vs shipped, platform distribution
- **Release validation:** Before tagging a release, check that all P0/P1 resolutions for the target version have `status: shipped` and their regression tests pass

### 12.4 Pattern Detection

Over time, `resolutions.jsonl` becomes a knowledge base:
- Query: "Most common failure components?" → group by component
- Query: "Which platforms have the most issues?" → group by platform
- Query: "Average time from REPORTED to SHIPPED?" → timestamp math
- Fleet skill (`skill_stability_report.py`): Periodically analyze `resolutions.jsonl` and produce a stability report in `knowledge/reports/`

---

## 13. Companion Documents

| Document | Purpose |
|----------|---------|
| `OPERATIONS.md` | Skill/module authoring, deployment steps, ops runbook, troubleshooting, issue reporting |
| `ROADMAP_v030_v040.md` | Future work phases (v0.32 → v0.40) + parallel tracks (Platform, Diagnostics) |
| `TECH_DEBT.md` | Technical debt tracking (cross-platform + diagnostics items open) |
| `CROSS_PLATFORM.md` | Platform matrix, FleetBridge spec, Windows-specific code inventory, migration plan |
| `MACHINE_PROFILE.md` | Hardware specs and VRAM limits for this dev machine |
| `fleet/CLAUDE.md` | Worker roles, skill outputs, messaging bridges |
| Section 14 (this doc) | Architectural Pattern 6: Reactive Streaming IPC — SSE event flow, fallback strategy, deprecated file-polling |
| Section 15 (this doc) | Security Architecture — defense-in-depth layers, OWASP LLM Top 10 coverage, compliance grades, controls reference |

---

## 14. Architectural Pattern 6: Reactive Streaming IPC

### 14.1 SSE Event Flow

The launcher consumes the dashboard's `/api/stream` SSE endpoint as its primary data source, replacing legacy file-polling.

| Source | Legacy (deprecated) | SSE (current) |
|--------|-------------------|---------------|
| Agent status | `parse_status()` reads `STATUS.md` every 4s | `_handle_sse_status()` callback on push |
| Task counts | `get_fleet_status()` SQL query every 4s | Included in SSE status event |
| Thermal data | `hw_state.json` file read every 3s | Future: thermal SSE event type |
| Ollama status | HTTP GET `/api/tags` every 8s | Future: ollama SSE event type |

### 14.2 Fallback Strategy

SSE is primary. If dashboard is unavailable:
- `_sse_active = False` triggers automatic fallback
- Legacy file-polling resumes at 8s interval (slower than original 4s)
- No user intervention needed — seamless degradation

### 14.3 Deprecated Functions (removal candidates)

These functions are kept for fallback but should be removed once SSE covers all data:
- `parse_status()` — reads STATUS.md (replaced by SSE status events)
- `write_status_md()` in supervisor.py — writes STATUS.md (only needed for fallback)
- `_schedule_refresh()` file-reading branches — replaced by `_handle_sse_status()`

---

## 15. Security Architecture

### 15.1 Defense-in-Depth Layers

| Layer | Components | Status |
|-------|-----------|--------|
| 1. Input Validation | PII scan, secret detection (14 patterns + base64), prompt injection (8 patterns), path traversal bounds, JSON schema validation | Active |
| 2. Execution Controls | Air-gap whitelist (14 skills), offline mode, affinity routing, Docker sandbox, skill timeout (600s), skill name whitelist | Active |
| 3. Output Guardrails | guardrails.py (toxicity, PII redaction, refusal, topic rails), adversarial review (3 providers), secret redaction | Active |
| 4. Post-Execution Monitoring | DLP scrub (task results + knowledge files), failure streak quarantine, stuck review auto-pass, integrity hashes | Active |
| 5. Audit & Observability | HMAC-signed audit log, per-worker logs with rotation, cost tracking, debug reports, resolution tracking | Active |

### 15.2 OWASP LLM Top 10 Coverage

| # | Risk | Grade | Implementation |
|---|------|-------|---------------|
| LLM01 | Prompt Injection | B+ | 8 injection patterns, blocking on detection |
| LLM02 | Insecure Output | A- | Adversarial review + guardrails.py + DLP |
| LLM04 | Model DoS | B+ | Token budgets + pre-execution cost estimation |
| LLM06 | Sensitive Info | A- | 14 secret patterns + base64 + PII + env-match |
| LLM07 | Insecure Plugin | A- | safe_path(), parameterized SQL, JSON validation |
| LLM08 | Excessive Agency | A | Affinity routing, quarantine, HitL, air-gap, capability budget |
| LLM09 | Overreliance | A- | Adversarial review, failure streaks, REVIEW gate |

### 15.3 Compliance Standards

| Standard | Grade | Key Controls |
|----------|-------|-------------|
| OWASP LLM Top 10 | B+ | Strong on 7/10 risks |
| NIST AI RMF | B | Good monitoring; formal governance planned |
| GDPR | B | Right to erasure, data classification, DLP |
| SOC 2 Type II | B- | Monitoring + audit; incident SOP documented |
| EU AI Act | B+ | Human oversight, risk assessment, model cards |

### 15.4 Security Controls Reference

| Control | File | Config |
|---------|------|--------|
| Dashboard bearer auth | dashboard.py | `[security] dashboard_token` |
| Rate limiting | dashboard.py | 60 req/60s per IP |
| CSRF protection | dashboard.py | Single-use tokens |
| Input PII/secret scan | _watchdog.py | 14 patterns + base64 |
| Prompt injection detection | _watchdog.py | 8 patterns, blocking |
| Path traversal prevention | code_review.py | FLEET_DIR bounds check |
| SSRF blocklist | browser_crawl.py | Internal IP blocklist |
| Entity validation | home_assistant.py | Regex format check |
| nmap target validation | pen_test.py | Alphanumeric + IP only |
| Error sanitization | dashboard.py | File paths stripped |
| Skill name whitelist | worker.py | Only skills/*.py names |
| Docker sandbox | worker.py | --network=none --memory=512m |
| Worker resource limits | supervisor.py | Windows Job Objects / cgroups |
| Token budgets | _models.py | warn/throttle/block per skill |
| Cost estimation | _models.py | Pre-execution token estimate |
| Capability budget | worker.py | 500 calls/session limit |
| Knowledge integrity | integrity.py | SHA-256 manifest + verify |
| Audit log | audit_log.py | HMAC-signed JSON events |
| Log rotation | supervisor.py | RotatingFileHandler 10MB/5 |
| TLS | dashboard.py | --tls flag, self-signed cert |
| Data classification | db.py | public/internal/confidential/restricted |
| Right to erasure | db.py | delete_user_data() GDPR Art. 17 |
| Secret rotation | secret_rotate.py | Slack+AWS auto, others semi-auto |
| DB encryption | db_encrypt.py | SQLCipher AES-256 |
| Schema migrations | db_migrate.py | PRAGMA user_version + .sql files |

---

## Version History

| Version | Theme | Key Deliverable |
|---------|-------|----------------|
| v0.0-v0.10 | Foundation | Fleet architecture, 46 skills, dual supervisors, smoke test |
| v0.14-v0.15 | Thermal + Training | hw_supervisor, training locks, roadmap to v0.20 |
| v0.20 | 24/7 Operation | Thermal management, model tiers, affinity routing |
| v0.21 | Dev Workflow | VS Code configs, fast smoke test, test isolation |
| v0.22 | Module System | Module loader, CRM + Accounts extracted |
| v0.23 | Full Extraction | All 6 tabs as modules, launcher.py reduction |
| v0.24 | Deployment | Profiles, dependency resolution, module discovery |
| v0.25 | Data Maturity | Data contracts, validation, cross-module flow, export |
| v0.26 | Deprecation | Safe lifecycle (active→deprecated→sunset→removed) |
| v0.27 | Dashboard v2 | SSE live updates, thermal/training/module endpoints, alerts |
| v0.28 | Training v2 | Discovery logging, profiles, cross-skill learning |
| v0.29 | Testing | Soak test, module integration tests, deprecation tests |
| v0.30 | Production | Framework blueprint, portable paths, tech debt review, customer-deployable |
| v0.31 | Task Graph | DAG dependencies, cascade fail, input/output validation, zero tech debt |
| v0.32 | UI Resilience | Timer guards, agents tab cache+configure, module refresh integration, timeout notification |
| v0.33 | Flow Verification | Offline/air-gap modes, model heartbeat consolidation (hw_supervisor), backup script, milestones, enhanced Ollama status |
| v0.34 | Walkthrough | 6-step first-run walkthrough with skip/skip-all, re-trigger from Config, fleet.toml persistence |
| v0.35 | Evaluator-Optimizer | REVIEW status, adversarial review skill, high-stakes gate in worker, 3-provider review (Claude/Gemini/local) |
| v0.36 | Semantic Watchdog | QUARANTINED status, failure streak detection, stuck review auto-pass, DLP secret scrubbing (DB + knowledge files) |
| v0.37 | Human-in-the-Loop | WAITING_HUMAN status, Fleet Comm tab, operator response flow, security advisory approve/dismiss |
| v0.38 | Security & Sandboxing | [security] config, Docker sandbox policy, pip-audit dependency scanning, 127.0.0.1 binding verification |
| v0.39 | Network & IoT | UniFi controller, Home Assistant, MQTT inspection skills |
| v0.40 | Browser Skills | Playwright browser_crawl with JS rendering, httpx fallback |
| v0.41 | Vision & Multi-Modal | Local vision via Ollama (llava/minicpm-v/qwen-vl), VRAM rotation in hw_supervisor |
| — | PT-1 FleetBridge | FleetBridge ABC, WslBridge/DirectBridge, replaced wsl()/wsl_bg() |
| — | PT-2 Build | Cross-platform build.py replacing build.bat |
| — | DT-1 Debug Reports | generate_debug_report(), log ring buffer, global exception handler |
| — | DT-2/3 Issues | Report Issue button, resolutions.jsonl, /api/resolutions endpoint |
| — | CM-1/2/3/4 Comms | Triple-layer channels (sup/agent/fleet/pool), notes scratchpad, CLI + dashboard |
| — | CT-1/CT-2 Cost | Usage table, PRICING/calculate_cost, /api/usage + /api/usage/delta, CLI usage cmd |
| — | CT-3/CT-4 Cost | Delta comparison CLI + regression endpoint, token budgets [budgets] config, budget enforcement |
| — | DT-4 Stability | stability_report.py skill, resolutions.jsonl pattern detection, knowledge output |
| — | Cross-Platform | FleetBridge abstraction, platform packaging, CI/CD matrix (parallel track) |
| — | Diagnostics | Debug reports, issue submission, resolution tracking, stability analysis (parallel track) |
| — | GR-1/2/3/4 Hardening | VRAM eviction, zombie cleanup, base64 DLP, WSL NAT detection |
| — | 4.1 God Object | Console/settings/boot extracted to ui/ namespace (5747→3492 lines, -39%) |
| — | 4.3/4.5 Process+Bridge | REST process control API (6 endpoints), NativeWindowsBridge, detect_cli() |
| — | 4.7/4.8 Skill Audit | 17 skills migrated to call_complex(), cross-platform network detection |
| — | Offline/Air-Gap | Network-aware skill dispatch, local-only fallback, air-gap whitelist, recovery backup |
| v0.42 | Auto-Boot | Zero-click start (systemd/launchd/Task Scheduler), idle skill evolution |
| v0.43 | Marathon ML | Multi-hour training with checkpoint/resume, context persistence |
| v0.44 | Unified Updater | git pull + uv sync in launcher, os.execv hot-reload, update banner |
| v0.45 | Omni-Box + HA | Ctrl+K command palette, model fallback cascade (Claude→Gemini→Local) |
| v0.46 | GitHub Sync | OAuth Device Flow skill, repo clone/push/backup autonomy |
| v0.47 | Owner Core | Shadow module with BIGED_OWNER_KEY gate, internal CRM, remote diagnostics |
| — | PT-3 Packaging | package_linux.py (AppImage), package_macos.py (.app/DMG), installer_cross.py |
| — | 4.6 tomlkit | Regex TOML writes → tomlkit (preserves comments/formatting) |
| 1.0 | Production | Zero debt, full cross-platform, all parallel tracks complete |
| 2.0 | Multi-Fleet | Federated supervisor mesh, remote dashboard, fleet cloning |
| 3.0 | Intelligent | ML-driven routing, predictive scaling, NL fleet control |
| 4.0 | Enterprise | Multi-tenant, RBAC, audit logging, SLA monitoring |
| 5.0 | Platform | Self-hosted SaaS, web launcher, marketplace, semver transition at 9.x → 0.1.00 |
| — | Post-1.0 Hardening | DLP expansion (Azure/GCP/DB-URI/private-key patterns, extended file-type scrub), MQTT wildcard blocking, 66 skills, 40 dashboard endpoints, 17 smoke tests, doc drift cleanup |
