# PyWebView Migration — Design Spec
**Date:** 2026-03-31
**Status:** Spec only — no implementation
**Supersedes:** `BigEd/launcher/launcher_tkinter.py` UI layer

---

## Problem

The current launcher (`BigEd/launcher/launcher.py` + `launcher_tkinter.py`) uses tkinter:
- Platform-specific widget look that doesn't match the dark dashboard aesthetic
- Duplicate UI logic: dashboard lives in Flask/HTML; launcher duplicates panels in tkinter
- Limited rich UI: no CSS animations, no canvas, no flexbox
- Hard to maintain two separate UI codebases (tkinter + HTML)

---

## PyWebView vs tkinter

| Dimension | tkinter (current) | PyWebView (target) |
|-----------|------------------|--------------------|
| Styling | OS-native widgets | Full CSS/HTML — matches dashboard theme |
| Animation | None | CSS transitions, canvas, requestAnimationFrame |
| Dev ergonomics | Python-only | HTML/CSS/JS — same skills as dashboard |
| Cross-platform | Good | Good (WebKit on Mac/Linux, EdgeWebView2 on Windows) |
| Package size | stdlib | ~5MB (pywebview) + EdgeWebView2 (pre-installed Win11) |
| Two-way comms | N/A | `window.pywebview.api` JS bridge |
| Testing | tkinter test utils | Browser DevTools + playwright |
| Build | No extra step | No extra step (pywebview bundles renderer) |

**Verdict:** PyWebView unifies the UI — one HTML/CSS codebase for both dashboard and launcher.

---

## Architecture

```
┌─────────────────────────────────────────┐
│  PyWebView Window                        │
│  ┌───────────────────────────────────┐  │
│  │  HTML/JS (launcher UI)             │  │
│  │  - Sidebar nav (same as dashboard) │  │
│  │  - Settings panels                 │  │
│  │  - Fleet status panels             │  │
│  │  - Walkthrough wizard              │  │
│  └─────────────┬─────────────────────┘  │
│                │ window.pywebview.api    │
└────────────────┼────────────────────────┘
                 │ Python bridge (JS ↔ Python)
┌────────────────▼────────────────────────┐
│  LauncherAPI (Python class)              │
│  - start_fleet()                         │
│  - stop_fleet()                          │
│  - get_status() → JSON                  │
│  - open_settings() / save_settings()    │
│  - open_logs()                           │
└────────────────┬────────────────────────┘
                 │ calls
┌────────────────▼────────────────────────┐
│  Existing fleet/ modules                 │
│  - supervisor.py (process control)       │
│  - db.py (status queries)               │
│  - config.py (settings R/W)             │
│  - lead_client.py (task dispatch)        │
└─────────────────────────────────────────┘
```

The Flask dashboard continues to run independently at `localhost:5555`.
The launcher embeds a PyWebView window pointing at the **same Flask app**,
with an additional `/launcher` route serving launcher-specific panels.

---

## Python Bridge API

```python
# BigEd/launcher/launcher_api.py

class LauncherAPI:
    """Exposed to JS as window.pywebview.api"""

    def start_fleet(self) -> dict:
        """Launch supervisor.py subprocess. Returns {ok, pid}."""

    def stop_fleet(self) -> dict:
        """Send SIGTERM to supervisor. Returns {ok}."""

    def get_status(self) -> dict:
        """Read fleet status from db.py. Returns agent/task counts."""

    def get_settings(self) -> dict:
        """Load fleet.toml. Returns parsed config dict."""

    def save_settings(self, section: str, key: str, value) -> dict:
        """Write one config value to fleet.toml. Returns {ok, error}."""

    def open_path(self, path: str) -> None:
        """Open a folder in the OS file manager (cross-platform)."""

    def get_logs(self, n_lines: int = 200) -> str:
        """Tail supervisor.log. Returns last n lines."""
```

JS usage:
```javascript
// In launcher HTML
const status = await window.pywebview.api.get_status();
document.getElementById('agent-count').textContent = status.agents_running;
```

---

## Migration Phases

### Phase 1 — PyWebView wrapper around existing dashboard (minimal change)

**Goal:** Replace the tkinter window with a PyWebView window loading `http://localhost:5555`.
The dashboard becomes the launcher. Tkinter is gone.

```python
# BigEd/launcher/launcher.py (Phase 1)
import webview
import threading
from fleet_bridge import start_fleet_if_needed

def main():
    start_fleet_if_needed()  # existing logic
    api = LauncherAPI()
    window = webview.create_window(
        "BigEd CC",
        "http://localhost:5555",
        js_api=api,
        width=1280, height=800,
        min_size=(900, 600),
    )
    webview.start(debug=False)

if __name__ == "__main__":
    main()
```

**Deliverables:**
- `BigEd/launcher/launcher_api.py` — LauncherAPI class
- Modified `BigEd/launcher/launcher.py` — PyWebView entry point
- `BigEd/launcher/launcher_tkinter.py` kept but not imported (deprecation period)

**Risk:** EdgeWebView2 must be installed on Windows (pre-installed on Win 10 1903+, Win 11).
Fallback: detect absence and show tkinter with a "WebView not available" message.

### Phase 2 — Port launcher panels to dedicated `/launcher` route

**Goal:** Add a `/launcher` Flask route with richer launcher UI (walkthrough, settings,
fleet start/stop) that is optimized for the desktop window context (no browser chrome).

```python
# fleet/launcher_blueprint.py
from flask import Blueprint, render_template, jsonify
launcher_bp = Blueprint("launcher", __name__)

@launcher_bp.route("/launcher")
def launcher_home():
    return render_template("launcher.html")

@launcher_bp.route("/launcher/status")
def launcher_status():
    # Aggregated status for launcher panels
    ...
```

**Deliverables:**
- `fleet/launcher_blueprint.py` — Flask blueprint
- `fleet/templates/launcher.html` — desktop-optimized launcher UI
- Registered in `dashboard.py` alongside existing blueprints

### Phase 3 — Remove tkinter

**Goal:** Delete `launcher_tkinter.py` and all tkinter imports.
All UI lives in HTML/CSS/JS.

**Deliverables:**
- Delete `BigEd/launcher/launcher_tkinter.py`
- Delete tkinter references in `BigEd/launcher/ui/`
- Update `SETUP.md` and `CLAUDE.md` to remove tkinter mentions
- Add `pywebview>=5.0` to `requirements.txt`

---

## Dependency Requirements

```
# requirements.txt additions
pywebview>=5.0          # MIT license, cross-platform
```

**Platform notes:**
- **Windows 11:** EdgeWebView2 pre-installed. No extra install.
- **Windows 10 < 1903:** Needs EdgeWebView2 runtime installer (~100MB).
- **macOS:** Uses WKWebView (built-in). No extra install.
- **Linux:** Needs `python3-gi`, `gir1.2-webkit2-4.0` (apt). Ship in `setup.sh`.

---

## Packaging Considerations

### PyInstaller / cx_Freeze
```python
# build.py additions
# pywebview requires WebView2Loader.dll on Windows
hiddenimports = ["webview", "webview.platforms.winforms"]
datas = [("webview/lib/x64/WebView2Loader.dll", "webview/lib/x64/")]
```

### Nuitka
PyWebView works with Nuitka; add `--include-package=webview`.

### Installer (setup.ps1)
```powershell
# Check EdgeWebView2
$edgeInstalled = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
if (-not $edgeInstalled) {
    Write-Host "Installing EdgeWebView2 runtime..."
    Invoke-WebRequest -Uri $WEBVIEW2_URL -OutFile "$env:TEMP\MicrosoftEdgeWebview2Setup.exe"
    Start-Process -Wait "$env:TEMP\MicrosoftEdgeWebview2Setup.exe" /silent
}
```

---

## Benefits Summary

1. **Single UI codebase** — dashboard HTML/CSS patterns work in launcher
2. **Richer UX** — CSS animations, canvas visualizations, responsive layout
3. **Easier theming** — design token sync via `token_bridge.py` works in both
4. **Smaller maintenance surface** — eliminate ~2,000 lines of tkinter code
5. **Dev parity** — Chrome DevTools available for launcher debugging

---

## Not in Scope

- Electron (too heavy, requires Node.js)
- Tauri (requires Rust — tracked in Phase 4 Rust rewrite spec)
- CEF (cefpython3) — unmaintained, heavy
- System tray / menu bar integration (tracked separately)
