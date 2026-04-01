"""Agent management, locks, and GDPR erasure — split from db.py (TD-04)."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def register_agent(name, role, pid):
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO agents (name, role, status, last_heartbeat, pid)
                VALUES (?, ?, 'IDLE', datetime('now'), ?)
                ON CONFLICT(name) DO UPDATE SET
                    status='IDLE', last_heartbeat=datetime('now'), pid=excluded.pid
            """, (name, role, pid))
    _retry_write(_do)


def heartbeat(name, status='IDLE', current_task_id=None):
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            conn.execute("""
                UPDATE agents SET last_heartbeat=datetime('now'), status=?, current_task_id=?
                WHERE name=?
            """, (status, current_task_id, name))
    _retry_write(_do)


def acquire_lock(name, holder, timeout_secs=7200):
    """Acquire a named exclusive lock. Returns True if acquired, False if held by another."""
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            row = conn.execute("SELECT holder, acquired_at FROM locks WHERE name=?", (name,)).fetchone()
            if row:
                if row["holder"] == holder:
                    return True
                try:
                    acquired = datetime.fromisoformat(row["acquired_at"]).replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - acquired).total_seconds()
                    if age < timeout_secs:
                        return False
                except Exception:
                    return False
                conn.execute("DELETE FROM locks WHERE name=?", (name,))
            conn.execute(
                "INSERT OR IGNORE INTO locks (name, holder) VALUES (?, ?)",
                (name, holder))
            row2 = conn.execute(
                "SELECT holder FROM locks WHERE name=?", (name,)).fetchone()
            return row2 is not None and row2["holder"] == holder
    return _retry_write(_do)


def release_lock(name, holder=None):
    """Release a named lock. If holder specified, only release if we hold it."""
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            if holder:
                conn.execute("DELETE FROM locks WHERE name=? AND holder=?", (name, holder))
            else:
                conn.execute("DELETE FROM locks WHERE name=?", (name,))
    _retry_write(_do)


def check_lock(name):
    """Check who holds a lock. Returns holder string or None."""
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT holder FROM locks WHERE name=?", (name,)).fetchone()
        return row["holder"] if row else None


def delete_user_data(identifier: str, scope: str = "agent") -> dict:
    """GDPR Art. 17: Right to erasure — purge all data for an agent or task submitter.

    Args:
        identifier: agent name or submitter identifier
        scope: "agent" (purge agent data) or "all" (purge everything matching identifier)
    Returns:
        dict with counts of deleted records per table
    """
    from db import get_conn, _retry_write

    deleted = {}
    def _do():
        with get_conn() as conn:
            r = conn.execute("DELETE FROM tasks WHERE assigned_to=?", (identifier,))
            deleted["tasks"] = r.rowcount
            r = conn.execute("DELETE FROM messages WHERE from_agent=? OR to_agent=?", (identifier, identifier))
            deleted["messages"] = r.rowcount
            r = conn.execute("DELETE FROM notes WHERE from_agent=?", (identifier,))
            deleted["notes"] = r.rowcount
            r = conn.execute("DELETE FROM usage WHERE agent=?", (identifier,))
            deleted["usage"] = r.rowcount
            r = conn.execute("DELETE FROM idle_runs WHERE agent=?", (identifier,))
            deleted["idle_runs"] = r.rowcount
            r = conn.execute("DELETE FROM agents WHERE name=?", (identifier,))
            deleted["agents"] = r.rowcount
    _retry_write(_do)

    knowledge_dir = Path(__file__).parent / "knowledge"
    deleted["knowledge_files"] = 0
    if knowledge_dir.exists():
        for f in knowledge_dir.rglob("*"):
            if f.is_file() and identifier in f.name:
                try:
                    f.unlink()
                    deleted["knowledge_files"] += 1
                except Exception:
                    pass

    return deleted
