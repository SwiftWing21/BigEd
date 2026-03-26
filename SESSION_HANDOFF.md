# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Skills | 96 standalone + 6 suites (was 132 files) | 2026-03-26 |
| Smoke Tests | 42/42 (Python) | 2026-03-26 |
| Rust Tests | 116+ | 2026-03-25 |
| Dashboard Endpoints | 26 (Rust) + 242 (Python, +6 gate/tasks) | 2026-03-26 |
| DB Tables | 9 (Rust schema) | 2026-03-25 |
| Branch | main | 2026-03-26 |
| Rust Crates | 6 (core, supervisor, server, bridge, gui, wasm) | 2026-03-25 |
| Rust Phase | All 6 phases complete + 18 audit fixes | 2026-03-25 |
| Helpers | 11 (_contract, _knowledge, _llm_parse, _dispatch, _report, _http, _models, _flywheel_rubric/grading/audit, _oss_core) | 2026-03-26 |
| Graph Views | 6 (fleet-overview, universe, data-flow, bottleneck-detector, knowledge-graph, training-pipeline) | 2026-03-26 |
| Graph Layouts | 4 (Radial, Radial Cluster, Cluster/fcose, Grid) | 2026-03-26 |

## Last Session

**Date:** 2026-03-26 (evening)
**Session:** VS Code Claude Code

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

- [ ] Root-cause the persistent 404 from Ollama `/api/generate` during skill_draft — Ollama responds 200 on direct curl but workers get 404 intermittently. May be model unload timing or request format edge case
- [ ] 6 pending tasks still waiting for non-core agents (coder_2, security, analyst, coder_3) — need to verify scale-up triggers them
- [ ] Resizable dashboard panels (Split.js for web, PanedWindow for tkinter) — ref: `memory/reference_panel_layout.md`
- [x] Remove 29 `_deprecated_` files after one release cycle of suite routing validation — DONE (2026-03-26)
- [ ] Migrate skills to use new helpers (_knowledge, _report, _llm_parse) — created but not yet adopted
- [ ] Render swimlane PNGs from Mermaid source (needs `mmdc` / Mermaid CLI)
- [ ] Hardcoded fonts in launcher.py: 46 instances of "Consolas"/"RuneScape" → theme constants
- [ ] Run skills through PyO3 bridge with new suite routing
- [ ] Fix WASM compilation (cfg-gate tokio/rusqlite/reqwest behind desktop feature)
- [ ] WebSocket + MessagePack transport (spec requirement, currently HTTP polling)
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
