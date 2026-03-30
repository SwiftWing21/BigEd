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
| Factorio Training | Phase 1, 5/8 lessons, hybrid RL+LLM, 4x speed | 2026-03-29 |

## Last Session

**Date:** 2026-03-29 (session 13 — marathon, ~6 hours)
**Session:** VS Code Claude Code — Death Spiral → Full Hybrid ML Training Pipeline

### Summary
Went from "nothing works, death spiral" to a hybrid RL+LLM agent training in Factorio, passing 5/8 Phase 1 curriculum lessons. 14 commits across infrastructure, Factorio integration, training pipeline, and performance tuning.

### Commits (14)
1. `ded93a9` — Death spiral fix (pool exhaustion, load_config, scheduler, fleet lifecycle)
2. `1950808` — RCON password env var pass-through
3. `9929202` — Curriculum path fix (fleet.toml → fleet/factorio/curricula)
4. `727ed5c` — Headless character creation (2-strategy)
5. `070e026` — Disconnected player guard + Factorio 2.0 max_health fix
6. `ebf1c20` — Map gen settings with ore resources
7. `1958ba8` — Preserve ore/trees on episode reset + bigger starting area
8. `d6446ce` — Buff Phase 1 starting items (50 iron, 20 copper, etc.)
9. `9b730f6` — Hybrid LLM teacher for RL training (500-step stuck threshold)
10. `1495cde` — ML tick updates bridge status (dashboard "Running" fix)
11. `e295628` — Strip /biged-cmd prefix (fixed ALL "invalid JSON" errors)
12. `5e1decd` — Replenish ore on episode reset (5M per tile)
13. `a9c95ac` — Non-blocking hybrid teacher (RL trains while LLM thinks)
14. `7a1034a` — CPU model for teacher (qwen3:1.7b), game speed 4x

### Factorio Agent Architecture
- **Hybrid RL+LLM**: PPO neural net explores continuously, LLM teacher (qwen3:1.7b CPU) intervenes when stuck 500+ steps
- **Non-blocking**: Teacher runs as background asyncio task, actions queued and executed one per tick interleaved with RL
- **Episode reset**: preserves ore/trees, replenishes to 5M/tile, gives starting items (50 iron, 20 copper, 4 furnaces, etc.)
- **Game speed**: 4x (10x disconnects spectator client)
- **Teacher model**: qwen3:1.7b on CPU (8b GPU model tanked Factorio FPS 120→20)

### Current Training State
- **5/8 Phase 1 lessons**: Body check, Find iron, Mine iron, Mine stone, Craft gear wheels
- **Stuck on lesson 5**: "Place a stone furnace" — agent uses up starting furnaces crafting other things
- **Agent uses connected player** (swiftwing) — user must be in-game for mine/craft/place

### Known Issues (Priority Order)
1. **Dependency resolver needed** — user requested: backwards-chain from goal → craft recipe → acquire raw materials. This would let teacher/RL solve "place furnace" by first crafting one from stone
2. **Agent needs connected player** — standalone headless character can observe but can't mine/craft/place. Need programmatic player creation or RCON inventory management
3. **setup_and_launch holds RCON** — blocks bridge connection until killed. Needs disconnect after health check
4. **RCON password regen on Start** — generates new password even when server already running
5. **Stale RUNNING tasks** — 2,219 ghost tasks need sweep on boot
6. **Dashboard Refresh button** — data fetches work, visual status fixed, needs more testing

**Next priorities:**
1. **Dependency resolver for teacher** — backwards-chaining crafting tree
2. **Programmatic player** — agent works without user connected
3. **Curriculum tuning** — lesson 5+ item budgeting
4. **Dashboard training viz** — reward curves, action charts
5. **setup_and_launch RCON cleanup**
