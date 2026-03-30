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
| Helpers | 11 (_contract, _knowledge, _llm_parse, _dispatch, _report, _http, _models, _flywheel_rubric/grading/audit, _oss_core) | 2026-03-26 |
| Graph Views | 6 (fleet-overview, universe, data-flow, bottleneck-detector, knowledge-graph, training-pipeline) | 2026-03-26 |
| Graph Layouts | 4 (Radial, Radial Cluster, Cluster/fcose, Grid) + Fractal Brain | 2026-03-27 |
| Audit Reports | 4 (backend, frontend, cross-platform, integration) | 2026-03-27 |
| v1.0 Blockers | 20 critical issues identified | 2026-03-27 |
| ModuleHub | 9 modules registered (+ factorio) | 2026-03-29 |

## Last Session

**Date:** 2026-03-29 (session 13)
**Session:** VS Code Claude Code — Death Spiral Debugging + Fleet Lifecycle

### Bugs Fixed (code changed, NOT YET COMMITTED)
1. **Factorio mode switch `NameError`** — 7 Factorio endpoints in dashboard.py used bare `load_config()` instead of `_load_config()`. Factorio Sandbox could never start from mode dropdown. VERIFIED WORKING.
2. **Scheduler `TypeError` crash every tick** — `[affinity] enabled = true` in fleet.toml is a bool, `_skill_to_role()` did `skill in skills` treating it as a list. Added `isinstance(skills, list)` guard in scheduler.py.
3. **DB connection pool exhaustion** — `_pool_size` counter in db.py only incremented, never decremented when Flask dev server threads died. After 20 requests all DB access failed (`/api/tasks/waiting-human` 500'd every 2s). Replaced blind counter with `_pool_conns` dict tracking `thread_id → connection` with dead-thread reaping. VERIFIED WORKING (no more 500s).
4. **Fleet lifecycle gaps** — No way to stop/restart fleet once auto-started:
   - Rewrote `/api/fleet/stop` in process_control.py — kills all fleet processes via psutil cmdline scan + self-terminates dashboard
   - Added `/api/fleet/restart` endpoint — stop all, relaunch supervisor, self-terminate
   - Added `auto_start = false` to fleet.toml `[fleet]` section
   - Both launchers (tkinter + webview) now check `auto_start` setting (webview always starts — it IS the dashboard)
5. **RCON password mismatch** — `setup_and_launch.py` generates a random RCON password but didn't sync to fleet.toml. Manually synced for this session. Root cause still needs fixing.

### Verified Working
- Mode switch to Factorio Sandbox from dropdown — activates correctly
- Bridge connects to RCON and enters tick loop
- `/api/tasks/waiting-human` returns 200 (pool fix works)
- Scheduler scaling ticks without crashing
- 51/51 smoke tests passing

### Still Broken (3 items — see Next Priorities)
1. **`ensure_player` Lua mod** — returns empty, agent has no body. Bridge ticks but skips every tick. Mod needs reload or Lua fix.
2. **RCON password race** — `setup_and_launch.py` generates random password but doesn't reliably sync to fleet.toml before bridge reads config.
3. **Phase 1 curriculum missing** — `No curriculum found for phase 1 in fleet/factorio/curricula`

### Files Modified (unstaged)
- `fleet/dashboard.py` — 7x `load_config()` → `_load_config()` in Factorio endpoints
- `fleet/scheduler.py` — `_skill_to_role()` skips non-list affinity values
- `fleet/db.py` — connection pool rewrite (thread-aware reaping)
- `fleet/process_control.py` — rewritten `/api/fleet/stop`, new `/api/fleet/restart`
- `fleet/fleet.toml` — added `auto_start = false`, synced RCON password
- `BigEd/launcher/launcher_tkinter.py` — `_should_auto_start()` method, conditional boot
- `BigEd/launcher/launcher_webview.py` — always starts supervisor (webview IS the dashboard)

**Commits this session:** 0 (fixes not committed yet)

**Next priorities:**
1. **Fix `ensure_player` Lua mod** — character body not created, bridge skips all ticks
2. **Fix RCON password sync** — `setup_and_launch.py` must write password to fleet.toml before starting bridge
3. **Fix phase 1 curriculum path** — curricula not found in `fleet/factorio/curricula`
4. **Commit all session 13 fixes** — 7 files changed, all tested
5. **Stale RUNNING tasks** — 2,219 ghost tasks from dead workers need sweep-to-FAILED on boot
6. **`pythonw.exe` on Windows ignores SIGTERM** — `/api/fleet/stop` should use `p.kill()`
7. **3 duplicate web_app.py processes** — investigate why 3 dashboard instances spawn
