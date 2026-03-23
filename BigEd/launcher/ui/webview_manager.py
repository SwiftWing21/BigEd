"""
BigEd CC -- Webview companion window manager for Hybrid ViewPort graph views.

Provides WebviewManagerMixin that is mixed into BigEdCC:
- _open_graph_view      (open/navigate pywebview companion window)
- _close_graph_view     (destroy companion window)
- _focus_graph_view     (bring companion to front)
- _open_graph_in_browser (fallback: open in default browser)
- _on_graph_node_click  (JS bridge: handle node click from web side)
- _on_graph_edge_click  (JS bridge: handle edge click from web side)

pywebview is optional -- if not installed, graph views open in the browser.
"""

import logging
import re
import sys
import threading
import webbrowser
from pathlib import Path

log = logging.getLogger("webview_manager")

# pywebview is optional -- fallback to browser if not installed
_WEBVIEW_AVAILABLE = False
try:
    import webview
    _WEBVIEW_AVAILABLE = True
except ImportError:
    log.info("pywebview not installed -- graph views will open in browser")

# Fleet config path (same resolution as launcher.py)
_FLEET_DIR = Path(__file__).resolve().parent.parent.parent / "fleet"
_FLEET_TOML = _FLEET_DIR / "fleet.toml"


def _resolve_dashboard_port() -> int:
    """Read dashboard port from fleet.toml, default 5555."""
    try:
        text = _FLEET_TOML.read_text(encoding="utf-8")
        m = re.search(r'^port\s*=\s*(\d+)', text, re.M)
        return int(m.group(1)) if m else 5555
    except Exception:
        return 5555


# ─── Webview Manager Mixin ──────────────────────────────────────────────────


class WebviewManagerMixin:
    """Manages pywebview companion window for graph visualization.

    Mixed into BigEdCC.  The dashboard URL is resolved from fleet.toml
    at call time (not cached) so it picks up config changes.
    """

    _webview_window = None
    _webview_thread = None
    _webview_ready = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────

    def _open_graph_view(self, view_name: str = "fleet-overview") -> None:
        """Open a graph view in a pywebview companion window.

        If pywebview is not installed, falls back to the default browser.
        If the window is already open, navigates to the new view and focuses.
        """
        url = f"{self._dashboard_url}/view/embed/{view_name}"

        # Already open -- just navigate + focus
        if self._webview_window is not None:
            try:
                self._webview_window.load_url(url)
                self._focus_graph_view()
                log.info("Navigated webview to %s", view_name)
                return
            except Exception:
                # Window was probably destroyed externally -- recreate
                log.warning("Existing webview window invalid, recreating")
                self._webview_window = None

        if not _WEBVIEW_AVAILABLE:
            log.info("pywebview unavailable -- opening %s in browser", view_name)
            self._open_graph_in_browser(view_name)
            return

        # Create the companion window in a background thread.
        # webview.start() blocks until the window is closed, so it must
        # run in its own thread.
        self._webview_ready.clear()

        def _run_webview():
            try:
                self._webview_window = webview.create_window(
                    f"BigEd \u2014 {view_name}",
                    url,
                    width=900,
                    height=700,
                    min_size=(600, 400),
                    js_api=self,  # exposes Python methods to JS bridge
                )
                self._webview_ready.set()
                webview.start(
                    gui="edgechromium" if sys.platform == "win32" else None,
                )
            except Exception:
                log.warning("Failed to start webview window", exc_info=True)
                self._webview_ready.set()
            finally:
                # Window closed (user or programmatic) -- clean up refs
                self._webview_window = None
                self._webview_thread = None

        self._webview_thread = threading.Thread(
            target=_run_webview, daemon=True, name="webview-companion",
        )
        self._webview_thread.start()
        log.info("Opened graph view: %s", view_name)

    def _close_graph_view(self) -> None:
        """Close the companion webview window if open.

        Safe to call even if no window is open.
        """
        win = self._webview_window
        if win is None:
            return
        try:
            win.destroy()
            log.info("Closed webview companion window")
        except Exception:
            log.warning("Error closing webview window", exc_info=True)
        finally:
            self._webview_window = None
            self._webview_thread = None

    def _focus_graph_view(self) -> None:
        """Bring the webview window to front.

        Uses a brief on_top toggle as a cross-platform focus trick.
        """
        win = self._webview_window
        if win is None:
            return
        try:
            win.on_top = True
            # Schedule reset after a short delay so the OS registers the raise
            threading.Timer(0.3, self._reset_webview_on_top).start()
        except Exception:
            log.warning("Could not focus webview window", exc_info=True)

    def _open_graph_in_browser(self, view_name: str = "fleet-overview") -> None:
        """Open graph view in the default web browser (fallback).

        Uses the full-chrome URL (``/view/graph/``) rather than the
        embed URL, so the page includes its own navigation chrome.
        """
        url = f"{self._dashboard_url}/view/graph/{view_name}"
        try:
            webbrowser.open(url)
            log.info("Opened graph view in browser: %s", url)
        except Exception:
            log.warning("Failed to open browser for %s", url, exc_info=True)

    # ── JS Bridge handlers (called from web side) ────────────────────────

    def _on_graph_node_click(self, node_id: str, node_type: str) -> None:
        """Handle node click from the graph JS side.

        Exposed via ``js_api=self`` so the web page can call
        ``pywebview.api._on_graph_node_click(id, type)``.
        """
        log.info("Graph node click: %s (%s)", node_id, node_type)
        if node_type == "agent":
            # Future: open agent detail / edit dialog in the native UI
            pass
        elif node_type == "skill":
            # Future: open skill info panel
            pass

    def _on_graph_edge_click(self, edge_id: str, edge_type: str) -> None:
        """Handle edge click from the graph JS side."""
        log.info("Graph edge click: %s (%s)", edge_id, edge_type)

    # ── Internal helpers ─────────────────────────────────────────────────

    @property
    def _dashboard_url(self) -> str:
        """Resolve dashboard base URL from fleet.toml config."""
        port = _resolve_dashboard_port()
        return f"http://127.0.0.1:{port}"

    def _reset_webview_on_top(self) -> None:
        """Reset on_top flag after focus trick."""
        win = self._webview_window
        if win is None:
            return
        try:
            win.on_top = False
        except Exception:
            pass
