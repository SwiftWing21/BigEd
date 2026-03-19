# BigEd CC Roadmap

> **Goal of 1.0:** Autonomous, cross-platform, verifiably safe agent fleet.
> **Goal of 5.0:** Multi-tenant SaaS-ready platform with federated fleet orchestration.

---

## Version Scheme

| Era | Format | Example | Notes |
|-----|--------|---------|-------|
| Pre-1.0 | `v0.XX` | v0.31, v0.48 | Feature versions — historical, frozen |
| 1.0 | `1.0` | 1.0 | Production release tag |
| Post-1.0 (dev) | `0.XX.00` | 0.15.00, 0.20.00 | Major infrastructure work |
| **Alpha** | `0.XX.YY` | 0.21.01, 0.21.02 | **Current era** — see below |
| Future | `X.0` | 2.0, 5.0 | Major platform milestones |

### Alpha Versioning (current)

BigEd CC is now an **alpha working product** — agents run unattended, fleet boots natively, swarm intelligence active. The versioning reflects two parallel work streams:

**`0.XX.00` — Milestones (S-Tier infrastructure)**
Major system capabilities. Each `0.XX` is a milestone from the S-Tier plan:
- 0.21 = Reliability, 0.22 = Observability, 0.23 = Auto-Intelligence, 0.24 = Security, 0.25 = Multi-Backend

**`0.XX.YY` — Patches (UX, agent quality, polish)**
Between milestones, `.YY` patches focus on end-user experience:
- **Reduce clicks** — streamline user flows, fewer steps to common actions
- **Agent enhancements** — fill skill gaps, improve output quality, tune idle behavior
- **Console flows** — retention between builds/updates, session persistence
- **GUI polish** — layout refinements, information density, responsiveness
- **Bug fixes** — stability issues discovered during alpha use

Example progression:
```
0.21.00  — S1 Reliability milestone (audit fixes, crash backoff, self-heal)
0.21.01  — UX: one-click task dispatch from agent cards
0.21.02  — Agent: improve idle evolution skill selection
0.21.03  — Console: preserve chat history across rebuilds
0.22.00  — S2 Observability milestone (health endpoint, alerts)
0.22.01  — UX: dashboard auto-open on boot complete
0.22.02  — Agent: quality scoring on task outputs
...
```

> **Note for AI assistants (Claude/Gemini):** Use `0.XX.00` for milestone features and `0.XX.YY` (YY > 0) for UX/agent/polish patches. Place chronologically after the last version. Pre-1.0 versions are frozen history.

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

### 2.0 — Multi-Fleet & Remote Orchestration

- Fleet-to-fleet communication (federated supervisor mesh)
- Remote dashboard access (auth + TLS + public URL)
- Fleet cloning (deploy identical fleet via config export)
- Plugin marketplace (community skills via git repos)

### 3.0 — Intelligent Orchestration

- ML-driven task routing (learn optimal agent→skill mapping from history)
- Predictive scaling (anticipate load from task patterns)
- Natural language fleet control ("scale up coders, pause research")
- Auto-generated SOPs from fleet behavior patterns

### 4.0 — Enterprise & Multi-Tenant

- Tenant isolation (separate DBs, configs, knowledge per customer)
- Role-based access control (RBAC with granular permissions)
- Full audit logging (who did what, when, with what cost)
- SLA monitoring (task completion time guarantees)

### 5.0 — Platform

- Self-hosted SaaS deployment (Docker Compose / K8s)
- Web-based launcher (replace desktop GUI with React/Next.js)
- Federated fleet orchestration (multiple physical machines, single control plane)
- Marketplace: skill store, model store, template store
- Version scheme transition: `5.0.0` -> semver

---

## Audit Coverage Check (per AUDIT_TRACKER.md)

> Reviewed at v0.25.00.

- **Criteria fully covered:** Architecture/SoC (A), Code Quality (A), Dynamic Abilities (S), Performance (A), Documentation (A), Data Processing+HITL (S), Usability/UX (A), Reliability/S1 (A), Observability/S2 (A), Security/S4 (A), Module/Plugin Support (A)
- **Criteria partially covered:** Testing (A-, adversarial suite added but no per-skill unit tests)
- **Criteria not addressed this cycle:** None — all S-tier milestones complete

**P1 issues remaining:** None
**P2 issues remaining:** None — all resolved
**P3 issues remaining:** P3-02 (deferred imports docs), P3-03 (per-skill unit tests), P3-04 (dashboard tests), P3-06 (get_optimal_model), P3-07 (batch error detail)

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
