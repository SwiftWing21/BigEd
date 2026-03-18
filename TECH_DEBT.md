# Technical Debt Tracking

This document tracks known technical debt, brittle architectural patterns, and temporary hacks that need to be addressed to ensure long-term stability of the BigEd Fleet.

> **Last reviewed:** v0.30 (2026-03-18)

## 1. Architecture & Core Code

### 1.1. WSL RPC Mechanisms (Brittle Dispatch)
- **Status:** OPEN — High priority
- **Description:** `launcher.py` uses string-interpolated Python snippets executed via `wsl -e bash -c` to enqueue tasks or scan keys (`_dispatch_raw` line ~1960, `KeyManagerDialog` lines ~3717-3734, `ModelDialog` line ~4018).
- **Impact:** High. Brittle escaping, silent failures, impossible to debug.
- **Locations:** `_dispatch_raw()`, `_add_custom_key()`, `_scan_skills()`, API key save in `ModelDialog`
- **Proposed Solution:** Create `fleet/rpc.py` with clean CLI arguments or JSON payloads, replacing inline `python -c` shell hacks.

### 1.2. Flat Task Queue (Missing DAG)
- **Status:** OPEN — Medium priority
- **Description:** `tasks` table is flat. Cannot express "Task C requires Task A and Task B to finish first."
- **Impact:** Medium. Forces manual serialization of multi-step workflows.
- **Proposed Solution:** Roadmap **v0.31**. Introduce `parent_id`, `depends_on`, and `WAITING` state to `db.py`.

## 2. Security & Data Integrity

### 2.1. Bash-based Secrets Management
- **Status:** OPEN — Medium priority
- **Description:** `KeyManagerDialog` uses `grep -v` and `echo` piped through WSL bash to read/write `~/.secrets` (line ~4018).
- **Impact:** Medium. Malformed string or interrupted write could corrupt the secrets file.
- **Proposed Solution:** Python utility in `fleet/` for atomic `.secrets` file read/update/write.

### 2.2. Unvalidated Skill Outputs
- **Status:** OPEN — Medium priority
- **Description:** Most skills return unstructured text or weakly-structured JSON.
- **Impact:** Medium. Downstream agents struggle to parse outputs, leading to hallucinated data extraction.
- **Proposed Solution:** Introduce strict JSON schema validation at `db.complete_task` boundary.

## 3. UI / UX Patterns

### 3.1. Main Thread Blocking (Database/Network)
- **Status:** OPEN — Low priority
- **Description:** SQLite queries in `launcher.py` (`_db_conn()` line ~1091) and all extracted modules run on the main UI thread. Includes `_refresh_agents()`, agent edit saves, and module refresh methods.
- **Impact:** Low (currently). As `tools.db` grows, UI may stutter during tab switching.
- **Proposed Solution:** Move DB reads to background threads, populate UI via `self.after(0, callback)`.

---

## Resolved (v0.30)

### [RESOLVED] 3.2. Hardcoded Absolute Paths
- **Resolved in:** v0.30 (2026-03-18)
- **What was fixed:**
  - `FLEET_DIR` now computed dynamically via `_find_fleet_dir()` — walks up from `_SRC_DIR` looking for `fleet/fleet.toml`, with `BIGED_FLEET_DIR` env var override.
  - WSL `wsl()` helper now converts `FLEET_DIR` to `/mnt/` path dynamically instead of hardcoded `/mnt/c/Users/max/Projects/Education/fleet`.
  - No hardcoded absolute paths remain in `launcher.py`.

### [RESOLVED] 1.2 (partial). The `launcher.py` Monolith
- **Resolved in:** v0.22-v0.23 (2026-03-18)
- **What was fixed:**
  - 6 tab modules extracted to `BigEd/launcher/modules/mod_*.py` with standard interface.
  - Module loader (`modules/__init__.py`) handles discovery, manifest, profiles, deprecation.
  - `launcher.py` reduced from ~5,800 to ~4,700 lines. Core tabs (Command Center, Agents) remain inline.
- **Remaining:** DB access (`_db_conn()`) and WSL dispatch (`_dispatch_raw()`) still coupled to launcher — modules call `self.app._method()`. Full decoupling deferred.

---
> **Maintenance Protocol:** Review this file during every major version bump (e.g., v0.30, v0.40). Move resolved items to the `Resolved` section with date and description.
