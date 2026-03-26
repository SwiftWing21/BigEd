# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Skills | 96 standalone + 6 suites (was 132 files) | 2026-03-26 |
| Smoke Tests | 40/40 (Python) | 2026-03-26 |
| Rust Tests | 116+ | 2026-03-25 |
| Dashboard Endpoints | 26 (Rust) + 236 (Python) | 2026-03-25 |
| DB Tables | 9 (Rust schema) | 2026-03-25 |
| Branch | main | 2026-03-26 |
| Rust Crates | 6 (core, supervisor, server, bridge, gui, wasm) | 2026-03-25 |
| Rust Phase | All 6 phases complete + 18 audit fixes | 2026-03-25 |
| Helpers | 11 (_contract, _knowledge, _llm_parse, _dispatch, _report, _http, _models, _flywheel_rubric/grading/audit, _oss_core) | 2026-03-26 |

## Last Session

**Date:** 2026-03-25 → 2026-03-26
**Session:** VS Code Claude Code

### Skill Plugin Restructure — Complete (Phases A-D)

Full audit and restructure of 132 fleet skills into a formal plugin architecture.

**Phase A — Contract Foundation:**
- Created `_contract.py` validator (SKILL_NAME, DESCRIPTION, VERSION, run signature checks)
- Added worker.py result coercion safety net (str→dict)
- Fixed 27 str-returning skills (274 `json.dumps` occurrences → direct dict returns)
- Fixed 18 non-standard run() signatures (8 broken `log` param, 3 `task/context` naming, 7 `log=None`)
- Fixed 2 raw sqlite3 violations (doc_freshness, _flywheel_core)
- Added VERSION + COMPLEXITY constants to all 125 skills
- Refactored providers.py to read COMPLEXITY from skill modules (fallback to dict)

**Phase B — Shared Helpers (5 new + 1 decomposition):**
- `_knowledge.py` — dir management + date-stamped file save (used by 57 skills)
- `_llm_parse.py` — JSON extraction from LLM responses (replaces 6 implementations)
- `_dispatch.py` — action routing boilerplate for suites
- `_report.py` — ReportBuilder class for markdown reports
- `_http.py` — URL probing with timeout/latency
- Decomposed `_flywheel_core.py` (891 lines) → `_flywheel_rubric.py` + `_flywheel_grading.py` + `_flywheel_audit.py`

**Phase C — Suite Consolidation (6 suites + 2 merges):**
- `ml_train_suite.py` — 4 ML training skills (22% reduction)
- `model_suite.py` — 3 model management skills, unified hardware detection (17% reduction)
- `code_suite.py` — 6 code review/quality skills (14% reduction)
- `git_suite.py` — 3 git/GitHub skills (52% reduction)
- `security_suite.py` — 6 security skills, advisory pipeline preserved (34% reduction)
- `skill_lifecycle_suite.py` — 5 lifecycle skills, gate semantics preserved (35% reduction)
- Merged oss_review_swarm → oss_review (swarm as mode flag)
- Merged config_drift_detect → config_validate (drift as action)
- SUITE_ROUTING shim in worker.py (27 legacy→6 suites, with kill-switch)
- 29 old files deprecated (prefixed `_deprecated_`)

**Phase D — Polish:**
- SUITE + TAGS metadata on 96 standalone skills
- health_check() integrated into smoke tests
- Air-gap whitelist unified with REQUIRES_NETWORK
- Marketplace manifest auto-generation from contract metadata
- Logging added to 68 skills that lacked it

**Swimlane Diagrams — 16 workflows documented:**
- 4 grouped Mermaid charts in `docs/swimlanes/` (1,383 lines)
- Core Runtime, Intelligence Loop, Operations, Enterprise
- README with rendering instructions

### Key Metrics
- Skills: 132 files → 96 standalone + 6 suites + 29 deprecated + 11 helpers
- ~3,200 lines saved (11% reduction)
- 27 double-serialization bugs fixed
- 8 broken-at-runtime skills fixed
- Smoke tests: 38/38 → 40/40
- Spec: `docs/superpowers/specs/2026-03-25-skill-plugin-restructure-design.md`

### Previous Session (2026-03-24 → 2026-03-25)
Complete Rust rewrite (Phases 0-6), 6-agent audit, 18 fixes, v0.9.0 benchmarks

## Next Priorities

- [ ] Remove 29 `_deprecated_` files after one release cycle of suite routing validation
- [ ] Migrate skills to use new helpers (_knowledge, _report, _llm_parse) — currently created but not yet adopted
- [ ] Render swimlane PNGs from Mermaid source (needs `mmdc` / Mermaid CLI)
- [ ] API key re-enable + live fleet testing with Rust server
- [ ] Run skills through PyO3 bridge with new suite routing (SUITE_ROUTING shim needs bridge awareness)
- [ ] Fix WASM compilation (cfg-gate tokio/rusqlite/reqwest behind desktop feature)
- [ ] Settings tab: wire interactive form controls (currently placeholder)
- [ ] Files tab + Graph View tab (missing from GUI)
- [ ] NeuralLanes animation (pulses, Bezier edges — currently static bars)
- [ ] WebSocket + MessagePack transport (spec requirement, currently HTTP polling)
- [ ] Auth middleware for server endpoints
- [ ] 24h stability test (supervisor + server running continuously)
- [ ] v1.0.0 graduation after bug testing

## Open Questions / Decisions Needed

- Suite routing: monitor for regressions before removing deprecated files — kill-switch is `suite_routing_enabled = false` in fleet.toml
- API key: re-enable after overnight $4 spend incident — need cost guard improvement?
- WASM: worth pursuing now or defer until native GUI is proven?
- Theme colors: spec says different values than implementation — which is canonical?
- db module interception (Rust-backed Python db): implement or defer?

## Known Issues

- mingw toolchain needs dlltool on PATH for bench/release builds (WinLibs mingw64/bin)
- shlwapi.lib generated manually for mingw (eframe dep) — fragile, breaks on toolchain update
- WASM target fails (tokio/rusqlite not WASM-compatible without feature gates)
- ~20 skills return str not dict — double-serialization in result_json
- Timeout doesn't cancel Python execution (GIL held, resource leak on hung skills)
- TOML write-back loses comments
- 3 temp files in fleet/ (tmp*.json) — safe to delete

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
