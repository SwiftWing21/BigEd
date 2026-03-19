# Technical Debt Tracking

This document tracks known technical debt, brittle architectural patterns, and temporary hacks that need to be addressed to ensure long-term stability of the BigEd Fleet.

> **Last reviewed:** 0.12.00 (2026-03-19)

All original technical debt resolved. Ongoing items from Gemini architecture audit tracked below.

---

## Emerging & Future-Proofing (v0.32 - v1.0)
*These are architectural bottlenecks that pose a risk to the cross-platform (PT-1/PT-4) and scalability goals of v1.0.*

### [DONE] 4.1. The `launcher.py` God Object
- **Resolved in:** 2026-03-19
- **What was fixed:** Three extraction phases: consoles.py (687 lines), settings.py (1286 lines), boot.py (303 lines). Orphaned HardwareDialog removed. launcher.py: 5747→~3600 lines (-37%). UI modules: 5 extracted files. _db_init moved to data_access.py. SSE client replaces polling.

### [DONE] 4.2. Aggressive UI Polling Loops
- **Resolved in:** 2026-03-19
- **What was fixed:** `ui/sse_client.py` SSE consumer integrated into launcher.py. SSE is primary data source for agent/task updates; polling reduced to 8s fallback when dashboard unavailable. `_handle_sse_status()` callback updates agents table reactively. `_fleet_api()` helper for REST calls.

### [DONE] 4.3. String-Based Process Control
- **Resolved in:** 2026-03-19
- **What was fixed:** 9 REST process control endpoints in process_control.py (Flask Blueprint). Launcher uses `_fleet_api()` for stop/health with wsl() fallback. Dashboard bearer token auth. Rate limiting + CSRF protection.

### [DONE] 4.4. Decentralized Data Access & Raw SQL
- **Resolved in:** 2026-03-19
- **What was fixed:** `data_access.py` DAL module with `DataAccess` class. All 4 launcher modules (mod_crm, mod_accounts, mod_onboarding, mod_customers) migrated from raw `_db_conn()` SQL to DAL methods (ensure_table, insert, query, update, delete).

### [DONE] 4.5. WSL Dependency & Bash Boot Scripts
- **Resolved in:** 2026-03-18
- **What was fixed:** `NativeWindowsBridge` in fleet_bridge.py with bash→Windows cmd translation (_translate_cmd), BIGED_NATIVE_WINDOWS=1 env toggle, detect_cli() in config.py for auto-detection of best local CLI per platform.

### [DONE] 4.6. Regex-Based Configuration Mutation
- **Resolved in:** 2026-03-19
- **What was fixed:** All regex TOML writes replaced with tomlkit across launcher.py AND hw_supervisor.py. Atomic writes via tempfile+os.replace. B4 (hw_supervisor regex) resolved. B7 (pgrep on Windows) resolved with psutil cross-platform detection.

### [DONE] 4.7. Bypassing Model Routing Layer
- **Resolved in:** 2026-03-18
- **What was fixed:** Refactored 12 skills (summarize, discuss, code_discuss, rag_query, account_review, legal_draft, key_manager, security_audit, code_write_review, skill_evolve, review_discards, pen_test) to use `call_complex()` from `_models.py` instead of raw `_ollama()` httpx calls. All inference now routes through cost tracking (CT-1) and budget enforcement (CT-4).

### [DONE] 4.8. OS-Specific Shell Commands in Skills
- **Resolved in:** 2026-03-18
- **What was fixed:** pen_test.py network detection rewritten with cross-platform support: psutil first (all platforms), ipconfig fallback (Windows), ip route fallback (Linux). Security audit OS-specific commands wrapped with platform branching.

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
