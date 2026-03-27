# PyWebView Launcher Migration — Design Spec

## Goal

Replace the 4377-line tkinter launcher with a ~150-line PyWebView wrapper that loads the existing Flask dashboard inside a native OS window. Fill 5 functionality gaps in the dashboard, migrate all settings to the dashboard Settings page with tabbed categories.

**Result:** One unified UI (web dashboard) served in a native desktop window. No more parallel tkinter implementation.

## Architecture

```
BigEd/launcher/launcher_webview.py  (~150 lines)
  ├── Single-instance lock (socket port 19876)
  ├── pythonw.exe relaunch (hide console)
  ├── Start supervisor.py subprocess
  ├── Wait for Flask to respond (poll /api/health)
  ├── PyWebView window → http://localhost:5555
  ├── Python↔JS bridge (BridgeAPI class)
  ├── pystray system tray (minimize, open, quit)
  └── Shutdown handler (stop fleet on exit)
```

The dashboard is the UI. PyWebView is just the native window frame.

## Gap Fills (dashboard additions)

### 1. Boot Progress Section

**Location:** New section in dashboard.html, shown as the initial view before dashboard loads.

**Behavior:**
- On first load, if `/api/health` returns unhealthy subsystems, show boot progress
- SSE events push boot stage updates: `{type: "boot_stage", data: {stage: "ollama", status: "starting", elapsed: 3.2}}`
- Stages: Ollama → Dr. Ders → Dashboard → Workers → Ready
- Each stage shows: name, elapsed time, status dot (yellow=starting, green=done, red=failed)
- Auto-transitions to main dashboard when all stages report "ready"
- No manual boot initiation needed — supervisor handles boot, dashboard just visualizes it

**Server side:** Add `_broadcast_sse({"type": "boot_stage", ...})` calls in `fleet/boot_sequence.py` at each stage completion.

### 2. Fleet Start/Stop Controls

**Location:** Dashboard header bar, next to the "Connected" indicator.

**Components:**
- "Stop Fleet" button (red, only shown when fleet is running)
- "Start Fleet" button (green, only shown when fleet is stopped)
- Calls existing `/api/fleet/start` and `/api/fleet/stop` endpoints

**Detection:** SSE `status` events already include agent count. If agents = 0 and supervisor offline → show Start. Otherwise → show Stop.

### 3. Ollama Model Dropdown

**Location:** Dashboard header bar, between the connection indicator and fleet controls.

**Components:**
- Dropdown showing current loaded model (from SSE thermal data)
- On change: POST `/api/fleet/model-switch` with `{model: "qwen3:4b"}`
- Server endpoint calls `process_manager.stop_ollama()` + `process_manager.start_ollama()` + keepalive ping with new model
- Dropdown populated from `/api/health` (available models list from Ollama /api/tags)

### 4. First-Run Walkthrough Modal

**Location:** Dashboard modal overlay, triggered when no `fleet.db` exists or a `_first_run` flag is set.

**Steps (3-panel wizard):**
1. **System Detection** — calls `/api/system-info` (already exists via system_info.py), shows: RAM, CPU cores, GPU, recommended tier
2. **Model Selection** — shows available Ollama models, pre-selects based on GPU VRAM
3. **Ready** — shows fleet.toml summary, "Launch Fleet" button

**Server side:** New `/api/walkthrough/complete` POST that saves settings to fleet.toml and clears the first-run flag.

### 5. Settings Page Overhaul

**Location:** Replace current Settings section in dashboard.html.

**Layout:** Top row of 6 category tabs, content below. Each tab loads its settings fields dynamically from `/api/settings/category/<name>`.

| Tab | Config Sections | Key Fields |
|-----|----------------|------------|
| **General** | [fleet], [naming], [dashboard], [logging] | Fleet name, disabled agents, dashboard port, log level, agent display names |
| **Models** | [models], [models.tiers], [review] | Default model, conductor model, tier routing, review provider, keep-alive |
| **Hardware** | [thermal], [gpu], [thermal.vram], [workers] | GPU power limit, thermal thresholds, VRAM caps, worker count, CPU affinity, RAM ceiling |
| **API & Keys** | [api_gate], API keys registry | Gate enable/disable, per-provider toggle, budget slider, TTL, all API keys with inline entry |
| **Operations** | [backup], [schedules], [mcp], [ingest] | Backup interval/depth, schedule toggles, MCP server list, ingest sources |
| **Advanced** | [federation], [security], [sso], [billing], [enterprise] | Federation peers, mTLS, SSO config, billing quotas, encryption |

**Server side:** New `/api/settings/category/<name>` GET endpoint returns fields with types, current values, and whether they're editable. New `/api/settings/update` POST to save changes (writes to fleet.toml via config.py).

**Existing API Keys panel** stays as a sub-section within "API & Keys" tab.

## PyWebView Launcher

### File: `BigEd/launcher/launcher_webview.py`

```python
# Core flow:
1. _acquire_instance_lock()     # socket on port 19876
2. _relaunch_windowless()       # pythonw.exe if needed
3. _start_supervisor()          # subprocess.Popen supervisor.py
4. _wait_for_dashboard()        # poll /api/health until 200
5. webview.create_window(...)   # native window → localhost:5555
6. _setup_tray()                # pystray with menu
7. webview.start()              # blocks until window closed
8. _shutdown()                  # stop supervisor, release lock
```

### Python↔JS Bridge

```python
class BridgeAPI:
    """Exposed to JS as window.pywebview.api"""

    def get_fleet_status(self):
        """Quick status for tray tooltip."""
        return {"agents": 4, "pending": 0, "running": True}

    def minimize_to_tray(self):
        """Called from JS close button override."""
        window.hide()

    def show_native_dialog(self, title, message):
        """Native OS message box."""
        webview.windows[0].create_confirmation_dialog(title, message)

    def open_file_dialog(self, title="Select file"):
        """Native file picker."""
        return webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False)
```

JS calls: `window.pywebview.api.minimize_to_tray()` etc.

### System Tray (pystray)

```python
menu = pystray.Menu(
    pystray.MenuItem("Open BigEd", _show_window),
    pystray.MenuItem("Open in Browser", _open_browser),
    pystray.Menu.SEPARATOR,
    pystray.MenuItem("Fleet Status", _show_status),
    pystray.MenuItem("Quit", _quit),
)
```

Window close behavior: instead of closing, hides to tray. "Quit" from tray menu actually exits.

### Close Behavior

Dashboard adds a close confirmation in JS:
```javascript
window.addEventListener('beforeunload', function(e) {
    // Ask PyWebView bridge to minimize instead of close
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.minimize_to_tray();
        e.preventDefault();
    }
});
```

## Migration Path

1. New file: `BigEd/launcher/launcher_webview.py` (primary launcher)
2. Rename: `BigEd/launcher/launcher.py` → `BigEd/launcher/launcher_tkinter.py` (fallback)
3. New file: `BigEd/launcher/launcher.py` — thin dispatcher:
   ```python
   try:
       import webview
       from launcher_webview import main
   except ImportError:
       from launcher_tkinter import main
   main()
   ```
4. `requirements.txt`: add `pywebview` and `pystray`
5. No tkinter code deleted — just bypassed when pywebview is available

## Dependencies

- `pywebview>=5.0` — native window (uses Edge WebView2 on Windows 11, already installed)
- `pystray>=0.19` — system tray (already used by existing launcher)
- `Pillow` — tray icon rendering (already a dependency)

## What This Does NOT Change

- `fleet/supervisor.py` — untouched (still the process manager)
- `fleet/dashboard.py` — existing endpoints untouched (new ones added)
- `fleet/templates/dashboard.html` — existing sections untouched (new ones added)
- `BigEd/launcher/ui/` — stays in repo (fallback path), not deleted
- `fleet.toml` — no schema changes (settings UI reads/writes existing keys)

## Error Handling

- PyWebView not installed → fallback to tkinter launcher
- Flask not responding after 30s → show error in webview window ("Fleet failed to start")
- pystray not available → no tray icon, window close actually closes
- Single-instance lock held → show message and exit

## Testing

- `python BigEd/launcher/launcher.py` → opens PyWebView window (or tkinter fallback)
- Dashboard boot progress visible during startup
- Settings tabs load and save correctly
- Tray icon appears, minimize/restore works
- Close window → minimizes to tray (not quit)
- Tray Quit → stops fleet + exits

### 6. BA Fractal + Fibonacci Graph Layout

**Location:** Custom Cytoscape layout in `fleet/templates/dashboard.html` + `fleet/static/layout_fractal.js`

**Algorithm:** Barabási–Albert fractal positioning with Fibonacci (golden angle) spiral distribution.

**Core math:**

1. **Radial distance by degree** (hub-spoke, high-degree = center):
   ```
   r(node) = R_max × (1 - degree / max_degree) ^ 0.6
   ```

2. **Angular placement via golden angle** (Vogel's sunflower spiral):
   ```
   θ(i) = i × 137.508°
   r(i) = c × √i
   ```
   Within each degree tier, nodes are placed along the golden spiral. This prevents overlap and creates the organic brain branching pattern.

3. **Type-based tier rings** (like concentric but fractal-spaced):
   - Tier 0 (center): supervisor, hub nodes
   - Tier 1: agents (active)
   - Tier 2: skills (by connection count)
   - Tier 3: models, folders, configs
   - Tier 4 (outer): tasks, chunks, messages

4. **Offline agent orbits** — idle/disconnected agents orbit their nearest skill island:
   ```
   orbit_r = island_center_r × 1.3
   orbit_θ = agent_index × (2π / num_offline)
   ```

5. **Activity-based inward drift** — frequently touched nodes (high task count in last hour) pull toward center:
   ```
   r_adjusted = r × (1 - min(activity_score, 1.0) × 0.4)
   ```
   Hot skill nodes drift 40% closer to the hub during active periods.

6. **Fibonacci container spacing** — island groups (agent + its skills + its tasks) are positioned using the golden ratio for inter-group spacing:
   ```
   group_r(g) = R_base × φ^g    (φ = 1.618)
   group_θ(g) = g × 137.508°
   ```
   This creates the spiral arm structure visible in BA fractal graphs.

**Registration:** Custom layout registered as `cytoscape.use(cytoscapeFractalBrain)`, selectable alongside fcose/concentric.

**Performance:** O(n) — single pass over nodes, no iterative simulation. Handles 10K+ nodes instantly.

## Pod Assignment (6 pods)

| Pod | Files | Scope |
|-----|-------|-------|
| **pod-launcher** | `BigEd/launcher/launcher_webview.py`, `BigEd/launcher/launcher.py` (dispatcher) | PyWebView window, pystray, process management, single-instance, bridge API |
| **pod-boot** | `fleet/templates/dashboard.html`, `fleet/boot_sequence.py`, `fleet/dashboard.py` | Boot progress section + SSE events from boot stages |
| **pod-controls** | `fleet/templates/dashboard.html`, `fleet/dashboard.py` | Header: start/stop buttons + Ollama model dropdown + walkthrough modal |
| **pod-settings** | `fleet/templates/dashboard.html`, `fleet/dashboard.py` | Settings overhaul: 6 category tabs, field rendering, save endpoint |
| **pod-integration** | All files | Wire close behavior JS, tray badge updates, fallback dispatcher, requirements.txt, smoke test |
| **pod-graph** | `fleet/static/layout_fractal.js`, `fleet/templates/dashboard.html` | BA fractal + Fibonacci custom layout, offline orbits, activity drift, layout selector |
