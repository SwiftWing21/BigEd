# BigEd CC Roadmap: v0.31 → 5.0

> **Goal of 1.0:** Autonomous, cross-platform, verifiably safe agent fleet.
> **Goal of 5.0:** Multi-tenant SaaS-ready platform with federated fleet orchestration.
> **Version scheme:** v0.41 → v0.42 → ... → v0.99 → 1.0 → ... → 9.x → 0.1.00 (semver transition)

---

## Program Milestones

| Milestone | Versions | Theme | Gate |
|-----------|----------|-------|------|
| 1. Verification & Onboarding | v0.33 – v0.34 | Prove it works, make it approachable | Smoke 10/10, Soak 13/13, GUI smoke, no P0 debt |
| 2. Autonomous Safety | v0.35 – v0.38 | Self-correction, operator comms, isolation | + review cycle, watchdog, HitL, sandbox tests; .secrets never in output |
| 3. External Integration | v0.39 – v0.41 | Network, browser, vision | + network/browser/vision tests; no OOM on 12GB |
| 4. Autonomous Continuity | v0.42 – v0.43 | Auto-boot, idle evolve, marathon ML | Zero-click start, stable multi-hour ML |
| 5. Elegance & Availability | v0.44 – v0.45 | Unified lifecycle, Omni-box, HA routing | Seamless updates, zero-downtime task execution |
| 6. User Sync & Admin Tools | v0.46 – v0.47 | GitHub pairing, restricted owner CRM | Seamless auth, private internal tooling |
| 7. Codebase Simplification | v0.48         | Cautious bloat/dead code removal | AST scanning, graveyard quarantine, 100% soak pass |
| 8. Autonomy Expansion      | 0.05.00 – 0.06.00 | Git, MLOps, and Security skills | New skills tested, key rotation verified |
| 9. Cross-Platform & v1.0 | PT-1 – PT-4, DT-1 – DT-4 | Anyone, anywhere, clear diagnostics | All tests on Win/Linux/macOS; FleetBridge 100%; zero debt |

## Release Process

**Branching (documented — not yet active):**
- Current: all work on `main`, `dev` branch used as periodic backup snapshot.
- Future: daily work on `dev`, merge to `main` only at milestones via `git merge --no-ff dev`.
- To activate: move daily work to `dev`, apply stability gate before each `dev → main` merge.

**Stability Gate Checklist (every milestone merge to main):**
```
## Release Gate: v0.XX
- [ ] Smoke tests: 10/10
- [ ] Soak tests: 13/13
- [ ] GUI smoke test: pass (v0.33+)
- [ ] TECH_DEBT.md: reviewed, no P0
- [ ] FRAMEWORK_BLUEPRINT.md: version row added
- [ ] ROADMAP: version marked DONE with date
- [ ] git status: clean
- [ ] Backup run: bash scripts/backup.sh
```

**Backup:** `bash scripts/backup.sh` — copies fleet.db, rag.db, tools.db, knowledge/ to `~/BigEd-backups/`. Keeps last 10. Run before every milestone merge and schema migration.

---

## v0.31 — Task Graph & Decomposition (DAG) [DONE]

Completed 2026-03-18. See `db.py` for implementation.

- `parent_id`, `depends_on` columns, `WAITING` status
- `_promote_waiting_tasks()` / `_cascade_fail_dependents()` on complete/fail
- `post_task_chain()` helper for sequential pipelines
- `post_task()` validates JSON payloads, clamps priority 1-10
- `complete_task()` validates result JSON, auto-wraps non-JSON
- Soak tests: 13/13 pass (3 new DAG/validation tests)

---

## v0.32 — UI Resilience & Clean Refresh [DONE]

Completed 2026-03-18. All changes in `launcher.py`.

- 32.1: All 3 timer chains (`_schedule_refresh`, `_schedule_hw`, `_schedule_ollama_watch`) wrapped in try/except/finally — exceptions logged via `_log_output`, reschedule always fires
- 32.2: `_agents_tab_refresh` converted from destroy/recreate to cache+configure pattern (`self._agents_tab_cache` dict), matching `_update_agents_table` — eliminates flicker
- 32.3: `self._tabs` ref stored; `_schedule_refresh` now calls `on_refresh()` for the active module tab only
- 32.4: `_poll_task_result` posts timeout notification when polling exhausts without DONE/FAILED

---

## v0.33 — End-to-End Flow Verification + Offline/Air-Gap + Model Heartbeat [DONE]

Completed 2026-03-18.

- 33.0: **Offline mode** (`offline_mode = true` in fleet.toml): external API skills gracefully rejected, local Ollama works, Discord/OpenClaw skipped, launcher shows OFFLINE badge
- 33.0: **Air-gap mode** (`air_gap_mode = true`): max isolation, deny-by-default skill whitelist (14 approved), dashboard disabled, secrets not loaded, launcher shows AIR-GAP badge
- 33.0: **Model heartbeat consolidation**: hw_supervisor now owns keepalive (every ~240s), conductor health (every ~60s), loaded model inventory. supervisor.py reduced to process lifecycle only.
- 33.0: **hw_state.json expanded**: `models_loaded` list + `conductor` status. Launcher reads for `+chat`/`-chat` in Ollama status bar.
- 33.0: **Enhanced Ollama status**: model name, CPU/GPU(queued_tasks), VRAM, conductor status
- 33.0: **Recovery**: `scripts/backup.sh` for fleet.db, rag.db, tools.db, knowledge/
- 33.0: **Milestones**: program milestones (v0.33→v1.0), stability gate checklist, release process
- 33.0: **Git branching**: `dev` branch as backup snapshot, full strategy documented
- Smoke: 10/10, Soak: 15/15 (2 new offline/air-gap tests)

**Goal:** Systematic verification that every user-facing flow completes its round-trip, with automated smoke tests for the GUI layer.

### 33.1 Flow Audit Checklist

Trace and verify every user action → backend → UI feedback path:

| Flow | Entry Point | Backend | UI Feedback | Status |
|------|------------|---------|-------------|--------|
| Taskbar dispatch | `_dispatch_task()` | `lead_client.py dispatch` via WSL | `_log_output()` callback | Verify |
| Console dispatch | `_execute_dispatch()` | `lead_client.py dispatch` via WSL | `_poll_task_result()` → chat | Verify |
| Console API key save | `_set_key_dialog()` | `lead_client.py secret set` via WSL | Status label update | Verify |
| Key Manager edit | `_edit_key()` | `lead_client.py secret set` via WSL | `_scan_lbl` + `_load_keys()` | Verify |
| Key Manager scan | `_scan_skills()` | `lead_client.py dispatch` via WSL | `_scan_lbl` update | Verify |
| Module CRM add | `_add_dialog()` | `_db_conn()` INSERT | `on_refresh()` | Verify |
| Module Accounts edit | `_edit_dialog()` | `_db_conn()` UPDATE | `on_refresh()` | Verify |
| Module Onboarding toggle | checkbox `_on_toggle` | `_db_conn()` UPDATE | `on_refresh()` | Verify |
| Module Customers edit | `_edit_dialog()` | `_db_conn()` UPDATE | `on_refresh()` | Verify |
| Agents tab edit | `_agents_edit_dialog()` | `_db_conn()` INSERT/DELETE | `_agents_tab_refresh()` | Verify |
| Header HW stats | `_schedule_hw()` | pynvml/psutil | `_apply_hw()` labels | Verify |
| Sidebar Ollama status | `_schedule_ollama_watch()` | HTTP GET /api/tags | `_apply_ollama_status()` | Verify |
| Fleet pills | `_schedule_refresh()` | `parse_status()` file read | `_update_pills()` | Verify |

### 33.2 GUI Smoke Test Script

Create `BigEd/launcher/gui_smoke_test.py` — headless verification of GUI flows:
- Import launcher module, instantiate `BigEdCC` with `withdraw()` (hidden window)
- Verify all timer registrations fire at least once without exception
- Verify module load/build_tab/on_refresh cycle for each enabled module
- Verify `_dispatch_raw()` constructs valid WSL command (string check, not execution)
- Verify `_db_init()` creates all expected tables
- Verify `_find_fleet_dir()` resolves correctly from launcher directory
- Run headless with `--smoke` flag, exit 0/1

### 33.3 Visual Refresh Regression Test

Manual test protocol (documented, not automated):
1. Launch app, observe Agents tab for 30s — no flicker on row updates
2. Switch between all module tabs — each loads data without stutter
3. Open Console, dispatch a task — verify result appears in chat within 60s
4. Open Key Manager — verify keys load, edit saves, scan queues
5. Monitor header stats for 60s — no color flicker at threshold boundaries
6. Kill a fleet worker (pkill) — verify Agents tab shows status change within 8s

---

## v0.34 — New User Walkthrough (First-Run Experience) [DONE]

Completed 2026-03-18.

- 34.1: `WalkthroughDialog` class — 6-step modal overlay with progress bar, skip/skip-all, "don't show again"
- 34.2: Steps: Welcome, API Keys (opens Key Manager), Fleet Profile, Ollama Setup, First Task examples, Console Tour
- 34.3: Persistence: writes `[walkthrough] completed = true` to fleet.toml with skipped_steps and timestamp
- 34.4: Re-trigger: "Setup Walkthrough" button in Config sidebar section
- 34.5: Auto-trigger on first launch (500ms delay after UI build) when `[walkthrough] completed = false`

**Goal:** Guide new users through initial setup on first launch, with the ability to skip individual steps or the entire walkthrough.

### 34.1 Walkthrough Engine (`launcher.py`)

A modal overlay or toplevel dialog sequence that triggers on first launch (or when `fleet.toml` has no `[walkthrough] completed = true`).

**Core mechanics:**
- Each step is a standalone dialog with: title, description, action area, and navigation buttons
- **"Skip" checkbox** per step — marks step as skipped, advances to next
- **"Skip All" button** — closes walkthrough immediately, marks all remaining as skipped
- **"Don't show again" checkbox** — persists to `fleet.toml [walkthrough] completed = true`
- Progress indicator: step N of M (dots or progress bar, matching updater style)
- Steps can be re-triggered from Config sidebar: "Re-run Setup Walkthrough"

### 34.2 Walkthrough Steps

| Step | Title | What it does | Skippable reason |
|------|-------|-------------|-----------------|
| 1 | Welcome | Brief overview of BigEd CC, what the fleet does | Returning users |
| 2 | API Keys | Guide through setting Claude/Gemini/search API keys via Key Manager dialog | User may only use local models |
| 3 | Fleet Profile | Select deployment profile (minimal/research/consulting/full), show what modules each enables | Already configured |
| 4 | Ollama Setup | Check if Ollama is running, show model tier table, offer to pull default model | Already installed |
| 5 | First Task | Pre-filled taskbar dispatch (e.g. "Research local AI deployment for small businesses") with explanation of what happens | User wants to explore first |
| 6 | Console Tour | Highlight the 3 console tabs (Claude/Gemini/Local), show how to switch and dispatch | Experienced users |

### 34.3 Persistence & Config

```toml
[walkthrough]
completed = false          # set true after finish or Skip All
skipped_steps = []         # list of step numbers skipped
completed_at = ""          # ISO date when walkthrough was completed/skipped
```

### 34.4 Implementation Notes

- Dialog class: `WalkthroughDialog(ctk.CTkToplevel)` with step state machine
- Each step is a dict: `{"title", "description", "build_content_fn", "validate_fn"}`
- `build_content_fn(parent_frame)` renders step-specific UI into the dialog body
- `validate_fn()` returns `(ok, msg)` — allows steps to verify setup before advancing (e.g., "Ollama not reachable" warning)
- Skip checkbox state passed to navigation: skipped steps log to `fleet.toml` but don't block progress
- Walkthrough respects current profile — only shows relevant steps (e.g., skip API Keys step if profile is minimal/local-only)

**Files:** `launcher.py` (new `WalkthroughDialog` class + trigger in `__init__` after `_build_ui`)

---

## v0.35 — The Evaluator-Optimizer Loop (Guard Rails) [DONE]

Completed 2026-03-18.

- 35.1: `REVIEW` status added to task lifecycle (RUNNING -> REVIEW -> DONE or REVIEW -> PENDING for retry)
- 35.2: `db.review_task()` and `db.reject_task()` functions with `review_rounds` column tracking
- 35.3: `skills/_review.py` — adversarial reviewer supporting 3 providers (Claude API, Gemini, local Ollama with /think)
- 35.4: `worker.py` — review gate in dispatch loop: checks `[review] enabled`, `HIGH_STAKES_SKILLS`, max rounds
- 35.5: HIGH_STAKES_SKILLS: code_write, code_write_review, legal_draft, security_audit, security_apply, pen_test, skill_draft, skill_evolve, branch_manager, product_release
- 35.6: Review failure auto-passes (don't block work on infra errors), max 2 rounds default (configurable)
- 35.7: Critique appended to payload as `_review_critique` + `_review_round` for worker context on retry
- Soak: 17/17 (2 new review tests: lifecycle + verdict parsing)

**Goal:** Prevent sub-par or hallucinated outputs from being finalized without adversarial review.

---

## v0.36 — Semantic Watchdog (Checker Agent) [DONE]

Completed 2026-03-18.

- 36.1: `QUARANTINED` agent status — worker checks and pauses if quarantined, operator clears via `db.clear_quarantine()`
- 36.2: `_watchdog.py` — semantic health monitor called by supervisor every 60s
  - Failure streak detection: 3+ consecutive failures → auto-quarantine agent
  - Stuck review detection: tasks in REVIEW >30min → auto-pass
  - DLP secret scrubbing: scans task results + knowledge/ files for leaked API keys (sk-*, AIza*, ghp_*, etc), redacts in-place
- 36.3: DB functions: `quarantine_agent()`, `clear_quarantine()`, `get_failure_streaks()`, `get_stuck_reviews()`
- 36.4: Supervisor integration: `run_cycle()` every 60s, `run_full_cycle()` (includes knowledge scan) every 10min
- 36.5: Secret patterns: Anthropic, Google, GitHub, Slack, AWS, Tavily + env-var exact-match detection
- Smoke: 10/10, Soak: 19/19 (2 new: quarantine lifecycle, DLP scrubbing)

**Goal:** Move beyond mechanical process restarts to semantic health monitoring.

---

## v0.37 — Unified Human-in-the-Loop (HitL) [DONE]

Completed 2026-03-18.

- 37.1: `WAITING_HUMAN` task status — agents can pause mid-task and ask operator a question
- 37.2: DB functions: `request_human_input()`, `respond_to_agent()`, `get_waiting_human_tasks()`
- 37.3: Operator response appended to payload as `_human_response` — task resumes to PENDING for re-claim
- 37.4: Worker handles `human_response` message type
- 37.5: **Fleet Comm tab** — always-on core tab showing:
  - Pending WAITING_HUMAN tasks with question, reply field, Send button
  - Security advisories from `knowledge/security/pending/` with Approve/Dismiss buttons
  - Auto-refreshes when active tab (every 3rd cycle)
- 37.6: Security remediation: Approve dispatches `security_apply`, Dismiss moves to `dismissed/` subfolder
- Soak: 20/20 (1 new: WAITING_HUMAN lifecycle)

**Goal:** Allow agents to dynamically request human input mid-task.

---

## v0.38 — Fleet Security & Isolation (Sandboxing) [DONE]

Completed 2026-03-18. **Milestone 2 (Autonomous Safety) complete.**

- 38.1: `[security]` config: sandbox_enabled, sandbox_skills, dependency_scan_enabled, network_hardening_enabled
- 38.2: Worker sandbox policy: Docker availability check for code_write/skill_test/benchmark (soft enforcement)
- 38.3: Dependency scanning in security_audit: pip check + pip-audit integration
- 38.4: Network hardening in pen_test: 127.0.0.1 binding verification for Ollama (11434) and Dashboard (5555) via ss/netstat
- Smoke: 10/10, Soak: 23/23

**Goal:** Protect the host environment from malicious or hallucinated agent code execution.

---

## v0.39 — Advanced Network & IoT Orchestration [DONE]

Completed 2026-03-18.

- 39.1: `unifi_manage.py` — UniFi Controller API: list_clients, list_devices, list_alerts, get_firewall, get_dpi. Auth via UNIFI_HOST/USER/PASS in ~/.secrets. Self-signed cert handling.
- 39.2: `home_assistant.py` — HA REST API: list_entities, list_automations, create_backup, list_backups, get_entity, call_service. Auth via HA_URL/HA_TOKEN.
- 39.3: `mqtt_inspect.py` — MQTT broker: listen (subscribe + capture for N seconds), publish. Auth via MQTT_HOST/PORT/USER/PASS. Uses paho-mqtt.
- 39.4: `[integrations]` config section: unifi_enabled, ha_enabled, mqtt_enabled
- 39.5: All 3 skills save to knowledge/network/, knowledge/home_assistant/, knowledge/mqtt/. All REQUIRES_NETWORK = True. Added to researcher affinity.

**Goal:** Deep integration with local infrastructure.

---

## v0.40 — Full DOM Web Interactivity (Browser Skills) [DONE]

Completed 2026-03-18.

- 40.1: `browser_crawl.py` — Playwright (headless Chromium): crawl (text+links), screenshot (PNG), extract (CSS selector). Graceful httpx fallback if Playwright not installed.
- 40.2: JS rendering with configurable wait_sec, viewport control, networkidle detection
- 40.3: Saves to knowledge/browser/. REQUIRES_NETWORK = True. Added to researcher affinity.

**Goal:** Overcome raw HTTP limitations for JS-heavy web applications.

---

## v0.41 — Local Vision & Multi-Modal Orchestration [DONE]

Completed 2026-03-18. **Milestone 3 (External Integration) complete.**

- 41.1: `vision_analyze.py` — local multimodal via Ollama: describe, ocr, analyze_chart. Supports llava, minicpm-v, qwen-vl. Uses base64 image encoding.
- 41.2: `[models] vision_model` config (default: llava)
- 41.3: VRAM rotation: skill signals hw_state.json with vision_request. hw_supervisor loads vision model on demand when not throttled/training. Clears flag after inference.
- 41.4: Saves to knowledge/vision/. REQUIRES_NETWORK = False (Ollama is local).
- Smoke: 10/10 (49 skills), Soak: 25/25 (2 new: skill imports + integration config)

**Goal:** Enable fleet agents to process visual data completely offline.

---

## v0.42 — Auto-Boot & Idle Skill Evolution

**Goal:** Zero-click fleet startup. Workers productively self-improve when idle.

### 42.1 Auto-Boot (System Service)

Platform-aware auto-start so the fleet runs on login/boot without manual intervention.

- **Windows:** Task Scheduler entry via `schtasks /create` — runs `supervisor.py` on user login
- **Linux:** `systemd --user` service file (`biged-fleet.service`) — `ExecStart=uv run python supervisor.py`
- **macOS:** `launchd` plist in `~/Library/LaunchAgents/` — runs on login
- `lead_client.py install-service` / `uninstall-service` commands — platform-conditional
- `fleet.toml [autoboot] enabled = true` config flag
- `supervisor.py` idempotent start: checks if already running before spawning

### 42.2 Idle Skill Evolution

When no tasks are pending, workers auto-discover improvement opportunities.

- `worker.py`: Idle detection — if no task claimed after N polls (configurable), enter idle mode
- Idle mode dispatches one of: `skill_evolve`, `skill_test`, `code_quality`, `benchmark`
- Skill selection: round-robin from skills with oldest `last_evolved` timestamp
- `fleet.toml [idle] enabled, interval_secs, skills` — configurable idle behavior
- `db.py`: `idle_runs` table tracking which skills were evolved, when, results
- Idle work is low-priority (priority=1) — any real task immediately preempts
- Budget-aware: idle work respects CT-4 daily budgets

### 42.3 Fleet Health Dashboard

- `/api/fleet/uptime` endpoint — fleet uptime since last start, restart count
- Auto-boot status in launcher sidebar (service installed / running / not configured)

---

## v0.43 — Marathon ML & Context Persistence

**Goal:** Stable multi-hour ML training with checkpoint/resume. Session context survives restarts.

### 43.1 Marathon Training Integration

- `autoresearch/` integration: `marathon_log` skill writes progress snapshots every N minutes
- Checkpoint detection: hw_supervisor monitors `autoresearch/checkpoints/` for new files
- Training resume: if supervisor restarts mid-training, detect checkpoint and resume
- VRAM budgeting: reserve 6GB for training, remaining for fleet (auto-scale to tier_low)

### 43.2 Context Persistence

- `marathon_log` auto-invoked at session boundaries (fleet start, fleet stop, midnight rollover)
- `knowledge/marathon/` serves as long-term project memory across sessions
- `lead_client.py marathon status` — shows active marathon sessions, snapshots, progress

### 43.3 Stability Gate

- 8-hour soak test: start fleet, run mixed workload + ML training, verify zero crashes
- Memory leak detection: RSS/VRAM tracked hourly, alert on >10% growth
- **Milestone 4 complete** when: auto-boot working on Win+Linux, idle evolution running, 8h soak clean

---

## Long-Range Roadmap: 1.0 → 5.0

## Feature Isolation Refactor (FI-1 through FI-3)

> Restructure fleet code so each feature is self-contained. Enables agents to work on complete vertical slices without cross-file conflicts.

### FI-1: Easy Extractions [DONE]

- `fleet/services.py` — auto-boot install/uninstall (from lead_client.py)
- `fleet/providers.py` — HA fallback cascade, PRICING, calculate_cost (from _models.py)
- `fleet/cost_tracking.py` — usage logging, summaries, deltas, budgets (from db.py)

### FI-2: Medium Extractions [DONE]

Completed 2026-03-19.

- `fleet/idle_evolution.py` — idle_runs DB functions (from db.py), re-exported via db module
- `fleet/comms.py` — channel constants + message/note CRUD + broadcast (from db.py), re-exported
- `fleet/process_control.py` — Flask Blueprint with all /api/fleet/* endpoints (from dashboard.py)

### FI-3: Complex Extractions [DONE]

Completed 2026-03-19.

- `fleet/marathon.py` — training detection, checkpoints, VRAM eviction (from supervisor.py)
- `fleet/diagnostics.py` — quarantine, failure streaks, stuck reviews (from db.py)
- `fleet/resource_mgmt.py` — deferred (hw_supervisor thermal scaling tightly coupled to main loop)

---

### 1.0 — Production Release [DONE]

Completed 2026-03-19.

- All milestones (1-8) complete: v0.31 through v0.48
- All parallel tracks done: PT-1/2/3/4, DT-1/2/3/4, CT-1/2/3/4, CM-1/2/3/4, GR-1/2/3/4
- All TECH_DEBT resolved: 4.1 through 4.8
- Feature isolation: FI-1/2/3 (9 extracted modules)
- v0.48 dead code scanner + cleanup
- Test suite: smoke 15/15, GUI smoke 8/8, soak 27+
- 55 skills, 31 dashboard endpoints, launcher 3492 lines (-39%)
- Cross-platform: CI matrix, 3 packagers, NativeWindowsBridge, detect_cli()

---

## Version Transition: 1.0 → 0.01.01

> **New versioning:** `MAJOR.MINOR.PATCH` where MAJOR tracks release milestones.
> v1.0 becomes the baseline. Next version: `0.01.01` (first post-1.0 patch).
> Full semver transition at 9.x → 0.1.00.

### 0.01.01 — Post-Release Stabilization

- Architecture research findings incorporated (agent patterns, task routing, UI comparison)
- Dead code graveyard quarantine executed (scanner findings resolved)
- Soak test run under full production load (8-hour validation)
- Community-reported bug fixes from 1.0 release

### 2.0 — Multi-Fleet & Remote Orchestration

- Fleet-to-fleet communication (federated supervisor mesh)
- Remote dashboard access (auth + TLS)
- Fleet cloning (deploy identical fleet to new machine via config export)
- Plugin marketplace (community skills via git repos)
- Version scheme: `2.x.y`

### 3.0 — Intelligent Orchestration

- ML-driven task routing (learn which agent handles which skill best)
- Predictive scaling (anticipate load from task patterns)
- Natural language fleet control ("scale up coders, pause research")
- Auto-generated SOPs from fleet behavior patterns
- Version scheme: `3.x.y`

### 4.0 — Enterprise & Multi-Tenant

- Tenant isolation (separate DBs, configs, knowledge per customer)
- Role-based access control (operator, admin, viewer)
- Audit logging (who did what, when, with what cost)
- SLA monitoring (task completion time guarantees)
- Version scheme: `4.x.y`

### 5.0 — Platform

- Self-hosted SaaS deployment (Docker Compose / K8s)
- Web-based launcher (replace desktop GUI with React/Next.js)
- Federated fleet orchestration (multiple physical machines, single control plane)
- Marketplace: skill store, model store, template store
- Version scheme transition: `5.0.0` → `0.1.00` (semver with patch at 9.x)

---

## v0.44 — Seamless Lifecycle (Unified Updater)
**Goal:** Deliver a polished, single-application experience by absorbing the updater into the launcher.
- **Unified Bootloader:** Deprecate `Updater.exe` and `build.bat`. Integrate `git pull` and dependency syncing (`uv sync`) directly into `launcher.py`'s initial loading sequence.
- **In-Place Restarts:** Implement `os.execv` to allow BigEd CC to transparently hot-reload itself after applying updates without requiring the user to manually relaunch the application.
- **Streamlined Loading Screen:** Combine Ollama checks, update checks, and fleet startup into a single, elegant pre-flight splash screen.

---

## v0.45 — Omni-Box & High Availability (HA) Routing
**Goal:** Maximize user interaction elegance and guarantee task execution despite API outages.
- **Command Palette (Omni-Box):** Implement a global `Ctrl+K` Spotlight-style overlay. Features predictive auto-complete for skills (e.g., `/web_search`), direct agent pinging (`@researcher`), and one-click re-runs of recent tasks.
- **Model Fallback Cascade:** Refactor `skills._models.call_model()` to support resilient fallback chains. If the primary model (e.g., Claude) fails due to rate limits or outages, gracefully fall back to Gemini, then Local Ollama.
- **Graceful Degradation Warnings:** If a task completes via a fallback model, append a non-intrusive warning to the UI task status (e.g., "✓ done (fallback: local)").

---

## v0.46 — Frictionless GitHub Sync & Zero-Bloat Baseline
**Goal:** Provide a seamless way for users to link their private repositories, while defaulting all non-core modules to off.
- **Zero-Bloat Baseline:** Update `fleet.toml` defaults so absolutely zero modules (no ingestion, no outputs) are enabled until the user actively opts in during the Setup Walkthrough.
- **GitHub Device Authorization Flow:** Build an OAuth App integration for the Walkthrough. Users are given an 8-character code to authorize BigEd CC via their browser, completely eliminating the need to manually generate or paste Personal Access Tokens (PATs).
- **Agent Git Autonomy:** With the OAuth token securely stored in `~/.secrets`, agents can autonomously provision private user repos, push code, and back up fleet state.

---

## v0.47 — Restricted Owner Core (Shadow Module)
**Goal:** Create a secure, isolated environment for the software owner to manage customer fleets and internal business operations.
- **Shadow Module Architecture:** Develop `mod_owner_core.py` (containing the internal CRM, global key manager, and remote fleet diagnostics) in a private repository/submodule.
- **Execution Gating:** The module loader strictly requires a verified `BIGED_OWNER_KEY` in `~/.secrets` to mount the tab.
- **Build Exclusion:** Update the `build.py` (PT-2) compiler logic to explicitly exclude `mod_owner_core.py` from public `dist/` artifacts, ensuring normal users cannot reverse-engineer the owner logic.

---

## v0.48 — Cautious Codebase Pruning (Bloat Cleanup)
**Goal:** Safely eliminate dead code, deprecated methods, and orphaned imports left behind by aggressive tech debt resolutions (e.g., `launcher.py` extraction, WSL bash removal) without breaking dynamic UI or agent calls.
- **AST-Based Dead Code Detection:** Utilize Abstract Syntax Tree (AST) scanning tools (like `vulture` or `ruff`) to mathematically identify unused variables, classes, and functions, rather than relying on brittle text searches.
- **The "Graveyard" Quarantine Pattern:** Instead of immediate hard deletion, move suspected dead code (like old UI rendering methods or raw SQLite strings) into a `_graveyard/` namespace. If a dynamic module import or edge-case execution path fails during testing, the code can be instantly restored.
- **Config & Dependency Audit:** Clean up `fleet.toml` fallback defaults and `requirements.txt` to remove packages (e.g., old bash utilities) that were only required by the pre-v0.30 architecture.
- **Regression Gate:** The prune is only merged to `main` when the codebase passes 100% of the `--fast` smoke tests, the GUI headless smoke test, and an 8-hour marathon soak test to ensure no obscure conditional logic was severed.

---

## 0.05.00 — Git & MLOps Autonomy (Skill Expansion)
**Goal:** Equip agents with native capabilities to manage repositories and generate training data.
- **`git_manager` & `github_interact`:** New skills allowing agents to safely stage, commit, branch, and interact with GitHub Issues/PRs using the OAuth integration.
- **`dataset_synthesize`:** Allow the fleet to generate and curate high-quality synthetic JSONL datasets (e.g., TinyStories style) to feed the overnight `autoresearch` Marathon ML loop.
- **`service_manager`:** Provide agents visibility into host OS services (systemd, schtasks) to verify or repair Auto-Boot mechanisms.

---

## 0.06.00 — Cryptographic & Security Self-Healing (planned)
**Goal:** Address the "Data at Rest" and "Key Rotation" critical gaps identified in architecture research.
- **`secret_rotate`:** Introduce an autonomous skill that responds to DLP alerts or time-based expiry by generating and applying new API keys seamlessly.
- **`db_encrypt`:** A maintenance skill that can safely migrate plaintext SQLite data into SQLCipher encrypted stores during offline or idle windows.
- **`db_migrate`:** A structured skill for agents to draft, test, and safely execute schema migrations (`ALTER TABLE`) without operator intervention.

---

## 0.07.00 — Security Hardening (Gemini 3rd Pass P0+P1)

**Goal:** Close the critical security gaps identified in architecture research Section 8.

- **Dashboard auth:** Flask bearer token middleware — all /api/* endpoints require `Authorization: Bearer <token>` header. Token stored in fleet.toml `[security] dashboard_token`.
- **Review enabled by default:** Change `[review] enabled = true` in fleet.toml — free safety improvement.
- **Docker sandbox execution:** Real container boundary for code_write, pen_test, skill_test, benchmark. `subprocess.run(["docker", "run", ...])` with volume mounts for input/output only.
- **Worker resource limits:** cgroups (Linux) / Job Objects (Windows) per worker process. Configurable in fleet.toml `[workers] memory_limit_mb, cpu_limit_percent`.
- **Cross-platform isolation:** Docker as strict prereq for native Linux/macOS deployment. Documented in OPERATIONS.md.

---

## 0.08.00 — Architecture Polish (Gemini 3rd Pass P2)

**Goal:** Resolve documentation gaps and architectural asymmetries.

- **SSE Pattern 6:** Formalize reactive streaming IPC in FRAMEWORK_BLUEPRINT. Deprecate legacy file-polling (`parse_status`, `STATUS.md` reads).
- **ML Bridge skill:** `ml_bridge.py` connecting autoresearch results.tsv → fleet.db usage/knowledge tables. Dashboard renders ML progress natively.
- **Async DAG manager:** Extract `_promote_waiting_tasks` + `_cascade_fail_dependents` into async queue to prevent SQLite WAL thundering herd on massive fan-in completions.
- **Move _db_init():** Extract launcher.py `_db_init()` schema creation to `data_access.py` — single source of truth for tools.db schema.
- **Rate limiting + CSRF:** Flask-Limiter on dashboard endpoints. CSRF tokens on state-changing POSTs.

---

## 0.09.00 — Audit & Observability (Gemini 3rd Pass P3)

**Goal:** Production-grade logging and transport security.

- **Centralized audit log:** JSON+HMAC structured event log aggregating supervisor, watchdog, DLP, cost tracking events into single tamper-evident trail.
- **Log rotation:** Python RotatingFileHandler (10MB per file, 5 backups) for all fleet logs.
- **TLS for dashboard:** Self-signed cert generation + HTTPS serving. Optional Let's Encrypt for remote access.

---

## 0.10.00 — Advanced Agent Flows (Skill Synergy)

**Goal:** Transition from isolated skill execution to complex, multi-agent automated pipelines, fully utilizing the 55+ skill inventory and specialized agent roles.

- **Proactive CRM Pipeline:** Link the `sales` and `onboarding` agents to the GUI modules via automated DAGs. The fleet autonomously triggers `lead_research`, followed by `account_review`, and stages outreach proposals in the CRM without operator prompting.
- **Knowledge Consolidation Flow:** Introduce `rag_compress` and `knowledge_prune` skills. Empower the `archivist` to autonomously detect topic bloat in `knowledge/`, summarize fragmented markdown files into master documents, and prune the originals to maintain RAG accuracy.
- **Swarm Consensus (Group Meetings):** Leverage the Layer 2 (`agent`) comms channel for pre-execution debates. For complex architectural tasks, force the `coder`, `security`, and `researcher` agents to reach a documented consensus in the chat before the `planner` dispatches the execution tasks.

---

## 0.15.00 — Model Manager & Hardware Profiles

**Goal:** Automated model inventory, installation, and hardware-aware profile switching.

- **model_manager.py skill:** Check installed vs needed, pull missing, detect hardware, recommend profiles
- **model_profiles.toml:** Predefined configs (dev_cpu, dev_gpu, dev_gpu_light, dev_cpu_light, minimal, production)
- **CLI:** `model-check`, `model-install`, `model-profile list|apply|recommend`
- **Startup validation:** hw_supervisor validates all configured models exist, warns + suggests pull commands

---

## 0.16.00 — Multi-Backend Model Support

**Goal:** Support local model providers beyond Ollama — llamafile, vLLM, LM Studio, any OpenAI-compatible server.

- **Backend abstraction in providers.py:** Unified interface for Ollama, llama.cpp, vLLM, LM Studio
- **OpenAI-compatible API routing:** All local backends expose `/v1/chat/completions` — single adapter
- **fleet.toml [models] expansion:** `backend` field (ollama/llamacpp/vllm/lmstudio/openai_compat), per-backend host config
- **Model registry:** `[models.registry]` maps logical names to backend-specific identifiers + download URLs
- **HuggingFace search:** `lead_client.py model-search "codellama"` — find GGUF models by name
- **Auto-backend detection:** `model-install codellama:13b` detects best available backend and pulls/downloads
- **llamafile single-binary support:** Download .llamafile → serve as self-contained binary, zero install
- **Model installer UI:** Launcher module or walkthrough step for browsing + installing models

---

## Parallel Track: Platform (Cross-Platform Support)

> These items run in parallel to version milestones. They don't bump version numbers — they are infrastructure improvements that land alongside regular releases.

### PT-1: Platform Abstraction [DONE]

Completed 2026-03-18.

- `fleet_bridge.py`: FleetBridge ABC, WslBridge (Windows→WSL), DirectBridge (Linux/macOS native)
- `create_bridge(FLEET_DIR)` at module level replaces wsl()/wsl_bg() functions
- wsl()/wsl_bg() are now thin wrappers around bridge.run()/bridge.run_bg()
- Platform-conditional CREATE_NO_WINDOW via `_NO_WINDOW` flag

### PT-2: Cross-Platform Build [DONE]

Completed 2026-03-18.

- `build.py` replaces `build.bat` — auto-detects --add-data separator (;/:)
- Skips pynvml hidden-import on macOS
- Platform-aware process termination (taskkill/pkill)
- CLI flags: --launcher, --updater, --setup for targeted builds

### PT-3: Platform Packaging [DONE]

Completed 2026-03-18.

- **Linux:** `package_linux.py` — AppImage build (PyInstaller + appimagetool), `.desktop` file generation + install
- **macOS:** `package_macos.py` — `.app` bundle via PyInstaller --windowed, DMG creation via hdiutil, code signing support
- **Installer abstraction:** `installer_cross.py` — platform-conditional install/uninstall (winreg on Windows, .desktop on Linux, /Applications on macOS), status check
- **Updater:** `.bat` trampoline replacement deferred (exec self-replacement on Linux/macOS documented)

### PT-4: Platform Testing [DONE]

Completed 2026-03-18.

- `.github/workflows/ci.yml` — GitHub Actions CI matrix (Win/Linux/macOS × Python 3.11/3.12)
- Smoke test per platform in `--fast` mode with in-memory DB
- Skill import verification across all 54 skills
- CLI command verification (status, detect-cli)
- Python syntax check across entire codebase
- Steam Deck validation: documented (CPU-only Ollama, Desktop Mode GUI)

---

## Parallel Track: Cost Intelligence (Token Usage & Optimization)

> Token-level cost tracking, delta comparison, and optimization feedback loop. Blueprint: `BigEd/qa_token_blueprint.md`.

### CT-1: Usage Capture [DONE]

Completed 2026-03-18.

- `usage` table in `fleet.db` (schema in blueprint Section 1)
- `db.log_usage()` + `db.get_usage_summary(period, group_by)` + `db.get_usage_delta()`
- `_call_claude()` extracts `resp.usage` and logs after every API call
- `call_complex()` signature extended: `skill_name`, `task_id`, `agent_name` passed through
- `PRICING` dict and `calculate_cost()` in `_models.py`
- Usage logging wrapped in try/except — never breaks skill execution
- Smoke test: 11/11 (1 new usage tracking test)

### CT-2: Cost Dashboard [DONE]

Completed 2026-03-18.

- `/api/usage` endpoint: daily/weekly/monthly aggregates by skill, model, agent
- `/api/usage/delta` endpoint: compare two time ranges with per-skill deltas
- `lead_client.py usage` CLI command showing cost breakdown table with cache savings
- Endpoint count: 17 → 19

### CT-3: Delta Comparison [DONE]

Completed 2026-03-18.

- `db.get_usage_delta()` — per-skill delta report between two date ranges
- `lead_client.py usage-delta` CLI — formatted table with direction arrows
- `/api/usage/regression` endpoint — auto-flags >20% token increase vs prior week
- Delta format: `{skill, previous_cost, current_cost, delta_pct, direction}`
- Soak test: delta comparison with inserted date-ranged data

### CT-4: Optimization Loop [DONE]

Completed 2026-03-18.

- `fleet.toml [budgets]` — per-skill daily USD budgets (warn-only, never blocks)
- `check_budget()` in `_models.py` — checks daily spend vs configured limit
- Budget warning in `call_complex()` — prints to stderr when exceeded
- Worker post-execution budget check — logs warning after task completes
- `lead_client.py budget` CLI — shows budget status table
- `/api/usage/budgets` endpoint — budget status with pct_used
- Soak test: budget exceeded detection

---

## Parallel Track: Comms (Layered Inter-Agent Communication)

> Triple-layer messaging: supervisor-to-supervisor, agent-to-agent, cross-layer broadcast, and supervisor-to-pool. Each layer has ephemeral messages (read-once inbox) and persistent notes (append-only scratchpad).

### CM-1: Channel Foundation [DONE]

Completed 2026-03-18.

- `channel` column on `messages` table + migration for existing DBs
- `notes` table (channel, from_agent, created_at, body_json) with index
- Channel constants: `CH_SUP`, `CH_AGENT`, `CH_FLEET`, `CH_POOL`
- Channel-aware `post_message`, `get_messages`, `broadcast_message`
- `post_note`, `get_notes`, `get_note_count`
- 3 smoke tests (routing isolation, note round-trip, backward compat)
- Smoke: 10/10, Soak: 23/23

### CM-2: Supervisor Layer (Layer 1) [DONE]

Completed 2026-03-18.

- hw_supervisor: registers as supervisor, posts sup notes on model transitions and thermal throttle events, reads sup inbox every 60s
- supervisor: registers as supervisor, reads sup inbox + notes every 30s, posts sup notes on training state changes and stale task recovery
- All hw_supervisor DB calls wrapped in try/except — thermal loop never blocks

### CM-3: Agent Layer (Layer 2) [DONE]

Completed 2026-03-18.

- worker.py: inbox filtered to `["fleet", "agent", "pool"]` — workers never see sup channel
- Migrated `code_discuss`, `discuss`, `fma_review` to `channel="agent"`
- Discussion `_load_discussion_so_far()` queries filter `AND channel IN ('agent', 'fleet')` for backward compat

### CM-4: CLI & Dashboard [DONE]

Completed 2026-03-18.

- `lead_client.py`: `--channel` flag on send/broadcast/inbox commands, new `notes` subcommand
- `dashboard.py`: `/api/comms` endpoint (per-channel message/note counts + recent activity), `data_stats` includes notes table

---

## Parallel Track: Diagnostics (Debug Report & Issue Resolution)

> Structured diagnostic pipeline from issue report to shipped fix.

### DT-1: Debug Report Infrastructure [DONE]

Completed 2026-03-18.

- `generate_debug_report()` — structured JSON: platform, hardware, fleet state, logs, error traceback
- `_log_ring` deque(maxlen=200) ring buffer on `_log_output()`
- Global exception handler wrapping `app.mainloop()` — auto-report on crash
- Sanitization: sk-*, AIza* keys redacted. Saved to `data/reports/debug_TIMESTAMP.json`

### DT-2: Issue Submission [DONE]

Completed 2026-03-18.

- "Report Issue" button in Config sidebar — generates debug report + opens reports directory
- File export: JSON to `data/reports/`

### DT-3: Resolution Tracking [DONE]

Completed 2026-03-18.

- `data/resolutions.jsonl` schema: report_id, fix_commit, regression_test, status
- Dashboard `/api/resolutions` endpoint (last 50 entries)
- Commit convention: `fix(component): description [report:uuid]`

### DT-4: Stability Analysis [DONE]

Completed 2026-03-18.

- `stability_report.py` skill: reads `data/resolutions.jsonl`, pattern detection (top components, platform distribution, severity breakdown, MTTR)
- Output: markdown report to `knowledge/reports/stability_report_YYYYMMDD.md`
- Release validation checklist: all P0/P1 resolved + regression tests pass before tagging
- Graceful handling of missing/empty resolutions data

---

## Parallel Track: Hardening (Gemini RAG Recommendations)

> Sourced from fleet Gemini analysis: `knowledge/reports/gemini_configuration_bug_resolutions.md`

### GR-1: Pre-Flight VRAM Eviction [DONE]

Completed 2026-03-18.

- `supervisor.py`: `_evict_gpu_models()` sends `keep_alive=0` to all loaded Ollama models before training starts
- `hw_supervisor.py`: `_evict_models_for_training()` mirror for hardware-level coordination
- Prevents CUDA OOM when PyTorch training competes with Ollama for 12GB VRAM

### GR-2: WSL2 Subnet Detection [DONE]

Completed 2026-03-18.

- `pen_test.py`: `_detect_wsl_nat()` checks for 172.x.x.x gateway indicating WSL2 NAT
- Warning injected into scan results when NAT detected, with `.wslconfig` fix instructions

### GR-3: Zombie Process Cleanup [DONE]

Completed 2026-03-18.

- `worker.py`: Process group (`os.setpgrp()`) on startup, `_cleanup_children()` on shutdown
- Signal handlers (SIGTERM/SIGINT) kill entire process group including child processes (Playwright, nmap)

### GR-4: Base64 Secret Detection [DONE]

Completed 2026-03-18.

- `_watchdog.py`: `_check_base64_secrets()` decodes base64 strings and checks against SECRET_PATTERNS
- Catches LLM-encoded API keys that bypass plain-text DLP scanning
- Integrated into existing task result + knowledge file scan flow
