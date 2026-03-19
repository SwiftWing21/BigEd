# BigEd CC Roadmap

> **Goal of 1.0:** Autonomous, cross-platform, verifiably safe agent fleet.
> **Goal of 5.0:** Multi-tenant SaaS-ready platform with federated fleet orchestration.

---

## Version Scheme

| Era | Format | Example | Notes |
|-----|--------|---------|-------|
| Pre-1.0 | `v0.XX` | v0.31, v0.48 | Feature versions, sequential |
| 1.0 | `1.0` | 1.0 | Production release tag |
| Post-1.0 | `0.XX.YY` | 0.01.01, 0.15.00 | MAJOR.MINOR.PATCH |
| Future | `X.0` | 2.0, 5.0 | Major platform milestones |

- **Pre-1.0 (v0.31 through v0.48):** Each `v0.XX` was a feature version building toward production. These are historical and should not be modified.
- **1.0:** The production release tag. All milestones, parallel tracks, and tech debt resolved.
- **Post-1.0 (0.XX.YY):** Semantic versioning. `0.XX` is the feature number, `.YY` is the patch level.
- **Future (2.0-5.0):** Major platform evolution milestones.

> **Note for AI assistants (Claude/Gemini):** When adding new roadmap items, use the `0.XX.YY` format and place them in chronological order after the last completed version. Pre-1.0 versions are frozen history.

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

---

## Phase 3: Planned

### 0.16.00 — Multi-Backend Model Support

**Goal:** Support local model providers beyond Ollama — llamafile, vLLM, LM Studio, any OpenAI-compatible server.

- **Backend abstraction in providers.py:** Unified interface for Ollama, llama.cpp, vLLM, LM Studio
- **OpenAI-compatible API routing:** All local backends expose `/v1/chat/completions` — single adapter
- **fleet.toml [models] expansion:** `backend` field (ollama/llamacpp/vllm/lmstudio/openai_compat), per-backend host config
- **Model registry:** `[models.registry]` maps logical names to backend-specific identifiers + download URLs
- **HuggingFace search:** `lead_client.py model-search "codellama"` — find GGUF models by name
- **Auto-backend detection:** `model-install codellama:13b` detects best available backend and pulls/downloads
- **llamafile single-binary support:** Download .llamafile -> serve as self-contained binary, zero install
- **Model installer UI:** Launcher module or walkthrough step for browsing + installing models

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
- Version scheme transition: `5.0.0` -> semver

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
