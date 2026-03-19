# BigEd CC Roadmap: v0.31 → v1.0

> **Goal of v1.0:** Autonomous, cross-platform, verifiably safe agent fleet. From flat task queue to graph-based, self-correcting, deploy-anywhere swarm.

---

## Program Milestones

| Milestone | Versions | Theme | Gate |
|-----------|----------|-------|------|
| 1. Verification & Onboarding | v0.33 – v0.34 | Prove it works, make it approachable | Smoke 10/10, Soak 13/13, GUI smoke, no P0 debt |
| 2. Autonomous Safety | v0.35 – v0.38 | Self-correction, operator comms, isolation | + review cycle, watchdog, HitL, sandbox tests; .secrets never in output |
| 3. External Integration | v0.39 – v0.41 | Network, browser, vision | + network/browser/vision tests; no OOM on 12GB |
| 4. Cross-Platform & v1.0 | PT-1 – PT-4, DT-1 – DT-4 | Anyone, anywhere, clear diagnostics | All tests on Win/Linux/macOS; FleetBridge 100%; zero debt |

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

## v0.39 — Advanced Network & IoT Orchestration
**Goal:** Deep integration with local infrastructure, bridging the gap between software agents and the physical/network environment.
- **UniFi Stack Understanding:** Elevate network skills to interface with UniFi Controller APIs, parse 1Gbps IDS/IPS stack alerts, and recommend/apply advanced network configurations (VLANs, firewall rules).
- **Home Assistant Maintenance:** Introduce building automation setup and maintenance tools, including automated backup/update cycles with granular version retention policies (e.g., keep specific versions, or keep the last 1, 3, or 5 versions).
- **Dynamic IoT Upskilling:** Leverage `skill_evolve` to allow agents to learn new entities and devices dynamically by reading Home Assistant Community Store (HACS) repositories (e.g., `ha-anker-solix`, `anker-solix-api`).
- **Local Protocol Inspection:** Integrate MQTT and API sniffing tools for deep local IoT debugging. Ensure authentication steps strictly utilize the fleet's `.secrets` manager for secure, repeatable access to local APIs.

---

## v0.40 — Full DOM Web Interactivity (Browser Skills)
**Goal:** Overcome raw HTTP request limitations to allow agents to interact with modern, JS-heavy web applications.
- **Playwright CPU Crawling:** Introduce a `browser_crawl` skill using Playwright/Selenium. Allows the agent to fully render pages on the CPU, executing JavaScript, managing cookies, and bypassing basic bot protections.
- **WSLg Headed Mode:** For rare occurrences or complex visual tasks, configure Playwright to run in headed mode, leveraging Windows 11 WSLg to project the full GUI browser onto the desktop for visual debugging or Vision coordinate clicking.

---

## v0.41 — Local Vision & Multi-Modal Orchestration
**Goal:** Enable fleet agents to process visual data completely offline, managing GPU constraints dynamically.
- **Local Vision Models:** Integrate support for local multimodal models (e.g., `llava`, `minicpm-v`, `qwen-vl`) via Ollama for analyzing browser screenshots, chart data, and physical environment feeds (e.g., Home Assistant cameras).
- **VRAM Rotation Flow:** Implement a model rotation queue in `hw_supervisor.py`. When a vision task is dispatched, evaluate available VRAM. If it fits, load it alongside the current worker model.
- **Eviction & Restoration:** If VRAM is too constrained, temporarily evict the primary LLM (shifting active text generation to the 0.6b CPU maintainer model), load the vision model on the GPU, execute the visual inference, and gracefully restore the original LLM state once complete.

---

## Parallel Track: Platform (Cross-Platform Support)

> These items run in parallel to version milestones. They don't bump version numbers — they are infrastructure improvements that land alongside regular releases.

### PT-1: Platform Abstraction

- `FleetBridge` ABC with `WslBridge` (Windows) and `DirectBridge` (Linux/Mac) implementations
- Replace all `wsl()` / `wsl_bg()` calls with `bridge.run()` / `bridge.run_bg()`
- Platform detection via `sys.platform` at startup
- Conditional `CREATE_NO_WINDOW` flags (Windows only)
- `_cpu_name()` platform branching: `winreg` → `/proc/cpuinfo` → `sysctl`
- `Path.home()` over `USERPROFILE` env var

### PT-2: Cross-Platform Build

- `build.py` replacing `build.bat` — auto-detects `--add-data` separator (`;` vs `:`)
- Skips `pynvml` hidden-import on macOS
- Icon format conversion (`brick.ico` → `brick.icns` for macOS)
- GitHub Actions CI workflow: 3-platform build matrix (Windows/Linux/macOS)

### PT-3: Platform Packaging

- **Linux:** AppImage packaging, `.desktop` file generation
- **macOS:** `.app` bundle, DMG creation, code signing + notarization notes
- **Installer abstraction:** Platform-conditional install/uninstall (registry on Windows, file copy + .desktop on Linux, /Applications on macOS)
- **Updater:** Replace `.bat` trampoline with `exec` self-replacement on Linux/macOS

### PT-4: Platform Testing

- Smoke test per platform in CI (headless, `--fast` mode)
- Platform-specific troubleshooting matrix (documented in `OPERATIONS.md`)
- Steam Deck (SteamOS/Arch) validation: CPU-only Ollama, Desktop Mode GUI

---

## Parallel Track: Cost Intelligence (Token Usage & Optimization)

> Token-level cost tracking, delta comparison, and optimization feedback loop. Blueprint: `BigEd/qa_token_blueprint.md`.

### CT-1: Usage Capture

- `usage` table in `fleet.db` (schema in blueprint Section 1)
- `db.log_usage()` + `db.get_usage_summary(period, group_by)`
- `_call_claude()` extracts `resp.usage` and logs after every API call
- `_call_gemini()` equivalent (if token data available from Gemini SDK)
- `call_complex()` signature extended: `skill_name`, `task_id`, `agent_name` passed through
- Usage logging wrapped in try/except — must never break skill execution

### CT-2: Cost Dashboard

- `/api/usage` endpoint: daily/weekly/monthly aggregates by skill, model, agent
- `/api/usage/delta` endpoint: compare two time ranges
- Dashboard widget: cost sparkline, top-5 expensive skills, cache hit rate
- `lead_client.py usage` CLI command showing cost breakdown table

### CT-3: Delta Comparison

- `compare_usage(period_a, period_b)` → per-skill delta report
- Version tagging: link usage records to git commit / version string
- Automated regression flag: >20% token increase on same skill = warning in dashboard
- Weekly summary → `knowledge/reports/usage_report_<date>.md`
- Delta format: `{metric, previous, current, delta_pct, direction}`

### CT-4: Optimization Loop

- Skill-level token budgets in `fleet.toml [budgets]`
- Budget enforcement: warn (not block) when skill exceeds budget
- Cache effectiveness report: savings from ephemeral cache vs full cost
- Prompt compression recommendations based on input_token trends
- Model routing validation: flag Opus usage where Sonnet/Haiku would suffice

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

### DT-1: Debug Report Infrastructure

- `generate_debug_report()` function collecting all diagnostic sources (platform, hardware, fleet state, logs, config)
- `_log_output` ring buffer (`collections.deque(maxlen=200)`) for launcher output persistence
- Global exception handler wrapping `launcher.py` main loop — auto-generates report on crash
- Report sanitization: strip API keys, anonymize paths

### DT-2: Issue Submission

- "Report Issue" UI button in launcher (sidebar or Config tab)
- Dialog: description field, reproduction steps, "Include logs" checkbox
- GitHub Issues integration via `gh` CLI (opt-in) — auto-create issue with report attached
- File export fallback — `.json` to Desktop for manual submission
- VS Code launch/task configs for dev workflow (`--debug` flag, "Generate Debug Report" task)

### DT-3: Resolution Tracking

- `data/resolutions.jsonl` schema: report_id → fix_commit → regression_test → status
- Commit convention: `fix(component): description [report:uuid]`
- Regression test linking: every P0/P1 fix must reference a test case
- Dashboard endpoint `/api/resolutions` serving resolution stats
- Ingestion script to append resolutions after fix ships

### DT-4: Stability Analysis

- Pattern detection queries on `resolutions.jsonl` (top components, platform distribution, MTTR)
- Release validation checklist: all P0/P1 resolved + regression tests pass before tagging
- Optional fleet skill (`skill_stability_report.py`) for periodic analysis → `knowledge/reports/`
