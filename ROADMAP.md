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

### 0.050.03b — P1 Reliability & Error Handling [PARTIAL]

**19 P1 bugs** — 12 fixed across v0.50.02b (35e2e2a) and v0.51.00b (24e21d4), 7 remaining.

**Boot/Installer:**
- [ ] Model load response parsing swallows JSONDecodeError silently (boot.py:778-790)
- [ ] Timeout values too short for large models / slow networks (boot.py:666,777)
- [ ] Missing env var null checks create relative paths (installer.py:73, boot.py:581-590)
- [ ] Model fallback error handling confusing — action card + exception simultaneously (boot.py:735-749)
- [ ] Build failure error message only shows first 3 words of command (installer.py:794-804)

**Supervisor:**
- [x] Worker zombie process leak — `worker_procs.pop(role, None)` on stop, `del worker_procs[role]` for disabled (supervisor.py:235, 947)
- [ ] VRAM threshold edge case — `>` vs `>=` causes oscillation at boundary (hw_supervisor.py:965-982)
- [x] Dynamic agents scale down — `_should_scale_down()` checks `_last_busy` timestamps, `SCALE_DOWN_IDLE_SECS=300` (supervisor.py:212-236, 919-921)

**Dashboard:**
- [x] SSE connection leak — clients cleaned in finally block and dead client removal (dashboard.py:176-177, 1324-1326)
- [ ] TOML injection in worker disable/enable — agent name not validated (dashboard.py:1286-1311)
- [ ] fetchJSON() has no error handling — silent failures across all panels (dashboard.html:240-243)

**Launcher GUI:**
- [x] SSE thread UI safety — all UI updates go through `_safe_after()` with `_alive` guard (launcher.py:920, 1089-1095)
- [x] Unguarded UI updates in `_poll_task_result()` — `winfo_exists()` checks before all after() calls (consoles.py:646, 652, 659)
- [x] Widget destroy during iteration — cached `_agent_rows` dict with update-only pattern, no destroy/recreate (launcher.py:933, 3033)
- [x] Font loading failure properly warned — prints to stderr with [WARN] prefix (theme.py:26)
- [x] Window geometry bounds-checked — `winfo_screenwidth/height()` validation before restore (launcher.py:905-908)

**Data Layer:**
- [x] SQLCipher key SQL injection — `safe_key = key.replace("'", "''")` before PRAGMA (db.py:119-120)
- [x] Provider column migration complete — backfill for claude/gemini/local on NULL rows (db.py:187-189)

### 0.050.04b — P2 Hardening & Performance [PARTIAL]

**27+ P2 bugs** — 5 key items fixed, remainder still open.

**Key items:**
- [x] N+1 query in `/api/status` — uses LEFT JOIN for current_task (dashboard.py:250-253)
- [x] DB indexes on tasks.status, tasks.assigned_to, tasks.parent_id — `idx_tasks_status`, `idx_tasks_assigned`, `idx_tasks_parent` (db.py:191-193)
- [ ] Missing foreign key on tasks.parent_id — orphaned DAG chains (PRAGMA foreign_keys=ON set, but no FK constraint in CREATE TABLE)
- [ ] VRAM threshold mismatch between fleet.toml and hw_supervisor defaults
- [ ] Config loaded once at import — stale after fleet.toml edits (config.py:27-29)
- [ ] DB timeout inconsistency (10s vs 2s vs 30s across layers)
- [x] Circuit breaker has exponential backoff — `min(60s * 2^cooldowns, 600s)` with cooldown counter (providers.py:38-52)
- [x] FALLBACK_CHAIN actively used — `_models.py call_complex()` iterates chain with circuit breaker (skills/_models.py:124-138)
- [ ] Boot timing file not atomic — concurrent write race (boot.py:119-133)
- [ ] pip missing --break-system-packages for system Python (installer.py:847-861)
- [ ] 65+ bare `except: pass` blocks across launcher hiding real errors
- [ ] Memory leaks: _model_perf_labels, _agent_activity deques never cleaned
- [ ] Alert monitor thread swallows all exceptions silently (dashboard.py:182-241)
- [ ] hw_state.json concurrent read/write without locking (dashboard.py:631-656)
- [ ] Content-Security-Policy header missing on dashboard
- [ ] Stale task recovery uses time-based detection instead of PID liveness (db.py:657-677)

### 0.050.05b — P3 Polish & Accessibility [PLANNED]

**14+ P3 items** — low priority, UX improvements.

- [ ] No progress feedback during long model loads (boot.py:765-788)
- [ ] fleet.toml path not verified before load (boot.py:160-168)
- [ ] Ctrl+K command palette undiscoverable — no UI hint
- [ ] OmniBox badge abbreviations unexplained (SYS/SKL/AGT)
- [ ] Dialog resize clipping on small screens
- [ ] SSE client start exception silently swallowed (launcher.py:871-881)
- [ ] Dashboard badge status values not validated (dashboard.html:232-235)
- [ ] No rate limiting on expensive dashboard endpoints
- [ ] Worker disable/enable not audit logged
- [ ] GITHUB_REPO typo in config.py default vs fleet.toml
- [ ] No distributed locking for federation mode (db.py:686-711)

### 0.051.00b — Startup Performance & UX Polish [PARTIAL]

**Goal:** Sub-700ms window visible, 144Hz-smooth refresh, hide dev scaffolding. Public beta polish.
Partially completed in v0.51.00b (24e21d4). Dr. Ders respawn, startup perf, disabled agents, idle evolution backoff all done. Dashboard web perf and refresh smoothing remain.

**CRITICAL: Dr. Ders respawn — FIXED**
- [x] Supervisor spawns hw_supervisor.py via `start_hw_supervisor()` and respawns on crash (supervisor.py:452-458, 967-970)
- [x] Dr. Ders model promotion uses explicit CPU assignment — `num_gpu=0` for conductor/failsafe (hw_supervisor.py:527-540, 760)
- [x] Models loaded with explicit `num_gpu` — `_model_gpu_assignment` dict tracks 99=GPU, 0=CPU per model (hw_supervisor.py:313-344)

**Legacy agent cleanup (hide dev scaffolding):**
- [x] Hide disabled agents section from launcher Fleet tab — production hides entirely, dev mode shows collapsed (launcher.py:2005-2058)
- [x] Worker checks disabled BEFORE DB registration — exits immediately if in `disabled_agents` (worker.py:364-368)
- [ ] Remove affinity config for permanently disabled agents (fleet.toml:145-150 — sales, onboarding, implementation, legal still present)
- [x] Disabled agents hidden from dashboard — heartbeat <60s filter excludes non-running agents (dashboard.py:254)

**Startup performance (target: window visible < 700ms):**
- [x] Defer pynvml GPU init — lazy `_ensure_gpu()` on first hw read, not at import (launcher.py:34-49)
- [x] Defer font loading to after window creation — `load_custom_fonts()` called in `__init__` after `super().__init__()` (launcher.py:889-890)
- [x] Defer `_refresh_status()` to after window visible — uses `_safe_after(100, ...)` (launcher.py:964)
- [ ] Lazy-load Fleet Comm + modular tabs on first click — all built upfront currently (launcher.py:1414-1462)
- [ ] Cache parse_status() for 1-2s — called 3x at startup (launcher.py:486, 870, 2714)

**Refresh cycle smoothing (target: no stalls > 16ms on 144Hz):**
- [ ] Increase HW stats interval 3s -> 5s — human eye can't perceive <100ms changes (launcher.py:3230)
- [ ] Skip parse_status() when SSE active — redundant I/O every 4s (launcher.py:3288)
- [ ] SSE client reads 1 byte at a time -> read 4KB chunks (sse_client.py:91)
- [x] Cache action cards — `_agent_rows` dict with update-only pattern instead of destroy/recreate (launcher.py:933, 3033-3040)

**Idle evolution quarantine spiral:**
- [ ] Check API key availability before dispatching idle evolution tasks (worker.py:495)
- [x] Exponential backoff between failed idle evolution — `_idle_failures` counter, pauses after 3 consecutive failures (worker.py:397, 512-534)
- [x] Auto-clear quarantine after 5 minutes of inactivity (worker.py:481-498)
- [ ] Gate idle evolution on local-only skills when API keys missing

**Dashboard web performance:**
- [ ] Batch 15 API calls into single `/api/dashboard` endpoint (dashboard.html:618-626)
- [ ] Reduce 30s polling to 5min for slow-changing data (knowledge, RAG, code stats)
- [ ] Update Chart.js data instead of destroy/recreate (dashboard.html:449, 477)

### 0.051.01b — Task Pipeline Optimization [PLANNED]

**Goal:** 30-40% throughput improvement, 15-20% API cost reduction. Addresses 10 bottlenecks from pipeline audit.

**Critical (implement first):**
- [ ] Atomic task claiming — combine SELECT+UPDATE into single query (db.py:241-281)
- [x] Enable prompt caching — `cache_control: ephemeral` on stable system prompts (providers.py:338)
- [ ] Async usage logging — buffer writes, flush on timer instead of sync per-call (cost_tracking.py:16-33)
- [ ] Adaptive polling — 100ms/500ms/2s based on queue depth + jitter (worker.py:505)

**Medium priority:**
- [x] Global idle evolution dedup — worker checks pending queue before creating idle task (worker.py:513-524)
- [ ] DAG promotion index — add `idx_tasks_depends` for faster WAITING resolution (db.py:330-382)
- [ ] API request batching — coalesce simple skills into 10-task batches (skills/_models.py)
- [ ] Deterministic Tier 2 sampling — by task_id hash, not random (intelligence.py:105)

**Lower priority:**
- [ ] Cache skill staleness ranking in idle evolution (idle_evolution.py:40-113)
- [ ] Batch-claim N tasks per poll when queue depth > threshold

### 0.051.02b — Auto-Save & Backup System [PLANNED]

**Goal:** Prevent data loss from power outage or crashes. Configurable backup frequency/depth/location.

**Implementation:**
- [ ] `fleet/backup_manager.py` — BackupManager class with auto-save thread
- [ ] `fleet.toml [backup]` section — enabled, interval_secs=300, depth=10, location, prune_enabled
- [ ] Backup targets: fleet.db, rag.db, tools.db, knowledge/, fleet.toml (configurable per-target)
- [ ] WAL checkpoint before backup — `PRAGMA wal_checkpoint(TRUNCATE)`
- [ ] Backup manifest JSON — timestamp, file hashes, row counts, integrity check results
- [ ] Integrity verification — `PRAGMA integrity_check` after each backup
- [ ] Prune beyond depth — with "do not clean" toggle + disk usage warning
- [ ] CLI: `lead_client.py backup`, `backup --list`, `backup --restore ID`
- [ ] Supervisor integration — backup on fleet startup + on skill_deploy completion
- [ ] Graceful shutdown saves task queue (already implemented in 0.051.00b)

### 0.051.03b — Intelligence Module + Cost Dashboard [PLANNED]

**Goal:** System transparency tab for understanding capabilities, model settings, prompt queue, evaluation.

**Implemented (needs testing):**
- [x] Intelligence module (mod_intelligence.py) — 5 panels: overview, model settings, prompt queue, evaluation, cost
- [x] API Cost Tracker dashboard panel — today/7d/30d spend, provider breakdown, projections
- [x] billing_ocr skill — OCR screenshots of Claude/Gemini billing dashboards
- [x] token_optimizer skill — audit usage patterns, recommend cost optimizations

**Remaining:**
- [ ] Prompt queue dispatches to configurable skill type (not just summarize)
- [ ] Model settings panel with live edit capability (write back to fleet.toml)
- [ ] Weight adjustment UI for skill complexity routing
- [ ] Evaluation routine live display (show Tier 1/2 scores as they happen)

### 0.051.04b — Autoresearch Pipeline Integration [PLANNED]

**Goal:** Wire disconnected research/training pipelines into closed feedback loops.

**Auto-bridges (currently manual):**
- [ ] Auto-trigger `research_cycle` workflow daily (gap detection → web search → summarize → index)
- [ ] Auto-trigger `skill_evolution_pipeline` weekly (evolve bottom 10% performing skills)
- [ ] `ml_bridge` auto-import when new `autoresearch/results.tsv` entries detected
- [ ] Dataset synthesize outputs → autoresearch data pipeline (JSONL → training)

**Dashboard visibility:**
- [ ] Evolution leaderboard panel (skill improvement rates, agent contributions)
- [ ] Quality metrics panel (code_quality scores, benchmark results over time)
- [ ] HITL notification when skill draft scores higher than deployed version

**Screenshot skill:**
- [x] `fleet/skills/screenshot.py` — capture full/window/region, UX test suite
- [ ] Automated UX test suite: capture launcher + dashboard + Fleet tab on each release
- [ ] Screenshot diff tool: compare before/after for visual regression

### 0.051.05b — GitHub Public Presence Update [PLANNED]

**Goal:** Update GitHub repo description, README, and metadata to reflect current enterprise capabilities.

- [ ] GitHub repo description: enterprise-ready AI agent fleet with SOC 2 compliance, multi-machine model control, all-OS support
- [ ] README.md refresh: current architecture, feature list, screenshots, quick start
- [ ] Credit: link to Karpathy's autoresearch repo (https://github.com/karpathy/build-nanogpt) for training pipeline inspiration
- [ ] Feature highlights: auto-installs dependencies, air-gap mode, HITL governance, 74+ skills
- [ ] Compliance section: SOC 2 alignment, DLP, RBAC, audit logging, encryption at rest
- [ ] Multi-machine: fleet federation, Dr. Ders hardware monitoring, cross-platform (Win/Linux/macOS)
- [ ] Badges: build status, Python version, license, platform support
- [ ] Topics/tags: ai-agents, fleet-management, ollama, claude, gemini, local-ai, enterprise

### 0.052.00b — Claude Manual Mode Integration (Enterprise) [PLANNED]

**Goal:** ToS-compliant hybrid system — unattended API automation (Lane 2) + human-guided Claude Code sessions (Lane 1). No lane crossing. Spec: `docs/specs/claude-manual-mode-integration.md`

**Phase 1: API audit system**
- [ ] Prompt queue management UI (ordered list, per-prompt model/tokens/repeat)
- [ ] Scheduler: recurring interval (1-30 day cadence) + single window block
- [ ] Audit engine: process queue against training/knowledge files via Anthropic API
- [ ] Results viewer with structured task list generation
- [ ] Token/cost tracking per audit run (integrates with existing CT-1/2/3/4)

**Phase 2: VS Code / Claude Code launch integration**
- [ ] "Open in Claude Code" button in UI (writes context files, launches VS Code)
- [ ] Auto-generate: task-briefing.md, audit-results.md from API audit output
- [ ] CLAUDE.md + .claude/rules/ templates for training file compliance
- [ ] .claude/skills/ training-review workflow template
- [ ] Cross-platform VS Code launch (macOS/Windows/Linux)

**Phase 3: HITL governance + handoff**
- [ ] "Manual Claude Code review requested" notification (in-app + optional email/Slack)
- [ ] HITL approval gate: any API consumption increase requires human confirm
- [ ] System recommendations (never auto-applied): frequency, model tier, scope changes
- [ ] Anomalous usage alerting (cost spike detection)
- [ ] Audit log for all configuration changes

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
- **Status:** [x] Phase 1+3 done (config, settings panel, dashboard API, web launcher). Phase 2 (wizard modal) and Phase 4 (web enable/disable buttons) pending.

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

### 0.100.00b — Multi-Fleet & Remote Orchestration [FUTURE]

- Fleet-to-fleet communication (federated supervisor mesh)
- Remote dashboard access (auth + TLS + public URL)
- Fleet cloning (deploy identical fleet via config export)
- Plugin marketplace (community skills via git repos)

### 0.200.00b — Intelligent Orchestration [FUTURE]

- ML-driven task routing (learn optimal agent→skill mapping from history)
- Predictive scaling (anticipate load from task patterns)
- Natural language fleet control ("scale up coders, pause research")
- Auto-generated SOPs from fleet behavior patterns

### 0.300.00b — Enterprise & Multi-Tenant [FUTURE]

- Tenant isolation (separate DBs, configs, knowledge per customer)
- Role-based access control (RBAC with granular permissions)
- Full audit logging (who did what, when, with what cost)
- SLA monitoring (task completion time guarantees)

### 0.400.00b — Platform & SaaS [FUTURE]

- Self-hosted SaaS deployment (Docker Compose / K8s)
- Web-based launcher (replace desktop GUI with React/Next.js)
- Federated fleet orchestration (multiple physical machines, single control plane)
- Marketplace: skill store, model store, template store

---

## Audit Coverage Check (per AUDIT_TRACKER.md)

> Reviewed at v0.30.01a.

- **Criteria fully covered:** All 12 dimensions at A or S grade
- **Criteria partially covered:** None
- **Criteria not addressed this cycle:** None — all milestones and audit items complete

**P1 issues remaining:** None
**P2 issues remaining:** None
**P3 issues remaining:** None — all resolved (P3-01 through P3-07)

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
