# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Smoke Tests | 51/51 | 2026-03-30 |
| Factorio Module Tests | 103/103 | 2026-03-30 |
| Branch | main | 2026-03-30 |
| Factorio Training | Phase 1, 5/8 lessons, hybrid RL+LLM + dependency resolver | 2026-03-30 |

## Last Session

**Date:** 2026-03-30 (session 14 — dependency resolver)
**Session:** VS Code Claude Code — Factorio Dependency Resolver

### Summary
Built the Factorio agent's dependency resolver — a backwards-chaining system that converts lesson goals into ordered action sequences by traversing a recipe DAG. Design spec → implementation plan → 10-task TDD execution via subagent-driven development. 103 Factorio tests passing, 51/51 smoke tests.

### Commits (11)
1. `b413025` — Design spec accuracy fixes (cooldown values, pseudocode notes)
2. `ada6bf0` — Implementation plan (10 tasks, TDD)
3. `f09b648` — RecipeDAG: JSON loader, resolve, yield, cycle detection (11 tests)
4. `ee559a2` — Dependency resolver: backwards chaining, inventory math, abstract actions (4 tests)
5. `28915ba` — Advanced resolver tests: yield, smelting, infrastructure, multi-result (11 more tests)
6. `c3b6579` — Criteria parser, starter recipes.json (27 recipes), Lua dump script (9 tests)
7. `e67f1a4` — Fix: restore test files to correct location
8. `78cc2b8` — RCON sync + wire resolver into hybrid teacher (3 tests + brain context)
9. `375b99e` — Bridge /api/shutdown endpoint + dashboard /api/factorio/restart

### New Files
- `fleet/factorio/recipe_dag.py` — RecipeDAG class (JSON loader, graph traversal, cycle detection, RCON sync)
- `fleet/factorio/dependency_resolver.py` — Backwards-chaining resolver (resolve, ResolutionPlan, criteria parser)
- `fleet/factorio/recipes.json` — 27 vanilla Phase 1-2 recipes
- `fleet/factorio/lua_mod/dump_recipes.lua` — Data-stage recipe dump for full game graph
- `tests/factorio/test_recipe_dag.py` — 11 tests
- `tests/factorio/test_dependency_resolver.py` — 16 tests
- `tests/factorio/test_criteria_parser.py` — 9 tests
- `tests/factorio/test_rcon_sync.py` — 3 tests

### How the Resolver Works
1. Teacher calls `parse_criteria_to_items()` on lesson criteria (e.g., "entities.stone-furnace >= 1")
2. `resolve()` backwards-chains through RecipeDAG: goal → recipe → ingredients → raw resources
3. Tracks running inventory, handles yield (copper-cable=2), smelting infrastructure, fuel
4. Returns `ResolutionPlan` tree + `to_actions()` flat list of abstract actions
5. Pure-craft goals bypass LLM entirely; partial goals inject summary into LLM prompt
6. Wired into `bridge.py:_teacher_generate_plan()` — runs before LLM, falls through gracefully

### Known Issues (Priority Order)
1. **Agent needs connected player** — standalone headless character can't mine/craft/place
2. **Resolver needs live testing** — wired in but not tested with running Factorio server
3. **Full recipe dump** — starter recipes.json has 27; Lua dump script ready for full ~2000 recipe extraction
4. **setup_and_launch holds RCON** — blocks bridge connection until killed
5. **RCON password regen on Start** — generates new password even when server already running
6. **Stale RUNNING tasks** — ghost tasks need sweep on boot

**Next priorities:**
1. **Live test resolver** — start Factorio, verify resolver produces correct action chains for lesson 5+
2. **Full recipe dump** — run dump_recipes.lua in Factorio, replace starter recipes.json
3. **Programmatic player** — agent works without user connected
4. **Curriculum tuning** — lesson 5+ should now work with resolver (furnace from stone)
5. **Dashboard training viz** — reward curves, action charts
6. **Dashboard game speed slider**
