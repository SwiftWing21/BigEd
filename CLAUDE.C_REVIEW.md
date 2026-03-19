# Code Review Notes

Running log of code review observations, patterns, and action items for the Education project.

## Review Categories
- **[STYLE]** — formatting, naming, consistency
- **[LOGIC]** — correctness, edge cases, bugs
- **[PERF]** — performance, resource usage
- **[SEC]** — security concerns
- **[ARCH]** — structural, design decisions
- **[DEBT]** — tech debt, cleanup opportunities

## Reviews

### 2026-03-17 — Initial setup

### 2026-03-18 — [ARCH/DEBT] launcher.py & System Architecture
- **Finding:** `launcher.py` has become a "God Object" (>4,700 lines), tightly coupling UI rendering, database schemas, raw WSL execution, hardware polling, and application lifecycle.
- **Severity:** High
- **Action:** Added to `TECH_DEBT.md` (4.1). Needs extraction of UI Consoles, Settings, and Hardware monitoring into a separate `ui/` namespace.
- **Status:** open

- **Finding:** Process management uses raw bash string commands (`pkill -f`, `pgrep`) sent over WSL. This is highly brittle and blocks native Windows support.
- **Severity:** Medium
- **Action:** Added to `TECH_DEBT.md` (4.3, 4.5). Recommended switching to native `psutil` process management or an API-driven `supervisor.py`.
- **Status:** open

### 2026-03-18 — [LOGIC/DEBT] Unmanaged DB Connections & API Orphans
- **Finding:** Newly added `launcher.py` methods (`count_waiting_human`, `_refresh_comm`) bypass `_db_query_bg()` and query SQLite synchronously, risking UI freezes.
- **Severity:** Medium
- **Action:** Flagged for refactoring. Should be shifted to background threads or SSE consumption.
- **Status:** open

- **Finding:** Autoresearch agent logs to `results.tsv`, but BigEd CC has no mechanism to read/display this file (UI relies entirely on `knowledge/reports/`).
- **Severity:** Low
- **Action:** Needs a bridge script or UI update to expose overnight training results.
- **Status:** open

- **Finding:** Dashboard REST API endpoints for process control exist but are entirely orphaned. Launcher still uses raw bash.
- **Severity:** Low
- **Action:** Next UI iteration needs to consume these API endpoints to resolve Tech Debt 4.3.
- **Status:** open

### 2026-03-18 — [ARCH/DEBT] Subverted Routing & Brittle Configs
- **Finding:** Skills (`account_review`, `legal_draft`, `pen_test`) are bypassing `skills._models.call_model()` and making raw HTTP requests to Ollama. This breaks cost/token tracking and budget enforcement.
- **Severity:** High
- **Action:** Added to `TECH_DEBT.md` (4.7). All skills must be refactored to use the central router.
- **Status:** open

- **Finding:** `launcher.py` uses `re.sub()` to parse and rewrite `fleet.toml` for UI state changes (like Walkthrough completion and settings updates).
- **Severity:** Medium
- **Action:** Added to `TECH_DEBT.md` (4.6). Should migrate to `tomlkit` to safely preserve file formatting and comments.
- **Status:** open

- **Finding:** `pen_test.py` hardcodes `ip route` via subprocess to find the local subnet. This is strictly Linux-bound and breaks cross-platform execution goals.
- **Severity:** Low (currently mitigated by WSL dependency)
- **Action:** Added to `TECH_DEBT.md` (4.8). Needs a cross-platform Python fallback for subnet discovery.
- **Status:** open

<!-- Entry template:
### YYYY-MM-DD — [CATEGORY] file_or_component
- **Finding:** what was observed
- **Severity:** low / medium / high
- **Action:** fix applied / deferred / noted
- **Status:** open / resolved
-->
