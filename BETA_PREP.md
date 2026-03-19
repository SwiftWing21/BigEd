# BigEd CC -- Beta Prep Release Notes

**Version:** Beta 1.0 (transitioning from Alpha 0.25.01)
**Date:** 2026-03-19
**License:** Apache 2.0 -- Michael Bachaud (SwiftWing21)
**Repository:** github.com/SwiftWing21/BigEds_Agents

---

## 1. Release Summary

BigEd CC is transitioning from Alpha (0.25.01) to Beta 1.0. All S-tier infrastructure milestones are complete. All P1, P2, and P3 audit issues are resolved. Technical debt is at zero. The system runs a 74-skill autonomous AI worker fleet with dual supervisors, swarm intelligence, and multi-backend model support on a single desktop machine.

**Key numbers at beta entry:**

| Metric | Value |
|--------|-------|
| Skills | 74 |
| Dashboard endpoints | 40+ |
| Smoke tests | 22/22 |
| Skill unit tests | 32 |
| Dashboard tests | 22 |
| Security tests | 7 |
| Open P0/P1/P2/P3 issues | 0 |
| Tech debt items | 0 (all 4.1-4.8 resolved) |
| Audit grade | S (overall) |

---

## 2. What's Included

### Phase 1: Pre-1.0 (v0.31 -- v0.48) -- 18 feature versions, 8 milestones

| Version | Feature | Milestone |
|---------|---------|-----------|
| v0.31 | Task Graph & Decomposition (DAG) | -- |
| v0.32 | UI Resilience & Clean Refresh | -- |
| v0.33 | End-to-End Flow Verification + Offline/Air-Gap + Model Heartbeat | M1 |
| v0.34 | New User Walkthrough (First-Run Experience) | -- |
| v0.35 | Evaluator-Optimizer Loop (Guard Rails) | -- |
| v0.36 | Semantic Watchdog (Checker Agent) | -- |
| v0.37 | Unified Human-in-the-Loop (HITL) | -- |
| v0.38 | Fleet Security & Isolation (Sandboxing) | M2 |
| v0.39 | Advanced Network & IoT Orchestration | -- |
| v0.40 | Full DOM Web Interactivity (Browser Skills) | -- |
| v0.41 | Local Vision & Multi-Modal Orchestration | M3 |
| v0.42 | Auto-Boot & Idle Skill Evolution | -- |
| v0.43 | Marathon ML & Context Persistence | M4 |
| v0.44 | Seamless Lifecycle (Unified Updater) | -- |
| v0.45 | Omni-Box & High Availability (HA) Routing | M5 |
| v0.46 | Frictionless GitHub Sync & Zero-Bloat Baseline | M6 |
| v0.47 | Restricted Owner Core (Shadow Module) | -- |
| v0.48 | Cautious Codebase Pruning (Bloat Cleanup) | M7 |

### Phase 2: Post-1.0 (0.01.xx -- 0.20.00) -- 20 infrastructure versions

| Version | Feature |
|---------|---------|
| 0.01.01 | Post-Release Stabilization |
| 0.01.02 | DAG & Cost Enhancements |
| 0.01.03 | Visualization & Forecasting |
| 0.05.00 | Git & MLOps Autonomy (Skill Expansion) |
| 0.06.00 | Cryptographic & Security Self-Healing |
| 0.07.00 | Security Hardening (Gemini 3rd Pass P0+P1) |
| 0.08.00 | Architecture Polish (Gemini 3rd Pass P2) |
| 0.09.00 | Audit & Observability (Gemini 3rd Pass P3) |
| 0.10.00 | Advanced Agent Flows (Skill Synergy) |
| 0.11.00 | Security Fixes (DLP Expansion) |
| 0.12.00 | Bug Fixes & Cross-Platform Stability |
| 0.13.00 | Compliance Framework (Part 1) |
| 0.14.00 | Compliance Framework (Part 2) |
| 0.15.00 | Model Manager & Hardware Profiles |
| 0.16.00 | Boot Stability + Native Windows Migration |
| 0.17.00 | Swarm Tier 1: Coordinated Evolution |
| 0.18.00 | Swarm Tier 2: Autonomous Research Loops |
| 0.19.00 | Swarm Tier 3: Swarm Intelligence |
| 0.20.00 | Additional Skills + GUI Overhaul |

### Phase 3: Alpha (0.21.00 -- 0.25.01) -- 5 S-tier milestones + 4 patches

| Version | Feature |
|---------|---------|
| 0.21.00 | **S1: Reliability** -- crash backoff, Ollama degradation, timer safety, budget throttle |
| 0.21.01 | Dr. Ders rename, token speed tracking, per-skill routing, HITL inline, memory watchdog |
| 0.21.02 | Gemini safety handling, native key manager, model update check |
| 0.21.03 | Intelligence scoring, HITL model recommendations, Gemini ToS compliance |
| 0.21.04 | UX polish, Fleet Comm modernization, P1-01/P1-02/P2-01 fixes |
| 0.22.00 | **S2: Observability** -- /api/health, performance endpoint, JSON logging, alerts pipeline |
| 0.23.00 | **S3: Auto-Intelligence** -- auto-trigger evolution/research, swarm affinity, Tier 2 LLM scoring |
| 0.24.00 | **S4: Security Defaults** -- SQLCipher, TLS, RBAC, API attribution, adversarial tests |
| 0.25.00 | **Multi-Backend** -- LocalBackend ABC, 3 backends, HuggingFace search, OpenAI adapter |
| 0.25.01 | P3 audit items resolved, comprehensive test suites added |

### Parallel Tracks (all complete)

| Track | Items | Summary |
|-------|-------|---------|
| PT (Platform) | PT-1/2/3/4 | FleetBridge ABC, cross-platform build, Linux/macOS packaging, CI matrix |
| CT (Cost) | CT-1/2/3/4 | Usage capture, cost dashboard, delta comparison, budget enforcement |
| CM (Comms) | CM-1/2/3/4 | Channel foundation, supervisor layer, agent layer, CLI + dashboard |
| DT (Diagnostics) | DT-1/2/3/4 | Debug reports, issue submission, resolution tracking, stability analysis |
| GR (Hardening) | GR-1/2/3/4 | VRAM eviction, WSL2 subnet, zombie cleanup, base64 secret detection |
| FI (Feature Isolation) | FI-1/2/3 | 9 extracted modules from launcher god-object |

### Current: v0.27.00

Settings Display panel and UI scaling features.

---

## 3. Audit Status

> Audited at v0.25.01 by Opus (2026-03-19). All 12 dimensions graded.

| # | Dimension | Grade | Trend | Key Notes |
|---|-----------|-------|-------|-----------|
| 1 | Architecture / SoC | A | up | theme.py extracted, fleet_api.py + data_access.py complete, launcher -234 LOC |
| 2 | Code Quality | A | up | All P1/P2/P3 resolved; deferred imports documented |
| 3 | Testing | A | up | 22/22 smoke + 32 skill unit tests + 22 dashboard tests + 7 security tests |
| 4 | Security | A | up | SQLCipher, TLS, RBAC, API attribution, adversarial testing |
| 5 | Reliability (S1) | A | stable | S1 complete -- crash backoff, Ollama degradation, timer safety |
| 6 | Observability (S2) | A | up | /api/health, /api/agents/performance, JSON logging, alerts pipeline |
| 7 | Usability / UX | A | stable | IQ on cards, timestamps, Fleet Comm modernized |
| 8 | Dynamic Abilities | S | up | Auto-trigger evolution/research, swarm affinity, multi-backend |
| 9 | Module / Plugin Support | A | up | Backend ABC, 3 backends, HuggingFace search, OpenAI adapter |
| 10 | Data Processing + HITL | S | up | Tier 2 LLM scoring, distributed tracing, auto-intelligence |
| 11 | Performance | A | stable | Code-aware token estimation, configurable timeout |
| 12 | Documentation | A | stable | CLAUDE.md thorough; compliance docs complete |
| -- | **Overall** | **S** | **up** | **All milestones complete. All P1/P2/P3 resolved. Zero open issues.** |

### Grading Rubric

| Grade | Meaning |
|-------|---------|
| S | Exceptional -- production-grade, zero known gaps |
| A | Excellent -- minor issues only, no blockers |
| B+ | Good -- some gaps, actively tracked |
| B | Adequate -- notable gaps, needs attention |
| C | Needs work -- blockers present |

### Open Issues

- P0 (Critical): None
- P1 (High): None
- P2 (Medium): None
- P3 (Low): None

---

## 4. Public Readiness Checklist

- [x] All tech debt resolved (4.1 through 4.8)
- [x] All audit criteria A or S grade (12/12 dimensions)
- [x] Apache 2.0 LICENSE + NOTICE added
- [x] README.md created
- [x] CONTRIBUTING.md created
- [x] SwiftWing21 username parameterized (configurable via fleet.toml `[github].owner`)
- [x] .gitignore covers secrets, data, certs, .claude/
- [x] No hardcoded local paths in source
- [x] Security tokens empty by default in config
- [x] Test coverage: 22/22 smoke, 32+ skill, 22 dashboard, 7 security

---

## 5. Known Limitations (Beta)

| Limitation | Details | Planned Resolution |
|------------|---------|-------------------|
| Desktop GUI only | Tkinter-based launcher; no web UI | Web UI planned for 5.0 (React/Next.js) |
| Windows-primary | Linux/macOS supported via FleetBridge + cross-platform layer, but less tested | CI matrix covers all 3; community testing will expand coverage |
| Single-machine fleet | All agents run on one host | Multi-fleet federation planned for 2.0 |
| Ollama required | Local inference depends on Ollama running | llama.cpp and Llamafile backends available as alternatives (0.25.00) |
| SQLCipher optional | Encrypted DB requires separate sqlcipher3 install; falls back to plain sqlite3 | Documented in setup guide; auto-detection in get_conn() |
| VRAM ceiling | 12GB GPU limits concurrent model loading | VRAM-aware scheduling already in place; multi-GPU planned for 2.0 |
| No multi-user auth | Single operator assumed | Multi-tenant RBAC planned for 4.0 |

---

## 6. HITL QA Checklist

### Launch and Boot

- [ ] Clean first launch (no data dir) -- walkthrough dialog appears with 6 steps
- [ ] Skip walkthrough -- "Skip All" works, "don't show again" persists to fleet.toml
- [ ] Normal launch -- fleet boots via 7-stage sequence, all agents register
- [ ] Launch with saved geometry -- window restores previous position and size
- [ ] Launch maximized -- window opens maximized correctly
- [ ] Sidebar collapsed state persists across restart
- [ ] Loading splash shows live boot timers and stage progress
- [ ] Auto-boot does NOT fire during first-run walkthrough (P3-05 fix)

### Fleet Operations

- [ ] Start fleet -- all workers come online, agent cards populate
- [ ] Stop fleet -- clean shutdown via REST API, no orphan processes (psutil-verified)
- [ ] Restart fleet -- clean stop/start cycle, all agents re-register
- [ ] Individual agent recovery -- click recover button on agent card, worker respawns
- [ ] Task dispatch -- manual task from omni-box (Ctrl+K), skill auto-complete works
- [ ] Task completion -- result appears in agent card with last result preview
- [ ] Task chain (DAG) -- post_task_chain creates sequential pipeline, dependencies resolve
- [ ] HITL flow -- WAITING_HUMAN task appears in Fleet Comm tab, respond via action panel, task resumes
- [ ] Security advisory flow -- advisory cards appear with lock icon, Approve/Dismiss work
- [ ] Idle evolution -- workers auto-dispatch evolution_coordinator tasks during idle (1h cooldown)
- [ ] Research loop -- research_loop auto-dispatched (2h cooldown)
- [ ] Budget enforcement -- throttle/warn/block modes trigger at configured thresholds

### Settings (all 7 panels)

- [ ] General -- theme change applies immediately, agent names editable, ingest path configurable
- [ ] Display -- UI scale slider adjusts layout, always-on-top toggle works, compact mode reduces padding
- [ ] Models -- model selector shows installed models, diffusion settings save, model profiles load
- [ ] Hardware -- GPU/CPU settings display correctly, thermal thresholds configurable
- [ ] API Keys -- key manager dialog opens (native Windows), keys save to ~/.secrets, keys load on restart
- [ ] Review -- review toggle enables/disables evaluator, evaluator settings (max rounds, provider) save
- [ ] Operations -- marathon start/stop works, diagnostics panel shows debug report

### Dashboard and Monitoring

- [ ] Agent cards show status indicator, IQ score (color-coded), tok/s, last result preview
- [ ] Model performance panel updates live -- IQ column, tok/s, calls, avg ms per model
- [ ] Counter cards accurate -- WAITING count, MODELS count reflect current state
- [ ] SSE streaming works -- reactive updates without polling (8s fallback when dashboard unavailable)
- [ ] `/api/health` endpoint returns correct aggregate status (healthy/degraded/unhealthy)
- [ ] `/api/agents/performance` endpoint returns per-agent tasks/hour, success rate, avg latency
- [ ] `/api/alerts` endpoint returns alert history, acknowledge_alert works
- [ ] `/api/usage` endpoint returns token/cost breakdown by model and time period
- [ ] Cost delta endpoint (`/api/usage/delta`) flags regressions correctly

### Security

- [ ] Dashboard bearer token auth works when `BIGED_DASHBOARD_TOKEN` is set
- [ ] RBAC roles enforced -- admin can write, viewer cannot, operator has middle permissions
- [ ] DLP scrubbing catches secrets in output (API keys, AWS, Azure, GCP, private keys, base64)
- [ ] TLS cert auto-generates on first dashboard start (self-signed RSA-2048)
- [ ] SQLCipher encryption works when sqlcipher3 is installed and `BIGED_DB_KEY` is set
- [ ] Fallback to plain sqlite3 is graceful when sqlcipher3 is not installed
- [ ] API attribution logging records write requests + 10% of GETs to audit trail
- [ ] CSRF protection active on dashboard (Flask-CSRF)
- [ ] Rate limiting active (Flask-Limiter)
- [ ] `127.0.0.1` binding verified (not 0.0.0.0)

### Evaluator and Review

- [ ] Review cycle triggers for high-stakes skills (code_write, pen_test, legal_draft)
- [ ] Adversarial reviewer provides feedback, worker iterates (max 2 rounds)
- [ ] QUARANTINED status applied on failure streak
- [ ] Stuck review auto-pass works (prevents infinite review loops)
- [ ] Gemini-sourced content excluded from training data (ToS compliance)

### Swarm Intelligence

- [ ] Evolution coordinator runs 6-stage DAG (draft, test, review, security, evolve, deploy)
- [ ] Research loop detects knowledge gaps and auto-dispatches research/summarize/train
- [ ] Swarm affinity routing assigns tasks to agents with proven skill proficiency (>80% success)
- [ ] Intelligence scoring (Tier 1 mechanical + Tier 2 LLM at 10% sampling) produces valid scores
- [ ] Model recommendation HITL flow creates request for operator approval before changing fleet.toml

### Stability

- [ ] 1-hour continuous run -- no crashes, no memory leaks, RSS stays bounded
- [ ] Memory watchdog (3-tier) -- GC on growth, cross-monitor restart at 600MB RSS
- [ ] Ollama restart recovery -- fleet detects Ollama unavailability and recovers when Ollama returns
- [ ] Worker crash -- escalating backoff schedule (15s, 30s, 60s, 120s, 300s) prevents thrashing
- [ ] Close and reopen -- all state persists (window geometry, sidebar, fleet config)
- [ ] Dr. Ders (hw_supervisor) -- park+guard pattern, stale hw_state.json handled
- [ ] Marathon ML -- multi-hour training with checkpoint/resume, no OOM on 12GB VRAM
- [ ] Offline mode -- air-gap mode works with deny-by-default whitelist

### Build and Update

- [ ] `build.py` succeeds -- PyInstaller build produces bigedcc.exe
- [ ] Built .exe launches and runs correctly (no missing imports, no path issues)
- [ ] Build detects platform automatically (--add-data separator, process termination method)
- [ ] `--production` flag disables DEV_MODE features in built executable
- [ ] Auto-update check works (git pull + uv sync in launcher loading sequence)
- [ ] Hot-reload via os.execv works after update
- [ ] Config export/import round-trips (fleet.toml read/write via tomlkit)
- [ ] Linux AppImage packaging (package_linux.py) -- builds on Linux
- [ ] macOS .app/DMG packaging (package_macos.py) -- builds on macOS

### Cross-Platform

- [ ] FleetBridge auto-selects correct bridge (NativeWindowsBridge on Windows, DirectBridge on Linux/macOS)
- [ ] GitHub Actions CI matrix passes (Windows/Linux/macOS x Python 3.11/3.12)
- [ ] Smoke tests pass on all 3 platforms
- [ ] Skill import verification passes on all platforms
- [ ] CLI commands work via detect_cli() auto-detection

### CLI (lead_client.py)

- [ ] `status` -- shows fleet overview
- [ ] `dispatch --skill <name>` -- dispatches task manually
- [ ] `hitl` -- lists WAITING_HUMAN tasks
- [ ] `advisories` -- lists security advisories
- [ ] `usage` -- shows token/cost summary
- [ ] `usage-delta` -- shows cost delta comparison
- [ ] `install-service` -- installs auto-boot service (Task Scheduler / systemd / launchd)

---

## 7. Post-Beta Roadmap

### 2.0 -- Multi-Fleet and Remote Orchestration

- Fleet-to-fleet communication (federated supervisor mesh)
- Remote dashboard access (auth + TLS + public URL)
- Fleet cloning (deploy identical fleet via config export)
- Plugin marketplace (community skills via git repos)
- Multi-GPU and multi-machine worker distribution

### 3.0 -- Intelligent Orchestration

- ML-driven task routing (learn optimal agent-to-skill mapping from history)
- Predictive scaling (anticipate load from task patterns)
- Natural language fleet control ("scale up coders, pause research")
- Auto-generated SOPs from fleet behavior patterns

### 4.0 -- Enterprise and Multi-Tenant

- Tenant isolation (separate DBs, configs, knowledge per customer)
- Granular RBAC with per-tenant permission sets
- Full audit logging (who did what, when, with what cost)
- SLA monitoring (task completion time guarantees)

### 5.0 -- Platform / SaaS

- Self-hosted SaaS deployment (Docker Compose / Kubernetes)
- Web-based launcher (replace desktop GUI with React/Next.js)
- Federated fleet orchestration (multiple physical machines, single control plane)
- Marketplace: skill store, model store, template store
- Version scheme transition to semver (5.0.0+)

---

*Generated 2026-03-19. Source: ROADMAP.md, audit_tracker.md, TECH_DEBT.md, CLAUDE.md.*
