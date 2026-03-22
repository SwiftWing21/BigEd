"""File-based HITL response flow for VS Code integration."""
from __future__ import annotations
import logging
import threading
from pathlib import Path

log = logging.getLogger("hitl_responder")

FLEET_DIR = Path(__file__).parent
RESPONSE_DIR = FLEET_DIR / "hitl-responses"


def create_response_file(task_id: int, agent_name: str,
                         question: str, context: str = "") -> Path:
    """Create a pre-filled HITL response file for VS Code editing."""
    RESPONSE_DIR.mkdir(exist_ok=True)
    path = RESPONSE_DIR / f"hitl-response-{task_id}.md"
    content = (
        f"# HITL Response — Task #{task_id}\n\n"
        f"**Agent:** {agent_name}\n"
        f"**Question:**\n\n{question}\n\n"
        f"---\n\n"
        f"## Your Response\n\n"
        f"<!-- Write your response below this line. Save the file when done. -->\n\n"
    )
    if context:
        content += f"\n---\n\n## Context\n\n{context}\n"
    path.write_text(content, encoding="utf-8")
    return path


def parse_response_file(path: Path) -> str | None:
    """Extract the operator's response from a saved HITL response file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    marker = "## Your Response"
    idx = text.find(marker)
    if idx < 0:
        return None
    after = text[idx + len(marker):]
    after = after.replace(
        "<!-- Write your response below this line. Save the file when done. -->", ""
    )
    ctx_idx = after.find("## Context")
    if ctx_idx >= 0:
        after = after[:ctx_idx]
    response = after.strip()
    return response if response else None


class HITLFilePoller:
    """Polls hitl-responses/ for saved files and dispatches responses."""

    def __init__(self, send_callback, poll_interval: float = 2.0):
        """
        send_callback(task_id: int, response: str) -> bool
        """
        self._send = send_callback
        self._interval = poll_interval
        self._active: dict[int, float] = {}  # task_id -> file mtime at creation
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, task_id: int, path: Path) -> None:
        """Register a response file for polling."""
        try:
            self._active[task_id] = path.stat().st_mtime
        except Exception:
            self._active[task_id] = 0
        if self._thread is None or not self._thread.is_alive():
            self._start()

    def _start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        while not self._stop.is_set() and self._active:
            completed = []
            for task_id, orig_mtime in list(self._active.items()):
                path = RESPONSE_DIR / f"hitl-response-{task_id}.md"
                if not path.exists():
                    completed.append(task_id)
                    continue
                try:
                    current_mtime = path.stat().st_mtime
                except Exception:
                    continue
                if current_mtime > orig_mtime:
                    response = parse_response_file(path)
                    if response:
                        try:
                            ok = self._send(task_id, response)
                            if ok:
                                log.info("HITL response sent for task #%d", task_id)
                                path.unlink(missing_ok=True)
                                completed.append(task_id)
                        except Exception:
                            log.warning("Failed to send HITL response #%d",
                                        task_id, exc_info=True)
            for tid in completed:
                self._active.pop(tid, None)
            self._stop.wait(self._interval)
