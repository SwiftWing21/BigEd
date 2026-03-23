"""
System tray integration via pystray — BigEd runs in background when window closed.

Provides TrayManagerMixin that is mixed into BigEdCC:
- _init_tray / _create_tray_icon        (setup)
- _minimize_to_tray / _on_tray_open_gui (hide/restore)
- _on_tray_open_dashboard               (open browser)
- _on_tray_quit                         (full shutdown)
- _update_tray_tooltip                  (live status)
- _notify_hitl                          (tray notification)
- _poll_hitl                            (periodic HITL check)
"""

import json
import logging
import sys
import threading
import webbrowser
from pathlib import Path

_log = logging.getLogger("tray")

# pystray is optional — graceful fallback if not installed
_PYSTRAY_OK = False
try:
    import pystray
    from pystray import MenuItem, Menu
    _PYSTRAY_OK = True
except ImportError:
    _log.info("pystray not installed — system tray disabled")

# PIL for icon loading (required by pystray)
_PIL_OK = False
try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _log.info("Pillow not installed — system tray disabled")


def _tray_available() -> bool:
    """Check if system tray dependencies are available."""
    return _PYSTRAY_OK and _PIL_OK


class TrayManagerMixin:
    """Mixin providing system tray integration for BigEdCC."""

    def _init_tray(self):
        """Initialize system tray icon. Call during __init__ after UI is built."""
        self._tray_icon = None
        self._tray_thread = None
        self._tray_running = False
        self._hitl_poll_active = False
        self._last_hitl_ids = set()  # track seen HITL tasks to avoid duplicate notifications
        self._tray_tooltip = "BigEd CC"

        if not _tray_available():
            _log.warning("System tray unavailable — pystray or Pillow missing")
            return

        self._create_tray_icon()

    def _create_tray_icon(self):
        """Create pystray Icon with menu. Does not start it yet."""
        if not _tray_available():
            return

        # Load icon image
        icon_image = self._load_tray_icon_image()
        if icon_image is None:
            _log.warning("Could not load tray icon — tray disabled")
            return

        # Build menu
        menu = Menu(
            MenuItem("Open BigEd", self._on_tray_open_gui, default=True),
            MenuItem("Open Dashboard", self._on_tray_open_dashboard),
            Menu.SEPARATOR,
            MenuItem(
                "Fleet Status",
                Menu(
                    MenuItem(lambda text: self._tray_fleet_status_text(), None, enabled=False),
                    MenuItem(lambda text: self._tray_agents_text(), None, enabled=False),
                    MenuItem(lambda text: self._tray_pending_text(), None, enabled=False),
                ),
            ),
            Menu.SEPARATOR,
            MenuItem("Quit", self._on_tray_quit),
        )

        self._tray_icon = pystray.Icon(
            name="BigEdCC",
            icon=icon_image,
            title=self._tray_tooltip,
            menu=menu,
        )

    def _load_tray_icon_image(self):
        """Load the brick.ico as a PIL Image for the tray icon."""
        try:
            from PIL import Image as PILImage
            # Try brick.ico first, then icon_1024.png
            import launcher as _mod
            here = _mod.HERE
            for name in ["brick.ico", "icon_1024.png"]:
                icon_path = here / name
                if icon_path.exists():
                    img = PILImage.open(str(icon_path))
                    # Resize for tray (typically 64x64 or 32x32)
                    img = img.resize((64, 64), PILImage.Resampling.LANCZOS)
                    return img
        except Exception:
            _log.warning("Failed to load tray icon image", exc_info=True)
        return None

    def _start_tray(self):
        """Start the tray icon on a background thread."""
        if self._tray_icon is None or self._tray_running:
            return

        self._tray_running = True

        def _run_tray():
            try:
                self._tray_icon.run()
            except Exception:
                _log.warning("Tray icon run() failed", exc_info=True)
            finally:
                self._tray_running = False

        self._tray_thread = threading.Thread(target=_run_tray, daemon=True, name="tray")
        self._tray_thread.start()

        # Start HITL polling if tray notifications are enabled
        self._start_hitl_poll()

    def _stop_tray(self):
        """Stop the tray icon."""
        self._hitl_poll_active = False
        if self._tray_icon is not None and self._tray_running:
            try:
                self._tray_icon.stop()
            except Exception:
                _log.warning("Failed to stop tray icon", exc_info=True)
            self._tray_running = False

    def _update_tray_tooltip(self):
        """Update tooltip: 'BigEd CC -- N agents, M pending'."""
        if self._tray_icon is None or not self._tray_running:
            return

        try:
            from fleet_api import fleet_health
            status = fleet_health()
            if status:
                agents = status.get("agents", [])
                tasks = status.get("tasks", {})
                n_agents = len(agents)
                busy = sum(1 for a in agents if a.get("status") == "BUSY")
                pending = tasks.get("Pending", 0)
                tip = f"BigEd CC -- {n_agents} agents, {busy} busy, {pending} pending"
            else:
                tip = "BigEd CC -- fleet offline"
        except Exception:
            tip = "BigEd CC"

        self._tray_tooltip = tip
        try:
            self._tray_icon.title = tip
        except Exception:
            pass

    def _tray_fleet_status_text(self) -> str:
        """Dynamic menu label for fleet status."""
        try:
            from fleet_api import fleet_health
            status = fleet_health()
            if status:
                return "Fleet: Running"
            return "Fleet: Stopped"
        except Exception:
            return "Fleet: Unknown"

    def _tray_agents_text(self) -> str:
        """Dynamic menu label for agent count."""
        try:
            from fleet_api import fleet_health
            status = fleet_health()
            if status:
                agents = status.get("agents", [])
                busy = sum(1 for a in agents if a.get("status") == "BUSY")
                idle = sum(1 for a in agents if a.get("status") == "IDLE")
                return f"Agents: {idle} idle, {busy} busy"
            return "Agents: --"
        except Exception:
            return "Agents: --"

    def _tray_pending_text(self) -> str:
        """Dynamic menu label for pending tasks."""
        try:
            from fleet_api import fleet_health
            status = fleet_health()
            if status:
                tasks = status.get("tasks", {})
                pending = tasks.get("Pending", 0)
                running = tasks.get("Running", 0)
                return f"Tasks: {pending} pending, {running} running"
            return "Tasks: --"
        except Exception:
            return "Tasks: --"

    def _on_tray_open_gui(self, icon=None, item=None):
        """Restore window from tray."""
        # Must schedule on the main tkinter thread via after()
        try:
            self.after(0, self._restore_from_tray)
        except Exception:
            _log.warning("Failed to schedule GUI restore from tray", exc_info=True)

    def _restore_from_tray(self):
        """Restore the window on the main thread."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            # Re-enable alive flag so timers resume
            self._alive = True
            self._schedule_refresh()
            self._schedule_hw()
        except Exception:
            _log.warning("Failed to restore window from tray", exc_info=True)

    def _on_tray_open_dashboard(self, icon=None, item=None):
        """Open dashboard in browser."""
        try:
            import launcher as _mod
            import re
            text = _mod.FLEET_TOML.read_text(encoding="utf-8")
            m = re.search(r'^port\s*=\s*(\d+)', text, re.M)
            port = int(m.group(1)) if m else 5555
        except Exception:
            port = 5555
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            _log.warning("Failed to open dashboard in browser", exc_info=True)

    def _on_tray_quit(self, icon=None, item=None):
        """Full quit -- stop fleet, destroy tray, exit."""
        self._hitl_poll_active = False
        # Schedule shutdown on the main tkinter thread
        try:
            self.after(0, self._tray_full_quit)
        except Exception:
            # If main thread is gone, just stop tray and exit
            self._stop_tray()
            import os
            os._exit(0)

    def _tray_full_quit(self):
        """Execute full shutdown from main thread (called via after())."""
        # Restore window briefly for clean shutdown
        try:
            self.deiconify()
        except Exception:
            pass

        self._stop_tray()
        # Use existing stop-and-close logic
        self._do_stop_and_close()

    def _minimize_to_tray(self):
        """Hide window, show tray icon. Fleet keeps running."""
        if self._tray_icon is None:
            # Tray not available — fall back to normal close behavior
            _log.info("Tray unavailable, falling back to _do_just_close")
            self._do_just_close()
            return

        # Save window geometry before hiding
        try:
            import launcher as _mod
            if _mod._load_settings().get("remember_position", True):
                self._geometry_file.parent.mkdir(parents=True, exist_ok=True)
                self._geometry_file.write_text(json.dumps({
                    "w": self.winfo_width(), "h": self.winfo_height(),
                    "x": self.winfo_x(), "y": self.winfo_y(),
                    "maximized": self.state() == "zoomed",
                }))
        except Exception:
            pass

        # Hide the window
        self.withdraw()

        # Start tray icon if not already running
        if not self._tray_running:
            self._start_tray()

        # Update tooltip with current status
        threading.Thread(target=self._update_tray_tooltip, daemon=True).start()

    def _notify_hitl(self, task_id, question):
        """Show tray notification for HITL request."""
        if self._tray_icon is None or not self._tray_running:
            return
        try:
            short_q = question[:80] if question else "Needs your input"
            self._tray_icon.notify(
                f"Task #{task_id}: {short_q}",
                "BigEd CC -- HITL Request",
            )
        except Exception:
            _log.warning("Failed to show HITL tray notification", exc_info=True)

    def _start_hitl_poll(self):
        """Start periodic HITL polling in background thread."""
        import launcher as _mod
        settings = _mod._load_settings()
        if not settings.get("tray_notifications", True):
            return

        self._hitl_poll_active = True

        def _poll_loop():
            while self._hitl_poll_active and self._tray_running:
                try:
                    self._poll_hitl()
                except Exception:
                    _log.warning("HITL poll failed", exc_info=True)
                # Sleep in small increments so we can stop quickly
                for _ in range(30):  # 30s total
                    if not self._hitl_poll_active:
                        return
                    import time
                    time.sleep(1)

        t = threading.Thread(target=_poll_loop, daemon=True, name="hitl-poll")
        t.start()

    def _poll_hitl(self):
        """Check for new HITL tasks and send notifications."""
        try:
            from fleet_api import fleet_api
            resp = fleet_api("/api/tasks/waiting-human", timeout=5)
            if resp and isinstance(resp, list):
                current_ids = set()
                for task in resp:
                    tid = task.get("id") or task.get("task_id")
                    if tid is not None:
                        current_ids.add(tid)
                        if tid not in self._last_hitl_ids:
                            question = (task.get("question")
                                        or task.get("payload")
                                        or task.get("skill", ""))
                            self._notify_hitl(tid, question)
                self._last_hitl_ids = current_ids
        except Exception:
            pass

    def _get_close_behavior(self) -> str:
        """Get the configured close behavior: 'tray' or 'quit'."""
        try:
            import launcher as _mod
            settings = _mod._load_settings()
            behavior = settings.get("close_behavior", "tray")
            if behavior in ("tray", "quit"):
                return behavior
        except Exception:
            pass
        return "tray"

    def _get_start_minimized(self) -> bool:
        """Check if app should start minimized to tray."""
        try:
            import launcher as _mod
            return _mod._load_settings().get("start_minimized", False)
        except Exception:
            return False
