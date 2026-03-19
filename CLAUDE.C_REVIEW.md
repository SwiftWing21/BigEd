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

<!-- Entry template:
### YYYY-MM-DD — [CATEGORY] file_or_component
- **Finding:** what was observed
- **Severity:** low / medium / high
- **Action:** fix applied / deferred / noted
- **Status:** open / resolved
-->
