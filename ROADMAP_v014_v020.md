# BigEd CC Roadmap: v0.14 → v0.20

> **Goal of v0.20:** 100% local 24/7 autonomous fleet operation on RTX 3080 Ti.
> Thermal-aware, training-safe, self-sustaining. No human intervention required.

## Default Model Configuration (locked for v0.20)

| Slot | Model | Runtime | Purpose |
|------|-------|---------|---------|
| GPU worker | qwen3:8b | GPU (VRAM) | Fleet task execution |
| Conductor | qwen3:4b | CPU (num_gpu=0) | User chat, intent parsing |
| Maintainer | qwen3:0.6b | CPU | Always-on health/intent fallback |

2 CPU + 1 GPU = default. hw_supervisor scales GPU model down under pressure.

## Thermal Targets

| Metric | Sustained | Burst (60s) | Action |
|--------|-----------|-------------|--------|
| GPU junction | ≤75°C | ≤78°C | Normal |
| GPU junction | 75-78°C | — | Reduce worker concurrency |
| GPU junction | >78°C | — | Pause GPU tasks, CPU-only mode |
| CPU package | ≤80°C | ≤85°C | Normal |
| Cooldown rate | — | — | Estimate ambient from delta-T/time |

---

## v0.14 — Thermal & Power Monitoring

**Goal:** hw_supervisor becomes a real hardware manager, not just VRAM watcher.

### 14.1 Thermal monitoring in hw_supervisor.py
- GPU temp via `pynvml.nvmlDeviceGetTemperature()`
- GPU power draw via `pynvml.nvmlDeviceGetPowerUsage()`
- CPU temp via `psutil` (or WMI fallback on Windows)
- Fan speed via `pynvml.nvmlDeviceGetFanSpeed()`
- All readings logged to `hw_state.json` (expanded schema)

### 14.2 Thermal throttling logic
- Sustained >75°C GPU: reduce `max_workers` by 2, increase poll interval
- Sustained >78°C GPU: pause all GPU tasks, switch to CPU-only Ollama
- Recovery: resume when <72°C for 60s consecutive

### 14.3 Ambient temperature estimation
- Track cooldown rate (°C/s) when transitioning from load to idle
- Estimate ambient = idle_temp - (delta_under_no_load × cooling_efficiency)
- Store in hw_state.json for dashboard/logging

### 14.4 Externalize hw_supervisor config to fleet.toml
- Move hardcoded thresholds (75%/90% VRAM, 5s poll, 15s grace, 30s cooldown) to `[thermal]` section
- Move tier models to `[models.tiers]`

**Files:** `fleet/hw_supervisor.py`, `fleet/fleet.toml`

---

## v0.15 — Training Lock & Exclusivity

**Goal:** Only one training process at a time. Fleet coordinates around it.

### 15.1 Training lock in DB
- New `locks` table: `name TEXT UNIQUE, holder TEXT, acquired_at TEXT`
- `db.acquire_lock("training", agent_name)` → bool
- `db.release_lock("training", agent_name)`
- `db.check_lock("training")` → holder or None

### 15.2 skill_train.py respects lock
- Before starting: acquire lock, fail gracefully if held
- On completion/crash: release lock (with try/finally)
- Workers check lock before claiming `skill_train` tasks

### 15.3 Supervisor training awareness
- supervisor.py reads training lock from DB (not just `pgrep train.py`)
- hw_supervisor.py checks lock too — pre-emptive VRAM clearing
- Workers pause GPU-heavy skills when training lock is held

### 15.4 autoresearch integration
- train_profile.py acquires DB lock before launching train.py
- Releases on exit (even OOM crash)

**Files:** `fleet/db.py`, `fleet/worker.py`, `fleet/supervisor.py`, `fleet/hw_supervisor.py`, `fleet/skills/skill_train.py`, `autoresearch/train_profile.py`

---

## v0.16 — Modular Tabs (Disable by Default)

**Goal:** Launcher tabs become config-driven. Only Command Center + Agents enabled by default.

### 16.1 Tab registry in fleet.toml
```toml
[launcher.tabs]
command_center = true   # always on
agents = true           # always on
crm = false
onboarding = false
customers = false
accounts = false
ingestion = true        # keep — active use
outputs = true          # keep — knowledge browser
```

### 16.2 Refactor launcher.py tab creation
- Each tab becomes a lazy-loaded module (only built when enabled)
- `_build_tabs()` reads config, skips disabled tabs
- Tab enable/disable from Settings panel (runtime toggle, saves to fleet.toml)

### 16.3 Tab module extraction (optional, if lines justify)
- CRM (40 lines) — keep inline
- Onboarding (50 lines) — keep inline
- Ingestion (125 lines) — candidate for extraction
- Outputs (67 lines) — keep inline

**Files:** `BigEd/launcher/launcher.py`, `fleet/fleet.toml`

---

## v0.17 — Data Foundation & Idle Policy

**Goal:** Agents stay productively busy building base data. Training prioritized.

### 17.1 Enable idle curriculum by default
- `idle_enabled = true` in fleet.toml
- Add `idle_min_queue_depth = 3` — only run idle tasks when pending queue < 3
- Idle timeout reduced to 15s (from 30s) for faster engagement

### 17.2 Training-focused idle curricula
- Researcher: arxiv_fetch focused on training techniques, model efficiency
- Analyst: analyze_results after each training run, track trends
- Coders: skill_evolve on underperforming skills, code quality audits
- Archivist: continuous RAG indexing, flashcard generation from training logs
- Planner: plan_workload with focus="training" + "research"
- Security: periodic config audit (fleet.toml, secrets, network)

### 17.3 Data accumulation targets
- knowledge/summaries/ — target 100+ documents (research papers, techniques)
- knowledge/leads/ — populate empty JSONL files (lead research runs)
- flashcards.jsonl — target 500+ cards
- RAG index — continuous incremental updates

### 17.4 Skill training in idle curriculum
- Add `skill_train` to coder idle curricula (dry_run first, then real)
- Planner queues `skill_train` for skills with >20% failure rate
- Training runs capped at 3 iterations during idle (not 5)

**Files:** `fleet/fleet.toml`, `fleet/idle_curricula/*.toml`, `fleet/skills/plan_workload.py`

---

## v0.18 — Better Data Handling

**Goal:** Data pipeline from raw → indexed → queryable → actionable.

### 18.1 Task result archival
- Completed tasks with result_json archived to knowledge/task_archive/
- Rotate: keep last 1000 in DB, archive older to JSONL
- Index archived results in RAG for fleet-wide learning

### 18.2 Structured data validation
- lead_research outputs: validate JSONL schema (company, contact, industry, score)
- summarize outputs: validate markdown structure (title, bullets, source)
- Reject malformed outputs → fail_task with "schema_validation" error

### 18.3 Knowledge deduplication
- RAG index tracks content hashes
- Skip re-indexing unchanged files
- Merge duplicate summaries on same topic

### 18.4 Dashboard data endpoints
- `/api/thermal` — live GPU/CPU temps, fan speed, power draw
- `/api/training` — training lock status, active run, results history
- `/api/data_stats` — knowledge/ size breakdown, growth rate, gaps

**Files:** `fleet/db.py`, `fleet/rag.py`, `fleet/dashboard.py`, `fleet/skills/lead_research.py`, `fleet/skills/summarize.py`

---

## v0.19 — Sustained Operation Hardening

**Goal:** Handle every failure mode. No human intervention for 7 days.

### 19.1 Watchdog timer
- If supervisor.py main loop stalls >60s, self-restart
- If hw_supervisor.py stalls >30s, workers default to safe mode
- Workers detect stale hw_state.json (>60s old) → assume safe, continue

### 19.2 DB maintenance
- Auto-VACUUM weekly (or at 100MB)
- WAL checkpoint every 6 hours
- Prune completed tasks older than 30 days (archive first)

### 19.3 Log rotation
- Per-worker logs: max 10MB, rotate to .log.1 (keep 3)
- supervisor.log: max 20MB, rotate
- search_waterfall.jsonl: max 50MB, truncate old entries

### 19.4 Memory leak detection
- Track worker RSS via psutil every 60s
- If worker RSS > 500MB, restart it (log warning)
- Track Ollama RSS — if >8GB on CPU model, restart Ollama

### 19.5 Network resilience
- Ollama health check with exponential backoff (not just 30s timeout)
- API calls (Claude/Gemini) retry with jitter on 5xx/timeout
- Discord bot auto-reconnect on disconnect

**Files:** `fleet/supervisor.py`, `fleet/hw_supervisor.py`, `fleet/worker.py`, `fleet/db.py`

---

## v0.20 — Always-On Certification

**Goal:** Verified 24/7 autonomous operation. All systems go.

### 20.1 Integration test suite
- Extended smoke_test.py: thermal readings, training lock, idle curriculum activation
- 1-hour soak test: launch fleet, submit 50 tasks, verify all complete
- Thermal stress test: run GPU model + training simultaneously, verify throttling works
- Kill-test: randomly kill workers/supervisors, verify recovery

### 20.2 Default configuration locked
```toml
[fleet]
eco_mode = false
idle_enabled = true
idle_timeout_secs = 15
idle_min_queue_depth = 3

[models]
local = "qwen3:8b"           # GPU worker
conductor_model = "qwen3:4b" # CPU user chat
maintainer = "qwen3:0.6b"    # CPU always-on

[thermal]
gpu_max_sustained_c = 75
gpu_max_burst_c = 78
cpu_max_sustained_c = 80
poll_interval_secs = 5
cooldown_target_c = 72
cooldown_window_secs = 60
ambient_estimation = true

[training]
exclusive_lock = true         # only 1 training at a time
auto_pause_gpu_tasks = true   # pause GPU skills during training
max_concurrent_training = 1
```

### 20.3 Operational runbook
- Document: startup sequence, expected thermal profile, failure recovery
- Verify: all 11+ workers register within 60s
- Verify: idle curriculum engages within 30s of empty queue
- Verify: training lock prevents concurrent training
- Verify: thermal throttling activates at 75°C, recovers at 72°C

### 20.4 24/7 launch mode
- `supervisor.py --daemon` flag: detached, auto-restart on crash
- `start.bat` updated for always-on (no manual restart needed)
- Nightly health report broadcast to all agents (logged)

**Files:** `fleet/smoke_test.py`, `fleet/supervisor.py`, `fleet/fleet.toml`, `BigEd/launcher/start.bat`

---

## Implementation Priority

| Version | Risk | Effort | Dependency |
|---------|------|--------|------------|
| v0.14 Thermal | Low | Med | pynvml already in use |
| v0.15 Training lock | Low | Low | DB schema addition |
| v0.16 Modular tabs | Low | Med | Launcher refactor |
| v0.17 Idle policy | Low | Low | Config + curricula edits |
| v0.18 Data handling | Med | Med | RAG + DB changes |
| v0.19 Hardening | Med | High | Touches everything |
| v0.20 Certification | Low | Med | Testing + config lock |
