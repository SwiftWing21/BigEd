# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-31 |
| Smoke Tests | ~34/51 passing (DB tables missing when fleet not running) | 2026-03-31 |
| Tests (pytest) | 465/477 passed (12 Factorio failures, expected) | 2026-03-31 |
| Factorio Tests | 248 passing | 2026-03-31 |
| Branch | main | 2026-03-31 |
| Dashboard | 3360 lines (decomposed from 5680 via 4 phases) | 2026-03-31 |
| Skills | 125 | 2026-03-31 |
| DB Tables | 29 | 2026-03-31 |
| Blueprints | 10 (factorio, sse, mode, federation, tasks, deploy, settings, ingest, modules, views) | 2026-03-31 |
| Factorio Training | Phase 1, deterministic building tools built, LLM-first pivot | 2026-03-31 |

## Last Session

**Date:** 2026-03-31 (sessions 0331 and 0331b)
**Session:** Two back-to-back sprints — tech debt execution + Factorio overhaul

### Session 0331 Summary (~40 commits)

Massive tech debt execution sprint across the entire project. Four waves:

1. **Audit P0 Fixes (10 tasks):** JWT bypass fixed (sso.py), path traversal blocked (tenant_admin + marketplace), inter-fleet auth added (geo_fleet), conn.close bug in providers, billing quota enforcement, raw sqlite3 replaced (process_control), filesystem guard audit log severity raised, pytest added to CI.

2. **Quick Wins (13 tasks):** Ruff linter in CI (non-blocking), Python 3.13 in test matrix, Lua sync script, bridge hasattr→__init__ fix, leash dict dedup, dead DEV_MODE removal, dashboard version from config, 36 sys.path.insert calls removed, Lua tracked_items synced (18→32), model prefs from fleet.toml, ActionType enum in reward.py, hot-path math imports to module level.

3. **Enterprise Wiring (8 tasks):** `billing.record_usage()` wired into worker, `guardrails.evaluate_output()` wired, RBAC unified (3→5 roles: admin/operator/developer/auditor/viewer), `tenant_api_keys` table + `validate_api_key()` added, `filesystem_guard` enforce=true by default, experiment auto-window wired, marketplace uninstall auth, SSO sessions persisted to DB.

4. **Dashboard Decomposition Phases 1-4 (5680→3360 lines):**
   - Phase 1: `dashboard_utils.py` (shared helpers)
   - Phase 2: `factorio_blueprint.py` (17 routes, -365 lines)
   - Phase 3: `sse_blueprint.py` + `alerts.py` (-338 lines)
   - Phase 4: 5 domain blueprints (mode/federation/tasks/deploy/settings, -1521 lines)

5. **Testing (27 new tests):** `tests/test_db.py` (10), `tests/test_providers.py` (10), `tests/test_health_monitor.py` (7), conftest.py with tmp_db fixture.

### Session 0331b Summary

Factorio overhaul — audit fixes, creative mode, deterministic building.

**Root cause found:** RL alone can't build factories. Agents placed 4 furnaces in 889 steps but no belts/inserters. Pivot: use LLM with deterministic tools (zones/templates), not RL learning placement from scratch.

1. **Audit Fixes (15 tasks):** Entropy 0.01→0.2 (was collapsing), PPO mask fix, ore proximity reward, max_attempts auto-skip. Blueprint RCON name `biged-blueprint`→`biged_blueprint` (every stamp was silently failing). Silent failure logging, None guards, `weights_only=True`, direction /15, `torch.from_numpy`.

2. **Creative/Sandbox Mode:** Lua exec_cmd auto-inserts items for place/craft/insert. Auto-clear crash debris + rocks + trees, auto-place infinity chests, `research_all`. `get_agent_context` now adopts existing NPC characters (no more duplicates).

3. **Deterministic Factory Building (10 tasks):**
   - `zone_manager.py` — rectangular area reservations (MINING/SMELTING/ASSEMBLY/etc.)
   - `layout_templates.py` — iron_smelter_4x, basic_power, drill_array_4x, output_chest
   - `belt_router.py` — A* pathfinding between points, collision-aware
   - `factory_builder.py` — IronLinePlan (4 drills → belts → 4 furnaces → chest)
   - `/api/build/iron_line` endpoint for one-click litmus test

4. **FPM Agent Count:** Spinbox (1-8) in Factorio Process Manager, saves to fleet.toml, auto-restarts bridge.

### Known Issues

1. **Tick loop freeze bug** — Step counter stops after phase advance, `storage.agent_chars` references go stale. Adopt-existing fix committed in Lua, needs server restart to take effect.
2. **RL exploration inefficiency** — Dependency resolver wired into teacher but `recipes.json` only has starter recipes. Full dump via `dump_recipes.lua` not yet run.
3. **Dashboard Phase 5 deferred** — ~1400 lines remain in dashboard.py (monitoring, metering, ops, knowledge blueprints + app factory).
4. **12 Factorio test failures** — `test_action_translator` direction names, `test_curriculum` load assertion. Not blocking other work.
5. **account_review.py** — raw `sqlite3.connect()` for launcher DB (different DB from fleet.db, intentional but worth revisiting).

### Commits (recent 30)

```
f15a325 refactor(dashboard): extract mode/federation/tasks/deploy/settings blueprints (Phase 4)
a8370e1 test: add unit tests for db.py and providers.py
cb77fb3 test: add unit tests for health_monitor.py circuit breakers and agent health
9af1086 feat(enterprise): add tenant_api_keys table + validate_api_key() function
07b1dc4 fix(security): unify RBAC_ROLES with PERMISSIONS — developer/auditor roles now work
54050b7 refactor(dashboard): extract SSE broadcaster + alert monitor to separate modules
cca9d73 refactor(dashboard): extract Factorio endpoints to factorio_blueprint.py
6eed3a7 refactor(dashboard): extract shared utils to dashboard_utils.py
4009d22 feat(enterprise): wire guardrails.evaluate_output + billing.record_usage into worker
88fc096 feat(testing): make db.py testable with in-memory DB, add conftest fixture
1786f92 feat(enterprise): persist SSO sessions to DB
5d43cf8 feat(enterprise): enable filesystem_guard by default
7ecf1f7 fix(factorio): FPM crash — gray variable not defined in _build_ui scope
b64e763 feat(factorio): IronLinePlan — deterministic litmus test factory layout
4123988 feat(factorio): /api/build/iron_line endpoint + blueprint-first curriculum
283bc9e refactor(dashboard): fix hardcoded version, remove 36 redundant sys.path.insert calls
40ae6d3 feat(factorio): A* belt routing — deterministic pathfinding between points
491de2d feat(factorio): layout templates — pre-computed entity coordinates for factory patterns
a2c9137 feat(factorio): zone system — rectangular area reservations for factory layout
2df6d6b feat(factorio): agent count control in FPM — spinbox (1-8) with Apply & Restart
489d30a fix(factorio): blueprint handler auto-inserts items in creative mode
c7c650e fix(factorio): blueprint RCON call used wrong name — every stamp was silently failing
ac2a158 fix(security): add _require_role to marketplace uninstall endpoint
868e443 fix(experiment): wire _in_auto_window() into _should_auto_approve() — was dead code
5e8762c refactor(process_manager): read model preferences from fleet.toml config
188aad5 docs(smoke): fix test count in docstring (33->51/54)
3f7ccdf refactor(launcher): remove dead DEV_MODE double-assignment
6bad0e7 refactor(factorio): use ActionType enum in reward.py, move hot-path math imports
4fcf426 fix(factorio): sync Lua tracked_items with Python TRACKED_ITEMS (18→32 items)
ccc3c50 feat(factorio): add sync_control_lua.py — eliminates manual copy between lua_mod and server_data
```

## Next Priorities

1. **Dashboard Phase 5** — `docs/superpowers/plans/2026-03-31-dashboard-decomposition.md` — monitoring, metering, ops, knowledge blueprints + app factory. Reduces dashboard.py from ~3360→~1400 lines.
2. **Factorio Litmus Test** — Run `/api/build/iron_line` with Factorio running. Verify IronLinePlan places correctly in creative mode.
3. **Fix tick loop freeze** — Restart bridge server after Lua changes. Verify `storage.agent_chars` adopt-existing fix works.
4. **Fix 12 Factorio test failures** — `test_action_translator` direction names, `test_curriculum` load assertion.
5. **Factorio tech debt** — `docs/superpowers/plans/2026-03-31-factorio-tech-debt.md` — bridge extraction, reward config, phase encoder, Lua refactor, GAE/checkpoints.
6. **Full recipe dump** — Run `dump_recipes.lua` in Factorio, replace starter `recipes.json` with full game data.
7. **LLM-first curriculum** — Now that deterministic tools exist (zones/templates/belt router), design curriculum where LLM uses them to build factory stages.
