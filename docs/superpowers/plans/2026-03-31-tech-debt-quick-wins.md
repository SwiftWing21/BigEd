# Tech Debt Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all XS-effort, high-impact tech debt items identified in the full audit — each task is 2-15 minutes.

**Architecture:** Independent fixes across multiple modules. No cross-task dependencies. All parallelizable.

**Tech Stack:** Python, TOML, YAML, Lua

---

## Task 1: Add ruff linter to CI

**File:** `.github/workflows/ci.yml`

**Current state (lines 85–106):** There is a `syntax-check` job that only runs `ast.parse` on all `.py` files. No ruff step exists.

**Fix steps:**
- [ ] Open `.github/workflows/ci.yml`
- [ ] After the `syntax-check` job's `Check Python syntax (all .py files)` step (after line 106), add a new step:

```yaml
      - name: Lint with ruff
        run: |
          pip install ruff
          ruff check fleet/ --select E,W,F
        continue-on-error: true
```

The `continue-on-error: true` prevents CI from blocking until the codebase is clean.

**How to test:** Push a branch and confirm the `syntax-check` job shows a `Lint with ruff` step in the Actions log.

**Commit message:** `ci: add ruff lint step to syntax-check job (non-blocking)`

---

## Task 2: Add Python 3.13 to CI matrix

**File:** `.github/workflows/ci.yml`

**Current state (line 17):**
```yaml
        python-version: ['3.11', '3.12']
```

**Fix steps:**
- [ ] Open `.github/workflows/ci.yml`
- [ ] Change line 17 to:

```yaml
        python-version: ['3.11', '3.12', '3.13']
```

Note: 3.14 is not yet stable (pre-release as of 2026-03). Use `'3.13'` which is the current stable release. Update to `'3.14'` once it ships as a stable release.

**How to test:** Push a branch and confirm the `smoke-test` job matrix now includes a `3.13` run alongside `3.11` and `3.12`.

**Commit message:** `ci: add Python 3.13 to test matrix`

---

## Task 3: Eliminate dual-copy divergence for control.lua

**Files:**
- `fleet/factorio/lua_mod/control.lua` (source of truth)
- `fleet/factorio/server_data/mods/biged-bridge/control.lua` (stale duplicate)

**Current state:** Both files exist and must be kept in sync manually. Windows symlinks require Developer Mode or admin elevation, so a copy script is the safe cross-platform approach.

**Fix steps:**
- [ ] Add a copy script at `fleet/factorio/sync_control_lua.py`:

```python
#!/usr/bin/env python3
"""Sync lua_mod/control.lua → server_data/mods/biged-bridge/control.lua.
Run after editing the source: python fleet/factorio/sync_control_lua.py
"""
import shutil
from pathlib import Path

src = Path(__file__).parent / "lua_mod" / "control.lua"
dst = Path(__file__).parent / "server_data" / "mods" / "biged-bridge" / "control.lua"
shutil.copy2(src, dst)
print(f"Synced {src} -> {dst}")
```

- [ ] Add a CI step in `.github/workflows/ci.yml` under the `syntax-check` job to detect drift:

```yaml
      - name: Check control.lua sync
        run: |
          python -c "
          from pathlib import Path
          src = Path('fleet/factorio/lua_mod/control.lua').read_bytes()
          dst = Path('fleet/factorio/server_data/mods/biged-bridge/control.lua').read_bytes()
          assert src == dst, 'control.lua out of sync — run fleet/factorio/sync_control_lua.py'
          print('control.lua in sync')
          "
```

- [ ] Run `python fleet/factorio/sync_control_lua.py` once to confirm current files are in sync before the CI check lands.

**How to test:** `python fleet/factorio/sync_control_lua.py` prints `Synced ...` with no error.

**Commit message:** `fix(factorio): add sync_control_lua.py — eliminate manual dual-copy divergence`

---

## Task 4: Move hasattr lazy inits to `__init__` in bridge.py

**File:** `fleet/factorio/bridge.py`

**Current state:**

Line 416–418 inside `ml_tick()`:
```python
if not hasattr(self, '_pending_demo_actions'):
    self._pending_demo_actions = []
    self._pending_demo_cmd_id = None
```

Line 612 inside the agent loop:
```python
prev_result = getattr(self, '_pack_prev_results', {}).get(agent_id, {"success": True})
```

**Fix steps:**
- [ ] In `FactorioBridge.__init__` (currently ends around line 55 before the `if self.config.mode == "ml":` block), add after `self._tick_count = 0`:

```python
        # Demo action queue (populated by human command handler in ml_tick)
        self._pending_demo_actions: list = []
        self._pending_demo_cmd_id = None
        # Per-agent pack result cache
        self._pack_prev_results: dict = {}
```

- [ ] In `ml_tick()`, remove lines 416–418:
```python
        if not hasattr(self, '_pending_demo_actions'):
            self._pending_demo_actions = []
            self._pending_demo_cmd_id = None
```

- [ ] On line 612, replace:
```python
prev_result = getattr(self, '_pack_prev_results', {}).get(agent_id, {"success": True})
```
with:
```python
prev_result = self._pack_prev_results.get(agent_id, {"success": True})
```

**How to test:** `python -c "from fleet.factorio.bridge import FactorioBridge"` (import succeeds). Run `python -m pytest tests/factorio/ -x -q` — all tests pass.

**Commit message:** `fix(factorio): move lazy inits to __init__ in FactorioBridge`

---

## Task 5: Deduplicate `_LEASH` dict in bridge.py

**File:** `fleet/factorio/bridge.py`

**Current state:** Two identical dicts defined inside methods:

Line 512 (inside one method):
```python
_LEASH_RADIUS = {1: 30, 2: 60, 3: 200, 4: 500}
leash_r = _LEASH_RADIUS.get(self.config.current_phase, 200)
```

Line 733 (inside another method):
```python
_LEASH = {1: 30, 2: 60, 3: 200, 4: 500}
max_r = _LEASH.get(self.config.current_phase, 200)
```

**Fix steps:**
- [ ] After the module-level imports (around line 20, after the `log = logging.getLogger(...)` line), add:

```python
# Leash radius per phase — agents are penalized for moving outside this boundary
_PHASE_LEASH_RADIUS: dict[int, int] = {1: 30, 2: 60, 3: 200, 4: 500}
```

- [ ] Replace line 512:
```python
        _LEASH_RADIUS = {1: 30, 2: 60, 3: 200, 4: 500}
        leash_r = _LEASH_RADIUS.get(self.config.current_phase, 200)
```
with:
```python
        leash_r = _PHASE_LEASH_RADIUS.get(self.config.current_phase, 200)
```

- [ ] Replace lines 733–734:
```python
            _LEASH = {1: 30, 2: 60, 3: 200, 4: 500}
            max_r = _LEASH.get(self.config.current_phase, 200)
```
with:
```python
            max_r = _PHASE_LEASH_RADIUS.get(self.config.current_phase, 200)
```

**How to test:** `python -m pytest tests/factorio/ -x -q` — all tests pass.

**Commit message:** `refactor(factorio): extract duplicate _LEASH dicts to module constant _PHASE_LEASH_RADIUS`

---

## Task 6: Remove duplicate DEV_MODE assignment in launcher

**File:** `BigEd/launcher/launcher_tkinter.py`

**Current state (lines 63–68):**
```python
# Developer mode — show advanced features (default ON during alpha)
# Set to False for production builds, or use env var BIGED_PRODUCTION=1
DEV_MODE = os.environ.get("BIGED_PRODUCTION", "").lower() not in ("1", "true")
# Production mode: frozen exe with _production_marker OR BIGED_PRODUCTION env var
_PRODUCTION_MARKER = _DIST_DIR / "_production_marker" if getattr(sys, 'frozen', False) else None
DEV_MODE = not (_PRODUCTION_MARKER and _PRODUCTION_MARKER.exists()) and \
           os.environ.get("BIGED_PRODUCTION", "").lower() not in ("1", "true")
```

Line 64's assignment to `DEV_MODE` is immediately overwritten on line 67–68.

**Fix steps:**
- [ ] Remove lines 63–65 (the first `DEV_MODE` assignment and its comment):
```python
# Developer mode — show advanced features (default ON during alpha)
# Set to False for production builds, or use env var BIGED_PRODUCTION=1
DEV_MODE = os.environ.get("BIGED_PRODUCTION", "").lower() not in ("1", "true")
```

- [ ] Keep lines 66–68 (the correct `_PRODUCTION_MARKER` + full `DEV_MODE`) and update the comment above them to:
```python
# DEV_MODE: ON unless frozen exe has _production_marker or BIGED_PRODUCTION=1 env var
```

**How to test:** `python -c "import sys; sys.path.insert(0,'BigEd/launcher'); import launcher_tkinter"` — no import error. `grep -n "DEV_MODE" BigEd/launcher/launcher_tkinter.py` shows exactly one assignment.

**Commit message:** `fix(launcher): remove dead first DEV_MODE assignment (shadowed by line 67)`

---

## Task 7: Fix stale version in dashboard health endpoint

**File:** `fleet/dashboard.py`

**Current state (line 861):**
```python
        "version": "0.22.00",
```

**Fix steps:**
- [ ] Near the top of `dashboard.py` (after the imports, before the Flask app is created), add a module-level constant by reading from `fleet.toml` via `config.py`:

```python
try:
    from config import load_config as _load_cfg
    _cfg = _load_cfg()
    _DASHBOARD_VERSION = _cfg.get("meta", {}).get("version", "0.400.00b")
except Exception:
    _DASHBOARD_VERSION = "0.400.00b"
```

- [ ] Replace line 861:
```python
        "version": "0.22.00",
```
with:
```python
        "version": _DASHBOARD_VERSION,
```

Note: If `fleet.toml` does not have a `[meta] version` key, add one:
```toml
[meta]
version = "0.400.00b"
```

**How to test:** `curl http://localhost:5555/api/health | python -m json.tool` — `version` reflects the value from `fleet.toml`, not `"0.22.00"`.

**Commit message:** `fix(dashboard): read version from fleet.toml instead of hardcoded "0.22.00"`

---

## Task 8: Remove redundant `sys.path.insert` in dashboard endpoints

**File:** `fleet/dashboard.py`

**Current state:** 35 occurrences of `sys.path.insert(0, str(FLEET_DIR))` inside individual route handler functions (lines 155, 2265, 2317, 2330, 2352, 2465, 2540, 2552, 2570, 2585, 2598, 2610, 2684, 2697, 2718, 2743, 2754, 2767, 2781, 3117, 3763, 3776, 3788, 3815, 3841, 3866, 3900, 4101, 4113, 4124, 4243, 4533, 4578, 4750, 4803, 4978).

There is already a module-level `sys.path.insert(0, str(FLEET_DIR))` near the top of the file that makes all per-handler copies redundant.

**Fix steps:**
- [ ] Confirm the module-level path insert exists at the top of `dashboard.py` (it does — the file imports from `fleet/` modules at module level, meaning FLEET_DIR is already on the path).
- [ ] Remove all per-handler occurrences of `sys.path.insert(0, str(FLEET_DIR))` inside route functions. Use search-and-replace: find `            sys.path.insert(0, str(FLEET_DIR))\n` (with leading spaces) and delete each line.

This is safe because:
1. The path is inserted once at module load.
2. `sys.path` persists for the lifetime of the process — inserting the same path 35 times per request is pure overhead and clutter.

**How to test:** `python -m pytest tests/ -x -q` — all tests pass. `grep -c "sys.path.insert" fleet/dashboard.py` returns 1 (the module-level insert only).

**Commit message:** `refactor(dashboard): remove 35 redundant per-handler sys.path.insert calls`

---

## Task 9: Fix smoke_test.py docstring count

**File:** `fleet/smoke_test.py`

**Current state (line 2):**
```python
"""Smoke test — validates the entire fleet startup chain. Run: uv run python smoke_test.py"""
```

The docstring does not mention a test count. The CLAUDE.md header mentions "33/33" but the actual file has 54 `def test_` functions. The count in the docstring (if present elsewhere) needs updating. Also, the `uv run` invocation in the docstring is wrong on Windows per CLAUDE.md.

**Fix steps:**
- [ ] Update line 2:

```python
"""Smoke test — validates the entire fleet startup chain. Run: python smoke_test.py"""
```

- [ ] Update `fleet/CLAUDE.md` line `Smoke: python smoke_test.py --fast (51/51)` to reflect the actual count if it has drifted. Run `python fleet/smoke_test.py --fast` to get the live pass count and update the CLAUDE.md line accordingly.

**How to test:** `python fleet/smoke_test.py --fast` — note the reported count, confirm docstring no longer says `uv run`.

**Commit message:** `fix(smoke_test): remove uv run from docstring (Windows-incompatible), sync count`

---

## Task 10: Sync tracked_items between Lua and Python

**Files:**
- `fleet/factorio/lua_mod/control.lua` — `CONFIG.tracked_items` (18 items, lines 21–28)
- `fleet/factorio/state_encoder.py` — `TRACKED_ITEMS` (30 items, lines 35–45)

**Current state:**

Lua `CONFIG.tracked_items` (18 items):
```lua
"iron-plate", "copper-plate", "steel-plate",
"iron-gear-wheel", "electronic-circuit", "advanced-circuit",
"automation-science-pack", "logistic-science-pack",
"transport-belt", "inserter", "assembling-machine-1",
"assembling-machine-2", "stone-furnace", "electric-mining-drill",
"pipe", "offshore-pump", "boiler", "steam-engine",
```

Python `TRACKED_ITEMS` (30 items) has these 18 plus 12 more:
`"iron-ore"`, `"copper-ore"`, `"coal"`, `"stone"`, `"wood"`, `"stone-brick"`, `"iron-stick"`, `"copper-cable"`, `"burner-inserter"`, `"fast-inserter"`, `"small-electric-pole"`, `"burner-mining-drill"`, `"lab"`, `"wooden-chest"` — and `"advanced-circuit"` is missing from Python but present in Lua.

The Python list is the authoritative one (it has `assert len(TRACKED_ITEMS) == 30`). The Lua list controls what inventory counts are serialized in the state dump — any item not in Lua's list will always read as 0 in Python's observation.

**Fix steps:**
- [ ] Update `fleet/factorio/lua_mod/control.lua` lines 21–28 to match all 30 Python `TRACKED_ITEMS`:

```lua
    tracked_items = {
        "iron-ore", "copper-ore", "coal", "stone", "wood",
        "iron-plate", "copper-plate", "steel-plate", "stone-brick",
        "iron-gear-wheel", "iron-stick", "copper-cable", "electronic-circuit",
        "automation-science-pack", "logistic-science-pack",
        "transport-belt", "inserter", "burner-inserter", "fast-inserter",
        "small-electric-pole", "pipe", "boiler", "steam-engine",
        "assembling-machine-1", "assembling-machine-2",
        "burner-mining-drill", "electric-mining-drill",
        "stone-furnace", "lab", "wooden-chest",
    },
```

- [ ] Run `python fleet/factorio/sync_control_lua.py` (from Task 3) to propagate to `server_data/`.

**How to test:** `python -c "from fleet.factorio.state_encoder import TRACKED_ITEMS; print(len(TRACKED_ITEMS))"` → `30`. Visually confirm Lua list length is 30.

**Commit message:** `fix(factorio): sync Lua tracked_items to all 30 Python TRACKED_ITEMS`

---

## Task 11: Fix hardcoded model preference list in process_manager.py

**File:** `fleet/process_manager.py`

**Current state (lines 183–184):**
```python
preference = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b", "qwen3:0.6b"]
```

`fleet.toml` already defines `[models.tiers]` with `default`, `mid`, `low`, `critical` keys (lines 175–178):
```toml
[models.tiers]
default = "qwen3:8b"
mid = "qwen3:8b"
low = "qwen3:1.7b"
critical = "qwen3:0.6b"
```

**Fix steps:**
- [ ] In `process_manager.py`, replace lines 183–184:
```python
        preference = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b", "qwen3:0.6b"]
```
with:
```python
        tiers = config.get("models", {}).get("tiers", {})
        preference = [
            tiers.get("default", "qwen3:8b"),
            tiers.get("mid", "qwen3:4b"),
            tiers.get("low", "qwen3:1.7b"),
            tiers.get("critical", "qwen3:0.6b"),
        ]
        # Deduplicate while preserving order
        seen: set = set()
        preference = [m for m in preference if not (m in seen or seen.add(m))]
```

**How to test:** `python -c "import sys; sys.path.insert(0,'fleet'); from process_manager import ProcessManager"` — no import error. Change `[models.tiers] default = "qwen3:4b"` in fleet.toml, restart fleet, confirm process_manager picks it up.

**Commit message:** `fix(process_manager): read model preference list from fleet.toml [models.tiers]`

---

## Task 12: Use ActionType enum instead of magic numbers in reward.py

**File:** `fleet/factorio/reward.py`

**Current state:**

Line 30:
```python
_MOVE_ACTION_TYPE = 3              # ActionType.MOVE value
```

Line 224:
```python
            if action_type == 10:  # STAMP
```

`ActionType` in `fleet/factorio/action_space.py`:
- `MOVE = 3`
- `PACK = 10`  ← line 224 comment says STAMP but value 10 is actually PACK; STAMP = 11

**Fix steps:**
- [ ] Add import at the top of `fleet/factorio/reward.py` (after `from factorio.state_parser import GameState`):
```python
from factorio.action_space import ActionType
```

- [ ] Remove line 30:
```python
_MOVE_ACTION_TYPE = 3              # ActionType.MOVE value
```

- [ ] Replace all three usages of `_MOVE_ACTION_TYPE` (lines 181, 193) and the action_type integer check (line 224):

Line 181: `if action_type == _MOVE_ACTION_TYPE:` → `if action_type == ActionType.MOVE.value:`

Line 193: `elif action_type != _MOVE_ACTION_TYPE:` → `elif action_type != ActionType.MOVE.value:`

Line 224: `if action_type == 10:  # STAMP` → `if action_type == ActionType.PACK.value:  # PACK (10); STAMP is 11`

Note: the comment on line 224 is wrong — value 10 is `PACK`, not `STAMP`. Fix the comment too.

**How to test:** `python -m pytest tests/factorio/test_reward.py -x -q` — all tests pass.

**Commit message:** `fix(factorio): replace magic action type numbers with ActionType enum in reward.py`

---

## Task 13: Move hot-path `import math` to module level

**Files:**
- `fleet/factorio/reward.py` — line 244: `import math` inside `_compute_per_agent_features` (called every tick)
- `fleet/factorio/state_encoder.py` — line 211: `import math` inside `_agent_feature_vector` (called every tick)

**Fix steps:**

**reward.py:**
- [ ] Open `fleet/factorio/reward.py`
- [ ] Add `import math` on line 5, after `import logging` and before `import numpy as np`
- [ ] Remove the `import math` on line 244 inside the method body

**state_encoder.py:**
- [ ] Open `fleet/factorio/state_encoder.py`
- [ ] Add `import math` after `import logging` near the top (line 24 area), before `import numpy as np`
- [ ] Remove the `import math` on line 211 inside `_agent_feature_vector`

**How to test:** `python -m pytest tests/factorio/test_state_encoder.py tests/factorio/test_reward.py -x -q` — all tests pass. Importing math at module level has no functional difference; Python caches module imports so this is purely a readability and micro-perf fix for hot paths.

**Commit message:** `fix(factorio): move hot-path import math to module level in reward.py and state_encoder.py`
