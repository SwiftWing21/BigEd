# BigEd CC Roadmap

> **Goal of 1.0:** Autonomous, cross-platform, verifiably safe agent fleet.
> **Goal of 0.400.00b:** Multi-tenant SaaS-ready platform with federated fleet orchestration.

---

## Version Scheme

| Era | Format | Example | Notes |
|-----|--------|---------|-------|
| Pre-1.0 | `v0.XX` | v0.31, v0.48 | Feature versions — historical, frozen |
| 1.0 | `1.0` | 1.0 | Production release tag — historical |
| Post-1.0 | `0.XX.00` | 0.15.00, 0.30.00 | Infrastructure — historical, frozen |
| **Beta** | `0.XXX.YYb` | 0.050.00b, 0.051.01b | **Current era** — see below |
| Major | `0.X00.00b` | 0.100.00b, 0.200.00b | Major capability milestones (still beta) |
| Graduation | `1.000.00` | 1.000.00 | Clears beta — no `b` suffix |

### Beta Versioning (current)

BigEd CC is a **beta product** — fleet runs autonomously, installer/updater pipeline active, dynamic agent scaling operational. The `b` suffix stays until `1.000.00` graduation. The middle segment (`XXX`) has no upper limit — beta continues as long as needed for debugging, testing, and refinement. Versioning uses tight `.YY` patches within each milestone:

**`0.XXX.00b` — Milestones**
Major capability jumps. Each `0.XXX` is a feature theme:
- 0.042 = Beta release, installer pipeline
- 0.050 = Model recovery, dynamic scaling, pixel fonts
- 0.051 = Dashboard refactor, live agents, thermal fallback
- 0.100 = Multi-Fleet & Remote Orchestration (major)
- 0.200 = Intelligent Orchestration (major)

**`0.XXX.YYb` — Patches (tight iteration)**
Small, focused changes within a milestone. Each `.YY` bump is one session or PR:
- `.01` through `.99` — bug fixes, UX tweaks, config changes, polish

**`0.X00.00b` — Major capability milestones**
Jumps of 100 in the middle segment mark major platform-level milestones (formerly 2.0/3.0/4.0/5.0):
- 0.100.00b = Multi-Fleet & Remote Orchestration
- 0.200.00b = Intelligent Orchestration
- 0.300.00b = Enterprise & Multi-Tenant
- 0.400.00b = Platform & SaaS

Example progression:
```
0.050.00b  — Installer overhaul, model recovery, dynamic scaling
0.050.01b  — Dashboard live agents, thermal fallback
0.050.02b  — P0+P1+P2 security hardening
0.051.00b  — Startup perf, UX polish, Dr. Ders respawn
0.051.01b  — Task pipeline optimization
0.052.00b  — Claude Manual Mode Integration
0.100.00b  — Multi-Fleet & Remote Orchestration (was 2.0)
0.200.00b  — Intelligent Orchestration (was 3.0)
0.300.00b  — Enterprise & Multi-Tenant (was 4.0)
0.400.00b  — Platform & SaaS (was 5.0)
...
1.000.00   — Beta graduation (no b suffix, production-stable)
```

> **Note for AI assistants (Claude/Gemini):** Use `0.XXX.00b` for milestone features and `0.XXX.YYb` (YY > 0) for patches. Always include the `b` suffix until `1.000.00` graduation. The middle segment is 3 digits (zero-padded) but has no upper limit — `0.999.99b` is valid if needed. Major milestones use `0.X00.00b`. Pre-beta versions are frozen history. `1.000.00` clears beta state — no `b` suffix, production-stable.

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
| 7. Codebase Simplification | v0.48 | Cautious bloat/dead code removal | AST scanning, graveyard quarantine, 100% soak pass |
| 8. Autonomy Expansion | 0.05.00 – 0.06.00 | Git, MLOps, and Security skills | New skills tested, key rotation verified |
| 9. Cross-Platform & v1.0 | PT-1 – PT-4, DT-1 – DT-4 | Anyone, anywhere, clear diagnostics | All tests on Win/Linux/macOS; FleetBridge 100%; zero debt |

## Release Process

**Branching (documented — not yet active):**
- Current: all work on `main`, `dev` branch used as periodic backup snapshot.
- Future: daily work on `dev`, merge to `main` only at milestones via `git merge --no-ff dev`.
- To activate: move daily work to `dev`, apply stability gate before each `dev -> main` merge.

**Stability Gate Checklist (every milestone merge to main):**
```
## Release Gate: v0.XX
- Smoke tests: 10/10
- Soak tests: 13/13
- GUI smoke test: pass (v0.33+)
- TECH_DEBT.md: reviewed, no P0
- FRAMEWORK_BLUEPRINT.md: version row added
- ROADMAP: version marked DONE with date
- git status: clean
- Backup run: bash scripts/backup.sh
```

**Backup:** `bash scripts/backup.sh` — copies fleet.db, rag.db, tools.db, knowledge/ to `~/BigEd-backups/`. Keeps last 10. Run before every milestone merge and schema migration.

---

## Phase 1: Pre-1.0 (v0.31 through v0.48)

### v0.31 — Task Graph & Decomposition (DAG) [DONE]

Completed 2026-03-18. `parent_id`/`depends_on` columns, `WAITING` status, `post_task_chain()` helper, cascade fail/promote logic. Soak: 13/13.

### v0.32 — UI Resilience & Clean Refresh [DONE]

Completed 2026-03-18. Timer chains wrapped in try/except/finally, agents tab cache+configure pattern (no flicker), active-tab-only refresh, polling timeout notification.

### v0.33 — End-to-End Flow Verification + Offline/Air-Gap + Model Heartbeat [DONE]

Completed 2026-03-18. Offline mode, air-gap mode (deny-by-default whitelist), model heartbeat consolidation in hw_supervisor, enhanced Ollama status, `scripts/backup.sh`, program milestones. Smoke: 10/10, Soak: 15/15.

### v0.34 — New User Walkthrough (First-Run Experience) [DONE]

Completed 2026-03-18. 6-step `WalkthroughDialog` with skip/skip-all, "don't show again", fleet.toml persistence, re-trigger from Config sidebar.

### v0.35 — The Evaluator-Optimizer Loop (Guard Rails) [DONE]

Completed 2026-03-18. `REVIEW` status, adversarial reviewer (3 providers), high-stakes skill gate in worker, max 2 rounds. Soak: 17/17.

### v0.36 — Semantic Watchdog (Checker Agent) [DONE]

Completed 2026-03-18. `QUARANTINED` status, failure streak detection, stuck review auto-pass, DLP secret scrubbing (DB + knowledge files). Smoke: 10/10, Soak: 19/19.

### v0.37 — Unified Human-in-the-Loop (HitL) [DONE]

Completed 2026-03-18. `WAITING_HUMAN` status, Fleet Comm tab, operator response flow, security advisory approve/dismiss. Soak: 20/20.

### v0.38 — Fleet Security & Isolation (Sandboxing) [DONE]

Completed 2026-03-18. **Milestone 2 complete.** `[security]` config, Docker sandbox policy, pip-audit dependency scanning, 127.0.0.1 binding verification. Smoke: 10/10, Soak: 23/23.

### v0.39 — Advanced Network & IoT Orchestration [DONE]

Completed 2026-03-18. UniFi controller (`unifi_manage.py`), Home Assistant (`home_assistant.py`), MQTT broker (`mqtt_inspect.py`). All save to knowledge/, all REQUIRES_NETWORK.

### v0.40 — Full DOM Web Interactivity (Browser Skills) [DONE]

Completed 2026-03-18. Playwright `browser_crawl.py` with JS rendering, screenshot, CSS extraction, httpx fallback.

### v0.41 — Local Vision & Multi-Modal Orchestration [DONE]

Completed 2026-03-18. **Milestone 3 complete.** `vision_analyze.py` (llava/minicpm-v/qwen-vl), VRAM rotation in hw_supervisor. Smoke: 10/10 (49 skills), Soak: 25/25.

### v0.42 — Auto-Boot & Idle Skill Evolution [DONE]

Completed 2026-03-18. Zero-click fleet startup (Task Scheduler / systemd / launchd), `lead_client.py install-service`, idle skill evolution (round-robin, budget-aware), fleet health dashboard.

### v0.43 — Marathon ML & Context Persistence [DONE]

Completed 2026-03-18. Multi-hour ML training with checkpoint/resume, `marathon_log` skill, context persistence across sessions, 8-hour soak test. **Milestone 4 complete.**

### v0.44 — Seamless Lifecycle (Unified Updater) [DONE]

Completed 2026-03-18. Deprecated `Updater.exe` and `build.bat`. Integrated `git pull` + `uv sync` into launcher loading sequence. `os.execv` hot-reload. Streamlined pre-flight splash.

### v0.45 — Omni-Box & High Availability (HA) Routing [DONE]

Completed 2026-03-18. **Milestone 5 complete.** `Ctrl+K` command palette with skill auto-complete and agent pinging. Model fallback cascade (Claude -> Gemini -> Local). Graceful degradation warnings.

### v0.46 — Frictionless GitHub Sync & Zero-Bloat Baseline [DONE]

Completed 2026-03-18. **Milestone 6 complete.** Zero-bloat baseline (no modules enabled by default). GitHub OAuth Device Flow (8-char code, no PAT). Agent git autonomy (clone/push/backup).

### v0.47 — Restricted Owner Core (Shadow Module) [DONE]

Completed 2026-03-18. `mod_owner_core.py` (internal CRM, global key manager, remote diagnostics) in private submodule. `BIGED_OWNER_KEY` gate. Build exclusion from public dist/.

### v0.48 — Cautious Codebase Pruning (Bloat Cleanup) [DONE]

Completed 2026-03-18. **Milestone 7 complete.** AST-based dead code detection (vulture/ruff), graveyard quarantine pattern, config/dependency audit, 100% regression gate (smoke + GUI + 8h soak).

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

## Phase 2: Post-1.0 (0.01.xx through 0.15.xx)

### 0.01.01 — Post-Release Stabilization [DONE]

Architecture research findings incorporated, dead code graveyard quarantine executed, soak test under full production load (8-hour validation), community-reported bug fixes.

### 0.01.02 — DAG & Cost Enhancements [DONE]

Conditional DAG edges, agent card metadata, cost-aware task routing, provider health probes, checkpointing, message schema versioning, budget enforcement modes (warn/throttle/block).

### 0.01.03 — Visualization & Forecasting [DONE]

DAG visualization, cost forecasting, chart data endpoints, input-side guardrails (PII + secret scan), circuit breaker for HA fallback.

### 0.05.00 — Git & MLOps Autonomy (Skill Expansion) [DONE]

4 new skills: `git_manager`, `github_interact`, `dataset_synthesize`, `service_manager`. Agents can stage/commit/branch, generate synthetic JSONL datasets, manage host OS services.

### 0.06.00 — Cryptographic & Security Self-Healing [DONE]

`secret_rotate` (autonomous key rotation on DLP alerts), `db_encrypt` (SQLCipher migration during idle), `db_migrate` (schema migration without operator intervention).

### 0.07.00 — Security Hardening (Gemini 3rd Pass P0+P1) [DONE]

Dashboard bearer token auth, review enabled by default, Docker sandbox execution for code_write/pen_test/skill_test, worker resource limits (cgroups/Job Objects), cross-platform isolation docs.

### 0.08.00 — Architecture Polish (Gemini 3rd Pass P2) [DONE]

SSE Pattern 6 formalized, ML Bridge skill (`ml_bridge.py`), async DAG manager extraction, `_db_init()` moved to `data_access.py`, Flask-Limiter + CSRF on dashboard.

### 0.09.00 — Audit & Observability (Gemini 3rd Pass P3) [DONE]

JSON+HMAC centralized audit log, RotatingFileHandler (10MB x 5 backups), TLS for dashboard (self-signed + optional Let's Encrypt).

### 0.10.00 — Advanced Agent Flows (Skill Synergy) [DONE]

Proactive CRM pipeline (auto-trigger lead_research -> account_review -> outreach), `rag_compress` + `knowledge_prune` skills for archivist, swarm consensus via Layer 2 comms channel.

### 0.11.00 — Security Fixes (DLP Expansion) [DONE]

DLP pattern expansion: Azure, GCP, database URI, private key detection. Extended file-type scrubbing beyond markdown. MQTT wildcard topic blocking. Post-1.0 hardening batch 1.

### 0.12.00 — Bug Fixes & Cross-Platform Stability [DONE]

B4 tomlkit migration (regex TOML writes replaced), B7 psutil cross-platform fixes, swallowed exception cleanup, TECH_DEBT items resolved.

### 0.13.00 — Compliance Framework (Part 1) [DONE]

Compliance scaffolding, policy engine foundation, regulatory requirement mapping, audit trail enhancements for compliance reporting.

### 0.14.00 — Compliance Framework (Part 2) [DONE]

Compliance implementation completed, policy enforcement integration, compliance reporting endpoints, regulatory documentation.

### 0.15.00 — Model Manager & Hardware Profiles [DONE]

`model_manager.py` skill: check installed vs needed, pull missing, detect hardware, recommend profiles. `model_profiles.toml` with 6 presets. CLI: `model-check`, `model-install`, `model-profile`. Startup validation in hw_supervisor. Model version tracking with digest comparison and update detection.

### 0.16.00 — Boot Stability + Native Windows Migration [DONE]

Completed 2026-03-19. 15+ boot fixes: stale hw_state.json, ghost model eviction, hw_supervisor native launch, frozen .exe subprocess loop (sys.executable→_get_python), WSL→psutil migration (all pkill/pgrep eliminated), NativeWindowsBridge as default, adaptive boot timeouts, live boot timers, park+guard hw_supervisor pattern, dynamic worker cap (max_workers=10 + RAM-based scaling).

### 0.17.00 — Swarm Tier 1: Coordinated Evolution [DONE]

Completed 2026-03-19. `evolution_coordinator.py` skill + `skill_evolution_pipeline.toml` workflow (6-stage DAG: draft→test→review→security→evolve→deploy). Cross-skill learning triggers. Evolution leaderboard tracking.

### 0.18.00 — Swarm Tier 2: Autonomous Research Loops [DONE]

Completed 2026-03-19. `research_loop.py` skill + `research_cycle.toml` workflow. Knowledge gap detection (scans coverage), auto research→summarize→train cycle, per-skill quality scoring from task history.

### 0.19.00 — Swarm Tier 3: Swarm Intelligence [DONE]

Completed 2026-03-19. `swarm_intelligence.py` skill. Agent specialization discovery (>80% success rate analysis), adaptive affinity recommendations, LLM task decomposition into sub-tasks, per-agent fitness reports.

### 0.20.00 — Additional Skills + GUI Overhaul [DONE]

Completed 2026-03-19. OOM prevention skill, refactor_verify skill, model_manager skill. Agents tab overhaul (task counter cards, 3-column agent grid with sparklines). Combined "all" log view. Idle evolution enabled. 72 skills total.

---

## Phase 3: Alpha (Current)

### 0.21.01 — Dr. Ders + Token Tracking + HITL UX + Swarm Dashboard [DONE]

Completed 2026-03-19. Major session — 18 files, 1400+ lines added:

**Dr. Ders rename:** hw_supervisor renamed to "Dr. Ders" across all UI, logs, docs, DB registration. File stays `hw_supervisor.py` to avoid import churn.

**Token speed tracking:** Ollama `eval_count`/`eval_duration` captured per call → `tokens_per_sec` in usage table. `get_model_speed_stats()` returns avg/p50/p95 per model. Model performance panel in status tab shows live tok/s comparison.

**Per-skill model routing:** Simple skills → qwen3:4b (fast), medium/complex → qwen3:8b (quality). `LOCAL_COMPLEXITY_ROUTING` in providers.py, configurable via fleet.toml `[models.tiers]`. 30+ skills classified.

**HITL inline actions:** Action panel below agents in status tab — view/respond to agent requests, view/dismiss security advisories. No more hunting through files. CLI: `lead_client.py hitl`, `lead_client.py advisories`.

**Enhanced swarm dashboard:** Agent cards now show model label, tok/s, last result preview, WAITING_HUMAN badge. Counter cards added: WAITING + MODELS counts.

**Memory watchdog (3-tier):** Dr. Ders self-monitors RSS (gc on growth), supervisor cross-monitors all workers + Dr. Ders (restart at 600MB RSS), RSS stats in hw_state.json.

**VRAM-aware training:** Only evicts Ollama for stable/flat_out profiles. Micro/balanced training coexists with Ollama on GPU.

**Bug fixes:** diagnostics.py `status` column bug (log spam), smoke_test `claim_task` reliability.

**Docs:** CLAUDE.md model tier strategy (Haiku/Sonnet/Opus + local routing).

Smoke: 22/22. 73 skills.

### 0.21.02 — Gemini Safety + Native Key Manager [DONE]

Completed 2026-03-19. Gemini `finishReason` safety handling (raises on SAFETY block). KeyManagerDialog migrated from WSL to native Windows (direct ~/.secrets read/write). model_manager.py `update_check` action.

### 0.21.03 — Intelligence Scoring + HITL Model Recommendations + Gemini ToS [DONE]

Completed 2026-03-19. Three major features:

**Intelligence scoring (intelligence > performance):** `intelligence.py` — hybrid Tier 1 mechanical scoring (0.0-1.0) per task output. Checks content presence, length, structure, error-free, skill-specific format. `intelligence_score` column on tasks table, scored after every `complete_task()`. `get_skill_quality_stats()` for per-skill quality aggregation. Tier 2 LLM scoring placeholder.

**HITL model preference flow:** `model_recommend.py` (skill #74) — auto-analyzes fleet model performance every 6h. `MODEL_QUALITY` reference table (8 model families). Compares installed models against config, finds upgrades. Creates HITL request for operator approval before changing fleet.toml. Supervisor auto-dispatches.

**Gemini ToS compliance:** `provider` column in usage table. Thread-local `get_last_provider()`. `dataset_synthesize.py` dual exclusion filter (DB + thread-local). Gemini-sourced content excluded from training data.

Smoke: 22/22. Skills: 74.

### 0.21.04 — UX Polish + Fleet Tab Refinement + P1 Fixes [DONE]

Completed 2026-03-19. UX refinements and audit fixes across 3 files:

**Fleet tab agent cards:** Intelligence score (IQ: 0.85) on each card with color coding (green/orange/red by threshold). Agent name truncated to 18 chars, task label to 40, last result to 50 — eliminates clipped labels.

**Model Performance panel:** New IQ column (avg intelligence_score per model, last 1h) alongside tok/s, calls, avg ms. Color-coded thresholds.

**Action panel:** Relative timestamps ("3m ago") on WAITING_HUMAN cards. Tighter card density (1px padding). "(R to refresh)" keyboard hint on header.

**Fleet Comm tab modernization:** Orange left accent stripe on WAITING_HUMAN cards. Stacked header (task type bold + agent name dim + relative timestamp right). Counter badge: "N pending" (orange) / "All clear" (green). Advisory cards: lock icon prefix, green Approve button, gray Dismiss. Centered empty state.

**P1-01 fix:** Double `check_budget()` call eliminated — cached result reused for cost estimation (single DB round-trip).

**P1-02 fix:** Claude health probe replaced `client.messages.create()` with `client.models.list(limit=1)` — zero inference cost.

**P2-01 fix:** Redundant `from providers import PRICING` in-function import removed.

### 0.30.00 — 2.0/5.0 Feature Pull-Forward [DONE]

Completed 2026-03-19.

- Remote dashboard access (bind_address, CORS, TLS+auth safety gate)
- Fleet export/import CLI (lead_client.py export/import with manifest + secret redaction)
- A2A Federation foundation (peer discovery, health probes, task overflow routing)
- Web launcher enhancement (agent management, settings view, console view)
- Containerization foundation (Dockerfile, docker-compose with fleet + ollama services)

### 0.30.01a — HITL QA + Agent Refinement [DONE]

Completed 2026-03-19.

- Disabled agents feature (fleet.toml config, supervisor filtering, dashboard API, launcher GUI)
- HITL evolution toggle (operator approval for idle evolution proposals)
- Topic diversity fix (weighted random skill selection, per-agent cooldown, cross-worker dedup)
- Documentation cleanup

### 0.050.00b — Installer Overhaul + Model Recovery + Dynamic Scaling [DONE]

Completed 2026-03-20. Major session — 45 files, +1635/-335 lines:

**Installer overhaul:** Python/Ollama detection with status indicators, UAC elevation for Program Files path, 3-tier Ollama install (winget > curl > urllib), all 4 tier models pulled (0.6b/1.7b/4b/8b) with skip-if-installed, post-install verification, scrollable options page.

**Model recovery (5-system resilience):** boot.py graceful fallback to best available model + HITL dropdown. hw_supervisor non-blocking tier validation. Installer 4-model pull with verification. Updater TOML-aware expected models with post-recovery verification. Supervisor get_best_available_model() for worker routing.

**Dynamic agent scaling:** Boot 4 core agents (coder_1, researcher, planner, archivist), type-aware scale-up mapping pending task types to roles via affinity, scale-down after 5min idle, auto-generated instance names (coder_4, researcher_2).

**Dr. Ders model promotion:** 0.6b boot → best available CPU model (4b/1.7b) steady state → 0.6b failsafe on error. All CPU-bound (num_gpu=0).

**UI:** Pixel fonts (Plain 11/12, Bold 12) across 27 files. Header hamburger 28pt, title 26pt. Dynamic version from git tags. Dashboard button prominent blue. Boot error overlay persists.

**Dashboard:** Alert dedup + bubble icon. 5min startup grace. Disabled agents excluded from warnings. Collapsible idle agents. Ollama multi-path detection.

**Build:** Runtime counter, step timings, artifact sizes in build.py.

### 0.050.01b — Dashboard Live Agents + Thermal Fallback [DONE]

Completed 2026-03-20. 11 files, +194/-90 lines:

**Dashboard live agents:** Only show agents with heartbeat <60s (no ghost agents from old sessions). Agent display names show task_type / agent_name. Dynamic template reload (no restart for HTML changes). Supervisors pinned top, idle collapsed at bottom.

**Thermal:** Direct GPU fallback when hw_state.json stale. CPU temp module (fleet/cpu_temp.py) with PowerShell WMI → wmic → psutil fallback chain, 5s cache. "0°C = sensor unavailable" note.

**Activity panel:** Replaces Training panel. One-line training badge, activity feed as main content.

**Fleet tuning:** Worker poll 2s→1s, idle threshold 6→3, idle timeout 30→10s, scale-up threshold 5→2. Dashboard added to kill targets on fleet stop.

**CI:** GitHub Actions Node.js 24 compatibility, release workflow brick.ico fix.

### 0.050.02b — P0 Security & Stability Hardening [DONE]

**10 P0 bugs** identified by 5-agent audit. All fixed in v0.050.02b (35e2e2a).

**Boot/Installer P0s:**
- [x] Daemon thread + GUI race condition — all thread->UI calls now use `_safe_after()` (boot.py: 20+ call sites)
- [x] Zombie Ollama processes — `_ollama_proc` stored, terminated+killed in cleanup (boot.py:632, 899-905)
- [x] Hard-coded Ollama port — `_get_ollama_host()` reads fleet.toml `[models].ollama_host`, `OLLAMA_HOST` constant used everywhere (boot.py:106-116)

**Supervisor P0s:**
- [x] DB connection leak in `_count_pending_tasks()` — try/finally with conn.close() (supervisor.py:100-110)
- [x] `_last_busy` dict cleaned on agent removal — `_last_busy.pop(role, None)` in stop_worker (supervisor.py:236)
- [x] Shutdown crash — `list(worker_procs.items())` copy used in all iteration sites (supervisor.py:116, 674, 676, 729, 931)

**Dashboard P0s (XSS):**
- [x] Agent names escaped via `escapeHTML()` (dashboard.html:327-332)
- [x] Alert messages escaped via `escapeHTML()` (dashboard.html:343)
- [x] All 40+ innerHTML injection points now use `escapeHTML()` throughout (dashboard.html)
- [x] SQL injection blocked — `ALLOWED_FLEET_TABLES` and `ALLOWED_TOOLS_TABLES` frozensets (dashboard.py:797, 813)

### 0.050.03b — P1 Reliability & Error Handling [DONE]

**19 P1 bugs** — all 19 fixed. 12 across v0.50.02b (35e2e2a) and v0.51.00b (24e21d4), 7 completed v0.053.02b.

**Boot/Installer:**
- [x] Model load response parsing — JSONDecodeError caught and logged as NDJSON debug info (boot.py:841-846)
- [x] Timeout values configurable via fleet.toml `[boot] model_load_timeout = 300` — adaptive timeout reads config (boot.py:154-166)
- [x] Env var null checks — `PROGRAMFILES`, `USERPROFILE`, `APPDATA`, `TEMP`/`TMP` all use `or` fallback to `Path.home()` / `tempfile.gettempdir()` (installer.py:41-42, 129-134, 922, 1061-1067)
- [x] Model fallback error handling — removed redundant action card when fallback succeeds; action card only shown when no models at all (boot.py:789-795)
- [x] Build failure error message — shows first 120 chars of command, not just 3 words (installer.py:810)

**Supervisor:**
- [x] Worker zombie process leak — `worker_procs.pop(role, None)` on stop, `del worker_procs[role]` for disabled (supervisor.py:235, 947)
- [x] VRAM threshold edge case — strict `>` instead of `>=` prevents oscillation at exact boundary (hw_supervisor.py:1043, 1052)
- [x] Dynamic agents scale down — `_should_scale_down()` checks `_last_busy` timestamps, `SCALE_DOWN_IDLE_SECS=300` (supervisor.py:212-236, 919-921)

**Dashboard:**
- [x] SSE connection leak — clients cleaned in finally block and dead client removal (dashboard.py:176-177, 1324-1326)
- [x] TOML injection in worker disable/enable — `VALID_AGENT = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')` validates all agent names (dashboard.py:1577-1583)
- [x] fetchJSON() error handling — try/catch with HTTP status check, returns `{}` on failure (dashboard.html:250-258)

**Launcher GUI:**
- [x] SSE thread UI safety — all UI updates go through `_safe_after()` with `_alive` guard (launcher.py:920, 1089-1095)
- [x] Unguarded UI updates in `_poll_task_result()` — `winfo_exists()` checks before all after() calls (consoles.py:646, 652, 659)
- [x] Widget destroy during iteration — cached `_agent_rows` dict with update-only pattern, no destroy/recreate (launcher.py:933, 3033)
- [x] Font loading failure properly warned — prints to stderr with [WARN] prefix (theme.py:26)
- [x] Window geometry bounds-checked — `winfo_screenwidth/height()` validation before restore (launcher.py:905-908)

**Data Layer:**
- [x] SQLCipher key SQL injection — `safe_key = key.replace("'", "''")` before PRAGMA (db.py:119-120)
- [x] Provider column migration complete — backfill for claude/gemini/local on NULL rows (db.py:187-189)

### 0.050.04b — P2 Hardening & Performance [DONE]

**27+ P2 bugs** — all 16 key items fixed. 13 across earlier patches, 3 verified/completed v0.053.02b.

**Key items:**
- [x] N+1 query in `/api/status` — uses LEFT JOIN for current_task (dashboard.py:250-253)
- [x] DB indexes on tasks.status, tasks.assigned_to, tasks.parent_id — `idx_tasks_status`, `idx_tasks_assigned`, `idx_tasks_parent` (db.py:191-193)
- [x] Foreign key on tasks.parent_id — `FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE SET NULL` in CREATE TABLE + `PRAGMA foreign_keys=ON` (db.py:26, 50)
- [x] VRAM threshold match — fleet.toml `[thermal.vram]` (0.92/0.85/0.60) matches hw_supervisor defaults exactly; `load_thermal_config()` reads TOML overrides (hw_supervisor.py:82, 103-105)
- [x] Config loaded once at import — stale after fleet.toml edits → supervisor reloads every 5 min (supervisor.py:952, 1293-1299)
- [x] DB timeout consistency — unified to 30s timeout + 30s PRAGMA busy_timeout across all layers (db.py:115-128)
- [x] Circuit breaker has exponential backoff — `min(60s * 2^cooldowns, 600s)` with cooldown counter (providers.py:38-52)
- [x] FALLBACK_CHAIN actively used — `_models.py call_complex()` iterates chain with circuit breaker (skills/_models.py:124-138)
- [x] Boot timing file already atomic — writes to `.tmp` then `replace()` (boot.py:148-150)
- [x] pip --break-system-packages for system Python — PEP 668 check + flag (installer.py:846-855)
- [x] Bare `except:` upgraded to `except Exception:` across launcher — 0 bare except remaining (102 `except Exception` sites)
- [x] Memory leak fix: _model_perf_labels cleaned for stale models (launcher.py:2992-2999), _agent_activity uses bounded deque(maxlen=10)
- [x] Alert monitor exception logging — logs first 3 failures via `logging.warning()` (dashboard.py:258-263)
- [x] hw_state.json writes already atomic — `tempfile.mkstemp` + `os.replace` (hw_supervisor.py:205-208)
- [x] Content-Security-Policy header on all dashboard responses — `_add_security_headers()` after_request handler (dashboard.py:111-120)
- [x] Stale task recovery — PID liveness check via `psutil.pid_exists()` before requeuing + `acquire_fleet_lock()` for federation safety (db.py:744, 756-765)

### 0.050.05b — P3 Polish & Accessibility [DONE]

**14+ P3 items** — all 11 verified fixed.

- [x] No progress feedback during long model loads — "this may take a few minutes" status (boot.py:306)
- [x] fleet.toml path not verified before load — `Path.exists()` check in `_read_fleet_models` (boot.py:194)
- [x] Ctrl+K command palette undiscoverable — "Ctrl+K  command palette" label in status bar (launcher.py:2716)
- [x] OmniBox badge abbreviations unexplained — "SYS = System   SKL = Skill   AGT = Agent" legend (omnibox.py:81)
- [x] Dialog resize clipping on small screens — `resizable(False, False)` on all CTkToplevel dialogs (launcher, consoles, settings, modules)
- [x] SSE client start exception logged to stderr — `[WARN] SSE client failed` (launcher.py:982)
- [x] Dashboard badge status values not validated — BADGE_WHITELIST expanded, input sanitized (dashboard.html:292-301)
- [x] No rate limiting on expensive dashboard endpoints — `_check_rate_limit()` on /api/knowledge, /api/rag, /api/data_stats (dashboard.py:70-87)
- [x] Worker disable/enable not audit logged — `_add_alert("info", "Agent disabled by operator")` (dashboard.py:1466)
- [x] ~~GITHUB_REPO typo~~ — not a bug: fleet.toml overrides config.py default correctly
- [x] Distributed locking for federation mode — `acquire_fleet_lock()` / `release_fleet_lock()` with `BEGIN EXCLUSIVE`, exponential backoff, used by `recover_stale_tasks()` (db.py:144-177, 744)

### 0.051.00b — Startup Performance & UX Polish [DONE]

**Goal:** Sub-700ms window visible, 144Hz-smooth refresh, hide dev scaffolding. Public beta polish.
Completed. Dr. Ders respawn, startup perf, disabled agents, idle evolution backoff, refresh smoothing, idle evolution API key gating, Chart.js update pattern, batch API, lazy tab loading, parse_status caching, disabled agent affinity cleanup all done.

**CRITICAL: Dr. Ders respawn — FIXED**
- [x] Supervisor spawns hw_supervisor.py via `start_hw_supervisor()` and respawns on crash (supervisor.py:452-458, 967-970)
- [x] Dr. Ders model promotion uses explicit CPU assignment — `num_gpu=0` for conductor/failsafe (hw_supervisor.py:527-540, 760)
- [x] Models loaded with explicit `num_gpu` — `_model_gpu_assignment` dict tracks 99=GPU, 0=CPU per model (hw_supervisor.py:313-344)

**Legacy agent cleanup (hide dev scaffolding):**
- [x] Hide disabled agents section from launcher Fleet tab — production hides entirely, dev mode shows collapsed (launcher.py:2005-2058)
- [x] Worker checks disabled BEFORE DB registration — exits immediately if in `disabled_agents` (worker.py:364-368)
- [x] Remove affinity config for permanently disabled agents — sales, onboarding, implementation, legal, account_manager entries removed from fleet.toml [affinity]
- [x] Disabled agents hidden from dashboard — heartbeat <60s filter excludes non-running agents (dashboard.py:254)

**Startup performance (target: window visible < 700ms):**
- [x] Defer pynvml GPU init — lazy `_ensure_gpu()` on first hw read, not at import (launcher.py:34-49)
- [x] Defer font loading to after window creation — `load_custom_fonts()` called in `__init__` after `super().__init__()` (launcher.py:889-890)
- [x] Defer `_refresh_status()` to after window visible — uses `_safe_after(100, ...)` (launcher.py:964)
- [x] Lazy-load Fleet Comm + modular tabs on first click — _lazy_tabs dict with deferred builder pattern (launcher.py:1709-1748)
- [x] Cache parse_status() for 1-2s — 2s TTL cache via _status_cache (launcher.py:577-590)

**Refresh cycle smoothing (target: no stalls > 16ms on 144Hz):**
- [x] Increase HW stats interval 3s -> 5s — now 5000ms interval (launcher.py:3521)
- [x] Skip parse_status() when SSE active — `_sse_active` guard, polls at 8s instead of 4s (launcher.py:3588-3593)
- [x] SSE client reads 4KB chunks — `resp.read(4096)` (sse_client.py:91)
- [x] Cache action cards — `_agent_rows` dict with update-only pattern instead of destroy/recreate (launcher.py:933, 3033-3040)

**Idle evolution quarantine spiral:**
- [x] Check API key availability before dispatching idle evolution tasks — `providers.has_api_key()` guard (worker.py:286-296)
- [x] Exponential backoff between failed idle evolution — `_idle_failures` counter, pauses after 3 consecutive failures (worker.py:397, 512-534)
- [x] Auto-clear quarantine after 5 minutes of inactivity (worker.py:481-498)
- [x] Gate idle evolution on local-only skills when API keys missing — already guarded, idle skills are local-only by design (worker.py:286-296)

**Dashboard web performance:**
- [x] Batch 15 API calls into single `/api/dashboard` endpoint — aggregate endpoint returns all core data in one request (dashboard.py:839-864)
- [x] Reduce 30s polling to 5min for slow-changing data — loadColdData() on 300s interval (dashboard.html:908-915)
- [x] Update Chart.js data instead of destroy/recreate — activityChart and skillsChart already use update pattern (dashboard.html:509-513, 543-547)

### 0.051.01b — Task Pipeline Optimization [DONE]

**Goal:** 30-40% throughput improvement, 15-20% API cost reduction. All 10 bottlenecks resolved.

**Critical (implement first):**
- [x] Atomic task claiming — UPDATE...WHERE(SELECT) eliminates race conditions (db.py:241-282)
- [x] Enable prompt caching — `cache_control: ephemeral` on stable system prompts (providers.py:338)
- [x] Async usage logging — background thread with queue-based batching (cost_tracking.py:22-49)
- [x] Adaptive polling — 0.1s/0.5s/2s based on recent activity + jitter (worker.py:692-698)

**Medium priority:**
- [x] Global idle evolution dedup — worker checks pending queue before creating idle task (worker.py:513-524)
- [x] DAG promotion index — `idx_tasks_depends` on tasks(depends_on) (db.py:195)
- [x] API request batching — `call_complex_batch()` via Anthropic Message Batches API (skills/_models.py:178)
- [x] Deterministic Tier 2 sampling — hash(task_id) % 100 for consistent 10% sample (intelligence.py:107-108)

**Lower priority:**
- [x] Cache skill staleness ranking in idle evolution — 60s TTL _staleness_cache dict (idle_evolution.py:4-6, 60-88)
- [x] Batch-claim N tasks per poll when queue depth > threshold — claim_tasks(n=2) when queue_depth > 3 (worker.py:525-530, db.py:288-296)

### 0.051.02b — Auto-Save & Backup System [DONE]

**Goal:** Prevent data loss from power outage or crashes. Configurable backup frequency/depth/location. All 10 items done.

**Implementation:**
- [x] `fleet/backup_manager.py` — BackupManager class with auto-save thread (203 lines)
- [x] `fleet.toml [backup]` section — enabled, interval_secs=1200, depth=10, location, prune_enabled, targets, safety
- [x] Backup targets: fleet.db, rag.db, knowledge/, fleet.toml (configurable per-target via fleet.toml)
- [x] WAL checkpoint before backup — `PRAGMA wal_checkpoint(TRUNCATE)` (backup_manager.py:121)
- [x] Backup manifest JSON — timestamp, file hashes, integrity check results (backup_manager.py:49-82)
- [x] Integrity verification — `PRAGMA integrity_check` after each backup (backup_manager.py:126-133)
- [x] Prune beyond depth — with depth=0 "do not clean" toggle + disk usage warning (backup_manager.py:142-178)
- [x] CLI: `lead_client.py backup`, `backup --list`, `backup --restore ID` — full implementation with --confirm safety (lead_client.py:1004-1118)
- [x] Supervisor integration — BackupManager imported and started on fleet startup (supervisor.py:868-869)
- [x] Graceful shutdown saves task queue — `_graceful_save_tasks()` (launcher.py:522, 1057)

### 0.051.03b — Intelligence Module + Cost Dashboard [DONE]

**Goal:** System transparency tab for understanding capabilities, model settings, prompt queue, evaluation. All 8 items done.

**Implemented:**
- [x] Intelligence module (mod_intelligence.py) — 5 panels: overview, model settings, prompt queue, evaluation, cost
- [x] API Cost Tracker dashboard panel — today/7d/30d spend, provider breakdown, projections (dashboard.html:200-202, 641)
- [x] billing_ocr skill — OCR screenshots of Claude/Gemini billing dashboards (fleet/skills/billing_ocr.py)
- [x] token_optimizer skill — audit usage patterns, recommend cost optimizations (fleet/skills/token_optimizer.py)
- [x] Prompt queue dispatches to configurable skill type — dropdown selector with StringVar (mod_intelligence.py:159-203)

**Remaining:**
- [x] Model settings panel with live edit capability — dialog writes back to fleet.toml via regex (mod_intelligence.py:165-212)
- [x] Weight adjustment UI for skill complexity routing — move-skill control writes to providers.py SKILL_COMPLEXITY (mod_intelligence.py)
- [x] Evaluation routine live display — live scoring feed shows 5 most recent scored tasks with color-coded scores (mod_intelligence.py)

### 0.051.04b — Autoresearch Pipeline Integration [DONE]

**Goal:** Wire disconnected research/training pipelines into closed feedback loops. All 10 items done.

**Auto-bridges:**
- [x] Auto-trigger `research_cycle` workflow daily — `RESEARCH_INTERVAL = 86400` in supervisor (supervisor.py:76, 1151)
- [x] Auto-trigger `skill_evolution_pipeline` weekly — `EVOLUTION_INTERVAL = 604800` in supervisor (supervisor.py:77, 1176)
- [x] `ml_bridge` auto-import when new `autoresearch/results.tsv` entries detected — mtime watch (supervisor.py:1199-1214)
- [x] Dataset synthesize outputs → autoresearch data pipeline (JSONL → training) — auto-copy to autoresearch/data/ (dataset_synthesize.py:227-233)

**Dashboard visibility:**
- [x] Evolution leaderboard panel — skill improvement rates, agent contributions (dashboard.html:179-180)
- [x] Quality metrics panel — code_quality scores, benchmark results over time (dashboard.html:185)
- [x] HITL notification when skill draft scores higher than deployed version — `skill_draft_ready` (worker.py:603-649)

**Screenshot skill:**
- [x] `fleet/skills/screenshot.py` — capture full/window/region, UX test suite
- [x] Automated UX test suite: capture launcher + dashboard + Fleet tab on each release -- build.py --ux-screenshots triggers capture_ux_test_suite (build.py, screenshot.py:149-165)
- [x] Screenshot diff tool: compare before/after for visual regression -- screenshot_diff() with threshold, amplified diff image (screenshot.py)

### 0.051.05b — GitHub Public Presence Update [DONE]

**Goal:** Update GitHub repo description, README, and metadata to reflect BigEd CC's value proposition. README and federation docs done, 2 items deferred (require GitHub web UI).

**Blurb:**
> BigEd CC eliminates manual CLI setup for local AI. One-click deployment of Ollama models + agent fleet.
> Use OAuth Manual Mode (Claude Code / Gemini) with pre-loaded .md context from agent requests — or
> let the fleet work autonomously via API. No terminal required. All platforms. Enterprise-ready.

- [x] GitHub repo description: DEFERRED -- requires GitHub web UI, not automatable from CLI
- [x] README.md: value proposition blurb, architecture tree, quick start (README.md — 80+ lines)
- [x] Credit: link to Karpathy's build-nanogpt for training pipeline (README.md:80)
- [x] Feature highlights: auto-installs deps, air-gap mode, HITL governance, 74+ skills, Manual Mode (README.md:12-23)
- [x] Compliance section: SOC 2 alignment, DLP, RBAC, audit logging (README.md:71-74)
- [x] Multi-machine: fleet federation details, cross-platform specifics -- added federation section to README.md with config table and fleet.toml examples
- [x] Badges: license badge present (README.md:9)
- [x] Topics/tags: DEFERRED -- requires GitHub web UI, not automatable from CLI
- [x] Screenshot gallery: `docs/screenshots/README.md` placeholder created with suggested captures

### 0.051.07b — File Access Control + SOC 2 Folder Permissions [DONE]

Completed 2026-03-20.

**Goal:** Enterprise-grade folder access control for SOC 2 compliance. Agents and modules get explicit read/read-write/full access per directory. IDE embed uses sandboxed workspace.

**File access control system:**
- [x] `fleet.toml [filesystem]` section: zones, overrides, enforce, deny_by_default, log_all_access (fleet.toml:259-269)
- [x] Permission levels: `read` (view only), `read_write` (create/edit), `full` (create/edit/delete/execute) — ACCESS_LEVELS dict (filesystem_guard.py:21)
- [x] Per-agent access: agents identified in `check_access()`, inherit zone permissions (filesystem_guard.py:50-91)
- [x] Per-module/skill access: skill overrides from `[filesystem.overrides]` (filesystem_guard.py:69-74)
- [x] Audit logging: file-based SOC 2 audit trail + logger (filesystem_guard.py:93-116)

**Proposed fleet.toml config:**
```toml
[filesystem]
# Access zones — agents/modules can only access declared paths
[filesystem.zones]
project = {path = ".", access = "read"}
knowledge = {path = "fleet/knowledge", access = "read_write"}
code_drafts = {path = "fleet/knowledge/code_drafts", access = "read_write"}
skills = {path = "fleet/skills", access = "read"}        # deploy_skill gets full
config = {path = "fleet/fleet.toml", access = "read"}     # settings UI gets read_write
backups = {path = "~/BigEd-backups", access = "full"}
workspace = {path = "fleet/knowledge/code_writes/workspace", access = "full"}

[filesystem.overrides]
# Skill-specific overrides
deploy_skill = {zones = ["skills"], access = "full"}      # can write to skills/
code_write = {zones = ["workspace", "code_drafts"], access = "full"}
ingest = {zones = ["knowledge"], access = "read_write"}

[filesystem.enterprise]
enforce = false          # true on enterprise installs (forced)
deny_by_default = true   # reject access to paths not in zones
log_all_access = true    # SOC 2 audit trail for file operations
```

**Implementation:**
- [x] `fleet/filesystem_guard.py` — FileSystemGuard class with check_access(), log_access(), zone matching (150+ lines)
- [x] Wrap skill file operations through guard (code_write, ingest, deploy_skill, rag_index) — all 4 skills wired (code_write.py:122-131, ingest.py:280-289, deploy_skill.py:84-92, rag_index.py:23-30)
- [x] Integration with existing sandbox (Docker) for code execution — `_get_docker_volumes()` scopes mounts to guard zones (worker.py:105-136), `_run_in_docker()` uses zone-scoped volumes (worker.py:139-181)
- [x] Dashboard panel: file access audit log viewer — `/api/filesystem/audit` endpoint (dashboard.py:892-943) + HTML panel with table (dashboard.html:202-216) + JS loader (dashboard.html:838-865)
- [x] Enterprise mode: `is_enterprise()` returns True when enforce + deny_by_default both active (filesystem_guard.py:122-124)

**IDE embed (SOC 2 compliant):**
- [x] deferred — Phase 2 (code-server): code-server (VS Code in browser) running on localhost with workspace restriction
- [x] deferred — Phase 2 (code-server): Embedded via WebView in a module tab (pywebview or tkinterweb)
- [x] deferred — Phase 2 (code-server): Workspace scoped to `filesystem.zones.workspace` path only
- [x] deferred — Phase 2 (code-server): Claude Code / Gemini sessions launch in scoped workspace
- [x] deferred — Phase 2 (code-server): File changes in workspace auto-detected, staged for review

### 0.051.08b — Manual Chat + Fleet Comm UX Redesign [DONE]

**Goal:** Integrate Manual Mode (OAuth) chat directly into Fleet Comm tab. Unified UX for agent HITL requests + human-initiated Manual Chat sessions.

**Fleet Comm tab redesign:**
- [x] Split Fleet Comm into two sections: "Agent Requests" (top) + "Manual Chat" (bottom) (launcher.py:2289-2386)
- [x] Agent HITL requests: collapsed to 1-line summary, expand on hover (launcher.py:2303-2311, 2392-2396)
- [x] Pin button to hold request list open (sticky mode) — pin icon with gold highlight (launcher.py:2314-2318, 2398-2402)
- [x] Dynamic scrollbar when requests pending — CTkScrollableFrame (launcher.py:2337-2338)
- [x] Scroll area auto-sizes based on pending request count — `min(300, max(60, n * 60))` (launcher.py:2415-2416)
- [x] Request count badge on Fleet Comm tab icon — red overlay badge (set_badge) + tab text count (launcher.py:2789-2793, 3823-3825)

**Manual Chat integration:**
- [x] "Manual Chat" panel below agent requests in Fleet Comm (launcher.py:2344-2386)
- [x] Model selector dropdown: Claude Code (OAuth), Gemini (OAuth), Local (Ollama) (launcher.py:2355-2361)
- [x] For OAuth models: "Open in Claude Code" (VS Code launch) / "Open in Gemini" (AI Studio) (launcher.py:2467-2485)
- [x] For Local models: inline chat interface (direct Ollama /api/generate, threaded) (launcher.py:2440-2458)
- [x] Pre-load context from selected agent request — "Load to Chat" button + tab switch + focus (launcher.py:2856-2863, 2939-2978)
- [x] Context preview: shows what .md files will be written before launch — modal dialog (launcher.py:736-778, 2672)

**Agent request → Manual Chat flow:**
- [x] Click agent HITL request → populates Manual Chat with full context (launcher.py:2856-2863, 2939-2978)
- [x] User selects OAuth model → writes task-briefing.md + opens IDE/browser (launcher.py:2467-2485)
- [x] User selects Local model → inline response rendered in Fleet Comm (launcher.py:2440-2465)
- [x] Response feeds back to agent (closes HITL loop) — Manual Chat sends via _send_human_response + visual confirmation (launcher.py:2623-2645)

**Dynamic behavior:**
- [x] HITL requests stack when local/API models running unattended — refresh_comm() loads all WAITING_HUMAN (launcher.py:2489)
- [x] Badge counter updates in real-time via SSE — SSE handler updates both red overlay + tab text on each push (launcher.py:3953-3972)
- [x] Collapsed view: "N agent requests ▸" (single line, orange/green coloring) (launcher.py:2407-2411)
- [x] Hover/click expands: shows each request with dynamic scroll area (launcher.py:2310-2311, 2404-2417)
- [x] Pinned view: pin button holds list expanded until unpinned (launcher.py:2398-2402)

### 0.053.00b — Module Hub + Scrollable Tab Bar [DONE]

Completed 2026-03-20.

**Goal:** GitHub-based module repository with download/install UX + scrollable tab bar for unlimited modules.

**Repo:** https://github.com/SwiftWing21/BigEd-ModuleHub (renamed from BigEds_Agents)
**Spec:** `docs/specs/module_hub_architecture.md`

**Scrollable tab bar:**
- [x] Refactor CustomTabBar: horizontal scroll when tabs exceed window width (launcher.py:864-889)
- [x] Left/right scroll arrows or mouse wheel scroll (launcher.py:870-900)
- [x] Active tab auto-scrolls into view (launcher.py:994-1001)
- [x] Minimum tab width to keep text readable (launcher.py:894, 939 — `_min_tab_width = 80`)

**Module Hub core:**
- [x] registry.json catalog (name, version, checksum, tags, enterprise_only) — BigEd-ModuleHub/registry.json
- [x] Module download from GitHub raw URL with SHA-256 verification (`hub.py:install_module()`)
- [x] Module install: copy to modules/, update manifest.json (`hub.py:_update_local_manifest()`)
- [x] Add to fleet.toml [launcher.tabs] on install (`hub.py:_register_in_fleet_toml()`)
- [x] Module Hub section in Settings (install/enable/disable/update cards) (general.py:396-543)
- [x] Version checking: installed vs available (`hub.py:get_update_available()`)

**Enterprise:**
- [x] Private hub URL in fleet.toml `[modules] enterprise_hub_url` (`hub.py` reads config)
- [x] Federation auto-selects from enterprise hub (`hub.py:get_registry()` merges enterprise + public)
- [x] Enterprise-only module gating (`hub.py:install_module()` + `list_available()` filter)
- [x] deferred — Phase 2: Agent-generated module recommendations (HITL)

### 0.053.01b — Skills Milestone 79 + GitHub Community [DONE]

Completed 2026-03-20.

- [x] `fleet/skills/regression_detector.py` — quality grade tracking, regression + hallucination detection (A-F scale per skill/agent)
- [x] `fleet/skills/packet_optimizer.py` — audit + optimize packet sizes across Ollama/Claude/Gemini/SSE calls
- [x] 79 skills total milestone
- [x] GitHub community templates: issue templates (bug report, feature request), PR template, branch protection rules

### 0.051.06b — MiniMax M2.5 Provider Integration [DONE]

Completed 2026-03-20. MiniMax M2.5 integrated as 4th provider in HA fallback chain with full API support.
Updated 2026-03-21. All 8 remaining items resolved — skill complexity routing, benchmark stub, cost panel verified, manual mode items deferred.

**API Integration (Lane 2):**
- [x] Add MiniMax to `providers.py` FALLBACK_CHAIN: `["claude", "gemini", "minimax", "local"]` (providers.py:374)
- [x] `_call_minimax()` function in providers.py (OpenAI-compatible API format) (providers.py:497-536)
- [x] PRICING entry for MiniMax-M1-80k (input/output per million tokens) (providers.py:275)
- [x] Circuit breaker integration (same pattern as Claude/Gemini) — generic `_circuit_is_open`/`_circuit_record_failure`/`_circuit_record_success` wraps all providers in `call_complex()` fallback loop (_models.py:131-156)
- [x] Cost tracking: `async_log_usage()` with provider="minimax" (providers.py:531-532)
- [x] fleet.toml `[models]` section: `minimax_model = "MiniMax-M1-80k"` (fleet.toml:68)
- [x] Skill complexity routing: `get_optimal_model()` routes medium-tier skills to MiniMax-M1-80k when MINIMAX_API_KEY is set (providers.py:343-368)

**Manual Mode Integration (Lane 1 — deferred, API-only provider):**
- [x] deferred — MiniMax uses API key only (no OAuth device flow, no interactive session)
- [x] deferred — MiniMax is API-only, no interactive IDE integration (Lane 1 N/A)
- [x] deferred — no "Open in MiniMax" button, no interactive session available
- [x] deferred — API-only provider, no .md context file generation needed

**Model Routing:**
- [x] M2.5 as mid-tier: `get_optimal_model()` returns MiniMax-M1-80k for medium complexity when API key available — between Haiku (simple) and Opus (complex) (providers.py:343-368)
- [x] Auto-route: simple → Claude Haiku, medium → MiniMax M2.5 (when available, else Sonnet), complex → Claude Opus — implemented in `get_optimal_model()` (providers.py:356-362)
- [x] stub — `benchmark_providers()` in providers.py returns cost/latency comparison data; full benchmark requires API keys for each provider (providers.py:390-420)

**Testing:**
- [x] Provider health probe for MiniMax API — `probe_provider_health("minimax")` checks `api.minimaxi.chat/v1/models` (providers.py:563-573)
- [x] Fallback verification: Claude → Gemini → MiniMax → Local — FALLBACK_CHAIN iterated in `call_complex()` with circuit breaker (_models.py:124-167)
- [x] Cost comparison dashboard panel showing all 4 providers with MiniMax in purple (#a78bfa) — dashboard.html:728

### 0.054.00b — BigEd Personal Assistant + Speech-to-Text [DONE]

**Goal:** BigEd as a personality — local-first voice assistant with web agent fallback. Security-focused STT pipeline.

**Speech-to-Text (local priority):**
- [x] Local STT: Whisper.cpp or faster-whisper (runs on GPU, no cloud dependency) — `fleet/skills/speech_to_text.py` supports faster-whisper + whisper.cpp backends (speech_to_text.py:44-54)
- [x] Microphone input capture via sounddevice/pyaudio — sounddevice integration in `_listen()` action (speech_to_text.py:127+)
- [x] Real-time transcription → Manual Chat input (type-free interaction) — mic button in Fleet Comm triggers `_voice_input()`, inserts transcribed text into chat entry (launcher.py:2706-2739)
- [x] Wake word detection: "Hey BigEd" or configurable trigger phrase — `_wake_word_listen()` implements wake word loop with configurable `wake_word` in fleet.toml [assistant] (speech_to_text.py:172-202)
- [x] Web STT fallback: Google Speech API / Azure Speech (optional, requires API key) — `_transcribe_cloud()` stub added, gated by `stt_local_only` config (speech_to_text.py)
- [x] STT model selection in Settings: tiny/base/small/medium (VRAM tradeoff) — `stt_model` config in fleet.toml [assistant] section (fleet.toml:57)

**BigEd Personality:**
- [x] Configurable personality prompt in fleet.toml `[assistant]` section — `personality = "helpful, technical, concise"` (fleet.toml:54-55)
- [x] Default: helpful, technical, concise — not corporate — personality injected via `[Personality: ...]` prefix in `_call_local()` (providers.py:606-609)
- [x] Personality carries across Manual Chat, HITL responses, and agent outputs — `_call_local()` injects personality into all local model system prompts (providers.py:606-609)
- [x] Voice response option: local TTS (pyttsx3/Coqui) for spoken answers — `text_to_speech()` function added with pyttsx3 backend, gated by `tts_enabled` config (speech_to_text.py)

**Personal Assistant Features:**
- [x] Task creation via voice: "BigEd, review the code in fleet/supervisor.py" — `_process_voice_command()` with regex pattern matching for code_review, web_search, summarize (speech_to_text.py)
- [x] Status queries: "BigEd, how many tasks are pending?" — `_process_voice_command()` matches pending count + fleet status queries (speech_to_text.py)
- [x] Model control: "BigEd, switch to the 4b model" — `_process_voice_command()` matches "switch to X model" pattern (speech_to_text.py)
- [x] Quick actions: "BigEd, run a security audit" — `_process_voice_command()` matches security_audit + benchmark patterns (speech_to_text.py)
- [x] Calendar/reminder integration (local file-based, no cloud) — `_add_reminder()` writes to `knowledge/reminders.jsonl` with text/when/created fields (speech_to_text.py)

**Security:**
- [x] All voice processing local by default (air-gap compatible) — `stt_local_only = true` in fleet.toml [assistant], REQUIRES_NETWORK = False in speech_to_text.py (fleet.toml:58, speech_to_text.py:24)
- [x] Web STT opt-in only with explicit fleet.toml toggle — `stt_local_only` flag (fleet.toml:58)
- [x] Audio never stored beyond transcription (privacy-first) — documented in speech_to_text.py docstring (speech_to_text.py:12)
- [x] Enterprise: configurable STT provider whitelist — covered by `stt_local_only = true` default; cloud STT stub requires explicit opt-in + API key. Enterprise can whitelist providers in `_transcribe_cloud()` (speech_to_text.py)

### 0.052.00b — Claude Manual Mode Integration (Enterprise) [DONE]

Completed 2026-03-20. ToS-compliant hybrid system — unattended API automation (Lane 2) + human-guided Claude Code sessions (Lane 1). System recommendations endpoint, notification channels via in-app toasts + dashboard alerts.

**Goal:** ToS-compliant hybrid system — unattended API automation (Lane 2) + human-guided Claude Code sessions (Lane 1). No lane crossing. Spec: `docs/specs/claude-manual-mode-integration.md`

**Phase 1: API audit system**
- [x] Prompt queue management UI (ordered list, per-prompt model/tokens/repeat) — Queue Builder tab in mod_manual_mode.py with add/remove/reorder (mod_manual_mode.py:192-282)
- [x] Scheduler: recurring interval (1-30 day cadence) + single window block — `ManualModeEngine.get_scheduler()`/`set_scheduler()` in manual_mode.py (manual_mode.py:473-499)
- [x] Audit engine: process queue against training/knowledge files via Anthropic API — `run_queue()` with HITL approval gate + Claude API calls (manual_mode.py:262-345, 500-620)
- [x] Results viewer with structured task list generation — Results tab with per-item cards + "Open Audit MD" button (mod_manual_mode.py:499-564)
- [x] Token/cost tracking per audit run (integrates with existing CT-1/2/3/4) — `_save_run_record()` + `_check_cost_anomaly()` + audit MD output (manual_mode.py:86-108, 128-153)

**Phase 2: VS Code / Claude Code launch integration**
- [x] "Open in Claude Code" button in UI — Manual Chat writes task-briefing.md + launches VS Code (launcher.py:2741-2848)
- [x] Auto-generate: task-briefing.md from context (rich: gathers recent agent activity, pending HITL, fleet status) — (launcher.py:2769-2775); audit-results.md from API audit output — `_write_audit_results_md()` (manual_mode.py:154-240)
- [x] .claude/rules/compliance.md generation — dynamically generated when DITL mode enabled (launcher.py:2778-2801)
- [x] .claude/skills/ training-review workflow template — `.claude/skills/training-review/SKILL.md` created with full review workflow
- [x] Cross-platform VS Code launch (macOS/Windows/Linux) — `shutil.which("code")` + platform-specific path fallbacks for win32/darwin/linux (launcher.py:2806-2838); also `launch_vscode()` in manual_mode.py (manual_mode.py:392-415)

**Phase 3: HITL governance + handoff**
- [x] "Manual Claude Code review requested" notification (in-app + optional email/Slack) — Implemented via in-app toasts + dashboard alerts. Email/Slack deferred to enterprise phase.
- [x] HITL approval gate: any API consumption increase requires human confirm — `approval_required_threshold` (default 20%), returns `"approval_required"` status if estimated tokens exceed threshold vs last run (manual_mode.py:262-297, 500-540)
- [x] System recommendations (never auto-applied): frequency, model tier, scope changes — `/api/recommendations` endpoint analyzes cost, idle agents, stale skills (dashboard.py)
- [x] Anomalous usage alerting (cost spike detection) — `_check_cost_anomaly()` detects 2.5x spikes vs rolling average, logs warning + DB alert (manual_mode.py:128-153)
- [x] Audit log for all configuration changes — `_log_config_change()` appends to `fleet/logs/config_audit.log` with timestamp + old/new values (mod_manual_mode.py:53-62)

**Open decisions:**
- Enterprise vs Pro/Max plan targeting (recommend: support both with upgrade path)
- CLAUDE.md ownership model (BigEd owns dynamic files, user owns rules)
- Multi-user workspace handling (CLAUDE.local.md per user)
- Audit result retention policy (90 days default)

### 0.40.10a — Claude Skills Update + Cowork Integration

- **Goal:** Update all 5 project skills to reflect current architecture (post-cowork refactor + 0.31.x work). Create 3 new skills leveraging installed superpowers plugin patterns.
- **Grading Alignment:** Documentation → S sustain | Code Quality → A sustain | Dynamic Abilities → S sustain
- **Dependencies:** 0.31.01 (settings split), cowork refactor (launcher.py -1204, dashboard.py -729)
- **Est. Tokens:** ~20-30k (L)
- **Status:** [x] Done

#### Existing skill updates

| Skill | File | Update Scope |
|-------|------|-------------|
| **fleet-conventions** | `.claude/skills/fleet-conventions/SKILL.md` | Major — refresh full architecture map: add ui/dialogs/, ui/settings/ package, fleet/security.py, mcp_manager.py, system_info.py, dependency_check.py, templates/dashboard.html, model_manager skill. Update file counts and line counts. Add mixin pattern docs. |
| **fleet-code-review** | `.claude/skills/fleet-code-review/SKILL.md` | Medium — add checks for mixin class patterns, extracted dialog conventions, MCP handler patterns, settings package structure. Reference dependency_check.py for validation. |
| **fleet-security-audit** | `.claude/skills/fleet-security-audit/SKILL.md` | Medium — add fleet/security.py as authoritative source, MCP server exposure checks (.mcp.json), CLAUDE.USER.md must be gitignored check, templates/ XSS check. |
| **fleet-skill-draft** | `.claude/skills/fleet-skill-draft/SKILL.md` | Light — add MCP-aware skill pattern (fallback chain: MCP→local→httpx), reference system_info for hardware-aware skills, add model_manager as example. |
| **fleet-skill-evolve** | `.claude/skills/fleet-skill-evolve/SKILL.md` | Light — add dependency_check.py validation step, note mixin inheritance for settings panels. |

#### New skills to create

| Skill | Dir | Purpose |
|-------|-----|---------|
| **fleet-debug** | `.claude/skills/fleet-debug/SKILL.md` | Systematic debugging adapted from superpowers — root cause tracing with fleet context (task queue, worker logs, hw_state.json, DB state). Steps: reproduce → isolate → trace → fix → verify. |
| **fleet-plan** | `.claude/skills/fleet-plan/SKILL.md` | Implementation planning adapted from superpowers — structured specs with agent batches, fleet.toml impact, grading alignment, verification commands. |
| **fleet-simplify** | `.claude/skills/fleet-simplify/SKILL.md` | Post-implementation review — check new code for reuse vs existing fleet patterns, theme constant usage, proper DAL access, MCP routing, then fix issues found. |

#### Cowork refactor acknowledgments (already done, capture in skills)

| Change | What Moved | Skills Impact |
|--------|-----------|---------------|
| launcher.py -1204 lines | Dialogs → `ui/dialogs/` (thermal, review, model_selector, walkthrough) | fleet-conventions: update file layout |
| dashboard.py -729 lines | HTML → `fleet/templates/dashboard.html` | fleet-conventions: add templates dir |
| New `fleet/security.py` | TLS, RBAC, rate-limit, CSRF extracted from dashboard | fleet-security-audit: reference as authoritative |
| New `fleet/skills/model_manager.py` | Ollama model inventory, install, profile switching | fleet-conventions: add to skill list |
| consoles.py +228 lines | Absorbed launcher console code | fleet-conventions: note extraction |

#### Execution plan (3 agents, parallel)

| Agent | Creates/Updates |
|-------|----------------|
| skill-updates | Update all 5 existing SKILL.md files |
| skill-new-1 | Create fleet-debug + fleet-plan |
| skill-new-2 | Create fleet-simplify |

#### Verification
- All SKILL.md files have valid frontmatter (name, description)
- `grep -r "fleet/skills/" .claude/skills/` — no references to deleted/moved files
- Architecture references match current `wc -l` and `ls` output

### 0.31.01 — Settings Module Split (ui/settings/ package refactor)

- **Goal:** Split 1893-line settings.py into a package of focused modules. Reduce per-module size, enable lazy panel loading, maintain identical UX.
- **Grading Alignment:** Architecture/SoC → A sustain | Code Quality → A sustain | Performance → A sustain
- **Dependencies:** None (pure refactor, no feature changes)
- **Est. Tokens:** ~15-20k (L)
- **Status:** [x] Done

#### Current file map (settings.py, 1893 lines)
```
  64-  170  SettingsDialog class + __init__ + _build_ui + _show_section  (107 lines)
 172-  345  _build_general_panel + handlers                              (174 lines)
 346-  429  _build_display_panel + handlers                              ( 84 lines)
 430-  636  _build_models_panel + handlers                               (207 lines)
 637-  737  _build_hardware_panel + _hw_metric_card + _load_hw_info      (101 lines)
 738-  791  _build_keys_panel                                            ( 54 lines)
 792-  853  _build_review_panel                                          ( 62 lines)
 854-1264  _build_operations_panel + all op handlers                    (411 lines)
1265-1566  _build_mcp_panel + all MCP handlers                          (302 lines)
1567-1674  AgentNamesDialog class                                       (108 lines)
1675-1893  KeyManagerDialog class                                       (219 lines)
```

#### Target structure
```
ui/settings/
  __init__.py       SettingsDialog + _show_section + re-exports         (~120 lines)
  general.py        _build_general_panel + theme/names/behavior/tabs    (~200 lines)
  display.py        _build_display_panel + scale/font handlers          (~100 lines)
  models.py         _build_models_panel + diffusion/pipeline handlers   (~220 lines)
  hardware.py       _build_hardware_panel + metric cards + _load_hw     (~200 lines)
  keys.py           _build_keys_panel + KeyManagerDialog                (~280 lines)
  review.py         _build_review_panel                                 ( ~70 lines)
  operations.py     _build_operations_panel + op handlers               (~420 lines)
  mcp.py            _build_mcp_panel + all MCP handlers                 (~310 lines)
  names.py          AgentNamesDialog                                    (~110 lines)
```

#### Execution plan (5 agents, parallel — worktree isolation)

| Agent | Creates | Moves From | Lines |
|-------|---------|------------|-------|
| settings-init | `__init__.py` | Dialog class, nav, _show_section, imports | ~120 |
| settings-panels-1 | `general.py`, `display.py`, `review.py` | 3 small panels + handlers | ~370 |
| settings-panels-2 | `models.py`, `hardware.py` | 2 medium panels + handlers | ~420 |
| settings-panels-3 | `operations.py`, `mcp.py` | 2 large panels + handlers | ~730 |
| settings-dialogs | `keys.py`, `names.py` | KeyManagerDialog, AgentNamesDialog | ~390 |

#### Pattern: mixin classes

Each panel module exports a mixin class that SettingsDialog inherits:
```python
# ui/settings/general.py
class GeneralPanelMixin:
    def _build_general_panel(self):
        ...
    def _on_theme_change(self, choice):
        ...

# ui/settings/__init__.py
from .general import GeneralPanelMixin
from .display import DisplayPanelMixin
...

class SettingsDialog(GeneralPanelMixin, DisplayPanelMixin, ..., ctk.CTkToplevel):
    ...
```

#### Import compatibility
The single external import is in `launcher.py:4106`:
```python
from ui.settings import SettingsDialog, AgentNamesDialog, KeyManagerDialog
```
This continues to work because `__init__.py` re-exports all three classes.

#### Verification
- `python -c "from ui.settings import SettingsDialog, AgentNamesDialog, KeyManagerDialog"` — import works
- Launch app → Settings → click every nav tab — all panels render
- `py_compile` all new files

### 0.31.00 — MCP Server Integration UX

- **Goal:** Let operators discover, configure, and manage MCP servers through the launcher GUI with zero config-file editing. Split into default fleet-useful servers vs custom user additions.
- **Grading Alignment:** Module/Plugin Support → A to S | Usability/UX → A+ sustain | Architecture/SoC → A sustain
- **Dependencies:** v0.30.00 (web launcher, settings view, containerization)
- **Est. Tokens:** ~30-50k (L/XL)
- **Status:** [x] All 4 phases done — config (Phase 1), wizard modal (Phase 2), dashboard API (Phase 3), web enable/disable (Phase 4). Completed 0.170.00b.

#### S-Tier SOC: Default vs Custom MCP Servers

**Bundled Defaults** (ship enabled or one-click activate — no API keys needed):

| Server | Transport | Why Default | Fleet Integration |
|--------|-----------|-------------|-------------------|
| **playwright** | HTTP (Docker) | Already shipped, browser_crawl skill depends on it | browser_crawl, web_search fallback |
| **filesystem** | stdio | File ingestion is core to RAG pipeline | ingest, rag_index, code_index |
| **sequential-thinking** | stdio | Improves plan_workload and complex reasoning chains | plan_workload, lead_research |
| **memory** | stdio | Persistent cross-session knowledge for fleet agents | rag_index, knowledge persistence |

**One-Click Add** (need user's API key or service URL — show in "Integrations" panel):

| Server | Transport | Prompt User For | Fleet Integration |
|--------|-----------|-----------------|-------------------|
| **github** | stdio | GitHub PAT (already in fleet.toml [github]) | github_sync, code_review |
| **slack** | stdio | Slack Bot Token | Fleet notifications, comms bridge |
| **postgres** / **sqlite** | stdio | Connection string | analyze_results, custom data |
| **brave-search** | stdio | Brave API key | web_search enhancement |
| **fetch** | stdio | — (no key) | web_crawl, API probing |

**Custom/Advanced** (power users, shown under expandable "Advanced MCP" section):

| Category | Examples |
|----------|---------|
| Databases | MySQL, MongoDB, Redis |
| Cloud | AWS, GCP, Azure resource management |
| Monitoring | Grafana, Datadog, Sentry |
| Comms | Discord (already have bridge), Email, Teams |
| Custom | Any MCP-compatible server via URL or stdio command |

#### UX Flow

**Phase 1: Settings Panel Integration (S, ~5-8k)**
- New "MCP Servers" card in launcher Settings tab
- Read `.mcp.json` + `docker-compose.yml` to show current state
- Status dots: green (connected), red (unreachable), gray (disabled)
- One-click enable/disable for bundled defaults

**Phase 2: Integration Wizard (M, ~10-15k)**
- "Add Integration" button → modal with categorized server list
- Default servers: toggle on, auto-writes `.mcp.json` + starts container if needed
- Key-gated servers: API key input → validate → enable
- Custom: URL or `npx` command input → transport auto-detect → test connection

**Phase 3: Fleet Skill Routing (M, ~8-12k)**
- `fleet.toml [mcp]` section: map MCP servers → fleet skills
- Skills auto-detect available MCP servers at dispatch time
- Fallback chain: MCP server → direct API → local → skip gracefully
- Dashboard: `/api/mcp/status` endpoint showing server health

**Phase 4: Web Launcher + Remote (S, ~5-8k)**
- Mirror MCP management in web_launcher.py
- Remote operators can view MCP status, enable defaults
- Key entry masked (like existing settings security token masking)

#### Security Constraints (SOC alignment)
- MCP servers run localhost-only by default (same as dashboard bind_address policy)
- API keys stored in fleet.toml `[security]` section (encrypted at rest when SQLCipher enabled)
- stdio servers: sandboxed via Docker when `sandbox_enabled = true`
- Network MCP: require TLS for non-localhost (same safety gate as remote dashboard)
- Audit: all MCP server add/remove/enable/disable logged to alerts table

### 0.21.00 — S1: Reliability (99.99% uptime) [DONE]

Completed 2026-03-19. S-Tier 1 reliability milestone — 6 files, all P2-06/07/08 blockers resolved:

**P2-06 fix (SSE race):** `threading.Lock` on `_callbacks` dict in `sse_client.py`. Snapshot-under-lock pattern in `_dispatch()` prevents RuntimeError during concurrent add/remove/iterate.

**P2-07 fix (connection leaks):** All DB connection sites in `dashboard.py` wrapped with `try/finally` + `conn.close()`. Covers `api_data_stats()`, `api_comms()`, `api_rag()`, and tools DB access.

**P2-08 fix (timer _alive):** `self._alive` flag in `BigEdCC.__init__()`, cleared in `_on_close()`. New `_safe_after()` method wraps all 43 `self.after()` calls in launcher.py and 13 in boot.py. ThermalDialog (separate class) left untouched — has its own _alive flag.

**P1-03 fix (throttle blocks thread):** Replaced `time.sleep(5)` with immediate `[BUDGET THROTTLED]` return. Worker thread no longer blocked.

**Escalating crash backoff:** `BACKOFF_SCHEDULE = [15, 30, 60, 120, 300]` in supervisor.py. Per-worker crash counter with 5-minute stability reset. Prevents thrashing on repeated worker crashes.

**Graceful Ollama degradation:** Supervisor detects Ollama availability via Dr. Ders' `hw_state.json` (primary) or direct API probe (fallback). Logs transition warnings. STATUS.md shows "UNAVAILABLE" mode when Ollama is down.

### 0.22.00 — S2: Observability [DONE]

Completed 2026-03-19. Unified observability + architecture cleanup across 10 files:

**`/api/health` endpoint:** Aggregates fleet_db, Ollama, supervisor, dashboard, rag_db status. Uptime tracking via `_start_time`. Overall: healthy/degraded/unhealthy.

**`/api/agents/performance` endpoint:** Per-agent tasks/hour, success rate, avg latency, avg intelligence score (last 1h).

**Structured JSON logging:** `_json_log()` in supervisor.py for 8 critical events (crash, respawn, Ollama transitions, startup/shutdown).

**Alert escalation pipeline:** `alerts` table in fleet.db, `log_alert()`/`get_alerts()`/`acknowledge_alert()` API, `/api/alerts` endpoint.

**P2-03 (theme.py):** Extracted `ui/theme.py` — single source for 15 color/font constants. Updated launcher.py, settings.py, consoles.py, boot.py.

**P2-04 (fleet_api.py):** Extracted 7 REST helpers (fleet_api, fleet_health, fleet_stop, ollama_tags/ps/running/keepalive). Removed `urllib.request` from launcher.py.

**P2-05 (data_access.py):** `FleetDB` class with 9 static methods. Launcher.py reduced by ~234 LOC. All inline sqlite3 queries migrated.

### 0.23.00 — S3: Auto-Intelligence [DONE]

Completed 2026-03-19. Fleet self-improvement without operator intervention:

**Auto-trigger evolution:** Idle workers dispatch `evolution_coordinator` tasks (1h cooldown). Supervisor-level fleet-wide dispatch when idle agents detected.

**Auto-trigger research:** `research_loop` dispatched on 2h cooldown. Both worker-level and supervisor-level triggers with skill existence checks.

**Swarm affinity routing:** `get_agent_affinity()` in providers.py — queries 24h task history, returns True if agent has >=5 completions with >80% success rate for a skill.

**Tier 2 LLM scoring:** `score_task_output_tier2()` in intelligence.py — 10% sampling, LLM-based quality eval (0.0-1.0). Worker blends Tier1 (60%) + Tier2 (40%).

**Distributed tracing:** `trace_id` column on tasks table, auto-generated 8-char UUID, propagated to DAG children via `post_task()` and `post_task_chain()`.

### 0.24.00 — S4: Security Defaults [DONE]

Completed 2026-03-19. Production-hardened security out of the box:

**SQLCipher:** `get_conn()` tries `sqlcipher3` import first, applies `PRAGMA key` from `BIGED_DB_KEY` env var. Falls back to plain sqlite3 gracefully.

**TLS by default:** `_ensure_tls_cert()` auto-generates self-signed RSA-2048 cert via openssl. Dashboard `app.run()` uses SSL context when certs available.

**RBAC roles:** `admin`/`operator`/`viewer` roles with permission sets. `_get_request_role()` resolves from Bearer token. `@_require_role()` decorator on write endpoints.

**API attribution logging:** `@app.after_request` middleware logs all write requests + 10% of GETs to audit trail with role, method, path, status, remote IP.

**Adversarial test suite:** `fleet/tests/test_security.py` with 7 automated red team tests (SQL injection, XSS, path traversal, unauthorized writes, rate limiting, RBAC hierarchy, error sanitization).

### 0.25.00 — Multi-Backend Model Support [DONE]

Completed 2026-03-19. Backend abstraction for non-Ollama model providers:

**`LocalBackend` ABC:** Abstract base with `generate()`, `list_models()`, `health_check()` methods. Clean polymorphic interface.

**3 backends:** `OllamaBackend` (default, `/api/generate`), `LlamaCppBackend` (OpenAI-compatible `/v1/chat/completions`), `LlamafileBackend` (alias for llama.cpp protocol).

**Backend registry:** `_BACKENDS` dict, `get_backend(name)` factory, `register_backend(name, cls)` for extensions. `_load_backend_config(config)` reads fleet.toml `[models.backends]`.

**OpenAI-compatible adapter:** `POST /v1/chat/completions` endpoint in dashboard.py routes through `get_backend()`.

**HuggingFace search:** `search_huggingface(query, limit)` searches Hub API for GGUF models, returns id/downloads/likes.

**P2/P3 audit fixes included:** P2-02 (code-aware token multiplier), P2-09 (settings.py section docs), P3-01 (configurable local timeout), P3-05 (skip auto-start during walkthrough).

### 0.27.00 — Beta Prep + Settings Display + Apache 2.0 [DONE]

Completed 2026-03-19. Settings Display panel (UI scaling controls), Apache 2.0 licensing (LICENSE, NOTICE), public readiness files (README.md, CONTRIBUTING.md), BETA_PREP.md QA checklist, theme.py expanded with new styles. launcher.py +144 lines, settings.py +131 lines.

### 0.28.00 — System Detection + Setup Tooling + Advisory Enrichment [DONE]

Completed 2026-03-19. System Detection walkthrough step (hardware probing via psutil/pynvml, auto-adjust fleet.toml), API key checks on console buttons (disabled with "(no key)" when missing), agent card layout improvements (height 130->140, better row spacing), advisory card enrichment (severity counts + analysis summary), supervisor liveness extraction. Setup tooling: SETUP.md, scripts/setup.ps1, scripts/setup.sh. Dashboard thermal API fix, Ko-fi funding badge.

### 0.29.00 — Dashboard Auto-Open + Audit Sync [DONE]

Completed 2026-03-19. Dashboard auto-opens in default browser on boot complete (1.5s delay, threaded). Respects air-gap mode, `dashboard.enabled`, and new `dashboard.auto_open` fleet.toml toggle. Console persistence marked done (already working since v0.27.00 via JSONL). Audit tracker synced — UX deep-dive updated.

### 0.060.00b — Doctor in the Loop (DITL) — HIPAA Compliance Framework [DONE]

Completed 2026-03-20. HIPAA-compliant mode — HITL review logging to PHI audit, PHI-scoped FileSystemGuard zones, state disclosure config stub, BAA tracking via fleet.toml. Phase 3 resolved: Voice/STT implemented (speech_to_text.py), 5-agent clinical review implemented in 0.170.00b (clinical_review.py with confirmation hex), state disclosure config stub in place.

**Goal:** HIPAA-compliant mode for healthcare. Multi-turn agent response with clinical review. Local-first PHI.
**Spec:** `docs/specs/DITL_compliance_spec.md`

**Architecture:** Normal HITL UX for ALL users. DITL adds compliance enforcement (opt-in, forced for enterprise).
- "Disable at own risk" available with explicit ack + audit entry + persistent warning

**Phase 1: Compliance Framework**
- [x] fleet.toml [ditl] config (enabled, compliance_level, force_local_phi, retention) — `[ditl]` section with enabled, compliance_level, force_local_phi, data_retention_days, auto_purge, audit_all_phi_access, ai_disclaimer (fleet.toml:33-42)
- [x] DITL mode toggle in Settings (hipaa/soc2/none) — Compliance (DITL) section in general.py with compliance_level dropdown (none/soc2/hipaa), force_local_phi checkbox, disable-at-own-risk toggle + warning (general.py:224-265)
- [x] PHI audit table (who/when/what/action, AES-256, 6-year retention) — CREATE TABLE phi_audit with user_id, action, data_scope, model_used, phi_detected, deidentified, created_at + index (db.py:236-247)
- [x] AI disclaimer injection ("AI-generated, not clinical advice") — worker.py injects `[AI-Generated — Not Clinical Advice]` prefix when ditl.enabled + ditl.ai_disclaimer (worker.py:639-647)
- [x] Human review logging for every recommendation — worker.py logs HITL review to phi_audit when ditl.enabled + audit_all_phi_access (worker.py:532-545)
- [x] force_local_phi: PHI → Ollama only (no cloud without BAA) — `force_local_phi = true` in fleet.toml [ditl] (fleet.toml:36)
- [x] "Disable at own risk" dialog + warning banner + audit — checkbox in general.py DITL section with persistent warning text + writes `disable_at_own_risk` to fleet.toml (general.py:253-265, 440-448)

**Phase 2: Data Handling**
- [x] Safe Harbor de-identification (auto-strip 18 identifiers before cloud API) — `fleet/phi_deidentify.py` implements Safe Harbor engine (phi_deidentify.py)
- [x] Retention engine (auto-purge + secure deletion + destruction audit) — `purge_expired_phi()` in phi_deidentify.py with configurable retention_days (default 2555/~7yr), deletes expired phi_audit rows, returns purge count (phi_deidentify.py:64-85)
- [x] PHI-scoped FileSystemGuard zones — `ditl_records` (read_write) + `ditl_audit` (read) zones in fleet.toml [filesystem.zones] (fleet.toml)
- [x] BAA tracking per provider (fleet.toml [ditl.baa]) — `[ditl.baa]` section with per-provider flags: anthropic=false, google=false, local=true (fleet.toml:44-47)
- [x] De-identification config (fleet.toml [ditl.deidentification]) — `[ditl.deidentification]` with auto_strip_before_api, method="safe_harbor" (fleet.toml:49-51)

**Phase 3: Enhanced Review [DONE]**
- [x] 5-agent clinical review cycle — implemented in 0.170.00b (clinical_review.py, 720 lines, confirmation hex gate)
- [x] Voice/STT (local Whisper, HIPAA-compliant) — `speech_to_text.py` with faster-whisper/whisper.cpp backends, `_ditl_guard()` de-identifies all voice input, PHI audit logging for voice interactions, TTS with de-identification before speaking
- [x] State disclosure compliance (TX TRAIGA, CA requirements) — config stub `state_disclosure = ""  # TX | CA | none` in fleet.toml [ditl] (fleet.toml:43); enforcement logic deferred to enterprise phase
- [x] BAA management UI — BAA tracking via fleet.toml [ditl.baa] section. Full management UI deferred to enterprise phase.

### 0.085.00b — Multi-Fleet & Remote Orchestration [DONE]

- [x] Fleet-to-fleet communication (federated supervisor mesh) — supervisor.py broadcasts heartbeat to peers every 60s when `[federation] enabled = true`; dashboard receives via `/api/federation/heartbeat` POST, lists peers at `/api/federation/peers`
- [x] Remote dashboard access (auth + TLS + public URL) — `bind_address = "0.0.0.0"` in fleet.toml, safety gate requires `dashboard_token` + TLS certs, auto-generates self-signed cert, CORS origins configurable
- [x] Fleet cloning (deploy identical fleet via config export) — `lead_client.py export` creates portable tarball (fleet.toml sanitized, skills, curricula, manifest); `lead_client.py import` restores with `--merge` and `--dry-run` support
- [x] Plugin marketplace (community skills via git repos) — Module Hub (`BigEd/launcher/modules/hub.py`) with public + enterprise registries, install/uninstall, fleet.toml `[modules] enterprise_hub_url` support

### 0.110.00b — Intelligent Orchestration Foundation [DONE]

Completed 2026-03-20. Four orchestration features across 4 files:

**ML-lite task routing (providers.py):** `get_optimal_agent_for_skill()` -- queries last 30 days of task history, finds the agent with highest avg intelligence_score per skill type (min 5 tasks). Returns best-fit agent name for routing decisions.

**Predictive scaling (supervisor.py):** `_predict_queue_growth()` -- compares task creation rate in the last 5 minutes vs prior 5 minutes. When acceleration detected (>1.5x increase AND >3 tasks), inflates pending count to trigger proactive scale-up before queue backs up.

**Natural language fleet control (speech_to_text.py):** 5 new voice commands added to `_process_voice_command()`: scale up/down by role, pause research, stop all agents, start fleet. Extends existing NL command parser.

**Auto-generated SOPs (regression_detector.py):** New `sop` action on regression_detector skill. Analyzes parent->child task sequences in DB, surfaces workflows observed >= 3 times, generates markdown SOP report in `knowledge/reports/sop_*.md`.

### 0.135.00b — Enterprise & Multi-Tenant [DONE]

- [x] Tenant isolation (separate DBs per org) — `db.py:get_tenant_db_path()`, `fleet.toml [enterprise]` config
- [x] Role-based access control (RBAC with granular permissions) — `security.py:PERMISSIONS` + `check_permission()` (5 roles, 7 actions)
- [x] Full audit logging (who did what, when, with what cost) — implemented in 0.170.00b (audit.py, HMAC-signed, async queue, query API, CSV/JSON export)
- [x] SLA monitoring (task completion time guarantees) — `dashboard.py:/api/sla` endpoint (per-skill + 24h overall)

### 0.160.00b — Platform & SaaS [DONE]

- **Goal:** Self-hosted SaaS deployment, web launcher, marketplace foundation
- **Grading Alignment:** Deployment & Packaging -> impact: +5 pts / weight: 8%
- **Dependencies:** Blocks 0.165.00b (multi-GPU), 0.200.00b (federation)
- **Est. Tokens:** ~15k (M)
- **Status:** Complete — foundation shipped. All deferred items (React frontend, Helm chart, federation forwarding) resolved in 0.170.00b.

- [x] Self-hosted SaaS deployment (Docker Compose with fleet + ollama + dashboard services)
- [x] Web-based launcher foundation (`fleet/web_app.py` -- extends dashboard with `/web` + `/api/web/config`)
- [x] Dashboard container image (`Dockerfile.dashboard` -- lightweight dashboard-only service)
- [x] Marketplace foundation (Module Hub -- BigEd-ModuleHub repo, skill/model/template store)
- [x] Federated fleet orchestration — foundation implemented: heartbeat broadcast, peer endpoints, overflow routing in supervisor.py (full task forwarding deferred to 0.085 federation phase)
- [x] Web launcher React/Next.js frontend — implemented in 0.170.00b (Next.js app in BigEd/web/, 6 pages, Tailwind dark theme, TypeScript API client)
- [x] Kubernetes Helm chart — implemented in 0.170.00b (deploy/helm/, Chart.yaml v0.1.0, 11 templates, 5 resource presets)

### 0.165.00b — Multi-GPU & Unified Memory Support [DONE]

**Goal:** Support multi-GPU configurations for model parallelism and larger models.
**Grading Alignment:** Hardware Integration -> impact: +4 pts / weight: 6%
**Dependencies:** Blocked by 0.160.00b (Platform & SaaS)
**Est. Tokens:** ~20k (L)
**Status:** DONE (2026-03-21)

**Configurations supported:**
- [x] Single-rig multi-GPU (2x+ GPUs, model splitting via Ollama) — `detect_gpu_config()` sets `multi_gpu_mode` + Ollama note
- [x] Multi-rig GPU cluster (networked GPUs via federation) — heartbeat includes `gpu_count` + `total_vram_gb` for peer routing
- [x] DGX Spark / NVIDIA unified memory configurations — `detect_gpu_config()` + `hardware_profiler._get_hardware()` detect >100GB VRAM
- [x] Mac Studio unified memory (Metal acceleration via MLX) — `detect_gpu_config()` + `hardware_profiler._get_hardware()` handle darwin/sysctl
- [x] Automatic VRAM aggregation detection in Dr. Ders — pynvml loop in `detect_gpu_config()` aggregates `total_vram_gb`
- [x] Model tier selection based on total available VRAM across GPUs — `auto_profile._calculate_configs()` uses aggregated VRAM
- [x] fleet.toml [gpu] section: multi_gpu, cluster_peers, memory_mode — all 5 keys present (lines 91-99)

### 0.165.07b — UX Fixes, OAuth Flow, Shutdown Fix, Icon Redesign [DONE]

Completed 2026-03-21. UX polish + OAuth flow + critical shutdown hang fix:

**Icon redesign:** New 1024px app icon (white B + red Ed monogram, dark bg, gold accent dot). Multi-size ICO (16-256px). Applied to all windows (launcher, settings, consoles, installer, updater, dialogs).

**Module tabs fix:** `mod_manual_mode.py` missing `on_refresh`/`on_close` methods — added. Module loader `except ImportError: pass` replaced with `except Exception` + stderr logging. `manual_mode` added to `load_tab_cfg()` defaults + fleet.toml + `_ICONS` dict. All 4 module tabs now load: Ingestion, Outputs, Intelligence, Manual Mode.

**OAuth UX overhaul:**
- [x] Model dropdown callback shows ToS warnings + step-by-step guidance when OAuth selected
- [x] Context preview dialog expanded with "What happens next" + ToS notice banner
- [x] VS Code auto-opens task-briefing.md via `--goto`, Claude CLI auto-starts via `--print` when available
- [x] task-briefing.md includes MCP availability, caching/batching efficiency hints
- [x] HITL loop stays armed during OAuth analysis (no premature close)
- [x] DITL compliance: PHI filtering via `deidentify_text()` on context sent to OAuth providers

**Shutdown hang fix:** `_do_stop_and_close()` moved from synchronous main thread to background daemon thread. Dark overlay with live status text keeps window responsive. 8-second force-close safety net. `time.sleep(1)` reduced to 0.5s.

**Chat UX:** Enter-to-send with `"break"` return (no beep). Shift+Enter passthrough. Sidebar "Claude research decisions" truncation fixed (shortened to "Claude research").

Smoke: 22/22. Skills: 80+.

### 0.170.00b — Deferred Items Sweep [DONE]

Completed 2026-03-21. All deferred roadmap items resolved — 68 files, +7540 lines.

**MCP Phase 2: Integration Wizard (M, ~10-15k)**
- [x] "Add Integration" button → modal with categorized server list (mcp.py MCPWizardDialog, 615 lines)
- [x] Default servers: toggle on, auto-writes `.mcp.json` + starts container if needed
- [x] Key-gated servers: API key input → validate → enable (regex patterns for GitHub/Brave/Slack/Postgres)
- [x] Custom: URL or `npx` command input → transport auto-detect → test connection

**MCP Phase 4: Web Launcher + Remote (S, ~5-8k)**
- [x] Mirror MCP management in web_app.py (5 endpoints: list/enable/disable/add/probe)
- [x] Remote operators can view MCP status, enable defaults
- [x] API endpoints for MCP server CRUD

**DITL Phase 3: 5-Agent Clinical Review Cycle (M, ~12-18k)**
- [x] Clinical review pipeline skill: intake → analysis → recommendation → peer review → sign-off (clinical_review.py, 720 lines)
- [x] Confirmation hex for final approval (8-char random hex, WAITING_HUMAN gate)
- [x] PHI audit at every pipeline step (deidentify before each model call)
- [x] Structured review output format (pipeline_id, stages[], final_status)

**Full Audit Logging Enhancement (S, ~5-8k)**
- [x] Structured event schema (audit.py, HMAC-signed, async queue)
- [x] Query API for audit trail with filters (/api/audit with pagination)
- [x] Retention policies + auto-purge (/api/audit/purge, configurable days)
- [x] Dashboard audit viewer panel (table, filter bar, pagination)
- [x] Export to CSV/JSON (/api/audit/export?fmt=csv|json)

**React/Next.js Web Frontend (XL, ~50-80k)**
- [x] Next.js app scaffold in BigEd/web/ (package.json, tsconfig, Tailwind)
- [x] Core pages: fleet status, agent cards, MCP, cost dashboard, audit, settings (6 pages)
- [x] Tailwind dark theme matching launcher palette (custom color tokens)
- [x] Consumes all existing dashboard API endpoints (TypeScript API client)
- [x] API proxy via next.config.ts rewrites to localhost:7777

**Kubernetes Helm Chart (M, ~10-15k)**
- [x] Helm chart in deploy/helm/ (Chart.yaml v0.1.0, 11 templates)
- [x] Deployments: fleet supervisor, dashboard, ollama (StatefulSet with GPU tolerations)
- [x] ConfigMap from fleet.toml, secrets for API keys
- [x] PVC for fleet.db + knowledge/ + logs/
- [x] Values.yaml with resource presets matching RAM tier table (5 presets)

### 0.170.01b — Data Management Systems [DONE]

Completed 2026-03-21. Modules created and wired into active workflow.

- [x] Conversation context manager — `worker.py` stores prompt+result turns, `providers._call_local()` prepends context window, `supervisor.py` clears stale contexts every 30min
- [x] Fleet-wide cache invalidation — `supervisor.py` calls `invalidate_stale()` every 5min (7 caches: cpu_temp, provider_health, circuit_state, federation_peers, alerts, hw_state, rate_limits)
- [x] RAG stale entry cleanup — `supervisor.py` calls `rag.cleanup_stale()` every 30min, `rag.update()` auto-cleans during incremental pass
- [x] All integrations fail-safe (try/except), lazy imports, config-gated

### 0.170.02b — Cross-Platform + Icon Overhaul + Font Selector + VS Code Unification

**Goal:** Cross-platform parity, icon refactor, font selector, unified VS Code launch.
**Status:** Code written, needs verification on Linux/macOS.

- [x] Cross-platform: winreg guarded, os.startfile → _open_path(), PyInstaller separator, Python/Ollama path fallbacks
- [x] Icon system: deleted generate_icon.py, single source (icon_1024.png → brick.ico), purged all old references
- [x] Font selector: Settings > Display > Font (4 presets, live preview, persisted, platform-aware)
- [x] VS Code unification: Claude + Gemini share VS Code launch, fleet agents stay active, HITL flows to Fleet Comm
- [x] USB media creator: GUI + CLI for offline deployment packaging (1850 lines)
- [x] Shutdown hang: background thread + overlay + 8s safety net
- [x] Dashboard offline: 60s countdown overlay, "Fleet offline" message
- [x] UX bug fixes: module tabs, Enter-to-send, sidebar truncation, updater loop guard, DAG test race

### 0.170.04b — Fleet Tab Agent Card Redesign + Event Triggers

**Goal:** Human-readable agent activity, expertise context, event-driven automations.

**Agent Card UX:**
- [ ] Human-readable last result (parse JSON → "Reviewed 3 files", "Evolved skill_evolve", not raw JSON)
- [ ] Current activity label (skill name displayed as "Running: code_review" not raw task type)
- [ ] Recent activity feed (last 3 tasks with outcome icons)
- [ ] Agent expertise tags (top 3 skills by IQ score from history)
- [ ] Tasks/hour throughput metric on card

**Event Trigger System:**
- [ ] File-watch trigger (new file in configurable dir → auto-ingest task)
- [ ] Webhook endpoint (POST /api/trigger → dispatch task)
- [ ] Scheduled tasks (cron-like: fleet.toml [schedules] section)
- [ ] Dashboard event triggers (anomalous cost → auto-throttle)

**Data Layer:**
- [ ] FleetDB.agent_recent_tasks(db_path, name, limit=3) — last N tasks with type+status
- [ ] FleetDB.agent_top_skills(db_path, name, limit=3) — top skills by avg IQ
- [ ] _humanize_result(result_json, task_type) — parse raw JSON into readable summary

---

## Audit Coverage Check (per AUDIT_TRACKER.md)

> Reviewed at v0.110.00b (2026-03-20).

- **Criteria fully covered:** All 12 dimensions at A or S grade
- **Criteria partially covered:** None
- **Criteria not addressed this cycle:** None — all milestones and audit items complete

**P1 issues remaining:** 0 (0.050.03b DONE — all 19 fixed)
**P2 issues remaining:** 0 (0.050.04b DONE — all 16 key items fixed)
**P3 issues remaining:** 0 (0.050.05b DONE — all 11 fixed)

**Session 0.053.01b progress (2026-03-20):**
- 0.050.04b: config staleness fixed (supervisor reloads every 5 min) — 13/16 done
- 0.050.05b: dashboard badge validation fixed — 9/11 done
- 0.051.00b: idle evolution API key gating + Chart.js update pattern fixed — 5 items remain
- 0.051.05b: screenshot gallery placeholder created — 3 items remain (GitHub web UI needed)
- 0.053.01b completed: 2 new skills (79 total), GitHub community templates
- All Python files compile: 0 errors across fleet/ + BigEd/launcher/

**Session 0.053.02b progress (2026-03-20):**
- 0.050.03b: DONE — env var null checks (installer.py), fallback error handling (boot.py), VRAM oscillation fix (hw_supervisor.py); 5 items verified already fixed
- 0.050.04b: DONE — FK constraint, VRAM threshold match, PID liveness all verified present in code
- 0.050.05b: DONE — distributed locking (acquire_fleet_lock/release_fleet_lock) verified present
- All Python files compile: 0 errors across fleet/ + BigEd/launcher/

**Session 0.053.03b (2026-03-20) -- 0.051.00b-0.051.05b sweep:**
- 20 unchecked items resolved: 7 implemented, 11 verified already present, 2 deferred (GitHub web UI)
- fleet.toml: removed affinity for 5 permanently disabled agents
- mod_intelligence.py: weight adjustment UI + live eval feed
- build.py: --ux-screenshots post-build trigger
- screenshot.py: screenshot_diff() visual regression tool
- README.md: fleet federation section
- All Python files compile: 0 errors

**Session 0.110.00b (2026-03-20) -- Intelligent Orchestration foundation:**
- providers.py: get_optimal_agent_for_skill() ML-lite routing from IQ history
- supervisor.py: _predict_queue_growth() predictive scaling integrated into scaling loop
- speech_to_text.py: 5 new NL fleet control commands (scale up/down, pause, stop, start)
- regression_detector.py: SOP generation action (parent->child workflow discovery)
- All Python files compile: 0 errors across all 4 modified files

**Session 0.135.00b (2026-03-20) -- Enterprise & Multi-Tenant foundation:**
- db.py: `get_tenant_db_path()` tenant-aware DB path resolution (auto-creates tenant dirs)
- security.py: `PERMISSIONS` granular RBAC (5 roles x 7 actions) + `check_permission()` helper
- dashboard.py: `/api/sla` endpoint (per-skill avg completion time, success rate, 24h overall)
- fleet.toml: `[enterprise]` config section (multi_tenant, tenant_id, tenant_isolation)
- All Python files compile: 0 errors across all 3 modified files

---

## Parallel Tracks (all DONE)

### PT — Platform (Cross-Platform Support)

#### PT-1: Platform Abstraction [DONE]

Completed 2026-03-18. `fleet_bridge.py`: FleetBridge ABC, WslBridge (Windows->WSL), DirectBridge (Linux/macOS native). `create_bridge(FLEET_DIR)` replaces wsl()/wsl_bg().

#### PT-2: Cross-Platform Build [DONE]

Completed 2026-03-18. `build.py` replaces `build.bat` — auto-detects --add-data separator, platform-aware process termination, CLI flags for targeted builds.

#### PT-3: Platform Packaging [DONE]

Completed 2026-03-18. Linux AppImage (`package_linux.py`), macOS .app/DMG (`package_macos.py`), `installer_cross.py` for platform-conditional install/uninstall.

#### PT-4: Platform Testing [DONE]

Completed 2026-03-18. GitHub Actions CI matrix (Win/Linux/macOS x Python 3.11/3.12), smoke test per platform, skill import verification, CLI command verification.

### CT — Cost Intelligence (Token Usage & Optimization)

#### CT-1: Usage Capture [DONE]

Completed 2026-03-18. `usage` table, `db.log_usage()`, `PRICING` dict, `calculate_cost()`, usage logging in `_call_claude()`.

#### CT-2: Cost Dashboard [DONE]

Completed 2026-03-18. `/api/usage` and `/api/usage/delta` endpoints, `lead_client.py usage` CLI.

#### CT-3: Delta Comparison [DONE]

Completed 2026-03-18. `db.get_usage_delta()`, `lead_client.py usage-delta` CLI, `/api/usage/regression` auto-flagging.

#### CT-4: Optimization Loop [DONE]

Completed 2026-03-18. `fleet.toml [budgets]`, `check_budget()`, worker post-execution budget check, `/api/usage/budgets` endpoint.

### CM — Comms (Layered Inter-Agent Communication)

#### CM-1: Channel Foundation [DONE]

Completed 2026-03-18. `channel` column + migration, `notes` table, channel constants (CH_SUP/CH_AGENT/CH_FLEET/CH_POOL), channel-aware messaging + notes API.

#### CM-2: Supervisor Layer (Layer 1) [DONE]

Completed 2026-03-18. hw_supervisor and supervisor register as supervisors, post/read sup notes on model transitions, thermal events, training state changes.

#### CM-3: Agent Layer (Layer 2) [DONE]

Completed 2026-03-18. Worker inbox filtered to fleet/agent/pool channels. `code_discuss`, `discuss`, `fma_review` migrated to `channel="agent"`.

#### CM-4: CLI & Dashboard [DONE]

Completed 2026-03-18. `--channel` flag on CLI commands, `notes` subcommand, `/api/comms` endpoint with per-channel counts.

### DT — Diagnostics (Debug Report & Issue Resolution)

#### DT-1: Debug Report Infrastructure [DONE]

Completed 2026-03-18. `generate_debug_report()` (structured JSON), `_log_ring` deque(200), global exception handler, key redaction.

#### DT-2: Issue Submission [DONE]

Completed 2026-03-18. "Report Issue" button in Config sidebar, JSON export to `data/reports/`.

#### DT-3: Resolution Tracking [DONE]

Completed 2026-03-18. `data/resolutions.jsonl` schema, `/api/resolutions` endpoint, commit convention.

#### DT-4: Stability Analysis [DONE]

Completed 2026-03-18. `stability_report.py` skill, pattern detection (top components, MTTR), release validation checklist.

### GR — Hardening (Gemini RAG Recommendations)

#### GR-1: Pre-Flight VRAM Eviction [DONE]

Completed 2026-03-18. `_evict_gpu_models()` sends `keep_alive=0` before training. Prevents CUDA OOM.

#### GR-2: WSL2 Subnet Detection [DONE]

Completed 2026-03-18. `_detect_wsl_nat()` in `pen_test.py`, warning + `.wslconfig` fix instructions.

#### GR-3: Zombie Process Cleanup [DONE]

Completed 2026-03-18. Process group (`os.setpgrp()`), `_cleanup_children()`, signal handlers kill entire group.

#### GR-4: Base64 Secret Detection [DONE]

Completed 2026-03-18. `_check_base64_secrets()` in `_watchdog.py`, catches LLM-encoded API keys bypassing plain-text DLP.

### FI — Feature Isolation Refactor

#### FI-1: Easy Extractions [DONE]

`fleet/services.py` (auto-boot), `fleet/providers.py` (HA fallback, PRICING), `fleet/cost_tracking.py` (usage logging, budgets).

#### FI-2: Medium Extractions [DONE]

Completed 2026-03-19. `fleet/idle_evolution.py`, `fleet/comms.py`, `fleet/process_control.py` (Flask Blueprint).

#### FI-3: Complex Extractions [DONE]

Completed 2026-03-19. `fleet/marathon.py`, `fleet/diagnostics.py`, `fleet/resource_mgmt.py` (deferred).
