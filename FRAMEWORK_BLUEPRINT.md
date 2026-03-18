# BigEd CC v0.30 — Framework Blueprint

> **Production-ready modular AI agent platform.** Customer-deployable, 24/7 capable, with safe deprecation, thermal management, and iterative skill training.

---

## 1. Architecture Overview

```
BigEd CC (v0.30)
├── Launcher (BigEd/launcher/)
│   ├── launcher.py          — Core app shell (~2500 lines)
│   │   ├── Header            — CPU/RAM/GPU/ETH stats (3s poll, hysteresis)
│   │   ├── Sidebar           — Fleet/Security/Research/Config/Consoles
│   │   ├── Core Tabs          — Command Center, Agents (always on)
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
│   ├── config.py              — TOML config loader
│   ├── dashboard.py           — Flask web dashboard v2 (SSE, alerts, 14 endpoints)
│   ├── smoke_test.py          — 10-check startup verification (--fast mode)
│   ├── soak_test.py           — 10-check extended validation (concurrency, WAL stress)
│   ├── fleet.toml             — Master configuration
│   └── skills/                — 46+ skill modules
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
tasks:    id, created_at, assigned_to, status, priority, type, payload_json, result_json, error
messages: id, from_agent, to_agent, created_at, read_at, body_json
locks:    name(PK), holder, acquired_at
```

WAL mode, 30s busy timeout, retry writes with jittered backoff.

## 4. Dashboard v2

### Endpoints (14 total)

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
| `/api/data_stats` | fleet.db + tools.db | Per-module data metrics |
| `/api/alerts` | in-memory | Active alerts |
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

### Smoke Test (10 checks, ~2s fast / ~10s full)

```
1. Skill imports (46+ modules)    6. Message round-trip
2. DB health (task lifecycle)     7. Broadcast round-trip
3. Config health (fleet.toml)     8. Stale recovery
4. Ollama reachable*              9. Training lock
5. RAG search*                    10. Thermal readings*
                                  (* = skipped in --fast)
```

### Soak Test (10 checks, concurrent stress)

```
1. Task flood (100 tasks)         6. Broadcast under load (20 agents)
2. Task claim/complete            7. Training lock lifecycle
3. Concurrent claims (threads)    8. Deprecation lifecycle
4. Lock contention                9. Module manifest validation
5. Stale recovery under load      10. DB WAL stress (6 threads)
```

## 7. Configuration Reference (fleet.toml)

| Section | Key Fields |
|---------|-----------|
| `[fleet]` | eco_mode, idle_enabled, max_workers, discord_bot_enabled |
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

## 8. Deployment

### Package Contents

```
BigEdCC/
├── BigEd/launcher/launcher.py  — Main executable (or PyInstaller bundle)
├── BigEd/launcher/modules/     — Module files + manifest
├── fleet/                      — Fleet runtime
├── fleet.toml                  — Customer configuration
└── README.md                   — Deployment guide
```

### Customer Configuration Steps

1. Choose deployment profile: `fleet.toml → [launcher] profile = "consulting"`
2. Enable/disable modules: `[launcher.tabs]` section
3. Set model tier: `[models] local = "qwen3:8b"` (adjust for hardware)
4. Configure thermal limits: `[thermal]` section (RTX 3080 Ti defaults)
5. Set API keys: `~/.secrets` (Claude, Gemini, search APIs)
6. Start: `python supervisor.py` + `python launcher.py`

### Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU | None (CPU mode) | RTX 3060+ (8GB+ VRAM) |
| RAM | 8GB | 16GB+ (conductor model uses ~3GB) |
| CPU | 4 cores | 8+ cores (workers + Ollama) |
| Storage | 10GB | 50GB+ (knowledge base growth) |

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
| v0.30 | Production | Framework blueprint, release checklist, customer-deployable |
