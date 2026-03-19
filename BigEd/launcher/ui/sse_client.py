"""
TECH_DEBT 4.2: SSE client for reactive UI updates.
Replaces aggressive 4s polling loops with push-based event consumption.

Usage:
    client = SSEClient("http://localhost:5555/api/stream")
    client.on("status", lambda data: update_agents(data))
    client.on("alert", lambda data: show_alert(data))
    client.start()  # runs in background thread
    ...
    client.stop()
"""
import json
import threading
import time
import urllib.request
from typing import Callable


class SSEClient:
    """Server-Sent Events client for consuming dashboard /api/stream."""

    def __init__(self, url: str = "http://localhost:5555/api/stream",
                 reconnect_delay: float = 5.0):
        self._url = url
        self._reconnect_delay = reconnect_delay
        self._callbacks: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._connected = False

    def on(self, event_type: str, callback: Callable):
        """Register a callback for an event type (e.g., 'status', 'alert')."""
        with self._lock:
            self._callbacks.setdefault(event_type, []).append(callback)

    def off(self, event_type: str, callback: Callable = None):
        """Unregister a callback. If callback is None, removes all for that type."""
        with self._lock:
            if callback is None:
                self._callbacks.pop(event_type, None)
            elif event_type in self._callbacks:
                self._callbacks[event_type] = [
                    cb for cb in self._callbacks[event_type] if cb != callback
                ]

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self):
        """Start consuming SSE events in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the SSE consumer."""
        self._running = False
        self._connected = False

    def _consume_loop(self):
        """Main loop: connect, read events, reconnect on failure."""
        while self._running:
            try:
                self._connect_and_read()
            except Exception:
                self._connected = False
            if self._running:
                time.sleep(self._reconnect_delay)

    def _connect_and_read(self):
        """Connect to SSE endpoint and read events."""
        req = urllib.request.Request(
            self._url,
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            self._connected = True
            self._dispatch("connected", {})

            buffer = ""
            event_type = "message"

            while self._running:
                try:
                    # Read one line (SSE is line-delimited)
                    chunk = resp.read(1).decode("utf-8", errors="replace")
                    if not chunk:
                        break  # connection closed

                    buffer += chunk

                    # Process complete lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.rstrip("\r")

                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                data = {"raw": data_str}
                            # Use event type from data if present
                            actual_type = data.get("type", event_type)
                            self._dispatch(actual_type, data)
                            event_type = "message"  # reset
                        elif line.startswith(":"):
                            pass  # comment/keepalive
                        elif line == "":
                            pass  # empty line (event boundary)

                except Exception:
                    if not self._running:
                        break
                    raise

        self._connected = False
        self._dispatch("disconnected", {})

    def _dispatch(self, event_type: str, data: dict):
        """Dispatch event to registered callbacks."""
        with self._lock:
            cbs = list(self._callbacks.get(event_type, []))
            cbs_star = list(self._callbacks.get("*", []))
        for cb in cbs:
            try:
                cb(data)
            except Exception:
                pass  # callbacks must not crash the SSE reader
        for cb in cbs_star:
            try:
                cb(event_type, data)
            except Exception:
                pass


def create_tk_sse_bridge(app, url="http://localhost:5555/api/stream"):
    """Create an SSE client that dispatches events to tkinter's main thread.

    Usage in launcher.py:
        sse = create_tk_sse_bridge(self)
        sse.on("status", self._handle_sse_status)
        sse.start()
    """
    client = SSEClient(url)
    original_dispatch = client._dispatch

    def _tk_dispatch(event_type, data):
        """Route SSE events through tkinter's after() for thread safety."""
        try:
            app.after(0, lambda: original_dispatch(event_type, data))
        except Exception:
            pass  # app may be destroyed

    client._dispatch = _tk_dispatch
    return client
