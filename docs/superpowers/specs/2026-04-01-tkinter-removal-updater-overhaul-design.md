# Tkinter Removal + Updater Overhaul Design Spec

**Date:** 2026-04-01  
**Status:** Approved  
**Scope:** Two independent sub-projects — tkinter dead code removal and updater rewrite

---

## Context

The BigEd CC launcher migrated from customtkinter to PyWebView (wrapping the Flask dashboard). The migration is complete — `launcher_webview.py` is the production path. However, ~19K lines of tkinter fallback code remain in the codebase, along with a standalone customtkinter-based updater (`updater.py`, 1,114 lines).

The tkinter fallback (`launcher_tkinter.py` + entire `ui/` directory) is dead code in the PyWebView path — `launcher_webview.py` has zero imports from any of these files. The updater has functional gaps: release-mode users get no auto-detection, no dashboard integration, and no scheduled checks.

### Decision Record

- **Drop tkinter fallback entirely** — deadweight and confusion complexity
- **Updater approach:** Dashboard-first (Approach A) — update panel in dashboard with headless helper for binary swap
- **Update check cadence:** Startup + 24h periodic with nav notification badge
- **Binary swapper:** Plain Python script for git users, frozen ~5MB exe for release users
- **Standalone tools** (installer, uninstaller, USB creator) — stay customtkinter, not part of this spec
- **Theme customization** — separate future spec, not part of this work

---

## Sub-Project 1: Tkinter Removal

Pure deletion of dead code plus minor updates to files that reference the deleted code.

### Files to Delete

Delete the entire `BigEd/launcher/ui/` directory except `tray.py` (which moves to `BigEd/launcher/tray.py`), plus the tkinter launcher and stale artifacts. This includes all `.py` files, `__init__.py` files, and any data files (e.g., `ui/data/boot_timing.json`).

**Launcher tkinter fallback:**
- `BigEd/launcher/launcher_tkinter.py` (4,424 lines)

**UI layer (all dead in webview path):**
- `BigEd/launcher/ui/__init__.py`
- `BigEd/launcher/ui/boot.py`
- `BigEd/launcher/ui/comm_tab.py`
- `BigEd/launcher/ui/consoles.py`
- `BigEd/launcher/ui/dispatch.py`
- `BigEd/launcher/ui/fleet_status.py`
- `BigEd/launcher/ui/ollama_manager.py`
- `BigEd/launcher/ui/omnibox.py`
- `BigEd/launcher/ui/skill_picker.py`
- `BigEd/launcher/ui/sse_client.py`
- `BigEd/launcher/ui/theme.py`
- `BigEd/launcher/ui/usage_tracker.py`
- `BigEd/launcher/ui/utils.py`
- `BigEd/launcher/ui/webview_manager.py`
- `BigEd/launcher/ui/data/` (entire directory, including `boot_timing.json`)

**Settings panels (all tkinter-only mixins):**
- `BigEd/launcher/ui/settings/__init__.py`
- `BigEd/launcher/ui/settings/consoles.py`
- `BigEd/launcher/ui/settings/display.py`
- `BigEd/launcher/ui/settings/general.py`
- `BigEd/launcher/ui/settings/hardware.py`
- `BigEd/launcher/ui/settings/keys.py`
- `BigEd/launcher/ui/settings/mcp.py`
- `BigEd/launcher/ui/settings/models.py`
- `BigEd/launcher/ui/settings/names.py`
- `BigEd/launcher/ui/settings/operations.py`
- `BigEd/launcher/ui/settings/review.py`

**Dialogs (all tkinter-only):**
- `BigEd/launcher/ui/dialogs/__init__.py`
- `BigEd/launcher/ui/dialogs/model_selector.py`
- `BigEd/launcher/ui/dialogs/review.py`
- `BigEd/launcher/ui/dialogs/submit_issue.py`
- `BigEd/launcher/ui/dialogs/thermal.py`
- `BigEd/launcher/ui/dialogs/walkthrough.py`

**Stale build artifacts:**
- `BigEd/launcher/Updater_new.spec`

### Files Kept (not deleted)

- `BigEd/launcher/ui/tray.py` — moved to `BigEd/launcher/tray.py`, update import in `launcher_webview.py`
- `BigEd/launcher/installer.py` — standalone CTk tool, separate build
- `BigEd/launcher/uninstaller.py` — standalone CTk tool, separate build
- `BigEd/launcher/create_usb_media.py` — standalone CTk tool, separate build (has try/except fallback for `ui.theme` import — will use inline defaults after deletion, accepted trade-off)

### Files to Update

**`BigEd/launcher/launcher.py`:**
Remove try/except fallback. Direct call to `launcher_webview.main()`:
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

**`BigEd/launcher/build.py`:**
- Remove `--collect-all customtkinter` from BigEdCC build (line 89)
- Remove `launcher_tkinter.py` from bundled assets (line 119)
- Remove Updater build function (replaced by UpdateHelper in sub-project 2)
- Add UpdateHelper build function

**`BigEd/launcher/BigEdCC.spec`:**
- Remove `launcher_tkinter.py` from datas (line 4)
- Remove `collect_all('customtkinter')` (lines 7-8)

**`BigEd/launcher/requirements.txt`:**
- Remove `customtkinter>=5.2.0` line (keep in installer/usb-creator requirements if they have separate ones)

**`pyproject.toml`:**
- Remove `customtkinter>=5.2.0` from launcher optional dependencies

**`fleet/dependency_check.py`:**
- Update customtkinter reference — no longer a core launcher dependency, only needed for standalone tools

**`fleet/tests/test_launcher.py`:**
- Remove test that verifies `launcher_tkinter.py` exists and is >1000 bytes

**`scripts/setup.sh`:**
- Remove `check_tkinter()` function (lines 273-321) and its call (line 573)

**`BigEd/launcher/build.bat`:**
- Remove `--collect-all customtkinter` from BigEdCC build line

**`BigEd/launcher/BigEdCC.spec`:**
- Change `('ui', 'ui')` datas entry to only bundle `tray.py` (since the rest of `ui/` is deleted)

**`BigEd/launcher/modules/mod_ingestion.py`:**
- Has unguarded `from ui.theme import ...` that will crash after deletion. Wrap in try/except with inline fallback constants, or remove the import if theme values are unused in the webview path.

**`BigEd/launcher/modules/mod_factorio.py`:**
- Has `from ui.theme import ...` wrapped in try/except — already safe, but verify fallback values are reasonable.

**`BigEd/launcher/gui_smoke_test.py`:**
- Imports from `ui.consoles`, `ui.settings`, `ui.boot`, `ui.omnibox`, `ui.sse_client` — all deleted. Either rewrite to test webview-path components only, or delete the file if it's fully obsolete.

**`scripts/pre_release_check.py`:**
- Line 63 checks `updater.py` exists — update to check for `update_manager.py` instead (or `update_helper.py`).

**`BigEd/launcher/installer.py`:**
- Lines 814-817 build the Updater exe with `--collect-all customtkinter`. Update to build UpdateHelper instead (no customtkinter).

**`BigEd/launcher/launcher_webview.py`:**
- Update `from ui.tray import ...` to `from tray import ...` (tray.py moves out of deleted ui/ directory).
- Update docstring referencing customtkinter.

**Standalone tools `requirements.txt` split:**
- The shared `BigEd/launcher/requirements.txt` currently serves both the core launcher and standalone tools (installer, usb-creator). After removing `customtkinter` from it, standalone tool builds will break. Create `BigEd/launcher/requirements-standalone.txt` with `customtkinter>=5.2.0` for those builds, or document that operators must install customtkinter manually for standalone tool builds.

**Documentation sweep** (update stale references post-implementation):
- `CLAUDE.md` — remove "Theme fonts: use constants from `ui/theme.py`" gotcha
- `CONTRIBUTING.md`, `CROSS_PLATFORM.md`, `FRAMEWORK_BLUEPRINT.md`, `SETUP.md`, `docs/WHAT_IS_BIGED.md`, `BigEd/STABILITY_GUIDE.md` — update/remove customtkinter and launcher_tkinter references

### Duplicate Code Note

`_kill_fleet_processes()` and `_kill_ollama()` exist in both `ui/boot.py` and `launcher_webview.py`. The `launcher_webview.py` versions are the live ones and already handle all cleanup. The `boot.py` originals are deleted with the rest of the tkinter code. No migration needed.

---

## Sub-Project 2: Updater Overhaul

Replace the standalone customtkinter updater with a dashboard-integrated update system.

### Architecture

```
Dashboard (browser/PyWebView)
  └─ Update panel (HTML/JS in _update.html)
      ├─ Shows version, mode, last check, update status
      ├─ SSE listener for real-time progress
      └─ Buttons: Check Now, Apply Update, View Changelog

Fleet Backend
  └─ update_blueprint.py (Flask routes)
      └─ update_manager.py (core logic)
          ├─ detect_mode() → "git" | "release"
          ├─ check_for_update() → {available, details}
          ├─ apply_update(progress_cb) → runs steps
          └─ get_manifest() / save_manifest()

Binary Swap (release mode + git exe rebuild)
  └─ update_helper.py (headless, ~100 lines)
      ├─ Git users: spawned as `python update_helper.py --swap ...`
      └─ Release users: frozen as UpdateHelper.exe (~5MB)
```

### New Files

**`fleet/update_blueprint.py`** (~150 lines)
Flask blueprint registered on the dashboard app.

Endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/update/status` | GET | Current version, mode, last check time, whether update available, details |
| `/api/update/check` | POST | Trigger an immediate check (git fetch or GitHub API). Returns result. |
| `/api/update/apply` | POST | Start the update process. Returns immediately; progress via SSE. |
| `/api/update/progress` | GET | SSE stream of `{step, status, percent, detail, elapsed}` events |
| `/api/update/cancel` | POST | Cancel a running update (kills subprocess) |
| `/api/update/history` | GET | Recent update history from manifest |
| `/api/update/rollback` | POST | Roll back to a previous release version (release mode only) |

**`fleet/update_manager.py`** (~400 lines)
Core update logic, absorbs good parts from `updater.py` and `release_updater.py`.

Key functions:
- `detect_mode() -> str` — checks for `.git` directory up to 3 parents. Returns `"git"` or `"release"`.
- `check_for_update() -> dict` — unified check. Git mode: `git fetch` + `rev-list --count`. Release mode: GitHub Releases API via `release_updater.check_release()`. Returns `{available: bool, mode: str, behind_count: int, commits: list, tag: str, body: str}`.
- `apply_update(progress_cb) -> dict` — runs update steps sequentially. Calls `progress_cb(step, status, percent, detail)` for each step. Git mode steps: git pull → pip install → PyInstaller rebuild. Release mode steps: download asset → verify → stage to temp.
- `spawn_swap_helper(pid, source_dir, target_dir)` — spawns the headless helper for binary replacement.
- `get_manifest() -> dict` / `save_manifest(data)` — MD5-based file hash tracking for incremental builds.
- `get_history() -> list` — reads update history from manifest `__history__` key.
- `current_version() -> str` — reads from `.bigedcc_version` file or git describe.

Background checker:
- `start_background_checker(interval_hours=24)` — starts a daemon thread.
- On startup: immediate check. Then sleeps `interval_hours`.
- When update found: stores result in module-level `_update_status` dict; pushes SSE event `update_available` via dashboard's SSE mechanism.
- Respects `offline_mode` and `air_gap_mode` from fleet config — skips checks.

Git mode step definitions (reused from current `updater.py` STEPS):
```python
STEPS_GIT = [
    ("Git Pull", ["git", "pull", "--ff-only"]),
    ("Install packages", ["pip", "install", "--upgrade", "-r", REQ_FILE]),
    ("Build BigEdCC", [PyInstaller command]),
]
```

Manifest-based skip logic preserved: compute MD5 of tracked files, skip step if unchanged and outputs exist.

**`fleet/update_helper.py`** (~100 lines)
Headless binary swapper. No GUI, no dependencies beyond stdlib.

CLI interface:
```
python update_helper.py --swap --pid <PID> --source <temp_dir> --target <install_dir> [--relaunch <exe_path>]
```

Steps:
1. Parse args
2. Wait for `--pid` to exit (poll every 500ms, timeout 30s)
3. Copy files from `--source` to `--target` (overwrite)
4. If `--relaunch` provided, spawn the exe
5. Clean up temp source directory
6. Exit

Error handling:
- If target directory requires elevation (e.g., `C:\Program Files\`), detect `PermissionError` and exit with clear error message (no silent failure). Launcher should avoid installing to protected directories, but the helper must handle it gracefully.
- Partial copy recovery: before overwriting, copy existing target files to `<target>/.update_backup/`. If copy fails midway, restore from backup. On success, delete backup. This prevents inconsistent installations from disk-full or antivirus-lock scenarios.

Self-update: if `UpdateHelper_new.exe` exists in source dir, replace self first via a tiny bat script (same proven pattern as current updater).

Frozen build: `UpdateHelper.spec` — PyInstaller `--onefile --console` (no `--windowed`, no customtkinter). Target size ~5MB.

**`fleet/templates/components/_update.html`** (~50 lines)
Dashboard section HTML.

**`fleet/UpdateHelper.spec`**
PyInstaller spec for the frozen helper.

### Dashboard UI

**Nav item:** Added to `_nav.html` sidebar. Shows green notification dot when `_updateAvailable` is true.

**Panel states:**

1. **Idle / Up to date:**
   - Shows current version, mode (git/release), last check timestamp
   - "Check Now" button
   - History table below

2. **Update available:**
   - Green banner: "Update available — N commits behind" (git) or "vX.Y.Z available" (release)
   - "Apply Update" button enabled
   - "View Changelog" button opens modal with git log or release body (markdown rendered)

3. **Updating:**
   - Step list with live status indicators (pending ○, running ⟳, complete ✓, error ✗)
   - Progress bar with percentage
   - Elapsed timer
   - "Cancel" button (terminates the subprocess)

4. **Restart required** (release mode / git exe rebuild):
   - "Restart to finish update" button
   - Spawns helper, closes app

5. **Error:**
   - Red banner with error message
   - "Retry" button
   - Consecutive failure counter (3+ failures shows warning)

**SSE events:**
- `update_available` — `{available: true, mode, behind_count, tag}`
- `update_progress` — `{step: str, status: "running"|"complete"|"error", percent: int, detail: str, elapsed: float}`
- `update_complete` — `{success: bool, restart_required: bool, error: str|null}`

**JS functions** (added to `_scripts_sse.html`):
- `loadUpdate()` — fetch `/api/update/status`, render panel
- `checkUpdateNow()` — POST `/api/update/check`, refresh panel
- `applyUpdate()` — POST `/api/update/apply`, start SSE listener for `update_progress` events; on `update_complete` with `restart_required: true`, transition to panel state 4 (Restart required); on `success: true` without restart, transition to state 1 (up to date)
- `cancelUpdate()` — POST `/api/update/cancel`, revert to idle state
- `showChangelog()` — fetch and render changelog modal

### Files Deleted (from updater)

- `BigEd/launcher/updater.py` (1,114 lines) — replaced by `update_manager.py` + `update_blueprint.py`
- `BigEd/launcher/Updater.spec` — replaced by `UpdateHelper.spec`
- `BigEd/launcher/release_updater.py` (374 lines) — absorbed into `update_manager.py`

### Files Updated (for updater)

- `fleet/dashboard.py` — register `update_bp` blueprint
- `fleet/templates/dashboard.html` — include `_update.html`
- `fleet/templates/components/_nav.html` — add Update nav item with notification dot
- `fleet/templates/components/_scripts.html` — add `case 'update': loadUpdate(); break;`
- `fleet/templates/components/_scripts_sse.html` — add update SSE handlers + JS functions
- `BigEd/launcher/build.py` — remove Updater build, add UpdateHelper build

### Migration Path for `release_updater.py`

`release_updater.py` contains solid GitHub API logic that gets absorbed into `update_manager.py`:
- `check_release()` → `update_manager.check_for_update()` (release branch)
- `download_asset()` → `update_manager._download_release_asset()`
- `apply_update()` → `update_manager._stage_release_files()`
- `list_releases()` → `update_manager.list_releases()` (for rollback)
- `get_repo_info()` → `update_manager._get_repo_info()`
- `read_installed_version()` → `update_manager.current_version()` (reads `.bigedcc_version`)
- `_load_github_config()` → `update_manager._load_github_config()` (reads `[github]` from fleet.toml)

After migration is verified, `release_updater.py` is deleted.

---

## Net Impact

| Metric | Before | After |
|---|---|---|
| Tkinter/CTk files | 33 files, ~20K lines | 0 (core launcher) |
| customtkinter dependency | Required for launcher | Only standalone tools |
| Updater files | 2 files, 1,488 lines (standalone CTk app) | 4 files, ~700 lines (dashboard-integrated) |
| Update UI | Separate window, manual trigger only | Dashboard panel, auto-check, SSE progress |
| Release auto-detection | None | Startup + 24h periodic |
| Update notification | Git-only green banner (tkinter) | Nav badge, all modes |
| Binary swap | CTk GUI + bat script | Headless helper (~100 lines) |
| BigEdCC.exe size | ~30MB (includes customtkinter) | ~15MB estimated (no CTk) |

---

## Implementation Order

1. **Tkinter removal** (Sub-Project 1) — pure deletion, low risk, do first
2. **update_manager.py** — core logic, testable independently
3. **update_blueprint.py** — REST endpoints
4. **update_helper.py** — headless swapper
5. **Dashboard UI** — HTML + JS + SSE wiring
6. **Build system updates** — build.py, specs, requirements
7. **Delete old updater files** — updater.py, release_updater.py, Updater.spec
8. **Tests** — update endpoint tests, helper integration test
