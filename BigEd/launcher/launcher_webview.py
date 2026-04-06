"""
BigEd CC -- PyWebView launcher.
Native window wrapping the Flask dashboard at localhost:5555.
Single-instance lock, pythonw relaunch, pystray system tray.
"""
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Use Qt backend — pythonnet doesn't support Python 3.14
if sys.platform == "win32":
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")
import webview

log = logging.getLogger("biged.launcher")

LOCK_PORT = 19876
DASHBOARD_URL = "http://localhost:5555"
HEALTH_URL = f"{DASHBOARD_URL}/api/health"

def _build_splash_html() -> str:
    """Build splash HTML with embedded icon (base64 if icon file exists)."""
    import base64
    icon_path = Path(__file__).resolve().parent / "icon_1024.png"
    if icon_path.exists():
        b64 = base64.b64encode(icon_path.read_bytes()).decode()
        logo = f'<img src="data:image/png;base64,{b64}" style="width:64px;height:64px;border-radius:14px;">'
    else:
        logo = '<div style="width:64px;height:64px;background:#3b82f6;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:700;color:#fff;">B</div>'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ margin:0; background:#0a0e1a; display:flex; align-items:center; justify-content:center; height:100vh; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
  .splash {{ text-align:center; }}
  .logo {{ margin:0 auto 24px; }}
  h1 {{ color:#e2e8f0; font-size:20px; margin:0 0 8px; }}
  p {{ color:#94a3b8; font-size:14px; margin:0 0 24px; }}
  .spinner {{ width:32px; height:32px; border:3px solid #1e293b; border-top-color:#3b82f6; border-radius:50%; animation:spin 0.8s linear infinite; margin:0 auto; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
</style>
</head><body>
<div class="splash">
  <div class="logo">{logo}</div>
  <h1>BigEd CC</h1>
  <p>Starting fleet...</p>
  <div class="spinner"></div>
</div>
</body></html>"""

SPLASH_HTML = _build_splash_html()

# When frozen (PyInstaller --onefile), __file__ is inside a temp extraction dir.
# Use the .exe's location as anchor instead.
# Layout: BigEd/launcher/dist/BigEdCC.exe → project root is 3 levels up
# Layout: BigEd/launcher/launcher_webview.py → project root is 3 levels up
if getattr(sys, "frozen", False):
    _EXE_DIR = Path(sys.executable).resolve().parent        # BigEd/launcher/dist/
    _LAUNCHER_DIR = _EXE_DIR.parent                         # BigEd/launcher/
    _PROJECT_ROOT = _LAUNCHER_DIR.parent.parent             # project root
else:
    _LAUNCHER_DIR = Path(__file__).resolve().parent         # BigEd/launcher/
    _PROJECT_ROOT = _LAUNCHER_DIR.parent.parent             # project root

FLEET_DIR = _PROJECT_ROOT / "fleet"
DATA_DIR = _LAUNCHER_DIR / "data"
ICON_PATH = _LAUNCHER_DIR / "icon_1024.png"

_lock_sock: socket.socket | None = None
_supervisor_proc: subprocess.Popen | None = None
_window: webview.Window | None = None
_tray_icon = None


# -- Secrets loading -----------------------------------------------------------

def _load_secrets_to_env():
    """Load all key=value pairs from ~/.secrets into os.environ.

    Called once at launcher startup so fleet workers and chat functions
    can access API keys via os.environ without knowing about ~/.secrets.
    """
    for secrets_path in (Path.home() / ".secrets", DATA_DIR / ".secrets"):
        if not secrets_path.exists():
            continue
        try:
            for line in secrets_path.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and v != "REPLACE_ME":
                    os.environ.setdefault(k, v)
        except Exception:
            pass


# -- Single-instance lock -----------------------------------------------------

def _acquire_instance_lock() -> bool:
    global _lock_sock
    try:
        _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_sock.bind(("127.0.0.1", LOCK_PORT))
        _lock_sock.listen(1)
        return True
    except OSError:
        return False


def _release_instance_lock():
    global _lock_sock
    if _lock_sock:
        try:
            _lock_sock.close()
        except Exception:
            pass
        _lock_sock = None


# -- Console-less relaunch (Windows) ------------------------------------------

def _relaunch_windowless():
    """Re-exec under pythonw.exe to hide the console window."""
    if sys.platform != "win32":
        return
    if os.environ.get("_BIGED_WINDOWLESS"):
        return  # already relaunched — prevent infinite loop
    if sys.executable.lower().endswith("pythonw.exe"):
        return
    # Only relaunch if running a .py script (not -c or interactive)
    if not sys.argv or not sys.argv[0].endswith(".py"):
        return
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.isfile(pythonw):
        return
    env = os.environ.copy()
    env["_BIGED_WINDOWLESS"] = "1"
    subprocess.Popen(
        [pythonw] + sys.argv,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    sys.exit(0)


# -- Supervisor ----------------------------------------------------------------

def _get_python() -> str:
    """Return a real Python interpreter — sys.executable points to the .exe when frozen."""
    if getattr(sys, "frozen", False):
        import shutil
        return shutil.which("python") or shutil.which("python3") or "python"
    return sys.executable


def _start_supervisor():
    global _supervisor_proc, DASHBOARD_URL, HEALTH_URL
    # Phase G: skip supervisor spawn when connected to Rust service tier
    service_url = os.environ.get("BIGED_SERVICE_URL")
    if service_url:
        DASHBOARD_URL = service_url
        HEALTH_URL = f"{service_url}/api/health"
        log.info("Connected to Rust service at %s — skipping Python supervisor", service_url)
        return
    supervisor_py = str(FLEET_DIR / "supervisor.py")
    python = _get_python()
    _supervisor_proc = subprocess.Popen(
        [python, supervisor_py],
        cwd=str(FLEET_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.info("Supervisor started (PID %s) via %s", _supervisor_proc.pid, python)


def _stop_supervisor():
    global _supervisor_proc
    if _supervisor_proc and _supervisor_proc.poll() is None:
        _supervisor_proc.terminate()
        try:
            _supervisor_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _supervisor_proc.kill()
        log.info("Supervisor stopped")
    _supervisor_proc = None


# -- Wait for dashboard -------------------------------------------------------

def _wait_for_dashboard(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(HEALTH_URL, timeout=5)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# -- Bridge API (JS ↔ Python) -------------------------------------------------

class BridgeAPI:
    """Exposed to JavaScript as window.pywebview.api"""

    def minimize_to_tray(self):
        if _window:
            _window.hide()
            # Create tray icon on first minimize (not on startup)
            if not _tray_icon:
                _setup_tray()

    def restore(self):
        if _window:
            _window.show()
            _window.restore()

    def status(self):
        try:
            resp = urllib.request.urlopen(HEALTH_URL, timeout=5)
            import json
            return json.loads(resp.read())
        except Exception:
            return {"healthy": False}

    def open_file_dialog(self):
        """Open native file dialog starting in user's home directory."""
        if not _window:
            return []
        result = _window.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=str(Path.home()),
            allow_multiple=True,
        )
        return list(result) if result else []

    def quit(self):
        _shutdown()


# -- System tray ---------------------------------------------------------------

def _setup_tray():
    global _tray_icon
    try:
        import pystray
        from PIL import Image as PILImage
    except ImportError:
        log.warning("pystray/Pillow not available -- no tray icon")
        return

    icon_img = PILImage.open(str(ICON_PATH)) if ICON_PATH.exists() else PILImage.new("RGB", (64, 64), "#8B4513")

    def _show_window(icon, item):
        if _window:
            _window.show()
            _window.restore()

    def _open_browser(icon, item):
        import webbrowser
        webbrowser.open(DASHBOARD_URL)

    def _quit_app(icon, item):
        _shutdown()

    def _fleet_status_text():
        """Dynamic tooltip with fleet status."""
        try:
            resp = urllib.request.urlopen(HEALTH_URL, timeout=2)
            data = json.loads(resp.read())
            status = "Online" if data.get("status") == "healthy" else "Degraded"
            return f"BigEd CC — {status}"
        except Exception:
            return "BigEd CC — Offline"

    menu = pystray.Menu(
        pystray.MenuItem("Open BigEd", _show_window, default=True),
        pystray.MenuItem("Open in Browser", _open_browser),
        pystray.MenuItem("Fleet Status", lambda icon, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit_app),
    )
    _tray_icon = pystray.Icon("BigEd", icon_img, _fleet_status_text(), menu)
    threading.Thread(target=_tray_icon.run, daemon=True).start()

    # Periodic tooltip update (every 30s)
    def _update_tray_tooltip():
        while _tray_icon and _tray_icon.visible:
            try:
                _tray_icon.title = _fleet_status_text()
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=_update_tray_tooltip, daemon=True).start()


# -- Close behavior preferences ------------------------------------------------

def _get_close_behavior() -> str:
    """Get configured close behavior: 'tray', 'quit', or 'ask'.

    Checks launcher settings.json first, then fleet.toml [launcher] section.
    Default is 'ask'.
    """
    # Check launcher settings.json
    settings_file = DATA_DIR / "settings.json"
    if settings_file.exists():
        try:
            behavior = json.loads(settings_file.read_text(encoding="utf-8")).get("close_behavior", "")
            if behavior in ("tray", "quit", "ask"):
                return behavior
        except Exception:
            pass
    # Fall back to fleet.toml [launcher] section
    toml_path = FLEET_DIR / "fleet.toml"
    if toml_path.exists():
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                return "ask"
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            behavior = data.get("launcher", {}).get("close_behavior", "ask")
            if behavior in ("tray", "quit", "ask"):
                return behavior
        except Exception:
            pass
    return "ask"


# -- Window close handler -----------------------------------------------------

def _on_closing():
    """Called when user clicks X — respects close_behavior preference."""
    close_behavior = _get_close_behavior()

    if close_behavior == "quit":
        _shutdown()
        return True

    if close_behavior == "tray" and _tray_icon is not None:
        # Minimize to tray instead of closing
        if _window:
            _window.hide()
        return False

    # "ask" or no preference — show confirmation dialog
    if _window:
        try:
            result = _window.create_confirmation_dialog(
                "Close BigEd CC",
                "Stop the fleet and exit?"
            )
            if result:
                _shutdown()
                return True
            return False
        except Exception as e:
            log.warning("Close dialog error: %s", e)
            _shutdown()
            return True
    _shutdown()
    return True


# -- Shutdown ------------------------------------------------------------------

def _kill_fleet_processes():
    """Terminate fleet-related processes (workers, supervisors) via psutil."""
    try:
        import psutil
    except ImportError:
        log.warning("psutil not available — skipping process cleanup")
        return

    fleet_scripts = {"worker.py", "supervisor.py", "hw_supervisor.py", "web_app.py"}
    fleet_dir_str = str(FLEET_DIR).lower()
    killed = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(cmdline).lower()
            # Match fleet processes by script name + fleet directory
            if not any(s in cmd_str for s in fleet_scripts):
                continue
            if fleet_dir_str not in cmd_str:
                continue
            # Don't kill ourselves
            if proc.pid == os.getpid():
                continue
            proc.terminate()
            killed.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Wait up to 5s for graceful termination, then force-kill
    if killed:
        gone, alive = psutil.wait_procs(killed, timeout=5)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        log.info("Killed %d fleet processes (%d forced)", len(killed), len(alive))


def _sweep_zombies():
    """Kill any remaining orphan fleet-related processes."""
    try:
        import psutil
    except ImportError:
        return

    fleet_dir_str = str(FLEET_DIR).lower()
    swept = 0
    for proc in psutil.process_iter(["pid", "cmdline", "status"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(cmdline).lower()
            if fleet_dir_str not in cmd_str:
                continue
            if proc.pid == os.getpid():
                continue
            # Kill any remaining fleet process (zombie or alive)
            proc.kill()
            swept += 1
            log.info("Swept orphan PID=%d cmd=%s", proc.pid, " ".join(cmdline[:3]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if swept:
        log.info("Swept %d orphan processes", swept)


def _close_db():
    """Checkpoint WAL and close DB connections."""
    try:
        fleet_dir = str(FLEET_DIR)
        if fleet_dir not in sys.path:
            sys.path.insert(0, fleet_dir)
        import db
        db.shutdown()
    except Exception as e:
        log.warning("DB shutdown error: %s", e)


def _shutdown():
    """Graceful 5-step shutdown."""
    global _tray_icon

    log.info("Shutdown: step 1/5 — saving state")
    _stop_supervisor()

    log.info("Shutdown: step 2/5 — unloading Ollama models")
    try:
        # Get list of loaded models
        resp = urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5)
        loaded = json.loads(resp.read()).get("models", [])
        for m in loaded:
            model_name = m.get("name", "")
            if not model_name:
                continue
            try:
                req = urllib.request.Request(
                    "http://localhost:11434/api/generate",
                    data=json.dumps({"model": model_name, "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
                log.info("Unloaded model: %s", model_name)
            except Exception:
                pass
    except Exception:
        pass  # Ollama not running or not responding

    log.info("Shutdown: step 3/5 — killing fleet processes")
    _kill_fleet_processes()

    log.info("Shutdown: step 4/5 — cleaning zombies")
    _sweep_zombies()

    log.info("Shutdown: step 5/5 — closing DB")
    _close_db()

    # Tray cleanup
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None

    _release_instance_lock()
    for w in webview.windows:
        try:
            w.destroy()
        except Exception:
            pass
    sys.exit(0)


# -- Entry point ---------------------------------------------------------------

def main():
    global _window

    # Hide console window immediately (before anything else renders)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0  # SW_HIDE
            )
        except Exception:
            pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    _load_secrets_to_env()

    # Sweep orphan fleet processes from prior sessions before anything else
    _sweep_zombies()

    if not _acquire_instance_lock():
        log.info("Another instance is already running (port %s held)", LOCK_PORT)
        try:
            _ALREADY_RUNNING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { margin:0; background:#0a0e1a; display:flex; align-items:center; justify-content:center; height:100vh; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
  .card { text-align:center; max-width:400px; padding:40px; }
  .logo { width:64px; height:64px; background:#3b82f6; border-radius:14px; display:flex; align-items:center; justify-content:center; margin:0 auto 24px; font-size:28px; font-weight:700; color:#fff; }
  h1 { color:#e2e8f0; font-size:20px; margin:0 0 12px; }
  p { color:#94a3b8; font-size:14px; margin:0; line-height:1.6; }
</style>
</head><body>
<div class="card">
  <div class="logo">B</div>
  <h1>BigEd is Already Running</h1>
  <p>Check your system tray for the BigEd icon.<br>Click it to restore the window.</p>
</div>
</body></html>"""
            webview.create_window("BigEd", html=_ALREADY_RUNNING_HTML)
            webview.start()
        except Exception:
            pass
        return

    _relaunch_windowless()

    # Queue starts active by default — user can pause via dashboard toggle.
    # Remove stale pause flag from previous sessions.
    pause_file = FLEET_DIR / ".queue_paused"
    if pause_file.exists():
        try:
            pause_file.unlink()
        except Exception:
            pass

    # Show splash window immediately — no waiting
    _window = webview.create_window(
        "BigEd CC",
        html=SPLASH_HTML,
        width=1280,
        height=860,
        min_size=(800, 600),
        js_api=BridgeAPI(),
    )
    _window.events.closing += _on_closing

    # Set window icon via Qt backend (pywebview doesn't expose icon param)
    def _set_icon():
        try:
            ico_path = Path(__file__).resolve().parent / "brick.ico"
            if not ico_path.exists():
                return
            from qtpy.QtGui import QIcon
            from qtpy.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.setWindowIcon(QIcon(str(ico_path)))
        except Exception as e:
            log.debug("Could not set window icon: %s", e)

    def _after_start():
        """Runs after webview window is shown — start fleet and navigate."""
        _set_icon()
        _start_supervisor()
        if _wait_for_dashboard():
            log.info("Dashboard ready — navigating from splash")
            time.sleep(0.3)  # brief pause so splash is visible
            if _window:
                _window.load_url(DASHBOARD_URL)
        else:
            log.error("Dashboard did not start within 30 seconds")
            if _window:
                _FAILED_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { margin:0; background:#0a0e1a; display:flex; align-items:center; justify-content:center; height:100vh; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
  .card { text-align:center; max-width:450px; padding:40px; }
  .logo { width:64px; height:64px; background:#ef4444; border-radius:14px; display:flex; align-items:center; justify-content:center; margin:0 auto 24px; font-size:28px; font-weight:700; color:#fff; }
  h1 { color:#e2e8f0; font-size:20px; margin:0 0 12px; }
  p { color:#94a3b8; font-size:14px; margin:0 0 24px; line-height:1.6; }
  .hint { color:#64748b; font-size:12px; margin-top:16px; }
</style>
</head><body>
<div class="card">
  <div class="logo">!</div>
  <h1>Fleet Failed to Start</h1>
  <p>The dashboard did not respond within 30 seconds.<br>This usually means Ollama is not installed or the supervisor crashed.</p>
  <p class="hint">Check fleet/logs/supervisor.log for details.<br>Try: python fleet/supervisor.py</p>
</div>
</body></html>"""
                _window.load_html(_FAILED_HTML)

    # Tray icon created on-demand when user minimizes to tray (not on startup)

    log.info("Starting PyWebView window (splash visible)")
    webview.start(func=_after_start)

    # If webview.start() returns (all windows destroyed), shut down
    _shutdown()


if __name__ == "__main__":
    main()
