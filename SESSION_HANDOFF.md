# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Smoke Tests | 51/51 | 2026-03-30 |
| Factorio Tests | 129/129 | 2026-03-30 |
| Branch | main | 2026-03-30 |
| Factorio Training | Phase 1, 4/8 auto, fully headless, hybrid RL+LLM + spatial memory | 2026-03-30 |

## Last Session

**Date:** 2026-03-30 (session 15 — spatial memory + headless autonomy)
**Session:** VS Code Claude Code — Spatial Memory + Full Headless Agent

### Summary
Built persistent spatial memory for the RL agent (remembers resource/entity locations beyond 64x64 grid), fixed headless autonomy (agent now crafts/places/mines without a human player connected), and preserved infrastructure across episode resets. Agent is training fully autonomously.

### Commits
1. `ead67fd` — Spatial memory design spec
2. `85996f0` — Spec v2 with integration details
3. `2732a98` — Implementation plan (5 tasks)
4. `c9265b3` — SpatialMemory class — entries, queries, features, state updates (21 tests)
5. `0084dfc` — StateEncoder accepts SpatialMemory, dynamic feature_dim 64/80 (5 tests)
6. `4309398` — Bridge wiring — tick updates + post-reset survey
7. `30671bc` — Preserve player-built infrastructure on episode reset (furnaces, drills, belts, etc.)
8. `de6ac6a` — Starting items use agent script inventory (works headless)

### Key Architecture Changes

**Spatial Memory (`fleet/factorio/spatial_memory.py`):**
- Sparse dict storing ResourceEntry and EntityEntry with world coordinates
- Updated every tick from existing get_state data (zero RCON overhead)
- Wide-area survey (200 tile radius) on episode reset
- `get_features()` returns 16 floats: bearing/distance to nearest resources + infrastructure counts
- Injected into state encoder, extending feature_dim from 64 to 80

**Headless Autonomy:**
- `get_agent_context()` provides script-owned inventory via `game.create_inventory(80)` when no player connected
- All exec_cmd actions (craft, place, mine, move, research) use `ctx.inventory` — works with both player and script inventory
- `_give_starting_items` now inserts into script inventory, not player inventory
- Agent trains fully without human connected to Factorio

**Episode Reset Preservation:**
- soft_reset keeps: resources, trees, furnaces, mining-drills, assembling-machines, transport-belts, inserters, electric-poles, boilers, generators, labs, containers
- Only clears: non-infrastructure entities (loose items, projectiles, etc.)

### Agent Status
- **Training autonomously** — no player connection needed
- **Phase 1, 4/8 lessons** auto-passed (body check, find iron, mine iron, mine stone)
- **Crafting works headless** — "Craft 10x stone-furnace -> OK"
- **Hybrid teacher** fires after 500 steps stuck, runs on qwen3:1.7b CPU (no FPS impact)
- **Spatial memory active** — accumulating resource/entity locations per tick
- **Game speed 2x** (10x disconnects spectator, 4x stutters)

### Known Issues
1. **RL exploration inefficiency** — agent crafts random items instead of lesson goals. Teacher helps but LLM plans aren't always correct
2. **Dependency resolver not yet live-tested** — wired into teacher but `recipes.json` only has 27 starter recipes
3. **Full recipe dump needed** — `dump_recipes.lua` ready but not run in Factorio yet
4. **setup_and_launch holds RCON** — blocks bridge until killed
5. **Stale RUNNING tasks** — 2,219 ghost tasks need sweep on boot
6. **Dashboard game speed slider** — planned but not built

**Next priorities:**
1. **Live test dependency resolver** — verify it produces correct craft chains for lesson 5+
2. **Full recipe dump** — run dump_recipes.lua, replace starter recipes.json
3. **Curriculum tuning** — better reward shaping for lesson 4+ (craft specific items)
4. **Dashboard training viz** — reward curves, spatial memory map display
5. **Dashboard game speed slider** — adjustable from Factorio panel
6. **LLM cognitive map** (future) — periodic strategic survey, expansion planning
