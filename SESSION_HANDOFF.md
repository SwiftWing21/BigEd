# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Smoke Tests | 51/51 | 2026-03-29 |
| Factorio Module Tests | 81/81 | 2026-03-29 |
| Branch | main | 2026-03-29 |
| Factorio Training | Phase 1, 5/8 lessons (body, find ore, mine iron, mine stone, craft gears) | 2026-03-29 |

## Last Session

**Date:** 2026-03-29 (session 13 — marathon)
**Session:** VS Code Claude Code — Death Spiral → Full ML Training Pipeline

### Summary
Went from "nothing works, death spiral" to an RL+LLM hybrid agent training in Factorio, passing 5 curriculum lessons. 11 commits across infrastructure bugs, Factorio integration, and training pipeline.

### Commits (11)
1. `ded93a9` — Death spiral fix (pool exhaustion, load_config, scheduler, fleet lifecycle)
2. `1950808` — RCON password env var pass-through
3. `9929202` — Curriculum path fix
4. `727ed5c` — Headless character creation (2-strategy)
5. `070e026` — Disconnected player guard + Factorio 2.0 max_health fix
6. `ebf1c20` — Map gen settings with ore resources
7. `1958ba8` — Preserve ore/trees on episode reset + bigger starting area
8. `d6446ce` — Buff Phase 1 starting items (50 iron, 20 copper, etc.)
9. `9b730f6` — Hybrid LLM teacher for RL training (500-step stuck threshold)
10. `1495cde` — ML tick updates bridge status (dashboard "Running" fix)
11. `e295628` — Strip /biged-cmd prefix (fixed ALL "invalid JSON" errors)

### Factorio Agent Status
- **Hybrid RL+LLM**: PPO policy explores, LLM teacher intervenes when stuck 500+ steps
- **5/8 Phase 1 lessons passed**: Body check, Find iron, Mine iron, Mine stone, Craft gear wheels
- **Stuck on lesson 5**: "Place a stone furnace" — agent crafts too many inserters/gears, runs out of stone-furnace items
- **Agent uses connected player** (swiftwing) — user must be connected for mine/craft/place to work
- **Ore replenishes** on episode reset (5M per tile, 8 patches around spawn)

### Key Architecture Decisions
- **Hybrid teacher**: After 500 steps on same lesson, LLM (Ollama qwen3:8b) generates action plan, executes via RCON, backs off 50 ticks
- **Action translator bug fixed**: `/biged-cmd` prefix was being passed to `exec_cmd` which expects raw JSON
- **Episode reset preserves**: resources and trees (was destroying everything)
- **Bridge status**: `update_status(True, ...)` now called in ML tick path (was missing)

### Known Issues
1. **Agent needs connected player** — standalone headless character can observe but can't mine/craft/place. User must be in-game. Long-term: need programmatic player creation or RCON-based inventory management
2. **setup_and_launch holds RCON** — blocks bridge connection until killed. Needs to disconnect after health check
3. **RCON password regen on every Start** — `setup_and_launch` generates new password even when server is already running
4. **Dependency resolver needed** — user requested: "if not in inventory → try craft → if can't craft → check has vs need → acquire diff". This would let the teacher work backwards from goals
5. **Dashboard Refresh button** — works for data fetch but "Running" display was broken (fixed), needs testing
6. **Stale RUNNING tasks** — 2,219 ghost tasks from dead workers need sweep
7. **3 duplicate web_app.py processes** — investigate multi-spawn

**Next priorities:**
1. **Dependency resolver for teacher** — backwards-chaining from lesson goals through crafting recipes to raw resources
2. **Programmatic player** — agent should work without user connected
3. **Curriculum tuning** — lesson 5+ needs better item budgeting or explicit craft-first hints
4. **Dashboard training viz** — reward curves, action distribution charts
5. **setup_and_launch RCON cleanup** — disconnect after health check
