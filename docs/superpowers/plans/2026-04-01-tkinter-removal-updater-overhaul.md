# Tkinter Removal + Updater Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove ~20K lines of dead tkinter/customtkinter code and replace the standalone updater with a dashboard-integrated update system.

**Architecture:** Two phases — Phase 1 deletes all tkinter fallback code and cleans up references. Phase 2 builds the new update system as a Flask blueprint + dashboard panel with SSE progress, background version checks, and a headless binary swapper for release-mode users.

**Tech Stack:** Python/Flask (backend), HTML/CSS/JS (dashboard UI), SSE (real-time progress), PyInstaller (frozen helper), GitHub Releases API (release-mode updates)

**Spec:** `docs/superpowers/specs/2026-04-01-tkinter-removal-updater-overhaul-design.md`

---

## File Structure

### Phase 1: Tkinter Removal (deletions + updates)

**Delete:** Entire `BigEd/launcher/ui/` directory except `tray.py` (moved), plus `launcher_tkinter.py`, `Updater_new.spec`

**Move:** `BigEd/launcher/ui/tray.py` → `BigEd/launcher/tray.py`

**Update:**
- `BigEd/launcher/launcher.py` — remove fallback
- `BigEd/launcher/launcher_webview.py` — update tray import
- `BigEd/launcher/build.py` — remove customtkinter refs
- `BigEd/launcher/BigEdCC.spec` — remove customtkinter + ui bundle
- `BigEd/launcher/build.bat` — remove customtkinter refs
- `BigEd/launcher/requirements.txt` — remove customtkinter
- `pyproject.toml` — remove customtkinter from extras
- `BigEd/launcher/modules/mod_ingestion.py` — guard theme import
- `fleet/dependency_check.py` — update customtkinter reference
- `fleet/tests/test_launcher.py` — remove tkinter test
- `scripts/setup.sh` — remove check_tkinter()

### Phase 2: Updater Overhaul (new files)

**Create:**
- `fleet/update_manager.py` — core update logic (~400 lines)
- `fleet/update_blueprint.py` — Flask REST endpoints (~150 lines)
- `fleet/update_helper.py` — headless binary swapper (~100 lines)
- `fleet/templates/components/_update.html` — dashboard section (~50 lines)
- `fleet/tests/test_update_manager.py` — unit tests

**Update:**
- `fleet/dashboard.py` — register update blueprint
- `fleet/templates/components/_nav.html` — add Update nav item
- `fleet/templates/components/_scripts.html` — add showSection case
- `fleet/templates/components/_scripts_sse.html` — add update JS + SSE handlers
- `fleet/templates/dashboard.html` — include _update.html

**Delete (after Phase 2 verified):**
- `BigEd/launcher/updater.py`
- `BigEd/launcher/Updater.spec`
- `BigEd/launcher/release_updater.py`

---

## Phase 1: Tkinter Removal

### Task 1: Move tray.py out of ui/ directory

**Files:**
- Move: `BigEd/launcher/ui/tray.py` → `BigEd/launcher/tray.py`
- Modify: `BigEd/launcher/launcher_webview.py`

- [ ] **Step 1: Copy tray.py to new location**

```bash
cp BigEd/launcher/ui/tray.py BigEd/launcher/tray.py
```

- [ ] **Step 2: Update import in launcher_webview.py**

Find any `from ui.tray` or `from ui import tray` references and update. The current file imports `pystray` directly (line 233), so verify no `ui.tray` import exists:

```bash
grep -n "ui.tray\|ui import.*tray" BigEd/launcher/launcher_webview.py
```

If no matches, `tray.py` is imported via `pystray` directly and the move is safe. If there are matches, update them from `from ui.tray import X` to `from tray import X`.

- [ ] **Step 3: Commit**

```bash
git add BigEd/launcher/tray.py BigEd/launcher/launcher_webview.py
git commit -m "refactor: move tray.py out of ui/ before tkinter removal"
```

### Task 2: Delete all tkinter UI files

**Files:**
- Delete: entire `BigEd/launcher/ui/` directory
- Delete: `BigEd/launcher/launcher_tkinter.py`
- Delete: `BigEd/launcher/Updater_new.spec`

- [ ] **Step 1: Delete the files**

```bash
rm -rf BigEd/launcher/ui/
rm BigEd/launcher/launcher_tkinter.py
rm -f BigEd/launcher/Updater_new.spec
```

- [ ] **Step 2: Verify tray.py is at new location**

```bash
test -f BigEd/launcher/tray.py && echo "OK" || echo "MISSING"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add -A BigEd/launcher/ui/ BigEd/launcher/launcher_tkinter.py BigEd/launcher/Updater_new.spec
git commit -m "chore: delete tkinter fallback — 20K lines of dead code

Remove launcher_tkinter.py, entire ui/ directory (except tray.py
already moved), and stale Updater_new.spec."
```

### Task 3: Update launcher.py to remove fallback

**Files:**
- Modify: `BigEd/launcher/launcher.py`

- [ ] **Step 1: Rewrite launcher.py**

Replace entire file content with:

```python
"""BigEd CC launcher."""
import os, sys

if sys.platform in ("win32", "linux"):
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")

def main():
    from launcher_webview import main as wv_main
    wv_main()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add BigEd/launcher/launcher.py
git commit -m "refactor: remove tkinter fallback from launcher dispatcher"
```

### Task 4: Update build system

**Files:**
- Modify: `BigEd/launcher/build.py`
- Modify: `BigEd/launcher/BigEdCC.spec`
- Modify: `BigEd/launcher/build.bat`
- Modify: `BigEd/launcher/requirements.txt`
- Create: `BigEd/launcher/requirements-standalone.txt`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update build.py**

Remove `--collect-all customtkinter` from the BigEdCC build command (around line 89). Remove `launcher_tkinter.py` from bundled assets (around line 119). Read the file first to find exact lines.

- [ ] **Step 2: Update BigEdCC.spec**

Remove `'launcher_tkinter.py'` from datas list (line 4). Remove `collect_all('customtkinter')` lines (lines 7-8). Change `('ui', 'ui')` datas entry to `('tray.py', '.')` since tray.py moved out.

- [ ] **Step 3: Update build.bat**

Remove `--collect-all customtkinter` from the BigEdCC build line.

- [ ] **Step 4: Update requirements.txt**

Remove `customtkinter>=5.2.0` line. Create `BigEd/launcher/requirements-standalone.txt` with:

```
customtkinter>=5.2.0
```

This serves the standalone tools (installer, usb-creator) that still need CTk.

- [ ] **Step 5: Update pyproject.toml**

Remove `customtkinter>=5.2.0` from the `[project.optional-dependencies]` launcher extras section.

- [ ] **Step 6: Commit**

```bash
git add BigEd/launcher/build.py BigEd/launcher/BigEdCC.spec BigEd/launcher/build.bat BigEd/launcher/requirements.txt BigEd/launcher/requirements-standalone.txt pyproject.toml
git commit -m "build: remove customtkinter from core launcher build chain

Standalone tools (installer, usb-creator) use requirements-standalone.txt."
```

### Task 5: Fix broken imports in surviving files

**Files:**
- Modify: `BigEd/launcher/modules/mod_ingestion.py`
- Modify: `BigEd/launcher/modules/mod_factorio.py` (verify)
- Modify: `BigEd/launcher/launcher_webview.py` (docstring)
- Delete or rewrite: `BigEd/launcher/gui_smoke_test.py`
- Modify: `fleet/dependency_check.py`
- Modify: `fleet/tests/test_launcher.py`
- Modify: `scripts/setup.sh`

- [ ] **Step 1: Fix mod_ingestion.py**

Read the file, find the `from ui.theme import ...` line. Note the **exact** constants imported (may be more than BG/TEXT/DIM — read the actual line). Wrap in try/except with inline fallback values matching the dashboard's dark theme CSS variables:

```python
try:
    from ui.theme import BG as _BG, BG2 as _BG2, TEXT as _TEXT, DIM as _DIM  # adjust to match actual imports
except ImportError:
    _BG, _BG2, _TEXT, _DIM = "#0a0e1a", "#1a1f2e", "#e2e8f0", "#64748b"
```

- [ ] **Step 2: Verify mod_factorio.py**

Read the file, confirm its `from ui.theme import ...` is already wrapped in try/except. If not, add the same pattern.

- [ ] **Step 3: Fix or delete gui_smoke_test.py**

Read `BigEd/launcher/gui_smoke_test.py`. It imports from `ui.consoles`, `ui.settings`, `ui.boot`, `ui.omnibox`, `ui.sse_client` — all deleted. Either delete the file entirely (if all tests are tkinter-specific) or rewrite to test only webview-path components.

- [ ] **Step 4: Update launcher_webview.py docstring**

Find any references to "customtkinter" or "CustomTkinter" in the docstring or comments and update to reflect PyWebView-only architecture.

- [ ] **Step 5: Update dependency_check.py**

Find the `"customtkinter": "launcher GUI"` line and change to `"customtkinter": "standalone tools (installer, USB creator)"` or remove it from the core dependency list if it's in a required section.

- [ ] **Step 6: Update test_launcher.py**

Remove the test that checks `launcher_tkinter.py` exists (around lines 61-65).

- [ ] **Step 7: Update setup.sh**

Remove the `check_tkinter()` function (lines 273-321) and its call (line 573). Search for `check_tkinter` and remove all related lines.

- [ ] **Step 8: Check for any other broken imports**

```bash
grep -rn "from ui\.\|import ui\." BigEd/launcher/ --include="*.py" | grep -v __pycache__ | grep -v tray
```

Fix any remaining references found.

- [ ] **Step 9: Commit**

```bash
git add BigEd/launcher/modules/ BigEd/launcher/gui_smoke_test.py BigEd/launcher/launcher_webview.py fleet/dependency_check.py fleet/tests/test_launcher.py scripts/setup.sh
git commit -m "fix: patch broken imports and dead tests after tkinter removal"
```

### Task 6: Documentation cleanup

**Files:**
- Modify: `CLAUDE.md`
- Scan and update: other docs referencing customtkinter/launcher_tkinter

- [ ] **Step 1: Update CLAUDE.md**

Remove the "Theme fonts: use constants from `ui/theme.py`" gotcha line. Update any references to `launcher_tkinter.py` in the structure section.

- [ ] **Step 2: Scan for stale references**

```bash
grep -rn "customtkinter\|launcher_tkinter\|ui/theme\|ui\.theme" docs/ CLAUDE.md fleet/CLAUDE.md CONTRIBUTING.md CROSS_PLATFORM.md FRAMEWORK_BLUEPRINT.md SETUP.md --include="*.md" 2>/dev/null | head -30
```

Update or remove each reference found.

- [ ] **Step 3: Commit**

```bash
git add -A *.md docs/
git commit -m "docs: remove stale tkinter/customtkinter references"
```

### Task 7: Verify Phase 1

- [ ] **Step 1: Run smoke tests**

```bash
cd fleet && python smoke_test.py --fast
```

Expected: All tests pass (51/51). No import errors from deleted files.

- [ ] **Step 2: Check for remaining tkinter imports in core launcher**

```bash
grep -rn "tkinter\|customtkinter\|import ctk" BigEd/launcher/ --include="*.py" | grep -v __pycache__ | grep -v installer | grep -v uninstaller | grep -v create_usb | grep -v requirements
```

Expected: No results (only standalone tools should reference CTk).

- [ ] **Step 3: Commit verification note** (if any fixes were needed)

---

## Phase 2: Updater Overhaul

### Task 8: Write update_manager.py — core logic with tests

**Files:**
- Create: `fleet/update_manager.py`
- Create: `fleet/tests/test_update_manager.py`

- [ ] **Step 1: Write tests for detect_mode, current_version, manifest**

Create `fleet/tests/test_update_manager.py`:

```python
"""Tests for update_manager — core update logic."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_detect_mode_git(tmp_path):
    """detect_mode returns 'git' when .git directory exists."""
    (tmp_path / ".git").mkdir()
    import update_manager
    with patch.object(update_manager, "_SEARCH_ROOTS", [tmp_path]):
        assert update_manager.detect_mode() == "git"


def test_detect_mode_release(tmp_path):
    """detect_mode returns 'release' when no .git found."""
    import update_manager
    with patch.object(update_manager, "_SEARCH_ROOTS", [tmp_path]):
        assert update_manager.detect_mode() == "release"


def test_current_version_from_file(tmp_path):
    """current_version reads .bigedcc_version file."""
    version_file = tmp_path / ".bigedcc_version"
    version_file.write_text("v0.400.00b")
    import update_manager
    with patch.object(update_manager, "_VERSION_FILE", version_file):
        assert update_manager.current_version() == "v0.400.00b"


def test_current_version_git_fallback(tmp_path):
    """current_version falls back to git describe."""
    version_file = tmp_path / ".bigedcc_version"
    import update_manager
    with patch.object(update_manager, "_VERSION_FILE", version_file):
        with patch.object(update_manager, "_git_describe", return_value="v0.400.00b-3-gabcdef"):
            assert update_manager.current_version() == "v0.400.00b-3-gabcdef"


def test_get_manifest_empty(tmp_path):
    """get_manifest returns empty dict when no manifest file."""
    import update_manager
    with patch.object(update_manager, "_MANIFEST_FILE", tmp_path / "nope.json"):
        assert update_manager.get_manifest() == {}


def test_save_and_load_manifest(tmp_path):
    """save_manifest writes JSON, get_manifest reads it back."""
    manifest_file = tmp_path / "manifest.json"
    import update_manager
    with patch.object(update_manager, "_MANIFEST_FILE", manifest_file), \
         patch.object(update_manager, "_DIST_DIR", tmp_path):
        update_manager.save_manifest({"__last_date__": "2026-04-01", "foo": "bar"})
        result = update_manager.get_manifest()
        assert result["__last_date__"] == "2026-04-01"
        assert result["foo"] == "bar"


def test_check_for_update_offline():
    """check_for_update returns not-available when offline."""
    import update_manager
    with patch.object(update_manager, "_is_offline", return_value=True):
        result = update_manager.check_for_update()
        assert result["available"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd fleet && python -m pytest tests/test_update_manager.py -v
```

Expected: FAIL — `update_manager` module not found.

- [ ] **Step 3: Write update_manager.py**

Create `fleet/update_manager.py` with the full implementation. Key components:
- `detect_mode()` — check for `.git` in search roots
- `current_version()` — read `.bigedcc_version`, fallback to `git describe`
- `get_manifest()` / `save_manifest()` — JSON file hash tracking
- `check_for_update()` — unified check (git fetch or GitHub API), respects offline mode
- `apply_update(progress_cb)` — run git pull + pip install (git mode) or download + stage (release mode)
- `cancel_update()` — set cancel event, terminate subprocess
- `spawn_swap_helper()` — spawn headless helper for binary swap
- `start_background_checker(interval_hours, sse_broadcast)` — daemon thread, 24h default

All subprocess calls must use `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` and explicit timeouts.

- [ ] **Step 4: Run tests**

```bash
cd fleet && python -m pytest tests/test_update_manager.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add fleet/update_manager.py fleet/tests/test_update_manager.py
git commit -m "feat: add update_manager.py — core update logic

Dual-mode (git/release), background checker, manifest tracking,
version detection, apply with progress callbacks."
```

### Task 9: Write update_blueprint.py — REST endpoints

**Files:**
- Create: `fleet/update_blueprint.py`
- Modify: `fleet/dashboard.py`

- [ ] **Step 1: Write update_blueprint.py**

Create `fleet/update_blueprint.py` with 7 endpoints:
- `GET /api/update/status` — current version, mode, last check, availability
- `POST /api/update/check` — trigger immediate check
- `POST /api/update/apply` — start update in background thread, progress via SSE
- `POST /api/update/cancel` — cancel running update
- `GET /api/update/progress` — SSE stream of progress events
- `GET /api/update/history` — recent update history
- `POST /api/update/rollback` — placeholder (501 Not Implemented)

All endpoints use `_require_role` and `_safe_error` from `dashboard_utils`. Progress events broadcast via `_broadcast_sse` with type `update_progress`. Completion broadcasts `update_complete`.

- [ ] **Step 2: Register blueprint in dashboard.py**

Add to the `_BLUEPRINTS` list (around line 124):

```python
    ("update_blueprint",      "update_bp",         False),
```

- [ ] **Step 3: Start background checker in dashboard startup**

In `_post_registration_setup()` (around line 149), add:

```python
    try:
        import update_manager
        update_manager.start_background_checker(interval_hours=24, sse_broadcast=_broadcast_sse)
    except Exception as e:
        log.warning("Failed to start update checker: %s", e)
```

- [ ] **Step 4: Commit**

```bash
git add fleet/update_blueprint.py fleet/dashboard.py
git commit -m "feat: add update_blueprint.py — 7 REST endpoints + background checker"
```

### Task 10: Write update_helper.py — headless binary swapper

**Files:**
- Create: `fleet/update_helper.py`

- [ ] **Step 1: Write update_helper.py**

Create `fleet/update_helper.py` — CLI tool with `--swap --pid --source --target [--relaunch]`.

Key logic:
- `_wait_for_exit(pid, timeout=30)` — poll `os.kill(pid, 0)` every 500ms
- `_swap_files(source, target)` — backup existing to `.update_backup/`, copy new files, rollback on failure, delete backup on success
- Handle `PermissionError` explicitly with clear error message
- Relaunch exe if `--relaunch` provided
- Clean up source temp directory

No dependencies beyond stdlib. Uses `creationflags=CREATE_NO_WINDOW` for relaunch subprocess.

- [ ] **Step 2: Commit**

```bash
git add fleet/update_helper.py
git commit -m "feat: add update_helper.py — headless binary swapper with rollback"
```

### Task 11: Dashboard UI — HTML section + nav entry

**Files:**
- Create: `fleet/templates/components/_update.html`
- Modify: `fleet/templates/components/_nav.html`
- Modify: `fleet/templates/dashboard.html`
- Modify: `fleet/templates/components/_scripts.html`

- [ ] **Step 1: Create _update.html**

Dashboard section with:
- Status banner (version, mode, last check)
- Action buttons (Check Now, Apply Update, View Changelog, Cancel)
- Progress area (step list, progress bar, timer) — hidden by default
- History table

- [ ] **Step 2: Add nav item in _nav.html**

Add before the Settings button:

```html
    <button class="nav-item" data-section="update" onclick="showSection('update')">
      <span class="nav-icon">&#8635;</span> Update
      <span class="nav-badge hidden" id="update-badge" style="background:var(--success);">!</span>
    </button>
```

- [ ] **Step 3: Include _update.html in dashboard.html**

Add `{% include "components/_update.html" %}` alongside other section includes.

- [ ] **Step 4: Add showSection case in _scripts.html**

Add to the switch block:

```javascript
    case 'update': loadUpdate(); break;
```

- [ ] **Step 5: Commit**

```bash
git add fleet/templates/
git commit -m "feat: add Update dashboard section — HTML + nav entry"
```

### Task 12: Dashboard UI — JavaScript + SSE handlers

**Files:**
- Modify: `fleet/templates/components/_scripts_sse.html`

- [ ] **Step 1: Add update JS functions**

Append to `_scripts_sse.html`:
- `loadUpdate()` — fetch status, render banner/buttons/history
- `checkUpdateNow()` — POST check, toggle badge, reload panel
- `applyUpdate()` — POST apply, show progress area, start timer
- `cancelUpdate()` — POST cancel, reset UI
- `_handleUpdateProgress(d)` — create/update step rows, update progress bar
- `_handleUpdateComplete(d)` — stop timer, show restart button if needed, reload
- `showChangelog()` — modal with git log commits or release body
- `loadUpdateHistory()` — render history table
- `_toggleUpdateBadge(show)` — show/hide green notification dot

Use safe DOM methods (`createElement`, `textContent`) for all dynamic content. Use `textContent` instead of `innerHTML` where possible; for structured HTML (step rows, history rows) use `createElement` with `textContent` for user data.

- [ ] **Step 2: Wire SSE events to handlers**

Find the SSE event dispatch section and add handlers for `update_progress`, `update_complete`, `update_available` event types.

- [ ] **Step 3: Commit**

```bash
git add fleet/templates/components/_scripts_sse.html
git commit -m "feat: add Update panel JS — SSE progress, changelog, history"
```

### Task 13: Delete old updater files

**Files:**
- Delete: `BigEd/launcher/updater.py`
- Delete: `BigEd/launcher/Updater.spec`
- Delete: `BigEd/launcher/release_updater.py`
- Modify: `BigEd/launcher/build.py`
- Modify: `BigEd/launcher/installer.py`

- [ ] **Step 1: Delete old files**

```bash
rm BigEd/launcher/updater.py BigEd/launcher/Updater.spec BigEd/launcher/release_updater.py
```

- [ ] **Step 2: Update build.py**

Remove the `build_updater()` function and references. Optionally add a `build_update_helper()` function that builds `fleet/update_helper.py` with PyInstaller `--onefile --console` (no customtkinter).

- [ ] **Step 3: Update installer.py**

Find lines referencing `updater.py` or Updater build. Update to reference `update_helper.py` and build UpdateHelper without customtkinter.

- [ ] **Step 4: Check pre_release_check.py**

```bash
grep -n "updater.py" scripts/pre_release_check.py 2>/dev/null
```

If found, update to reference `fleet/update_manager.py`.

- [ ] **Step 5: Commit**

```bash
git add -A BigEd/launcher/updater.py BigEd/launcher/Updater.spec BigEd/launcher/release_updater.py BigEd/launcher/build.py BigEd/launcher/installer.py
git commit -m "chore: delete old standalone updater (1,488 lines replaced by dashboard-integrated system)"
```

### Task 14: Final verification

- [ ] **Step 1: Run smoke tests**

```bash
cd fleet && python smoke_test.py --fast
```

Expected: All pass.

- [ ] **Step 2: Test update endpoints**

```bash
curl -s http://localhost:5555/api/update/status | python -m json.tool
curl -s -X POST http://localhost:5555/api/update/check | python -m json.tool
curl -s http://localhost:5555/api/update/history | python -m json.tool
```

- [ ] **Step 3: Verify no customtkinter in core launcher**

```bash
grep -rn "customtkinter\|import ctk" BigEd/launcher/ --include="*.py" | grep -v __pycache__ | grep -v installer | grep -v uninstaller | grep -v create_usb | grep -v requirements
```

Expected: No results.

- [ ] **Step 4: Verify dashboard Update section**

Navigate to dashboard, click "Update" in sidebar. Verify: version displays, Check Now works, history renders.

- [ ] **Step 5: Commit any final fixes**
