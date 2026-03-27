# PyWebView Launcher Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4382-line tkinter launcher with a ~150-line PyWebView wrapper that loads the Flask dashboard in a native OS window, filling 5 functionality gaps and overhauling Settings with 6 tabbed categories.

**Architecture:** PyWebView creates a native window pointing at localhost:5555. pystray handles system tray. Flask dashboard serves all UI. Old tkinter launcher preserved as fallback.

**Tech Stack:** Python 3.11+, pywebview>=5.0, pystray>=0.19, Flask, SSE, Edge WebView2

**Spec:** `docs/superpowers/specs/2026-03-27-pywebview-launcher-design.md`

---

## File Map

| File | Action | Pod |
|------|--------|-----|
| `BigEd/launcher/launcher_webview.py` | **Create** | pod-launcher |
| `BigEd/launcher/launcher.py` | **Rewrite** (thin dispatcher) | pod-launcher |
| `BigEd/launcher/launcher_tkinter.py` | **Rename** from launcher.py | pod-launcher |
| `fleet/boot_status.py` | **Create** | pod-boot |
| `fleet/boot_sequence.py` | **Modify** | pod-boot |
| `fleet/templates/dashboard.html` | **Modify** | pod-boot, pod-controls, pod-settings |
| `fleet/dashboard.py` | **Modify** | pod-boot, pod-controls |
| `fleet/process_control.py` | **Modify** | pod-controls |
| `fleet/tests/test_launcher.py` | **Create** | pod-integration |
| `requirements.txt` | **Modify** | pod-integration |

---

## Task 1: pod-launcher — PyWebView + System Tray

Create the new launcher. Full code in spec. Key steps:
- [ ] Rename `launcher.py` → `launcher_tkinter.py`
- [ ] Create `launcher_webview.py` (~150 lines): single-instance lock, pythonw relaunch, start supervisor, wait for dashboard, PyWebView window, BridgeAPI class, pystray tray
- [ ] Create thin dispatcher `launcher.py`: try webview import, fallback to tkinter
- [ ] Verify `pip install pywebview pystray`
- [ ] Test launcher opens native window
- [ ] Commit

---

## Task 2: pod-boot — Boot Progress Overlay

- [ ] Create `fleet/boot_status.py` (~30 lines): JSON file-based stage tracking (update_stage, read, clear)
- [ ] Modify `fleet/boot_sequence.py`: add `boot_status.update_stage()` at each of 7 stages
- [ ] Add `GET /api/boot/status` endpoint to dashboard.py
- [ ] Add boot overlay HTML to dashboard.html (fixed overlay, stage list with dots/timers)
- [ ] Add JS: poll `/api/boot/status` every 500ms, render stages, auto-hide when ready
- [ ] Commit

---

## Task 3: pod-controls — Header Controls + Model Dropdown + Walkthrough

- [ ] Modify dashboard.html header bar: add model dropdown + fleet start/stop buttons
- [ ] Add JS: `switchModel()`, `fleetStart()`, `fleetStop()`, `updateHeaderControls()` (wired to SSE)
- [ ] Add `POST /api/fleet/model-switch` endpoint to process_control.py
- [ ] Enhance `/api/health` to return available models list + current loaded model
- [ ] Add walkthrough modal HTML + JS (3-step: system info → model → launch)
- [ ] Add `GET /api/walkthrough/needed` + `POST /api/walkthrough/complete` endpoints
- [ ] Commit

---

## Task 4: pod-settings — Settings Page with 6 Category Tabs

- [ ] Replace settings section HTML with tabbed layout (General, Models, Hardware, API & Keys, Operations, Advanced)
- [ ] Add JS: `switchSettingsTab()`, `loadSettingsTab()`, `_renderSettingsSection()`, `_saveSettingField()`
- [ ] Tab→section mapping: general=[fleet,naming,dashboard,logging], models=[models,models.tiers,review,idle], hardware=[thermal,gpu,workers,capacity], apikeys=[api_gate,_api_keys], operations=[backup,schedules,mcp,ingest], advanced=[federation,security,sso,billing]
- [ ] API Keys panel integrated into apikeys tab
- [ ] Inline field editing: bool=checkbox, text/int/float=input, saves on blur via PUT /api/settings/<section>
- [ ] Commit

---

## Task 5: pod-integration — Wiring + Tests

- [ ] Add pywebview, pystray to requirements.txt
- [ ] Add close-to-tray JS bridge (beforeunload → pywebview.api.minimize_to_tray)
- [ ] Wire `updateHeaderControls(data)` into SSE handler
- [ ] Create `fleet/tests/test_launcher.py` (5 tests: boot_status, dispatcher, webview module, bridge API, tkinter fallback exists)
- [ ] Run tests + smoke tests
- [ ] Commit
