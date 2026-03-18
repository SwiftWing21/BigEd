# Technical Debt Tracking

This document tracks known technical debt, brittle architectural patterns, and temporary hacks that need to be addressed to ensure long-term stability of the BigEd Fleet.

> **Last reviewed:** v0.30 (2026-03-18)

## 1. Architecture & Core Code

### 1.1. Flat Task Queue (Missing DAG)
- **Status:** OPEN — Medium priority
- **Description:** `tasks` table is flat. Cannot express "Task C requires Task A and Task B to finish first."
- **Impact:** Medium. Forces manual serialization of multi-step workflows.
- **Proposed Solution:** Roadmap **v0.31**. Introduce `parent_id`, `depends_on`, and `WAITING` state to `db.py`.

## 2. Security & Data Integrity

### 2.1. Unvalidated Skill Outputs
- **Status:** OPEN — Medium priority
- **Description:** Most skills return unstructured text or weakly-structured JSON.
- **Impact:** Medium. Downstream agents struggle to parse outputs, leading to hallucinated data extraction.
- **Proposed Solution:** Introduce strict JSON schema validation at `db.complete_task` boundary.

---

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
