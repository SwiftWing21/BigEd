# Technical Debt Tracking

This document tracks known technical debt, brittle architectural patterns, and temporary hacks that need to be addressed to ensure long-term stability of the BigEd Fleet.

> **Last reviewed:** v0.31 (2026-03-18)

All tracked technical debt has been resolved. See Resolved section below.

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
