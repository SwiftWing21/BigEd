# Factorio Agent — 3 Critical Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 3 remaining blockers preventing the Factorio ML agent from training: no player body, RCON password desync, wrong curriculum path.

**Architecture:** Three independent fixes that can be dispatched in parallel. Each touches different files with no overlap. Fix 1 is Lua + Python, Fix 2 is Python-only, Fix 3 is a one-line config fix + test.

**Tech Stack:** Lua (Factorio mod API), Python (asyncio bridge), TOML config

**Parallelism:** All 3 tasks are fully independent — dispatch simultaneously.

---

## Task 1: Fix `ensure_player` — Create Character in Headless Mode

**Problem:** In headless Factorio (no connected client), `game.players` is empty. The `ensure_player` Lua function returns `{has_player: false}` without creating a character. The bridge then skips every tick because `state.player_alive` is false.

**Root cause:** `get_agent_context()` at `control.lua:54-65` iterates `game.players` but finds none in headless mode. `fn_ensure_player()` at line 620 just reports the situation instead of fixing it.

**Fix:** Use the Factorio API to create a ghost player entity in headless mode. The server admin can create a player via `/c game.create_force_player()` pattern, or more reliably, just send an RCON command to create one before the bridge starts ticking.

**Files:**
- Modify: `fleet/factorio/lua_mod/control.lua:575-629` (fn_ensure_player)
- Modify: `fleet/factorio/bridge.py:437-442` (initial ensure_player call)
- Test: `fleet/factorio/tests/test_bridge.py` (mock test for body check flow)

- [ ] **Step 1: Read the current Lua mod and understand Factorio 2.0 headless player API**

The key Factorio API for creating a player in headless:
```lua
-- In Factorio 2.0, game.players is keyed by player_index.
-- A headless server has zero players until one connects or we create one.
-- We can create a character entity directly and assign it.
```

Check the Factorio modding docs for `LuaForce`, `LuaSurface.create_entity`, and whether a character can exist without a `LuaPlayer`. If it can't, the bridge must use RCON `/c` commands to create one.

- [ ] **Step 2: Update `fn_ensure_player` to handle the no-player case**

In `fleet/factorio/lua_mod/control.lua`, replace the no-player path (lines 620-628):

```lua
-- No player in headless mode — create a character entity without a player
-- The character will be controlled directly via RCON commands
if not ctx.has_player then
    local surface = ctx.surface
    local force = ctx.force
    if surface and force then
        local spawn = force.get_spawn_position(surface)
        local char = surface.create_entity{
            name = "character", position = spawn, force = force,
        }
        if not char then
            local fallback = surface.find_non_colliding_position("character", spawn, 10, 1)
            if fallback then
                char = surface.create_entity{
                    name = "character", position = fallback, force = force,
                }
            end
        end
        if char then
            -- Store reference for get_state to find
            global.biged_character = char
            game.print("[BigEd Bridge] Created headless agent character at spawn")
            return helpers.table_to_json({
                success = true,
                headless = true,
                has_player = false,
                has_character = true,
                position = {x = char.position.x, y = char.position.y},
                health = char.health,
                max_health = char.prototype.max_health,
                alive = true,
            })
        end
    end
    return helpers.table_to_json({
        success = false,
        headless = true,
        has_player = false,
        error = "no_surface_or_force",
        note = "Cannot create character — no valid surface or force",
    })
end
```

**Important:** Also update `get_agent_context()` to check `global.biged_character` as a fallback:

At line 54, before the `game.players` iteration, add:
```lua
-- Check for headless character (created by ensure_player)
if not ctx.has_player and global.biged_character and global.biged_character.valid then
    ctx.character = global.biged_character
    ctx.has_player = false  -- no LuaPlayer, but we have a character
    ctx.has_character = true
    ctx.surface = global.biged_character.surface or ctx.surface
end
```

Then update `fn_get_state()` to use `global.biged_character` when there's no player. Look at how `player_alive` is computed in `fn_get_state()` and make sure it checks `global.biged_character.valid and global.biged_character.health > 0`.

- [ ] **Step 3: Update the bridge to handle headless character response**

In `fleet/factorio/bridge.py`, the `ml_tick` body check at line 269-284 checks `state.player_alive`. Update `fleet/factorio/state_parser.py` to also check for a `has_character` field as fallback:

```python
# In parse_state(), when building GameState:
player_alive = player.get("alive", False) or player.get("has_character", False),
```

- [ ] **Step 4: Copy updated Lua mod to Factorio mods directory**

The mod source is at `fleet/factorio/lua_mod/`. It needs to be installed to Factorio's mods dir. Run:
```bash
python -c "
import sys; sys.path.insert(0, 'fleet')
from factorio.lua_installer import install_lua_mod
result = install_lua_mod()
print(result)
"
```

Then the headless server needs a restart to pick up the Lua changes. Kill the factorio.exe process and let the bridge or dashboard re-launch it.

- [ ] **Step 5: Verify the fix end-to-end**

```bash
# Kill existing factorio + bridge
taskkill /F /IM factorio.exe 2>nul
# Restart via dashboard Start button, or:
cd fleet && python -m factorio.setup_and_launch
# Check bridge output for "Created headless agent character"
# Check: curl http://127.0.0.1:27016/api/status — should show running: true, tick > 0
```

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/lua_mod/control.lua fleet/factorio/bridge.py fleet/factorio/state_parser.py
git commit -m "fix(factorio): create character in headless mode for ML agent body"
```

---

## Task 2: Fix RCON Password Sync in `setup_and_launch.py`

**Problem:** `setup_and_launch.py` generates a random RCON password and writes it to fleet.toml (step 2), then starts the server with that password (step 5), then starts the bridge (step 6). But the bridge sometimes reads fleet.toml before the write is flushed, getting the old password. This causes `RCON authentication failed` on every connect attempt.

**Root cause:** No filesystem sync between writing fleet.toml and starting the bridge subprocess. Also, the bridge process reads fleet.toml independently — if there's any caching or buffering, it gets stale data.

**Fix:** Pass the password to the bridge via environment variable as the source of truth, with fleet.toml as fallback.

**Files:**
- Modify: `fleet/factorio/setup_and_launch.py:290-297` (bridge launch)
- Modify: `fleet/factorio/bridge_config.py:45` (read env override)
- Test: manual verification

- [ ] **Step 1: Pass RCON password via environment variable**

In `fleet/factorio/setup_and_launch.py`, update the bridge launch (lines 290-297):

```python
    # Step 6: Start bridge
    print("\n[6/6] Starting BigEd bridge...")
    bridge_env = {**os.environ, "PYTHONPATH": str(FLEET_DIR)}
    bridge_env["BIGED_RCON_PASSWORD"] = password  # pass directly, no filesystem race
    bridge_proc = subprocess.Popen(
        [sys.executable, "-m", "factorio.bridge"],
        cwd=str(FLEET_DIR),
        env=bridge_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
```

- [ ] **Step 2: Read env override in bridge_config.py**

In `fleet/factorio/bridge_config.py`, find where `rcon_password` is loaded from TOML and add an env override.

Read the file first, then add after the TOML load:
```python
# Environment override — setup_and_launch passes this to avoid filesystem race
import os
env_pw = os.environ.get("BIGED_RCON_PASSWORD")
if env_pw:
    config.rcon_password = env_pw
```

Place this right after the `rcon_password` is loaded from the TOML config dict.

- [ ] **Step 3: Also add a filesystem flush after writing fleet.toml**

In `fleet/factorio/setup_and_launch.py`, after `update_fleet_toml(password)` (line 254), add:

```python
    update_fleet_toml(password)
    # Flush to disk — prevents race with bridge reading stale config
    try:
        with open(TOML_PATH, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
```

- [ ] **Step 4: Verify**

```bash
cd fleet && python -m factorio.setup_and_launch --dry-run
# Check that the dry-run output shows the password being set
# Then run for real and verify bridge connects without auth errors
```

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/setup_and_launch.py fleet/factorio/bridge_config.py
git commit -m "fix(factorio): pass RCON password via env to avoid filesystem race"
```

---

## Task 3: Fix Curriculum Path — Point to Correct Directory

**Problem:** Bridge logs `No curriculum found for phase 1 in fleet/factorio/curricula` and also `No curriculum found for phase 1 in fleet/idle_curricula`.

**Root cause:** Two path issues working together:
1. `fleet.toml` line 862 sets `curriculum_dir = "fleet/idle_curricula"` — but that dir has files named `factorio_01_bootstrap.toml`, not `phase1_*.toml`
2. `CurriculumManager.__init__` default is `"fleet/factorio/curricula"` — this dir HAS the right files (`phase1_bootstrap.toml`) but it's a relative path that resolves wrong when CWD isn't the project root

**The actual phase TOML files exist at:** `fleet/factorio/curricula/phase1_bootstrap.toml` (and phase2, 3, 4)

**Fix:** Update fleet.toml to point to the correct directory, and make CurriculumManager resolve the path relative to the fleet directory.

**Files:**
- Modify: `fleet/fleet.toml:862` (fix path)
- Modify: `fleet/factorio/curriculum_manager.py:13-15` (resolve path correctly)
- Test: `fleet/factorio/tests/test_curriculum.py` (verify loading)

- [ ] **Step 1: Fix fleet.toml curriculum_dir**

In `fleet/fleet.toml`, change line 862:

```toml
# Old:
curriculum_dir = "fleet/idle_curricula"
# New:
curriculum_dir = "fleet/factorio/curricula"
```

- [ ] **Step 2: Make CurriculumManager resolve paths relative to fleet dir**

In `fleet/factorio/curriculum_manager.py`, update `__init__` (line 13-15):

```python
def __init__(self, current_phase: int = 1, curricula_dir: str = "fleet/factorio/curricula"):
    self._phase = current_phase
    path = Path(curricula_dir)
    if not path.is_absolute():
        # Resolve relative to project root (parent of fleet/)
        fleet_dir = Path(__file__).parent.parent
        path = fleet_dir.parent / curricula_dir
    self._curricula_dir = path
    self._meta: dict = {}
    self._lessons: list[dict] = []
    self._tracker: LessonTracker | None = None
    self._completed_phases: list[int] = []
    self._load_phase(current_phase)
```

- [ ] **Step 3: Verify curriculum loads**

```bash
cd c:/Users/max/Projects/Education
python -c "
import sys; sys.path.insert(0, 'fleet')
from factorio.curriculum_manager import CurriculumManager
cm = CurriculumManager(current_phase=1, curricula_dir='fleet/factorio/curricula')
print('Phase:', cm._phase)
print('Lessons:', len(cm._lessons))
print('Meta:', cm._meta)
obj = cm.get_current_objective()
print('Objective:', obj)
"
```

Expected: Loads `phase1_bootstrap.toml`, shows lesson count > 0.

- [ ] **Step 4: Run existing curriculum tests**

```bash
cd fleet && python -m pytest factorio/tests/test_curriculum.py -v
```

- [ ] **Step 5: Commit**

```bash
git add fleet/fleet.toml fleet/factorio/curriculum_manager.py
git commit -m "fix(factorio): point curriculum_dir to phase TOML files, resolve paths from fleet root"
```

---

## Task 0 (Pre-requisite): Commit Session 13 Fixes

Before starting the 3 tasks above, commit the 4 bug fixes from this session that are sitting unstaged.

**Files to commit:**
- `fleet/dashboard.py` — 7x `load_config()` → `_load_config()`
- `fleet/scheduler.py` — skip non-list affinity values
- `fleet/db.py` — connection pool thread-aware reaping
- `fleet/process_control.py` — rewritten `/api/fleet/stop`, new `/api/fleet/restart`
- `fleet/fleet.toml` — `auto_start = false`, synced RCON password
- `BigEd/launcher/launcher_tkinter.py` — `_should_auto_start()`, conditional boot
- `BigEd/launcher/launcher_webview.py` — always start supervisor for webview

```bash
git add fleet/dashboard.py fleet/scheduler.py fleet/db.py fleet/process_control.py fleet/fleet.toml BigEd/launcher/launcher_tkinter.py BigEd/launcher/launcher_webview.py
git commit -m "fix: death spiral — pool exhaustion, load_config, scheduler, fleet lifecycle

- Fix 7 Factorio endpoints using bare load_config() instead of _load_config()
- Fix scheduler TypeError when affinity map has non-list values (enabled = true)
- Rewrite DB connection pool with thread-aware reaping (fixes pool exhaustion)
- Rewrite /api/fleet/stop to kill all fleet processes via psutil
- Add /api/fleet/restart endpoint
- Add auto_start config toggle (default false)
- Launcher respects auto_start; webview always starts (it IS the dashboard)"
```
