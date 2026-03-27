"""
BigEd CC -- PyWebView launcher.
Native window wrapping the Flask dashboard at localhost:5555.
Single-instance lock, pythonw relaunch, pystray system tray.
"""
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
FLEET_DIR = Path(__file__).resolve().parent.parent.parent / "fleet"
ICON_PATH = Path(__file__).resolve().parent / "icon_1024.png"

_lock_sock: socket.socket | None = None
_supervisor_proc: subprocess.Popen | None = None
_window: webview.Window | None = None
_tray_icon = None


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

def _start_supervisor():
    global _supervisor_proc
    supervisor_py = str(FLEET_DIR / "supervisor.py")
    _supervisor_proc = subprocess.Popen(
        [sys.executable, supervisor_py],
        cwd=str(FLEET_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.info("Supervisor started (PID %s)", _supervisor_proc.pid)


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

    menu = pystray.Menu(
        pystray.MenuItem("Open BigEd", _show_window, default=True),
        pystray.MenuItem("Open in Browser", _open_browser),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit_app),
    )
    _tray_icon = pystray.Icon("BigEd", icon_img, "BigEd CC", menu)
    threading.Thread(target=_tray_icon.run, daemon=True).start()


# -- Window close → minimize ---------------------------------------------------

def _on_closing():
    """Called when user clicks X -- close the app normally."""
    _shutdown()
    return True  # allow close


# -- Shutdown ------------------------------------------------------------------

def _shutdown():
    global _tray_icon
    _stop_supervisor()
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
    os._exit(0)


# -- Entry point ---------------------------------------------------------------

def main():
    global _window

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if not _acquire_instance_lock():
        log.info("Another instance is already running (port %s held)", LOCK_PORT)
        try:
            webview.create_window("BigEd", html="<h2>BigEd is already running.</h2><p>Check your system tray.</p>")
            webview.start()
        except Exception:
            pass
        return

    _relaunch_windowless()
    _start_supervisor()

    if not _wait_for_dashboard():
        log.error("Dashboard did not start within 30 seconds")
        webview.create_window(
            "BigEd -- Error",
            html="<h2>Fleet failed to start</h2><p>Dashboard did not respond at localhost:5555 within 30 seconds.</p>",
        )
        webview.start()
        _release_instance_lock()
        return

    _window = webview.create_window(
        "BigEd CC",
        url=DASHBOARD_URL,
        width=1280,
        height=860,
        min_size=(800, 600),
        js_api=BridgeAPI(),
        confirm_close=True,
    )
    _window.events.closing += _on_closing

    _setup_tray()

    log.info("Starting PyWebView window")
    webview.start()

    # If webview.start() returns (all windows destroyed), shut down
    _shutdown()


if __name__ == "__main__":
    main()
