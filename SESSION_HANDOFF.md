# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Skills | 96 standalone + 6 suites (was 132 files) | 2026-03-26 |
| Smoke Tests | 48/48 (Python) + 41 restructure/hardening | 2026-03-27 |
| Factorio Module Tests | 45/45 (9 test files) | 2026-03-27 |
| Ingest Sources | 18 (12 task + 5 RAG + 1 factorio-knowledge, ~2M+ rows) | 2026-03-27 |
| API Keys | 12 registered, 3 set | 2026-03-26 |
| Rust Tests | 116+ | 2026-03-25 |
| Dashboard Endpoints | 26 (Rust) + 254 (Python, +12 ingest) | 2026-03-26 |
| DB Tables | 9 (Rust schema) | 2026-03-25 |
| Branch | main | 2026-03-26 |
| Rust Crates | 6 (core, supervisor, server, bridge, gui, wasm) | 2026-03-25 |
| Rust Phase | All 6 phases complete + 18 audit fixes | 2026-03-25 |
| Helpers | 11 (_contract, _knowledge, _llm_parse, _dispatch, _report, _http, _models, _flywheel_rubric/grading/audit, _oss_core) | 2026-03-26 |
| Graph Views | 6 (fleet-overview, universe, data-flow, bottleneck-detector, knowledge-graph, training-pipeline) | 2026-03-26 |
| Graph Layouts | 4 (Radial, Radial Cluster, Cluster/fcose, Grid) + Fractal Brain | 2026-03-27 |
| Audit Reports | 4 (backend, frontend, cross-platform, integration) | 2026-03-27 |
| v1.0 Blockers | 20 critical issues identified | 2026-03-27 |

## Last Session

**Date:** 2026-03-28 (session 3)
**Session:** VS Code Claude Code — Factorio RCON integration, dashboard tab, dual-process viewer

### Factorio RCON — All 3 Issues Fixed
- **Remote interface:** Switched from `commands.add_command` to `remote.call()` — reliable RCON responses in Factorio 2.0
- **Achievement warning:** Auto-prime console on connect (send dummy `/c` first)
- **Headless playerless mode:** `get_agent_context()` uses surface+force directly without requiring a player
- **API changes fixed:** `helpers.table_to_json`, `force.add_research()`, `force.get_item_production_statistics(surface)`, `force.get_chunks` removed

### Dual-Process Viewer — Working
- **Standalone headless:** `F:\Factorio` with isolated `--config F:\Factorio\data-biged\config.ini` (separate lock file)
- **Steam client:** Connects as spectator via Multiplayer → `localhost:34197`
- Both run simultaneously — no license conflicts
- Successfully placed first entity in-game via RCON (stone furnace appeared in viewer)

### Dashboard Factorio Tab
- New "Factorio" nav item (conditionally shown when module enabled)
- Bridge status, game tick, cadence selector
- Launch Spectator button, training phase selector
- Game state display, resource list
- Proxy endpoints `/api/factorio/bridge-status` and `/api/factorio/bridge-state` (CORS fix)

### Module Manager Fixes
- Enable/disable toggle fixed: reads runtime state from `fleet.toml [launcher.tabs]` instead of manifest `default_enabled`
- Dot color + button text update correctly
- `Promise.all` per-fetch `.catch()` prevents spinner-stuck on API errors

### Build Fixes
- `build.py`: UTF-8 stdout on Windows, graceful pythonnet failure handling
- `launcher_tkinter.py`: Added `main()` entry point (was missing, caused exe crash)

### Architecture: Bridge Decoupled from Supervisor
- Reverted all `process_manager.py` changes — fleet supervisor is clean
- `setup_and_launch.py` manages both Factorio server + bridge (self-contained)
- Bridge auto-restarts if it crashes, Ctrl+C stops both

### Next Session Priority: Agent Loop
The bridge ticks and reads state, RCON executes commands, but **no LLM is driving decisions yet**. Need:
1. Bridge tick → dispatch `factorio_plan` task to fleet worker with game state + strategy guide
2. Worker LLM reasons → returns JSON action array
3. Bridge executes actions via RCON
4. Curriculum evaluation checks success criteria after each cycle
5. Auto-advance through 4 training phases

**Standalone headless path:** `F:\Factorio` with `--config F:\Factorio\data-biged\config.ini`
**RCON password:** stored in `fleet.toml [factorio] rcon_password`

### Previous Session

**Date:** 2026-03-28 (session 2)
**Session:** VS Code Claude Code — Dashboard nav restructure + model display fix

### Pipeline → Analytics Merge
- Removed Pipeline as a standalone nav tab — all its content now lives under Analytics
- **Analytics section order:** Task Flow → Agent Timeline → Token/Tasks/Cost/Perf charts → Neural Activity Map → Skills/Model charts → Knowledge Graph → Kanban (bottom)
- Removed Pipeline from sidebar nav, omnibox search, `loadSectionData` switch
- `loadAnalytics()` now calls all former pipeline loaders (lane graph, kanban, swimlane, neural graph)

### Knowledge Graph Scoping Fix
- `switchGraphLayout()` and related functions were defined inside `loadNeuralGraph()` (function-scoped) but called from HTML `onchange` (global scope) — moved `switchGraphLayout` to `window` scope
- `cytoscape.use(cytoscapeFcose)` was re-registered on every `loadNeuralGraph()` call — made idempotent with `window._graphFcoseRegistered` guard
- Graph layout name now persists via `window._graphLayoutName`

### Header Model Dropdowns — Show Running Models
- **Bug:** GPU/CPU dropdowns showed all 4 installed Ollama models; user couldn't tell which were actually loaded
- **Fix:** Added `/api/ollama/ps` endpoint in dashboard.py (proxies Ollama `/api/ps`). Frontend now uses VRAM ratio from `size_vram/size` to determine GPU vs CPU model assignment
- Dashboard restart needed to pick up new endpoint

### Next Priorities
1. Dashboard restart to activate `/api/ollama/ps` endpoint and verify model dropdowns
2. Knowledge graph loading — API returns 422 nodes fine, may need browser debugging if still failing
3. Factorio RCON tuning (carried over)
4. v1.0 audit blockers (20 critical issues)

## Previous Session

**Date:** 2026-03-28 (session 1)
**Session:** VS Code Claude Code — Dashboard timeline + queue pause fixes

### Agent Timeline Fix
- Anchored `maxTs = now` in `loadSwimlane()` so right edge is always "now"

### Queue Pause/Resume — Made Functional
- Replaced in-memory `_queue_paused` with `.queue_paused` flag file (cross-process)
- Workers check flag before claiming tasks
- Optimistic UI toggle with SSE sync

---

**Date:** 2026-03-27 (session 3)
**Session:** VS Code Claude Code — Factorio Sandbox Module (design + full implementation)

### Factorio Sandbox Module v0.1.0 — COMPLETE

Built a BigEd module that trains fleet agents to play Factorio autonomously through a 4-phase training curriculum.

**Spec:** `docs/superpowers/specs/2026-03-27-factorio-sandbox-design.md`
**Plan:** `docs/superpowers/plans/2026-03-27-factorio-sandbox.md`

**Architecture:** Fat module with bridge service — long-running bridge process talks to Factorio via RCON, maintains persistent WorldModel, exposes localhost API. Fleet skills wrap the bridge. Launcher module provides tab UI.

**What was built (25+ files, 45 tests):**
- `fleet/factorio/` — RCON client, state parser (GameState/GameMetrics/Entity), action translator, WorldModel with diff-based event detection, CadenceController (4 modes + adaptive boost/decay), curriculum engine with safe criteria parser, bridge API (Flask localhost), bridge main process, Lua installer, config
- `fleet/factorio/lua_mod/` — Factorio 2.0 Lua mod (state serializer + 9 command actions)
- `fleet/skills/factorio_{observe,plan,act,train}.py` — 4 skills following standard contract
- `fleet/idle_curricula/factorio_{01,02,03,04}_*.toml` — 4-phase curriculum (bootstrap → goals → KPIs → survival)
- `BigEd/launcher/modules/mod_factorio.py` — launcher tab (status, cadence slider, spectator button, curriculum progress)
- `fleet/factorio/knowledge/` — 31 reference docs (15 vanilla wiki + 7 Space Age wiki + 8 Lua API + 1 agent guide)
- `fleet/factorio/setup_and_launch.py` — one-click setup script

**Config additions:** fleet.toml `[factorio]` section (30+ keys), `[affinity] sandbox`, `[budgets]` for 4 skills, `[[ingest.sources]]` for knowledge dir

**Integration:** process_manager.py spawn/monitor/shutdown for bridge, providers.py skill complexity routing

### RCON Integration Status — NEEDS TUNING

The module is fully built and tested (45/45 Python tests, 48/48 smoke). Live testing against Factorio 2.0.76 + Space Age revealed:

**Working:**
- RCON auth handshake ✓
- Lua mod loads cleanly ✓
- Save creation ✓
- Server starts with `auto_pause=false` ✓

**Needs fixing next session:**
1. **`commands.add_command` RCON responses** — Factorio 2.0 doesn't return responses for mod-registered commands via RCON. Fix: switch to `/c remote.call()` pattern (remote interfaces) or inline `/c` Lua that calls mod functions
2. **Achievement warning** — first `/c` via RCON triggers "disable achievements?" warning. Need auto-accept or pre-command on save creation
3. **Headless player creation** — `game.get_player(1)` returns nil in headless with no connections. Need to create a character entity programmatically via `on_init`
4. **Factorio 2.0 API changes** — `game.table_to_json` → `helpers.table_to_json` (already fixed), `require("json")` removed (already fixed)

**Server settings needed:** `auto_pause: false` in server-settings.json (otherwise RCON commands don't execute with no players connected)

### Previous session (2026-03-27 session 2)

**Session:** VS Code Claude Code — knowledge graph overhaul + v1.0 audit (4 Opus pods)

### Knowledge Graph Overhaul — 7-Task Plan Executed
- **Backend:** Task aggregation (200→33 task groups), compound parent nodes (30 communities), edge deduplication (600→289 edges)
- **Frontend:** fCoSE layout replacing fractal-brain for organic clustering, compound node styles (:parent), zoom-based LOD with community centroids
- **Both templates:** dashboard.html + view_graph.html updated with compound support, parent validation (orphan refs crash Cytoscape)
- **Smoke test:** New graph universe test (46/46 pass)
- **Spec:** `docs/superpowers/specs/2026-03-27-knowledge-graph-overhaul-design.md`
- **Plan:** `docs/superpowers/plans/2026-03-27-knowledge-graph-overhaul.md`

### Live Activity Enrichment
- Backend: `/api/activity/live` + SSE broadcaster now JOIN usage table (model, duration, tokens, speed, cost, IQ, priority, trace_id)
- Frontend: configurable column pills, Settings → Display tab with localStorage-persisted toggles
- Default on: Model, Duration, IQ Score. All 8 columns toggleable.

### Dashboard UX
- Right-click = soft refresh (current section), Shift+right-click = hard reload
- Select All checkbox on Ingest page
- Settings → Advanced: "Link to a BigEd" federation UI (peer list, health probes, federation toggles, RAG host mode)
- Active Units SSE filter: disabled agents + 5min heartbeat
- Ko-fi removed from public repo README (pushed to upstream)

### v1.0 Audit (4 Opus Pods)
4 parallel audit reports in `docs/audit/2026-03-27-*-audit.md`:
- **Backend:** 6 critical (raw sqlite3, claim_task race, alert scoping, no pooling)
- **Frontend:** 6 critical (isDark() undefined, --destructive missing, model dropdown broken, rAF loop)
- **Cross-Platform:** 4 critical (Dr. Ders exits NullBackend, AMD VRAM, PyWebView Linux, model tier assumptions)
- **Integration:** 4 critical (no requirements.txt, sparse backups, no e2e tests, no load tests)
- **Total:** 20 critical, 26 high, 28 medium, 17 low
- **Machine readiness:** 3080Ti=READY, 1070=READY with config, Steam Deck=NOT READY (3 critical)

### Cleanup
- Code drafts: 34→13 files (21 empty deleted)
- Fonts: 5 hardcoded instances → theme constants
- .gitignore: +7 patterns (tmp*.json, biged-rs runtime, demo.py, .superpowers/, screenshots)
- Coder summarize tasks removed from idle curriculum

### Previous Session (2026-03-27 session 1)

### Supervisor Restructure — COMPLETE

Decomposed `fleet/supervisor.py` (1890 lines) into 5 focused modules + thin orchestrator using team-orchestrator (4 parallel pods in worktrees):

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `process_manager.py` | 526 | All subprocess lifecycle: Ollama, workers, dashboard, Dr. Ders, Discord, OpenClaw |
| `health_monitor.py` | 854 | Health sweeps, memory watchdog, circuit breakers, diagnostics, stale task recovery |
| `scheduler.py` | 726 | Dynamic scaling, auto-triggers, training detection, cost anomaly, capacity bonus |
| `federation_manager.py` | 163 | Peer heartbeat, overflow routing, mTLS, discovery |
| `boot_sequence.py` | 225 | 16-step ordered startup sequence |
| `supervisor.py` | 201 | Thin orchestrator: main loop, signals, status writes (was 1890) |
| `self_healing.py` | 36 | Re-export shim → health_monitor.py |
| `diagnostics.py` | 18 | Re-export shim → health_monitor.py |

**Tests:** 25/25 restructure tests + 45/45 smoke tests = zero regressions
**Commits:** b349dee → 370517b → bc8938a → 5044592 (4 sequential, no conflicts)
**Spec:** `docs/superpowers/specs/2026-03-26-supervisor-restructure-design.md`
**Plan:** `docs/superpowers/plans/2026-03-26-supervisor-restructure.md`

### Workflow Hardening — 13 Fixes Complete

Deep audit of all task workflows found 15+ issues (death spirals, memory leaks, chokepoints). Fixed the 13 critical/high items via 4-pod team:

| Pod | Fixes | Key Changes |
|-----|-------|-------------|
| pod-health | 3 | Circuit breaker memory cap (1000/skill), deque recovery log, tick stagger |
| pod-scheduler | 6 | Evolution dedup, research overlap guard, atomic offset, tick stagger, VRAM eviction, staleness cache |
| pod-dashboard | 3 | SSE client leak reaper, federation peer TTL, rate limiter eviction |
| pod-ingest | 2 | Cache orphan cleanup, dispatch failure tracking + auto-remove |

**Tests:** 16 new hardening tests (41 total) + 45/45 smoke = zero regressions
**Commits:** 2b451b0 → b2f5ecb → eb60fe9 → 5bbae14
**Spec:** `docs/superpowers/specs/2026-03-27-workflow-hardening-design.md`
**Plan:** `docs/superpowers/plans/2026-03-27-workflow-hardening.md`

### Additional Fixes (inline, not team-dispatched)
- OOM check: dynamic VRAM estimation (no hardcoded fallback — infers from model name, Ollama API, or 50% GPU)
- Summarize skill: `content` key mismatch fixed, 575 tasks requeued
- Agent count: filter to 5min heartbeat (was showing 17 ghost agents)
- Activity feed: detail column (skill_name, source, error reason)
- Dashboard: adaptive polling (2s active / 15s minimized / 60s hidden), independent bar scaling, "active"→"running" label
- Knowledge graph: fcose layout, compact brain, hover-explode, click-select, SSE-driven pulses, all 59 disconnected nodes wired, fractal-brain custom layout
- PyWebView: Qt backend fix for Python 3.14 (PYWEBVIEW_GUI=qt), dual model dropdowns (GPU/CPU), boot overlay hide fix
- LHM: auto-launch headless with elevation, dependency checks for Windows/Linux/macOS

### Previous Session (2026-03-26 late evening)

### Ingestion Hub — Complete Phase 1

Full dataset ingestion system built end-to-end:

**Backend:**
- `ingest_manager.py` (~420 lines): HF Dataset Viewer API client, JSONL cache with 2GB LRU eviction, staging CRUD (DB-backed), dispatch to fleet tasks or RAG
- `ingest_blueprint.py` (228 lines): 12 REST endpoints under `/api/ingest/*` (sources CRUD, schema, rows, staging, dispatch, cache stats, file upload)
- `db.py`: `ingest_sources` + `ingest_staging` tables added to init_db()
- `fleet.toml`: `[ingest]` config section + 6 pre-configured HuggingFace datasets

**Pre-configured HF datasets:**
| Dataset | Rows | Agent | Skill |
|---------|------|-------|-------|
| ronantakizawa/github-codereview | 355K | coder | code_review |
| fasterinnerlooper/codereviewer | 317K | coder | code_review |
| ccdv/arxiv-summarization | 215K | researcher | summarize |
| armanc/scientific_papers | 300K+ | researcher | summarize |
| tranquangtien15092005/code-vulnerable-10000 | 10K+ | security | security_audit |
| happylife365/code-quality-large | 18K | coder | code_quality |

**Dashboard UI:** New "Ingest" nav item + page with source pills (color-coded by agent role), inline expand with config/row preview, staging area with color dots, cache usage bar. All JS uses safe DOM methods (createElement/textContent, no innerHTML).

**Specs:** `docs/superpowers/specs/2026-03-26-ingestion-hub-design.md` + `2026-03-26-web-ingest-phase2-design.md`

**IMPORTANT:** Dashboard needs restart to pick up new Python modules (Flask caches). Source pills won't render until restart.

### Dashboard Debugging (Chrome DevTools MCP)

- Fleet page: was empty ("No Active Agents") — `/api/status` had 60s heartbeat filter. Fixed to show all 17 agents with IDLE/OFFLINE/DISABLED status, dimmed inactive rows
- CPU temp: installed LibreHardwareMonitor via winget, new LHM/REST API strategy in cpu_temp.py (port 8085, dynamic probe). Reads AMD Ryzen 7 5800X at 45°C
- System Load: was always 0% — psutil.cpu_percent needs priming. Added 2s background sampler thread
- CPU temp "N/A" display instead of misleading "0°C" when sensor unavailable
- timeAgo() UTC timezone fix (DB stores UTC, JS was parsing as local)
- Status badges: OFFLINE=blue, DISABLED=grey (was both red)

### Model Performance Fixes

- `intent.py`: conductor model (qwen3:4b) now logs usage via async_log_usage — appears in Model Performance panel
- `data_access.py`: model_performance() merges Ollama /api/ps loaded models even without recent usage data

### Firecrawl Integration

- `.mcp.json`: firecrawl-mcp server config (gitignored, needs FIRECRAWL_API_KEY env var)
- `keys_registry.toml`: FIRECRAWL_API_KEY entry added (12 keys total)
- User needs to add key: `echo 'export FIRECRAWL_API_KEY=...' >> ~/.secrets`

### Web Ingestion Phase 2 Spec

Three-stage quality gate before crawling:
1. Free probe (HEAD + robots.txt, 0 credits, <1s)
2. Single-page scrape (1 Firecrawl credit, quality signals)
3. Local model relevance scoring (qwen3:4b, 0 cost)
Only proceeds to full crawl if score >= 6/10 and user approves.

### Previous Session (2026-03-26 evening)

### Fleet Training Audit & Health Fixes

**Agent performance review** — full DB audit of 30K+ tasks, 17 agents:
- Top performers: security (93.3%), analyst (93.2%), coder_3 (90.9%), archivist (88.1%)
- coder_1 death spiral: 20,509 failures (4.2% success) — skill_draft retry loop

**Death spiral fix** (3 root causes):
1. `self_healing.py` — auto-retried `skill_draft` failures every 60s creating flood. Added `_NO_RETRY_TYPES` skip set for lifecycle skills
2. `skill_learn.py` — proposed `skill_draft_fix` which created more skill_draft tasks. Added `LIFECYCLE_SKIP` set blocking proposals for lifecycle skills
3. `supervisor.py:1530` — evolution_coordinator dispatched with `evolve_bottom_10` (action removed in suite restructure). Fixed to `evolve`

**Task queue cleanup:**
- Cancelled 2,030 stale tasks (2,004 WAITING + 26 PENDING from March 22)
- Cleared quarantine for 5 agents (coder_1, coder_2, archivist, planner, researcher)
- Dispatched 16 fresh training tasks across all agent types

**Coder_1 rehab:**
- Cleared quarantine, assigned simple tasks (code_review, code_quality, summarize)
- Completed 4 tasks successfully — code_quality found real findings in `_flywheel_core.py`

### RAM Scaling Upgrade

- `fleet.toml`: new `ram_ceiling_pct = 95` — scale-up blocked when system RAM exceeds this
- `system_info.py`: raised all tier caps (32GB: 10→14, 64GB+: 16→28 workers)
- `supervisor.py`: `_should_scale_up()` now checks RAM via psutil before scaling, reads `max_workers` from config instead of hardcoded 16
- This machine (32GB): now allows 14 workers (was 10), with 55% RAM used

### Launcher Close-to-Tray Bug Fix

- **Root cause:** `_get_close_behavior()` defaulted to `"tray"`, so clicking X always silently minimized to tray — close dialog never shown
- **Fix:** default changed to `"ask"` — dialog shows with 4 options: Stop & Exit, Keep Running, Minimize to Tray, Cancel
- User can check "Remember my choice" to auto-minimize in future
- Countdown close handler now supports all 3 remembered actions (stop/keep/tray)

### Previous Session (2026-03-26 daytime)

### API Governance System — Complete

Full API cost control system: gate, session budgets, provider management, graph visibility.

**Spec:** `docs/superpowers/specs/2026-03-25-api-governance-design.md`
**Plan:** `docs/superpowers/plans/2026-03-25-api-governance.md`

**Core Gate (`fleet/api_gate.py` — new):**
- Thread-safe GateState with session budgets, provider whitelisting, TTL expiry, drain modes
- Ring buffer (deque maxlen=200) for real-time API call visualization
- Safe by default: gate OFF, $0 budget, local-only fallback
- Precedence: air_gap > offline_mode > gate (gate cannot override hard overrides)

**Provider Integration:**
- `providers.py`: gate check + record_call in all `_call_claude()`, `_call_gemini()`, `_call_minimax()`
- `_models.py`: `call_complex(purpose=)` with gate fallback to local
- Configurable per-provider fallback chains in `fleet.toml [api_gate]`
- Old hardcoded `FALLBACK_CHAIN = ["claude", "gemini", "local"]` replaced

**Review & Bypass Fixes:**
- `_review.py`: refactored to use `call_complex(purpose="review")`, fail-hold instead of fail-open
- `marketing.py`: direct `anthropic.Anthropic()` bypass → routes through `call_complex()`
- Smoke test `test_no_direct_api_imports` catches future violations

**Config Defaults (fleet.toml):**
- `[api_gate]` section added (master switch, per-provider controls)
- `[review] provider` changed: `"api"` → `"local"` (no surprise API calls)
- `[budgets] enforcement` changed: `"warn"` → `"block"` (hard limits)
- `complex_provider` and `api_keys_required` deprecated (superseded by gate)

**Controls:**
- CLI: `lead_client.py api {enable,disable,status,drain-mode}`
- Dashboard: 5 REST endpoints (`/api/gate/{status,enable,disable,drain-mode,ring}`)
- Dashboard UI: API Gate card in Settings with budget slider, provider checkboxes, TTL, kill switch
- SSE: gate events (enable, disable, budget_warning, budget_hit, fallback) push to all browsers

### Universe Graph — Fixed & Animated

**Root causes fixed:**
- Heartbeat filter (120s/300s) → `IS NOT NULL` — all registered agents appear
- `fleet-overview.json`: 1 source → 6 sources (supervisor, rag, reinforcement, knowledge, autoresearch, universe)
- Node deduplication across sources (was 6+ duplicates)

**15 node type colors** (was 2 — green/purple by source):
- Agent=#10b981, Skill=#8b5cf6, Task=#f59e0b, Model=#ec4899, Folder=#06b6d4, Message=#6366f1, Config=#64748b, API Call=#ef4444, Hub=#3b82f6, Index=#14b8a6, Trainer=#d946ef, Evaluator=#a855f7, Scorer=#f97316, Supervisor=#059669, Chunk=#0ea5e9

**4-tier progressive zoom:**
- Overview (<0.3x): no labels, 10px dots
- Far (0.3-0.6x): agent/hub labels only, 8px font
- Mid (0.6-1.2x): all labels 9px, 24px nodes
- Close (>1.2x): full labels 12px, 36px nodes
- Task nodes stay unlabeled until close zoom (200+ task IDs were overwhelming)

**Layouts:**
- Radial: concentric by degree (hub-spoke)
- Radial Cluster: concentric by type (agents outer → config inner)
- Cluster: fcose (10K+ node capable, CSP-compatible via cdnjs)
- Grid: sorted by type into columns
- Tree layout removed (breadthfirst doesn't work for cyclic graphs)

**Animations:**
- AnimationManager particles flow along edges (flow/pulse/fade)
- All edge types mapped: assigned, runs, writes, reads, api_call, routes_to, communicates, uses_model
- Active at all zoom levels (was detail-only)
- Initial start bug fixed (_currentZoomLevel guard was skipping first call)

**Performance scaling (2000+ nodes):**
- fcose layout engine (O(n log n) vs cose's O(n²))
- Performance tiers: >500 nodes reduces detail, >1500 draft mode
- textureOnViewport for GPU-accelerated panning
- Batch element addition

### Build Files Updated

- Dockerfile: multi-stage Rust build (biged-bridge PyO3 .so)
- .dockerignore: added biged-rs/target/
- release.yml: Rust toolchain + cargo check verification
- Helm chart: appVersion 0.9.0, rust.enabled toggle
- .vscode/tasks.json: 5 Rust tasks (check, test, clippy, build, run)
- setup.sh/setup.ps1: Rust toolchain detection
- biged-bridge Cargo.toml: [lib] crate-type = ["cdylib", "rlib"]

### Dashboard Fixes (30+ commits)

- `/api/usage/budgets` 500 error: was iterating non-numeric config keys
- `/api/tasks/recent` endpoint: added (swimlane was 404)
- `/api/skills/available`: now returns SUITE, TAGS, COMPLEXITY metadata
- Neural Graph: fixed data parsing (d.sources[].nodes not d.nodes)
- Skill picker: groups by suite → tag → first letter
- Kanban: 5 columns (added DONE, FAILED, WAITING_HUMAN)
- Queue status: error feedback instead of silent swallow
- Views sidebar: URL `/api/views/config` → `/api/views/configs`, data key fix
- All 6 view configs updated with full sources + live animation rules
- Error overlay race condition: ViewEngine + page-level both showing errors
- fcose CDN: unpkg.com → cdnjs.cloudflare.com (CSP compatible)

### Launcher Fixes

- Neural Activity → Fleet Activity panel (DB-first, no dashboard dependency)
- Agent panel empty after boot: DB fallback when STATUS.md not written yet
- `_agent_cards` crash: pre-init in __init__ before tab builds
- Console window: pythonw.exe relaunch (no visible terminal)
- Supervisor boot sub-steps: tails supervisor.log for progress detail
- Intelligence tab: dynamic skill count, API Gate info, local-first routing text
- Icon mismatch: "Ingestion" → "Files" in _ICONS dict
- Model perf refresh wrapped in try/except
- Walkthrough: 74 → 130+ skills

### Infrastructure

- `fleet/dependency_check.py`: added cpu-temp check (14 checks total)
- `fleet/api_gate.py`: new module (270 lines)
- Smoke tests: 38 → 42 (api_gate, no_direct_api_imports)

### Previous Session (2026-03-25 → 2026-03-26)
Skill Plugin Restructure (Phases A-D): 132→96 standalone + 6 suites, 27 double-serialization fixes, 8 broken skills fixed, 16 swimlane diagrams

### Previous Session (2026-03-24 → 2026-03-25)
Complete Rust rewrite (Phases 0-6), 6-agent audit, 18 fixes, v0.9.0 benchmarks

## Next Priorities

- [x] **Restart dashboard** — verified, Ingest page + API Keys panel both working
- [x] **Dashboard API Keys panel** — 12 keys, inline entry, tier badges, signup hints
- [x] **Firecrawl key added** — fixed UTF-16 encoding issue in ~/.secrets
- [x] **Alexandria Library** — 17 HF sources configured (2M+ rows), marathon curricula updated
- [x] **FIX: Worker death loop** — ROOT CAUSE: 1024MB Job Object limit + health sweep counting synthetic failures. Fixed: 2048MB limit, synthetic_prefix excluded from error rate + quarantine queries.
- [x] **FIX: Dr. Ders duplicates** — PID file system (fleet/pid_manager.py). Boot checks is_running() before spawning.
- [x] **FIX: coder_1 quarantine** — diagnostics.py failure streak query now excludes synthetic_prefix
- [x] **Implement `ingest_batch` action** — wired in research_loop.py, tested with camel-chemistry (tasks) + cosmopedia (RAG)
- [x] **Test end-to-end ingest** — verified: 7 tasks dispatched, 5 RAG entries created, offsets tracked
- [x] **Live Activity feed** — SSE real-time, radial graph toggle, omnibox dropdown
- [x] **Performance** — Ollama NUM_PARALLEL=4, CPU affinity, worker CPU uncapped, startup parallelized
- [x] **Log rotation** — fresh logs each boot, keep last 10 sessions
- [x] **Supervisor restructure** — 1890→201 lines, 5 modules extracted, 25 tests, 45/45 smoke green
- [x] **Workflow hardening** — 13 fixes across 4 subsystems, 16 new tests, zero regressions
- [x] **Summarize skill fix** — content key mismatch, 575 tasks requeued
- [x] **Agent count fix** — filter to 5min heartbeat (was showing 17 ghosts)
- [x] **UI: merged fleet activity into agents panel** — single panel, boot-aware
- [x] **UI: adaptive polling** — 2s active, 15s/60s hidden, SSE paused when no clients
- [x] **UI: pipeline bar scaling** — agents/models scale independently
- [x] **Knowledge graph overhaul** — fcose layout, compact brain, hover-explode, click-select, SSE-driven pulses, all nodes wired
- [x] **PyWebView launcher migration** — 6-pod team: native window, boot overlay, header controls, settings 6-tab, fractal brain graph, integration. Qt backend fix for Python 3.14.
- [x] **Knowledge graph overhaul v2** — task aggregation, compound parents, fCoSE, edge dedup, LOD centroids (7-task plan executed)
- [x] **Live Activity enrichment** — model, duration, IQ, tokens, speed, cost, priority, trace_id + Settings → Display toggles
- [x] **Link to BigEd UI** — Settings → Advanced: peer list, health probes, federation/discovery/routing/RAG host toggles
- [x] **Code drafts cleanup** — 34→13 files (21 empty deleted)
- [x] **Fonts cleanup** — 5 hardcoded → theme constants
- [x] **v1.0 Audit** — 4 Opus pods: 20 critical, 26 high, 28 medium, 17 low. Reports in docs/audit/
- [ ] **Factorio RCON tuning** — fix mod command responses (switch to remote.call pattern), achievement auto-accept, headless player creation. See SESSION_HANDOFF.md "RCON Integration Status"
- [ ] **FIX 20 CRITICAL AUDIT ISSUES** — see docs/audit/2026-03-27-*-audit.md. Top: raw sqlite3, claim_task race, isDark(), requirements.txt, Dr. Ders NullBackend
- [ ] **Steam Deck readiness** — Dr. Ders NullBackend exit, AMD VRAM detection, PyWebView Qt backend for Linux
- [ ] **GTX 1070 config** — model tier adjustment (qwen3:8b fills 86% of 8GB VRAM)
- [ ] **3-machine federation test** — RTX 3080 Ti + GTX 1070 + Steam Deck OLED
- [ ] **Task Command Center** — brainstorm saved: docs/superpowers/specs/2026-03-27-task-command-center-brainstorm.md
- [ ] **Hardware profiles** — replace RTX 3080 Ti hardcoded values with auto-detected GPU profiles (cpu_only→datacenter). Self-tuning via autoresearch. Spec: `memory/project_hardware_profiles.md`
- [ ] **Model backend abstraction** — LocalModelManager ABC to decouple from Ollama. Support vLLM, llama.cpp, LM Studio. Spec: `memory/project_model_backend_abstraction.md`
- [ ] **Micro Lab** — brainstorm GPU simulation sharing (OpenMM/CFD alongside qwen3:8b)
- [ ] **Push to upstream/public** — haven't pushed in ~1 week, need testing pass first
- [ ] **Qwen 3.5 upgrade** — watch for Ollama GGUF availability, then config swap in fleet.toml
- [ ] **Factorio multi-character (Phase 5)** — planned for after single-agent works. See spec Future Work section
- [ ] **Web Ingestion Phase 2** — implement spec (3-stage quality gate + Firecrawl crawl)
- [ ] **Contextual "missing key" toasts** — when API returns `missing_key` error, show toast with entry link
- [ ] Root-cause Ollama 404 during skill_draft — may be model unload timing
- [ ] Resizable dashboard panels (Split.js) — ref: `memory/reference_panel_layout.md`
- [ ] Migrate skills to use new helpers (_knowledge, _report, _llm_parse)
- [x] Hardcoded fonts in launcher.py: 5 instances → theme constants (was 46 estimate, most already done)
- [ ] Run skills through PyO3 bridge with new suite routing
- [ ] Fix WASM compilation (cfg-gate tokio/rusqlite/reqwest behind desktop feature)
- [ ] WebSocket + MessagePack transport
- [ ] Auth middleware for server endpoints
- [ ] 24h stability test (supervisor + server running continuously)
- [ ] v1.0.0 graduation after bug testing

## Open Questions / Decisions Needed

- Suite routing: monitor for regressions before removing deprecated files — kill-switch is `suite_routing_enabled = false` in fleet.toml
- API Gate: tested in-memory only — need real API key session testing to verify budget enforcement end-to-end
- WASM: worth pursuing now or defer until native GUI is proven?
- Theme colors: spec says different values than implementation — which is canonical?
- Graph animation: particles render on canvas overlay — may need WebGL for >2000 animated edges
- Ollama 404 mystery: model is loaded, direct curl works, but workers get 404 intermittently — possibly model unload/swap timing

## Known Issues

- mingw toolchain needs dlltool on PATH for bench/release builds (WinLibs mingw64/bin)
- shlwapi.lib generated manually for mingw (eframe dep) — fragile, breaks on toolchain update
- WASM target fails (tokio/rusqlite not WASM-compatible without feature gates)
- Timeout doesn't cancel Python execution (GIL held, resource leak on hung skills)
- TOML write-back loses comments
- 3 temp files in fleet/ (tmp*.json) — safe to delete
- Gemini API still hitting 404s (free tier endpoint mismatch) — gate blocks this now but root cause unresolved
- ~20 skills return str not dict — fixed in restructure but verify with live testing
- Ollama intermittent 404: direct curl returns 200 but workers get 404 on `/api/generate` — may be model swap timing
- 47 code drafts in `knowledge/code_drafts/` — most are 0-byte (empty LLM output from failed generations), only 2 have content

## Doc Freshness

Run `python fleet/skills/doc_freshness.py` standalone or dispatch via fleet to audit all docs for stale values.

---

## Session Protocol

**On session start:**
1. Read this file for context
2. Read CLAUDE.md for conventions
3. Check git log for any commits since last session

**On session end:**
1. Update "Current State" table with any changed metrics
2. Update "Last Session" with what you did
3. Move completed items from "Next Priorities" and add new ones
4. Note any open questions or issues discovered
5. Commit this file with your other changes

**Between sessions:**
- Run `doc_freshness` skill periodically to catch stale docs
- Keep GEMINI_DOC_CLEANUP.md as reference for known debt
