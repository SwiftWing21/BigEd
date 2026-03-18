"""SQLite data layer — all DB access goes through this module."""
import sqlite3
import time
import random
from datetime import datetime, timezone
from pathlib import Path


def utc_to_local(utc_str: str | None) -> str:
    """Convert a UTC datetime string from the DB to local time string."""
    if not utc_str:
        return "never"
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_str

DB_PATH = Path(__file__).parent / "fleet.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    role            TEXT NOT NULL,
    status          TEXT DEFAULT 'IDLE',
    current_task_id INTEGER,
    last_heartbeat  TEXT,
    pid             INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT DEFAULT (datetime('now')),
    assigned_to  TEXT,
    status       TEXT DEFAULT 'PENDING',
    priority     INTEGER DEFAULT 5,
    type         TEXT NOT NULL,
    payload_json TEXT,
    result_json  TEXT,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent   TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    read_at    TEXT,
    body_json  TEXT
);

CREATE TABLE IF NOT EXISTS locks (
    name        TEXT PRIMARY KEY,
    holder      TEXT NOT NULL,
    acquired_at TEXT DEFAULT (datetime('now'))
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout at SQLite level (more reliable than Python-level timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")  # 30s in ms — SQLite retries internally
    return conn


def _retry_write(fn, retries=8):
    """Retry a write operation with jittered backoff on OperationalError (locked)."""
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == retries - 1:
                raise
            time.sleep(0.2 * (2 ** attempt) + random.uniform(0, 0.1))


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def register_agent(name, role, pid):
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
    def _do():
        with get_conn() as conn:
            conn.execute("""
                UPDATE agents SET last_heartbeat=datetime('now'), status=?, current_task_id=?
                WHERE name=?
            """, (status, current_task_id, name))
    _retry_write(_do)


def claim_task(agent_name, affinity_skills=None):
    """Atomically claim the highest-priority pending task for this agent.

    If affinity_skills is provided, prefer tasks matching those skills first.
    Falls back to any unassigned task if no affinity match is available.
    """
    with get_conn() as conn:
        row = None
        # Try affinity-matched tasks first
        if affinity_skills:
            placeholders = ','.join('?' * len(affinity_skills))
            row = conn.execute(f"""
                SELECT id, type, payload_json FROM tasks
                WHERE status='PENDING' AND (assigned_to=? OR assigned_to IS NULL)
                  AND type IN ({placeholders})
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            """, (agent_name, *affinity_skills)).fetchone()

        # Fall back to any available task
        if not row:
            row = conn.execute("""
                SELECT id, type, payload_json FROM tasks
                WHERE status='PENDING' AND (assigned_to=? OR assigned_to IS NULL)
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            """, (agent_name,)).fetchone()

        if not row:
            return None
        conn.execute("""
            UPDATE tasks SET status='RUNNING', assigned_to=?
            WHERE id=? AND status='PENDING'
        """, (agent_name, row['id']))
        # Verify we won the race
        check = conn.execute(
            "SELECT assigned_to FROM tasks WHERE id=?", (row['id'],)
        ).fetchone()
        if check and check['assigned_to'] == agent_name:
            return dict(row)
    return None


def complete_task(task_id, result_json):
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='DONE', result_json=? WHERE id=?",
                (result_json, task_id)
            )
    _retry_write(_do)


def fail_task(task_id, error):
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='FAILED', error=? WHERE id=?",
                (str(error), task_id)
            )
    _retry_write(_do)


def requeue_task(task_id):
    """Put a task back into the PENDING queue (e.g. on temporary overload)."""
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='PENDING', assigned_to=NULL WHERE id=?",
                (task_id,)
            )
    _retry_write(_do)


def post_task(type_, payload_json, priority=5, assigned_to=None):
    result = [None]
    def _do():
        with get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO tasks (type, payload_json, priority, assigned_to, status)
                VALUES (?, ?, ?, ?, 'PENDING')
            """, (type_, payload_json, priority, assigned_to))
            result[0] = cur.lastrowid
    _retry_write(_do)
    return result[0]


def post_message(from_agent, to_agent, body_json):
    def _do():
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json)
                VALUES (?, ?, ?)
            """, (from_agent, to_agent, body_json))
    _retry_write(_do)


def get_messages(agent_name, unread_only=True, limit=20):
    """Retrieve messages for an agent. Marks them read on fetch."""
    with get_conn() as conn:
        where = "WHERE to_agent=?"
        if unread_only:
            where += " AND read_at IS NULL"
        rows = conn.execute(f"""
            SELECT id, from_agent, to_agent, created_at, body_json
            FROM messages {where}
            ORDER BY created_at DESC LIMIT ?
        """, (agent_name, limit)).fetchall()
        if rows:
            ids = [r['id'] for r in rows]
            conn.execute(
                f"UPDATE messages SET read_at=datetime('now') WHERE id IN ({','.join('?' * len(ids))})",
                ids
            )
        return [dict(r) for r in rows]


def broadcast_message(from_agent, body_json):
    """Send a message to ALL registered agents."""
    def _do():
        with get_conn() as conn:
            agents = conn.execute("SELECT name FROM agents").fetchall()
            for a in agents:
                conn.execute("""
                    INSERT INTO messages (from_agent, to_agent, body_json)
                    VALUES (?, ?, ?)
                """, (from_agent, a['name'], body_json))
            return len(agents)
    return _retry_write(_do)


def recover_stale_tasks(timeout_secs=900):
    """Requeue RUNNING tasks whose assigned agent has gone stale (no heartbeat)."""
    recovered = []
    def _do():
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT t.id, t.assigned_to, t.type
                FROM tasks t
                LEFT JOIN agents a ON t.assigned_to = a.name
                WHERE t.status = 'RUNNING'
                  AND (a.last_heartbeat IS NULL
                       OR (julianday('now') - julianday(a.last_heartbeat)) * 86400 > ?)
            """, (timeout_secs,)).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE tasks SET status='PENDING', assigned_to=NULL WHERE id=?",
                    (r['id'],)
                )
                recovered.append(dict(r))
    _retry_write(_do)
    return recovered


def get_task_result(task_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def acquire_lock(name, holder, timeout_secs=7200):
    """Acquire a named exclusive lock. Returns True if acquired, False if held by another."""
    def _do():
        with get_conn() as conn:
            # Check for stale lock
            row = conn.execute("SELECT holder, acquired_at FROM locks WHERE name=?", (name,)).fetchone()
            if row:
                if row["holder"] == holder:
                    return True  # already held by us
                # Check if stale (exceeded timeout)
                try:
                    from datetime import datetime, timezone
                    acquired = datetime.fromisoformat(row["acquired_at"]).replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - acquired).total_seconds()
                    if age < timeout_secs:
                        return False  # held by someone else, not stale
                except Exception:
                    return False
                # Stale — remove and acquire
                conn.execute("DELETE FROM locks WHERE name=?", (name,))
            conn.execute(
                "INSERT INTO locks (name, holder) VALUES (?, ?)", (name, holder))
            return True
    return _retry_write(_do)


def release_lock(name, holder=None):
    """Release a named lock. If holder specified, only release if we hold it."""
    def _do():
        with get_conn() as conn:
            if holder:
                conn.execute("DELETE FROM locks WHERE name=? AND holder=?", (name, holder))
            else:
                conn.execute("DELETE FROM locks WHERE name=?", (name,))
    _retry_write(_do)


def check_lock(name):
    """Check who holds a lock. Returns holder string or None."""
    with get_conn() as conn:
        row = conn.execute("SELECT holder FROM locks WHERE name=?", (name,)).fetchone()
        return row["holder"] if row else None


def get_pending_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) as n FROM tasks WHERE status='PENDING'").fetchone()['n']


def get_fleet_status():
    with get_conn() as conn:
        agents = conn.execute(
            "SELECT name, role, status, current_task_id, last_heartbeat, pid FROM agents ORDER BY name"
        ).fetchall()
        counts = {
            s: conn.execute(
                "SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,)
            ).fetchone()['n']
            for s in ('PENDING', 'RUNNING', 'DONE', 'FAILED')
        }
        return {'agents': [dict(a) for a in agents], 'tasks': counts}
