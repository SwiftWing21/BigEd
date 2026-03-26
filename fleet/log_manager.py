"""Log session manager — rotate logs on startup, keep last N sessions.

On each fleet boot:
1. Archive current logs to logs/sessions/YYYY-MM-DD_HHMMSS/
2. Start fresh log files
3. Delete oldest sessions beyond the retention limit

Config (fleet.toml):
  [logging]
  session_retention = 10    # keep last N session log archives
  max_log_size_mb = 50      # max single log file size (future: mid-session rotation)
"""
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).resolve().parent
LOGS_DIR = FLEET_DIR / "logs"
SESSIONS_DIR = LOGS_DIR / "sessions"

# Files to rotate (everything except .gitkeep and sessions/)
LOG_EXTENSIONS = {".log", ".jsonl"}


def _get_retention() -> int:
    """Read session retention from fleet.toml, default 10."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("logging", {}).get("session_retention", 10)
    except Exception:
        return 10


def rotate_logs() -> dict:
    """Archive current logs to a timestamped session folder and start fresh.

    Call once at supervisor startup, before any logging begins.
    Returns dict with session_id, files_archived, sessions_pruned.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Collect log files to archive
    log_files = [
        f for f in LOGS_DIR.iterdir()
        if f.is_file() and f.suffix in LOG_EXTENSIONS and f.stat().st_size > 0
    ]

    if not log_files:
        return {"session_id": None, "files_archived": 0, "sessions_pruned": 0,
                "message": "No logs to archive"}

    # Create session archive folder
    session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Move log files to archive
    archived = 0
    for f in log_files:
        try:
            dest = session_dir / f.name
            shutil.move(str(f), str(dest))
            archived += 1
        except Exception as e:
            # File might be locked by a still-running process — copy instead
            try:
                shutil.copy2(str(f), str(dest))
                # Truncate the original
                f.write_text("")
                archived += 1
            except Exception:
                pass  # Skip files we can't move or copy

    # Prune old sessions beyond retention limit
    retention = _get_retention()
    pruned = _prune_sessions(retention)

    result = {
        "session_id": session_id,
        "files_archived": archived,
        "sessions_pruned": pruned,
        "archive_path": str(session_dir),
    }
    return result


def _prune_sessions(keep: int) -> int:
    """Delete oldest session archives beyond the keep limit."""
    if not SESSIONS_DIR.exists():
        return 0

    sessions = sorted(
        [d for d in SESSIONS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,  # lexicographic = chronological for YYYY-MM-DD_HHMMSS
        reverse=True,
    )

    pruned = 0
    for old_session in sessions[keep:]:
        try:
            shutil.rmtree(str(old_session))
            pruned += 1
        except Exception:
            pass
    return pruned


def list_sessions() -> list[dict]:
    """Return list of archived sessions with metadata."""
    if not SESSIONS_DIR.exists():
        return []

    sessions = []
    for d in sorted(SESSIONS_DIR.iterdir(), key=lambda x: x.name, reverse=True):
        if not d.is_dir():
            continue
        files = list(d.iterdir())
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        sessions.append({
            "session_id": d.name,
            "files": len(files),
            "size_mb": round(total_size / (1024 * 1024), 2),
            "path": str(d),
        })
    return sessions


def get_session_log(session_id: str, filename: str) -> str | None:
    """Read a specific log file from a session archive."""
    log_path = SESSIONS_DIR / session_id / filename
    if not log_path.exists() or not log_path.is_file():
        return None
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
