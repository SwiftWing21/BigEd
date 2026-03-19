# Technical Debt Tracking

This document tracks known technical debt, brittle architectural patterns, and temporary hacks that need to be addressed to ensure long-term stability of the BigEd Fleet.

> **Last reviewed:** v0.41 + PT/DT tracks (2026-03-18)

---

## Open — Cross-Platform

### 4.1. WSL-Only Fleet Communication
- **Location:** `launcher.py:256-282` (`wsl()` / `wsl_bg()` functions)
- **Problem:** All fleet communication shells through WSL Ubuntu. On Linux/Mac, WSL doesn't exist — fleet should run natively via `DirectBridge`.
- **Impact:** Blocks deployment on any non-Windows platform.
- **Fix:** Implement `FleetBridge` abstraction (see `CROSS_PLATFORM.md`). Replace ~20 `wsl_bg()` call sites with `bridge.run_bg()`.
- **Track:** PT-1 (Platform Abstraction)

### 4.2. winreg-Only Installer/Uninstaller
- **Location:** `installer.py:15,61-88`, `uninstaller.py:10,50-61`
- **Problem:** Install/uninstall uses Windows registry (`winreg`) for Add/Remove Programs. No equivalent for Linux/macOS.
- **Impact:** Blocks packaged distribution on non-Windows.
- **Fix:** Platform-conditional install: registry on Windows, `.desktop` file on Linux, `/Applications` copy on macOS.
- **Track:** PT-3 (Platform Packaging)

### 4.3. Windows-Only Build Pipeline
- **Location:** `BigEd/launcher/build.bat`
- **Problem:** Build script is a `.bat` file. PyInstaller `--add-data` uses `;` separator (Windows-only).
- **Impact:** Cannot build on Linux/macOS without manual command modification.
- **Fix:** Replace with `build.py` that auto-detects separator and platform-specific flags. See `build_reference.md` S4.
- **Track:** PT-2 (Cross-Platform Build)

### 4.4. Updater Self-Swap Uses .bat Script
- **Location:** `updater.py:454-466`
- **Problem:** Updater writes a `_swap_updater.bat` trampoline to replace itself while running. `.bat` files don't exist on Linux/macOS.
- **Impact:** Updater non-functional on non-Windows.
- **Fix:** On Linux/macOS, use `os.execv()` for in-place process replacement. Conditional trampoline strategy.
- **Track:** PT-3 (Platform Packaging)

## Open — Diagnostics

### 5.1. `_log_output()` Not Persisted
- **Location:** `launcher.py` (`_log_output` method, ~line 2000)
- **Problem:** Writes only to the GUI text widget. All launcher output lost on close.
- **Impact:** Cannot reconstruct what happened leading up to a crash or issue.
- **Fix:** Add `collections.deque(maxlen=200)` ring buffer mirroring `_log_output()` calls. Optionally persist to `data/launcher_output.log` with 1MB rotation.
- **Track:** DT-1 (Debug Report Infrastructure)

### 5.2. No Global Exception Handler in Launcher
- **Location:** `launcher.py` main entry point
- **Problem:** Unhandled exceptions in the main loop crash silently. No diagnostic capture.
- **Impact:** Crashes produce no actionable data for debugging.
- **Fix:** Wrap main loop in try/except that calls `generate_debug_report()` with traceback, saves to `reports/debug/`, shows notification.
- **Track:** DT-1 (Debug Report Infrastructure)

### 5.3. No Structured Error Reporting Format
- **Problem:** No unified format for capturing system state at time of error. Diagnosis requires manual log-tailing across multiple files.
- **Impact:** Slow issue diagnosis, incomplete bug reports from users.
- **Fix:** Implement `generate_debug_report()` producing structured JSON (see `FRAMEWORK_BLUEPRINT.md` S10.1).
- **Track:** DT-1 (Debug Report Infrastructure)

### 5.4. Dashboard Alerts In-Memory Only
- **Location:** `dashboard.py` alert system
- **Problem:** Alerts stored in-memory list (100-item buffer). Lost on dashboard restart.
- **Impact:** Historical alert data unavailable for post-incident review.
- **Fix:** Persist alerts to `fleet.db` or `data/alerts.jsonl`. Load on startup. Include in debug reports.
- **Track:** DT-3 (Resolution Tracking)

## Open — Hardware & Development Notes

### 6.1. Available Test Hardware
- **Primary dev:** Windows 11 PC, RTX 3080 Ti (12GB VRAM), see `MACHINE_PROFILE.md`
- **Linux test device:** Steam Deck OLED 1TB (SteamOS / Arch Linux base, AMD APU RDNA2, 16GB unified RAM)
  - Suitable for validating Linux deployment path (PT-1 through PT-4)
  - CPU-only Ollama recommended — limited VRAM headroom
  - Desktop Mode required for GUI testing
  - `python3-tk` available via `sudo pacman -S tk`

---

## Resolved (v0.33)

### [RESOLVED] 7.1. Duplicate Ollama Keepalive Logic
- **Resolved in:** v0.33 (2026-03-18)
- **What was fixed:**
  - `supervisor.py` had its own `_ping_ollama_keepalive()` called every 240s in the main loop, plus `_warmup_conductor()` at startup.
  - `hw_supervisor.py` already polled every 5s with full GPU/VRAM awareness.
  - Consolidated: hw_supervisor now owns model keepalive (every ~240s / 48 polls), conductor health check (every ~60s / 12 polls), and loaded model inventory.
  - `hw_state.json` expanded with `models_loaded` list and `conductor` status.
  - supervisor.py reduced to process lifecycle only (start/stop Ollama, training detection). Zero Ollama HTTP calls in main loop.
  - Launcher reads `hw_state.json` for conductor status (`+chat` / `-chat` suffix in Ollama status bar).

### [RESOLVED] 7.2. No Offline/Air-Gap Mode
- **Resolved in:** v0.33 (2026-03-18)
- **What was fixed:**
  - `fleet.toml`: `offline_mode` and `air_gap_mode` flags.
  - `config.py`: `is_offline()`, `is_air_gap()`, `AIR_GAP_SKILLS` whitelist. Air-gap implies offline.
  - `worker.py`: Checks `REQUIRES_NETWORK` before dispatch (offline), enforces whitelist (air-gap).
  - 11 skills tagged `REQUIRES_NETWORK = True` (web_search, web_crawl, arxiv_fetch, lead_research, generate_image, generate_video, branch_manager, marketing, pen_test, key_manager, product_release).
  - `_models.py`: Forces local provider when offline.
  - `supervisor.py`: Skips Discord/OpenClaw (offline), skips dashboard + secrets (air-gap).
  - `dashboard.py`: Refuses to start in air-gap mode.
  - Launcher: OFFLINE/AIR-GAP badge in header, API console buttons disabled, dashboard button disabled.
  - Soak tests: 2 new tests (offline skill rejection, air-gap whitelist). 15/15 pass.

---

## Resolved (v0.31)

### [RESOLVED] 1.1. Flat Task Queue (Missing DAG)
- **Resolved in:** v0.31 (2026-03-18)
- **What was fixed:**
  - Added `parent_id` (INTEGER) and `depends_on` (TEXT/JSON array of task IDs) columns to tasks table.
  - Added `WAITING` status — tasks with unmet dependencies start as WAITING, auto-promote to PENDING when all deps complete.
  - `complete_task()` calls `_promote_waiting_tasks()` to check and promote dependents.
  - `fail_task()` calls `_cascade_fail_dependents()` to propagate failures to downstream WAITING tasks.
  - Added `post_task_chain()` helper for sequential task pipelines (A -> B -> C).
  - Schema migration is backward-compatible — `init_db()` adds columns if missing.
  - Soak tests: `test_task_dag` (chain promotion) and `test_task_dag_cascade_fail` — both pass.

### [RESOLVED] 2.1. Unvalidated Skill Outputs
- **Resolved in:** v0.31 (2026-03-18)
- **What was fixed:**
  - `post_task()` now validates `payload_json` is valid JSON (raises `ValueError` if not).
  - `post_task()` clamps priority to 1-10 range.
  - `complete_task()` validates `result_json` is valid JSON; auto-wraps non-JSON results in `{"raw": ...}`.
  - `complete_task()` accepts both str and dict results (auto-serializes dicts).
  - Soak test: `test_post_task_validation` — passes.

## Resolved (v0.30)

### [RESOLVED] 1.2. WSL RPC Mechanisms (Brittle Dispatch)
- **Resolved in:** v0.30 (2026-03-18)
- **What was fixed:**
  - Added `lead_client.py dispatch` command — clean CLI for task dispatch with `--skill`, `--b64`, `--priority`, `--assigned-to` flags.
  - `_dispatch_raw()` now calls `lead_client.py dispatch` instead of inline `python -c` snippet.
  - `KeyManagerDialog._add_custom_key()` and `_scan_skills()` converted to use `lead_client.py dispatch`.
  - Zero inline `python -c` hacks remain in launcher.py.

### [RESOLVED] 2.2. Bash-based Secrets Management
- **Resolved in:** v0.30 (2026-03-18)
- **What was fixed:**
  - Added `lead_client.py secret` command with `set`, `get`, `list` actions — pure Python, atomic file writes via temp-file-then-rename.
  - `KeyManagerDialog._edit_key()` now calls `lead_client.py secret set` instead of bash `grep -v`/`echo`.
  - `_ConsoleBase._set_key_dialog()` converted to use `lead_client.py secret set`.
  - No bash-based secrets manipulation remains.

### [RESOLVED] 3.1. Main Thread Blocking (Database/Network)
- **Resolved in:** v0.30 (2026-03-18)
- **What was fixed:**
  - Added `_db_query_bg(query_fn, callback)` helper to `BigEdCC` — runs DB query in background thread, delivers results to UI thread via `self.after(0, callback)`.
  - `_agents_tab_refresh()` converted to background DB query.
  - All 4 module `on_refresh()` methods (CRM, Accounts, Customers, Onboarding) converted to background DB queries.
  - User-triggered one-shot operations (Save, Export) remain synchronous — acceptable since they're not periodic.

### [RESOLVED] 3.2. Hardcoded Absolute Paths
- **Resolved in:** v0.30 (2026-03-18)
- **What was fixed:**
  - `FLEET_DIR` now computed dynamically via `_find_fleet_dir()` — walks up from `_SRC_DIR` looking for `fleet/fleet.toml`, with `BIGED_FLEET_DIR` env var override.
  - WSL `wsl()` helper now converts `FLEET_DIR` to `/mnt/` path dynamically instead of hardcoded `/mnt/c/Users/max/Projects/Education/fleet`.
  - No hardcoded absolute paths remain in `launcher.py`.

### [RESOLVED] 1.3 (partial). The `launcher.py` Monolith
- **Resolved in:** v0.22-v0.23 (2026-03-18)
- **What was fixed:**
  - 6 tab modules extracted to `BigEd/launcher/modules/mod_*.py` with standard interface.
  - Module loader (`modules/__init__.py`) handles discovery, manifest, profiles, deprecation.
  - `launcher.py` reduced from ~5,800 to ~3,500 lines. Core tabs (Command Center, Agents) remain inline.
- **Remaining:** DB access (`_db_conn()`) and WSL dispatch still coupled to launcher — modules call `self.app._method()`. Full decoupling is a future consideration but not blocking.

---
> **Maintenance Protocol:** Review this file during every major version bump (e.g., v0.30, v0.40). Move resolved items to the `Resolved` section with date and description.
