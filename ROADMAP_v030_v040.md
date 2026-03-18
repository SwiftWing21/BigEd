# BigEd CC Roadmap: v0.31 → v0.40

> **Goal of v0.40:** Advanced fleet orchestration, semantic guardrails, and dynamic task decomposition. Moving from a flat task queue to a graph-based, highly reliable swarm.

## v0.31 — Task Graph & Decomposition (DAG) [DONE]

Completed 2026-03-18. See `db.py` for implementation.

- `parent_id`, `depends_on` columns, `WAITING` status
- `_promote_waiting_tasks()` / `_cascade_fail_dependents()` on complete/fail
- `post_task_chain()` helper for sequential pipelines
- `post_task()` validates JSON payloads, clamps priority 1-10
- `complete_task()` validates result JSON, auto-wraps non-JSON
- Soak tests: 13/13 pass (3 new DAG/validation tests)

---

## v0.32 — UI Resilience & Clean Refresh

**Goal:** Eliminate visual flicker, protect timer chains from silent death, and ensure all data flows complete the round-trip to the UI.

### 32.1 Resilient Timer Callbacks

**Problem:** All three timer chains (`_schedule_refresh` 4s, `_schedule_hw` 3s, `_schedule_ollama_watch` 8s) have unprotected main-thread calls. A single exception in any callback silently kills the timer — no recovery, no log, no indication to the user.

**Fix:** Wrap each timer body in a standardized guard:
```python
def _schedule_refresh(self):
    try:
        self._update_pills(parse_status())
        self._update_agents_table(parse_status())
        # ... bg thread for I/O ...
    except Exception as e:
        self._log_output(f"Refresh error: {e}")
    finally:
        self.after(4000, self._schedule_refresh)
```

Apply to:
- `_schedule_refresh()` (~line 1515) — pills + agents + log + advisory
- `_schedule_hw()` (~line 1475) — CPU/RAM/GPU/ETH stats
- `_schedule_ollama_watch()` (~line 1630) — Ollama status polling

**Files:** `launcher.py`

### 32.2 Eliminate Agents Tab Flicker (Cache + Configure)

**Problem:** `_agents_tab_refresh()` calls `.destroy()` on ALL child widgets every refresh cycle, then recreates them from scratch. This causes visible flicker on the Agents tab.

**Note:** `_update_agents_table()` in the Command Center tab already does this correctly — it caches widget refs and calls `.configure()` to update text/colors in place. The Agents tab uses the older pattern.

**Fix:** Mirror the Command Center pattern:
- Pre-create a dict `self._agent_row_cache = {}` mapping agent name to widget tuple
- On refresh: `.configure()` existing rows, create new ones, `.grid_remove()` stale ones
- Never `.destroy()` during periodic refresh — only on tab close

**Files:** `launcher.py` (`_agents_tab_refresh`)

### 32.3 Module Refresh Integration

**Problem:** Modules implement `on_refresh()` but it's never called by the launcher's timer system. Module data goes stale unless the user manually switches tabs.

**Fix:** In `_schedule_refresh()`, call `on_refresh()` for the currently visible module tab:
```python
# Only refresh the active module to avoid unnecessary DB work
active_tab = self._tabview.get()
for name, mod in self._modules.items():
    if getattr(mod, "LABEL", name.title()) == active_tab:
        try:
            mod.on_refresh()
        except Exception:
            pass
        break
```

**Files:** `launcher.py` (`_schedule_refresh`)

### 32.4 Dispatch Result Feedback (Timeout Notification)

**Problem:** `_poll_task_result()` in the console has a 60s timeout. If the task doesn't complete in time, the polling thread exits silently — user gets no feedback.

**Fix:** Add a timeout notification at the end of the polling loop:
```python
# After while loop exhausts
self.after(0, lambda: self._append(
    "system", f"Task {task_id} ({skill}) — still running after {timeout}s. "
    "Check fleet status for updates."))
```

**Files:** `launcher.py` (`_poll_task_result`)

---

## v0.33 — End-to-End Flow Verification

**Goal:** Systematic verification that every user-facing flow completes its round-trip, with automated smoke tests for the GUI layer.

### 33.1 Flow Audit Checklist

Trace and verify every user action → backend → UI feedback path:

| Flow | Entry Point | Backend | UI Feedback | Status |
|------|------------|---------|-------------|--------|
| Taskbar dispatch | `_dispatch_task()` | `lead_client.py dispatch` via WSL | `_log_output()` callback | Verify |
| Console dispatch | `_execute_dispatch()` | `lead_client.py dispatch` via WSL | `_poll_task_result()` → chat | Verify |
| Console API key save | `_set_key_dialog()` | `lead_client.py secret set` via WSL | Status label update | Verify |
| Key Manager edit | `_edit_key()` | `lead_client.py secret set` via WSL | `_scan_lbl` + `_load_keys()` | Verify |
| Key Manager scan | `_scan_skills()` | `lead_client.py dispatch` via WSL | `_scan_lbl` update | Verify |
| Module CRM add | `_add_dialog()` | `_db_conn()` INSERT | `on_refresh()` | Verify |
| Module Accounts edit | `_edit_dialog()` | `_db_conn()` UPDATE | `on_refresh()` | Verify |
| Module Onboarding toggle | checkbox `_on_toggle` | `_db_conn()` UPDATE | `on_refresh()` | Verify |
| Module Customers edit | `_edit_dialog()` | `_db_conn()` UPDATE | `on_refresh()` | Verify |
| Agents tab edit | `_agents_edit_dialog()` | `_db_conn()` INSERT/DELETE | `_agents_tab_refresh()` | Verify |
| Header HW stats | `_schedule_hw()` | pynvml/psutil | `_apply_hw()` labels | Verify |
| Sidebar Ollama status | `_schedule_ollama_watch()` | HTTP GET /api/tags | `_apply_ollama_status()` | Verify |
| Fleet pills | `_schedule_refresh()` | `parse_status()` file read | `_update_pills()` | Verify |

### 33.2 GUI Smoke Test Script

Create `BigEd/launcher/gui_smoke_test.py` — headless verification of GUI flows:
- Import launcher module, instantiate `BigEdCC` with `withdraw()` (hidden window)
- Verify all timer registrations fire at least once without exception
- Verify module load/build_tab/on_refresh cycle for each enabled module
- Verify `_dispatch_raw()` constructs valid WSL command (string check, not execution)
- Verify `_db_init()` creates all expected tables
- Verify `_find_fleet_dir()` resolves correctly from launcher directory
- Run headless with `--smoke` flag, exit 0/1

### 33.3 Visual Refresh Regression Test

Manual test protocol (documented, not automated):
1. Launch app, observe Agents tab for 30s — no flicker on row updates
2. Switch between all module tabs — each loads data without stutter
3. Open Console, dispatch a task — verify result appears in chat within 60s
4. Open Key Manager — verify keys load, edit saves, scan queues
5. Monitor header stats for 60s — no color flicker at threshold boundaries
6. Kill a fleet worker (pkill) — verify Agents tab shows status change within 8s

---

## v0.34 — New User Walkthrough (First-Run Experience)

**Goal:** Guide new users through initial setup on first launch, with the ability to skip individual steps or the entire walkthrough.

### 34.1 Walkthrough Engine (`launcher.py`)

A modal overlay or toplevel dialog sequence that triggers on first launch (or when `fleet.toml` has no `[walkthrough] completed = true`).

**Core mechanics:**
- Each step is a standalone dialog with: title, description, action area, and navigation buttons
- **"Skip" checkbox** per step — marks step as skipped, advances to next
- **"Skip All" button** — closes walkthrough immediately, marks all remaining as skipped
- **"Don't show again" checkbox** — persists to `fleet.toml [walkthrough] completed = true`
- Progress indicator: step N of M (dots or progress bar, matching updater style)
- Steps can be re-triggered from Config sidebar: "Re-run Setup Walkthrough"

### 34.2 Walkthrough Steps

| Step | Title | What it does | Skippable reason |
|------|-------|-------------|-----------------|
| 1 | Welcome | Brief overview of BigEd CC, what the fleet does | Returning users |
| 2 | API Keys | Guide through setting Claude/Gemini/search API keys via Key Manager dialog | User may only use local models |
| 3 | Fleet Profile | Select deployment profile (minimal/research/consulting/full), show what modules each enables | Already configured |
| 4 | Ollama Setup | Check if Ollama is running, show model tier table, offer to pull default model | Already installed |
| 5 | First Task | Pre-filled taskbar dispatch (e.g. "Research local AI deployment for small businesses") with explanation of what happens | User wants to explore first |
| 6 | Console Tour | Highlight the 3 console tabs (Claude/Gemini/Local), show how to switch and dispatch | Experienced users |

### 34.3 Persistence & Config

```toml
[walkthrough]
completed = false          # set true after finish or Skip All
skipped_steps = []         # list of step numbers skipped
completed_at = ""          # ISO date when walkthrough was completed/skipped
```

### 34.4 Implementation Notes

- Dialog class: `WalkthroughDialog(ctk.CTkToplevel)` with step state machine
- Each step is a dict: `{"title", "description", "build_content_fn", "validate_fn"}`
- `build_content_fn(parent_frame)` renders step-specific UI into the dialog body
- `validate_fn()` returns `(ok, msg)` — allows steps to verify setup before advancing (e.g., "Ollama not reachable" warning)
- Skip checkbox state passed to navigation: skipped steps log to `fleet.toml` but don't block progress
- Walkthrough respects current profile — only shows relevant steps (e.g., skip API Keys step if profile is minimal/local-only)

**Files:** `launcher.py` (new `WalkthroughDialog` class + trigger in `__init__` after `_build_ui`)

---

## v0.35 — The Evaluator-Optimizer Loop (Guard Rails)
**Goal:** Prevent sub-par or hallucinated outputs from being finalized without adversarial review.
- Introduce `REVIEW` state to the task lifecycle.
- High-stakes skills (like `legal_draft`, `security_audit`, `coder` outputs) transition to `REVIEW` instead of `DONE`.
- An independent agent (or Claude/Gemini API directly) acts as the adversarial reviewer. If it fails, status → `RUNNING` with the critique appended for the original worker to fix.

---

## v0.36 — Semantic Watchdog (Checker Agent)
**Goal:** Move beyond mechanical process restarts to semantic health monitoring.
- A lightweight background skill that monitors the `tasks` table for hallucination loops or excessive error rates (N failures in a row).
- If an anomaly is detected, it proactively changes the worker's status to `QUARANTINED` and sends an alert to the dashboard.

---

## v0.37 — Unified Human-in-the-Loop (HitL)
**Goal:** Allow agents to dynamically request human input mid-task.
- Upgrade the `messages` table and UI to support direct Agent <-> Human threads via a new "Fleet Comm" tab.
- Agents can pause execution, post a message ("Found 3 leads, which one should I draft the proposal for?"), enter a `WAITING_HUMAN` state, and resume once the operator replies.
