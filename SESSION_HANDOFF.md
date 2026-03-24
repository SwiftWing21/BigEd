# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.400.00b | 2026-03-23 |
| Skills | 130+ | 2026-03-23 |
| Smoke Tests | 33/33 | 2026-03-23 |
| Dashboard Endpoints | 190+ | 2026-03-23 |
| DB Tables | 20 | 2026-03-23 |
| Branch | main | 2026-03-23 |

## Last Session

**Date:** 2026-03-23 ~11:30pm
**What was done:**
- P1 skills (2): outcome_tracker, prompt_optimize
- P2 skills (8): knowledge_digest, api_health_probe, pair_program, doc_generate, config_drift_detect, data_pipeline, webhook_dispatch, skill_dependency_map
- P3 skills (4): model_eval_framework, mcp_probe, agent_personality_tune, cross_fleet_knowledge_sync (in progress)
- Dashboard: flowchart rendering (Cytoscape dagre), Views sidebar panel
- 17 orphaned docs archived to docs/archive/ with index
- FRAMEWORK_BLUEPRINT.md, OPERATIONS.md, ROADMAP.md updated (stale counts/versions)
- Session handoff system created (this file + doc_freshness skill)

**Commit:** `c04734f` feat: 10 skills, flowchart rendering, Views sidebar, doc archival

## Next Priorities

- [ ] Test graph views with fleet running (verify live data rendering)
- [ ] FRAMEWORK_BLUEPRINT.md deeper rewrite (architecture tree updated, but body still v0.41-era)
- [ ] Verify SSE client integration in launcher (sse_client.py wired up?)
- [ ] P3 skill testing with live fleet
- [ ] CONTRIBUTING.md smoke test count update (says 22/22, now 33/33)

## Open Questions / Decisions Needed

- MiniMax provider: re-enable when API key available?
- FRAMEWORK_BLUEPRINT.md: full rewrite vs incremental updates?
- Graph views: need visual QA with fleet running

## Known Issues

- 3 temp files in fleet/ (tmp*.json) — safe to delete
- FRAMEWORK_BLUEPRINT.md body (sections 2+) still describes v0.41 module system — header/tree updated but deeper sections need rewrite
- Some skills reference `prompt` and `result` columns on tasks table but schema has `payload_json` and `result_json`

## Doc Freshness

Run `python fleet/skills/doc_freshness.py` standalone or dispatch via fleet to audit all docs for stale values (skill counts, version numbers, endpoint counts, smoke test counts).

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
