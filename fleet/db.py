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


def claim_task(agent_name):
    """Atomically claim the highest-priority pending task for this agent."""
    with get_conn() as conn:
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


def get_task_result(task_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


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
