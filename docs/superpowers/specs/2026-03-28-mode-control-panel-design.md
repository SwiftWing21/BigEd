# Mode Control Panel — Design Spec

**Date:** 2026-03-28
**Status:** Draft
**Scope:** Unified operational mode switcher + modifier drawer for the BigEd dashboard

---

## Problem

Operational modes (Factorio Sandbox, Fleet Training, Research Marathon, Normal Fleet) each have scattered start/stop/toggle buttons across different dashboard pages. There's no single place to see what mode the system is in, swap between modes, or manage cross-cutting modifiers (Queue Pause, API Gate, etc.).

## Solution

A **persistent strip** between the header bar and main content inside `.main-wrapper`, visible on every dashboard page. It shows the active mode, lets you switch modes with confirmation, and has a collapsible drawer for modifier toggles.

---

## Layout

### Strip (~40px tall, static flex child in `.main-wrapper`)

The dashboard layout is `aside.sidebar` + `.main-wrapper` (column flexbox containing `.header-bar` then `.main-content`). The strip is injected as a static element between `.header-bar` and `.main-content` — no sticky positioning needed, since `.main-content` scrolls independently below it.

```
+---------------------------------------------------------------------+
|  [Factory Factorio v]     [Queue Paused] [API Gate $2.50/$5] [gear] |
|   ^ mode selector pill      ^ status chips                  ^ gear  |
+---------------------------------------------------------------------+
```

**Left side — Active Mode pill:**
- Displays current mode as a lit pill: icon + label
- Click opens a dropdown listing all available modes
- Each dropdown entry shows: name, one-line description, current state (Running / Stopped / Available)
- Selecting a mode while another is running triggers a confirmation dialog

**Right side — Status chips + gear:**
- Small chips for active modifiers at a glance (only shown when enabled)
- Gear icon toggles the modifier drawer

### Modifier Drawer (~120px, collapsible)

```
+---------------------------------------------------------------------+
|  Queue Pause  [====]   API Gate  [====]  $2.50 / $5.00 budget       |
|  Eco Mode     [    ]   Offline   [    ]  HITL Evolution  [    ]     |
+---------------------------------------------------------------------+
```

- Expands below the strip when gear icon is clicked
- Toggle switches for each modifier
- API Gate shows budget inline when enabled
- Modifier endpoints:
  - Queue Pause: `POST /api/queue/pause`, `POST /api/queue/resume`
  - API Gate: `POST /api/gate/enable`, `POST /api/gate/disable`
  - Eco Mode, Offline Mode, HITL Evolution: `POST /api/config` with key `fleet.eco_mode`, `fleet.offline_mode`, `fleet.hitl_evolution` respectively (generic config endpoint, triggers config reload)

---

## Big Modes

| Mode | Show when | Start action | Stop action | Indicator |
|------|-----------|-------------|-------------|-----------|
| **Normal Fleet** | Always (default) | No-op — standard supervisor | Stop workers via existing fleet API | Agent count + task rate |
| **Factorio Sandbox** | `[factorio] enabled = true` in fleet.toml AND `factorio.bridge` module importable | `POST /api/factorio/start` | `POST /api/factorio/stop` | Tick count + paused state |
| **Fleet Training** | `[training]` section exists in fleet.toml | Acquire exclusive training lock | Release lock, resume GPU tasks | Training progress % |
| **Research Marathon** | `autoresearch/` dir exists AND `experiment` module importable | Spawn autoresearch loop | Signal stop + drain | Experiments completed |

### Mode visibility

Modes are **conditionally displayed** based on installation state. The `/api/mode/status` endpoint returns an `available_modes` array. The frontend only renders modes present in that array.

If a mode's module is not installed or not enabled in config, it never appears in the dropdown. If only "normal" is available, the pill still renders but the dropdown has a single entry.

### Mode switching

Switching from one big mode to another requires **confirmation** when the current mode is actively running. The flow:

1. User clicks a different mode in the dropdown
2. If current mode is running, confirmation dialog: "Factorio is running. Stop it and switch to Research Marathon?" with Cancel / Switch
3. On confirm, `POST /api/mode/switch` with `force: true` and `expected_current: "factorio"` (compare-and-swap)
4. Backend acquires `_mode_switch_lock`, verifies `expected_current` matches actual current mode, tears down current, starts new
5. If start fails after teardown, falls back to "normal" mode, SSE pushes error event
6. Strip updates to show new mode + "Starting..." state, button disabled until SSE confirms
7. SSE pushes final state once the new mode is live

Modes are **not mutually exclusive at the config level** — Normal Fleet is the baseline. Factorio/Training/Research overlay on top. Switching to Normal tears down whatever overlay is active.

### Mode detail schemas

The `detail` field in status responses is mode-polymorphic. Each mode's shape:

| Mode | Detail fields |
|------|--------------|
| normal | `{}` (empty) |
| factorio | `{tick: int, paused: bool, cadence: string}` |
| training | `{progress_pct: float, profile: string, lock_holder: string}` |
| research_marathon | `{experiments_done: int, current_experiment: string, running_since: string}` |

Frontend must handle unknown keys gracefully — render only fields it recognizes.

---

## Backend

### Thread safety

```python
import threading
_mode_switch_lock = threading.Lock()
```

The switch endpoint acquires this lock before checking/modifying mode state. The `force` request includes `expected_current` for compare-and-swap — if the actual current mode doesn't match, the request is rejected with HTTP 409.

### `GET /api/mode/status`

Returns current mode, available modes, and modifier states. Called on page load and piggybacks on the existing SSE broadcaster cycle.

```json
{
  "active": "factorio",
  "state": "running",
  "available_modes": [
    {
      "id": "normal",
      "name": "Normal Fleet",
      "icon": "lightning",
      "description": "Standard worker pool with idle evolution",
      "state": "available"
    },
    {
      "id": "factorio",
      "name": "Factorio Sandbox",
      "icon": "factory",
      "description": "Train AI agents in Factorio",
      "state": "running"
    },
    {
      "id": "training",
      "name": "Fleet Training",
      "icon": "brain",
      "description": "Exclusive skill optimization with GPU lock",
      "state": "available"
    }
  ],
  "detail": {
    "tick": 42000,
    "paused": false,
    "cadence": "adaptive"
  },
  "modifiers": {
    "queue_paused": false,
    "api_gate": {"enabled": true, "budget": 5.0, "spent": 2.50},
    "eco_mode": false,
    "offline_mode": false,
    "hitl_evolution": false
  }
}
```

### `POST /api/mode/switch`

Coordinator endpoint that calls existing start/stop endpoints internally.

```json
// Request (first call — probe for conflict)
{"mode": "research_marathon", "force": false}

// Response — conflict (current mode running)
{
  "conflict": true,
  "current": "factorio",
  "current_name": "Factorio Sandbox",
  "message": "Factorio is running. Stop it and switch to Research Marathon?"
}

// Request with force + compare-and-swap (user confirmed)
{"mode": "research_marathon", "force": true, "expected_current": "factorio"}

// Response — stale (mode changed between confirmation dialog and click)
{"error": "stale", "current": "normal"}  // HTTP 409

// Response — success
{"success": true, "mode": "research_marathon", "state": "starting"}

// Response — partial failure (teardown succeeded but start failed)
{"success": false, "mode": "normal", "error": "Research marathon failed to start: ...",
 "fallback": true}
```

**Implementation:** The switch endpoint is a thin coordinator. It:
1. Acquires `_mode_switch_lock`
2. Checks if a conflicting mode is running — returns `conflict` if `force=false`
3. If `force=true`, verifies `expected_current` matches actual — returns 409 if stale
4. Calls the existing stop endpoint for the current mode (e.g., `api_factorio_stop()`)
5. Calls the existing start endpoint for the new mode (e.g., `api_factorio_start()`)
6. If step 5 fails, mode falls back to "normal", returns `{fallback: true}` with error
7. Returns success with "starting" state

No mode lifecycle logic is duplicated. The switch endpoint delegates to existing handlers.

### Mode detection logic (for `available_modes`)

Uses `importlib.util.find_spec()` for safe probing without import side effects:

```python
import importlib.util

def _detect_available_modes() -> list[dict]:
    modes = [{"id": "normal", "name": "Normal Fleet", "icon": "lightning",
              "description": "Standard worker pool with idle evolution"}]

    cfg = load_config()

    # Factorio — only if enabled + module exists
    if cfg.get("factorio", {}).get("enabled"):
        if importlib.util.find_spec("factorio.bridge") is not None:
            modes.append({"id": "factorio", "name": "Factorio Sandbox", "icon": "factory",
                          "description": "Train AI agents in Factorio"})

    # Training — only if config section exists
    if cfg.get("training"):
        modes.append({"id": "training", "name": "Fleet Training", "icon": "brain",
                      "description": "Exclusive skill optimization with GPU lock"})

    # Research Marathon — only if autoresearch dir exists + experiment module
    if (Path(__file__).parent.parent / "autoresearch").is_dir():
        if importlib.util.find_spec("experiment") is not None:
            modes.append({"id": "research_marathon", "name": "Research Marathon", "icon": "microscope",
                          "description": "Autonomous experiment loop"})

    return modes
```

### Active mode detection

`_get_active_mode()` probes each mode's running state:

```python
def _get_active_mode() -> str:
    # Factorio — probe bridge HTTP
    try:
        cfg = load_config()
        port = cfg.get("factorio", {}).get("bridge_port", 27016)
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1)
        data = json.loads(resp.read())
        if data.get("running"):
            return "factorio"
    except Exception:
        pass

    # Training — check exclusive lock
    try:
        from db import query
        lock = query("SELECT * FROM training_locks WHERE released_at IS NULL LIMIT 1")
        if lock:
            return "training"
    except Exception:
        pass

    # Research Marathon — check running experiment
    try:
        from experiment import get_running_experiment
        if get_running_experiment():
            return "research_marathon"
    except Exception:
        pass

    return "normal"
```

### SSE integration

The existing `_sse_broadcaster()` already runs on an adaptive timer. Add `mode` to the payload:

```python
payload["mode"] = {
    "active": _get_active_mode(),
    "state": _get_mode_state(),
    "modifiers": _get_modifier_states(),
}
```

This lets the strip update in real-time without polling.

---

## Frontend

### HTML structure

Injected between `.header-bar` and `.main-content` inside `.main-wrapper`:

```html
<div id="mode-control-strip" class="mode-strip">
  <div class="mode-strip-left">
    <button id="mode-selector" class="mode-pill" onclick="toggleModeDropdown()">
      <span id="mode-icon"></span>
      <span id="mode-label">Normal Fleet</span>
      <span class="mode-chevron">v</span>
    </button>
    <div id="mode-dropdown" class="mode-dropdown" style="display:none;">
      <!-- populated by JS from available_modes -->
    </div>
  </div>
  <div class="mode-strip-right">
    <div id="mode-chips"></div>
    <button class="mode-gear-btn" onclick="toggleModifierDrawer()">gear</button>
  </div>
</div>
<div id="modifier-drawer" class="modifier-drawer" style="display:none;">
  <!-- toggle switches populated by JS -->
</div>
```

### CSS

```css
.mode-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 40px;
  padding: 0 16px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.mode-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: none;
}

.mode-pill:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.mode-dropdown {
  position: absolute;
  top: 36px;
  left: 16px;
  min-width: 280px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  z-index: 91;
}

.mode-dropdown-item {
  padding: 10px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
}

.mode-dropdown-item:last-child {
  border-bottom: none;
}

.mode-dropdown-item:hover {
  background: rgba(255,255,255,0.05);
}

.mode-dropdown-item.active {
  background: rgba(16,185,129,0.1);
  border-left: 3px solid var(--primary);
}

.mode-dropdown-item .mode-item-name {
  font-weight: 600;
  font-size: 13px;
}

.mode-dropdown-item .mode-item-desc {
  font-size: 11px;
  color: var(--muted-foreground);
}

.mode-dropdown-item .mode-item-state {
  font-size: 10px;
  text-transform: uppercase;
  font-weight: 700;
}

.modifier-drawer {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
  padding: 12px 16px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
```

### JavaScript

Key functions:

- `loadModeStatus()` — fetch `/api/mode/status`, populate pill + dropdown + chips
- `toggleModeDropdown()` — show/hide dropdown (close on outside click)
- `selectMode(modeId)` — `POST /api/mode/switch {force: false}`; if conflict, show confirmation dialog; on confirm, re-POST with `force: true, expected_current: current`; disable button until SSE confirms or 10s timeout
- `toggleModifierDrawer()` — show/hide drawer
- `toggleModifier(name)` — call existing endpoint for that modifier
- SSE handler updates mode pill color + label on each push

### Confirmation dialog

Built programmatically using `document.createElement` to match existing dashboard modal patterns (walkthrough overlay style). Uses `textContent` for all user-visible text — no raw HTML insertion.

```javascript
function _showModeConfirm(message, onConfirm) {
  var overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:200;display:flex;align-items:center;justify-content:center;';
  var card = document.createElement('div');
  card.style.cssText = 'background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:400px;';
  var msg = document.createElement('p');
  msg.style.marginBottom = '16px';
  msg.textContent = message;
  card.appendChild(msg);
  var actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
  var cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn btn-sm btn-outline';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = function() { document.body.removeChild(overlay); };
  var switchBtn = document.createElement('button');
  switchBtn.className = 'btn btn-sm';
  switchBtn.style.cssText = 'background:var(--destructive);color:#fff;';
  switchBtn.textContent = 'Switch';
  switchBtn.onclick = function() { document.body.removeChild(overlay); onConfirm(); };
  actions.appendChild(cancelBtn);
  actions.appendChild(switchBtn);
  card.appendChild(actions);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
}
```

---

## Error handling

| Scenario | Behavior |
|----------|----------|
| Mode start fails after teardown | Falls back to "normal", strip shows error toast, SSE pushes `{fallback: true}` |
| Bridge unreachable during switch | Switch endpoint returns 502, strip stays on current mode |
| Stale compare-and-swap | Returns 409, frontend re-fetches mode status and shows updated state |
| Double-click on switch | Button disabled after first click, re-enabled on SSE confirm or timeout (10s) |
| Modifier toggle fails | Chip reverts to previous state, console.error logged |

---

## Interaction with existing controls

The mode-specific pages (Factorio tab, etc.) keep their existing controls for detailed operations (pause/resume, cadence, directives, etc.). The strip is for **high-level mode switching only** — it doesn't replace the detailed control surfaces.

When a mode is started from the strip, the corresponding tab's UI reflects the running state as it does today. The Factorio tab's Start/Stop button should sync with mode strip state (both read the same bridge probe).

---

## Files to create/modify

| File | Change |
|------|--------|
| `fleet/dashboard.py` | Add `/api/mode/status`, `/api/mode/switch`, `_detect_available_modes()`, `_get_active_mode()`, `_get_mode_state()`, `_get_modifier_states()`, `_mode_switch_lock` |
| `fleet/templates/dashboard.html` | Add strip HTML between header-bar and main-content, CSS, JS functions (loadModeStatus, selectMode, toggleModeDropdown, toggleModifierDrawer, _showModeConfirm), SSE handler update |
| `fleet/fleet.toml` | No changes (reads existing config sections) |

---

## Out of scope

- Mode-specific sub-controls (Factorio cadence, training profiles) — stay on their respective tabs
- Creating new modes — this spec covers the framework; adding a new mode is just appending to `_detect_available_modes()` and adding start/stop handlers
- Mobile/responsive layout — follow existing dashboard responsive patterns
