"""Boot status tracker -- JSON file-based stage progress for the boot overlay.

Writes stage progress to fleet/.boot_status.json so the dashboard can poll it.
"""

import json
import time
from pathlib import Path

_STATUS_FILE = Path(__file__).parent / ".boot_status.json"
_STAGES = ["pid", "db", "ollama", "dashboard", "dr_ders", "workers", "ready"]
_boot_start: float = 0.0


def update_stage(stage: str, status: str = "done"):
    """Update a boot stage. status: 'starting', 'done', 'failed'."""
    global _boot_start
    if not _boot_start:
        _boot_start = time.time()
    data = read()
    data["stages"][stage] = {
        "status": status,
        "elapsed": round(time.time() - _boot_start, 1),
    }
    if stage == "ready" and status == "done":
        data["stage"] = "ready"
    else:
        data["stage"] = stage
    _STATUS_FILE.write_text(json.dumps(data), encoding="utf-8")


def read() -> dict:
    """Read current boot status. Returns default if no file."""
    try:
        if _STATUS_FILE.exists():
            return json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "stage": "starting",
        "stages": {s: {"status": "pending", "elapsed": 0} for s in _STAGES},
    }


def clear():
    """Remove status file after boot completes."""
    try:
        _STATUS_FILE.unlink(missing_ok=True)
    except Exception:
        pass
