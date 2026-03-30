# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Skills | 96 standalone + 6 suites (was 132 files) | 2026-03-26 |
| Smoke Tests | 51/51 | 2026-03-29 |
| Factorio Module Tests | 81/81 (state parser + ML + curriculum + episode) | 2026-03-29 |
| Ingest Sources | 18 (12 task + 5 RAG + 1 factorio-knowledge, ~2M+ rows) | 2026-03-27 |
| API Keys | 12 registered, 3 set | 2026-03-26 |
| Rust Tests | 116+ | 2026-03-25 |
| Dashboard Endpoints | 26 (Rust) + 256+ (Python, +/api/fleet/restart) | 2026-03-29 |
| DB Tables | 9 (Rust schema) | 2026-03-25 |
| Branch | main | 2026-03-29 |
| Rust Crates | 6 (core, supervisor, server, bridge, gui, wasm) | 2026-03-25 |
| Rust Phase | All 6 phases complete + 18 audit fixes | 2026-03-25 |
| ModuleHub | 9 modules registered (+ factorio) | 2026-03-29 |

## Last Session

**Date:** 2026-03-29 (session 13)
**Session:** VS Code Claude Code — Death Spiral → ML Agent Training

### Summary
Went from "nothing works, death spiral" to an RL agent training in Factorio with ore-rich maps, passing curriculum lessons. Major debugging session with 6 commits fixing cascading infrastructure bugs + Factorio integration.

### Commits (6)
1. `ded93a9` — **Death spiral fix:** DB connection pool exhaustion (thread-aware reaping), 7x `load_config()` → `_load_config()`, scheduler TypeError on bool affinity, fleet lifecycle (`/api/fleet/stop` rewrite, `/api/fleet/restart`, `auto_start` toggle)
2. `1950808` — **RCON password race:** pass password via `BIGED_RCON_PASSWORD` env var from `setup_and_launch` to bridge subprocess, `os.fsync()` after fleet.toml write
3. `9929202` — **Curriculum path:** `fleet.toml curriculum_dir` → `fleet/factorio/curricula`, CurriculumManager resolves relative paths from project root
4. `727ed5c` — **Headless character creation:** 2-strategy ensure_player (create_character API for connected players, surface.create_entity fallback for headless)
5. `070e026` — **Disconnected player guard:** don't assign character to disconnected players (Factorio API restriction), fix `prototype.max_health` → `entity.max_health` for Factorio 2.0
6. `ebf1c20` — **Map gen settings:** add iron/copper/coal/stone/uranium/oil/trees with generous richness (was barren map)

### Current Factorio Agent State
- **Agent is training:** Phase 1 (Bootstrap), 8 lessons, game speed 10x
- **Lesson 0 passed:** Body check (character alive, 250 HP)
- **Lesson 1 passed:** Find iron ore (ores present on map)
- Agent attempts: mine, move, craft, research, set_recipe, remove_entity
- **Headless server running** at RCON port 27015, bridge at 27016

### Known Issues (for next session)
1. **`"mine requires a connected player"`** — standalone character entity (Strategy 2) can't mine because it's not attached to a LuaPlayer. Need either: (a) implement mining via direct RCON entity manipulation, or (b) figure out how to create a connected player in headless mode, or (c) pre-join a spectator client
2. **`"invalid json"` on many actions** — move, research, craft, set_recipe all return `{"error": "invalid json"}`. The Lua action handlers may be returning malformed responses or the action translator is sending bad RCON commands
3. **soft_reset returns "no_player"** — episode manager can't reset properly, falls back to hard_reset (save + restart). Needs to handle headless character
4. **Dashboard Refresh button** doesn't work (browser cache? JS issue). Shift+right-click works.
5. **Stale RUNNING tasks** — 2,219 ghost tasks from dead workers, need sweep-to-FAILED on boot
6. **3 duplicate web_app.py processes** — investigate why 3 dashboard instances spawn
7. **`pythonw.exe` ignores SIGTERM** — `/api/fleet/stop` should use `p.kill()` on Windows

### Files Modified This Session
- `fleet/dashboard.py` — 7x load_config fix
- `fleet/scheduler.py` — affinity type guard
- `fleet/db.py` — connection pool rewrite
- `fleet/process_control.py` — fleet stop/restart endpoints
- `fleet/fleet.toml` — auto_start, curriculum_dir, RCON password
- `BigEd/launcher/launcher_tkinter.py` — auto_start check
- `BigEd/launcher/launcher_webview.py` — always start supervisor
- `fleet/factorio/lua_mod/control.lua` — headless character, disconnected player guard, max_health fix
- `fleet/factorio/bridge_config.py` — RCON password env override
- `fleet/factorio/setup_and_launch.py` — env var pass-through, fsync, map gen resources
- `fleet/factorio/curriculum_manager.py` — relative path resolution

**Next priorities:**
1. **Fix mining for headless character** — the #1 blocker for meaningful RL training
2. **Fix invalid JSON action responses** — most actions fail to parse
3. **Fix soft_reset for headless** — episode manager needs headless support
4. **Dashboard decomposition** — execute the spec from session 12
5. **Dashboard training visualization** — reward curves, action distribution charts
