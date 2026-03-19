"""SQLite data layer — all DB access goes through this module."""
import json
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
    error        TEXT,
    parent_id    INTEGER,
    depends_on   TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent   TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    read_at    TEXT,
    body_json  TEXT,
    channel    TEXT DEFAULT 'fleet'
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    body_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_channel_created
    ON notes (channel, created_at);

CREATE TABLE IF NOT EXISTS locks (
    name        TEXT PRIMARY KEY,
    holder      TEXT NOT NULL,
    acquired_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    skill               TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_create_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    task_id             INTEGER,
    agent               TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_skill ON usage(skill);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);

CREATE TABLE IF NOT EXISTS idle_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    agent       TEXT NOT NULL,
    skill       TEXT NOT NULL,
    result      TEXT,
    cost_usd    REAL DEFAULT 0.0
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


# ── Channel Constants (extracted to comms.py) ─────────────────────────────────
from comms import CH_SUP, CH_AGENT, CH_FLEET, CH_POOL

# ── Messaging (extracted to comms.py) ────────────────────────────────────────
from comms import post_message, get_messages, broadcast_message, post_note, get_notes, get_note_count

VALID_TASK_STATUSES = {"PENDING", "RUNNING", "DONE", "FAILED", "WAITING", "REVIEW", "WAITING_HUMAN"}


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migrate: add columns if missing (safe for existing DBs)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "parent_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER")
        if "depends_on" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT")
        if "review_rounds" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN review_rounds INTEGER DEFAULT 0")
        if "conditions" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN conditions TEXT")
        if "classification" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN classification TEXT DEFAULT 'internal'")
        # Migrate messages: add channel column if missing
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "channel" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN channel TEXT DEFAULT 'fleet'")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_messages_inbox
            ON messages (to_agent, channel, read_at)""")


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
    """Mark a task as DONE and promote any WAITING dependents."""
    # Validate result is valid JSON
    if result_json:
        try:
            parsed = json.loads(result_json) if isinstance(result_json, str) else result_json
            if isinstance(parsed, dict) and parsed.get("error"):
                # Skill returned an error in the result — still mark DONE but log it
                pass
            if not isinstance(result_json, str):
                result_json = json.dumps(result_json)
        except (json.JSONDecodeError, TypeError):
            result_json = json.dumps({"raw": str(result_json)[:2000]})

    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='DONE', result_json=? WHERE id=?",
                (result_json, task_id)
            )
            # Async DAG promotion (0.08.00) — prevents WAL thundering herd
            try:
                from dag_queue import enqueue_promotion
                enqueue_promotion(task_id)
            except ImportError:
                _promote_waiting_tasks(conn)  # fallback to sync
    _retry_write(_do)


def fail_task(task_id, error):
    """Mark a task as FAILED. Cascades: any WAITING tasks depending on this are also FAILED."""
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='FAILED', error=? WHERE id=?",
                (str(error), task_id)
            )
            # Async cascade-fail (0.08.00) — prevents WAL thundering herd
            try:
                from dag_queue import enqueue_cascade_fail
                enqueue_cascade_fail(task_id, str(error))
            except ImportError:
                _cascade_fail_dependents(conn, task_id, str(error))  # fallback to sync
    _retry_write(_do)


def _promote_waiting_tasks(conn):
    """Check all WAITING tasks and promote to PENDING if dependencies are met.

    Supports conditional edges: if a task has a `conditions` JSON dict mapping
    dep_task_id (str) -> substring, the dep's result_json must contain that
    substring for the condition to pass.  A None/missing condition means any
    completion suffices.
    """
    waiting = conn.execute(
        "SELECT id, depends_on, conditions FROM tasks WHERE status='WAITING' AND depends_on IS NOT NULL"
    ).fetchall()
    for row in waiting:
        try:
            dep_ids = json.loads(row["depends_on"])
        except (json.JSONDecodeError, TypeError):
            continue

        conditions = {}
        try:
            if row["conditions"]:
                conditions = json.loads(row["conditions"])
        except (json.JSONDecodeError, TypeError, IndexError):
            pass

        if not dep_ids:
            conn.execute("UPDATE tasks SET status='PENDING' WHERE id=?", (row["id"],))
            continue

        # Check if all dependencies are DONE
        placeholders = ",".join("?" * len(dep_ids))
        done_tasks = conn.execute(
            f"SELECT id, result_json FROM tasks WHERE id IN ({placeholders}) AND status='DONE'",
            dep_ids
        ).fetchall()

        if len(done_tasks) != len(dep_ids):
            continue  # not all deps done yet

        # Check conditions on each completed dependency
        all_met = True
        for dt in done_tasks:
            cond = conditions.get(str(dt["id"]))
            if cond and dt["result_json"]:
                if cond not in dt["result_json"]:
                    all_met = False
                    break
            elif cond and not dt["result_json"]:
                # Condition specified but dep has no result — condition not met
                all_met = False
                break

        if all_met:
            conn.execute("UPDATE tasks SET status='PENDING' WHERE id=?", (row["id"],))


def _cascade_fail_dependents(conn, failed_id, error):
    """Fail any WAITING tasks that depend on a failed task."""
    waiting = conn.execute(
        "SELECT id, depends_on FROM tasks WHERE status='WAITING' AND depends_on IS NOT NULL"
    ).fetchall()
    for row in waiting:
        try:
            dep_ids = json.loads(row["depends_on"])
        except (json.JSONDecodeError, TypeError):
            continue
        if failed_id in dep_ids:
            conn.execute(
                "UPDATE tasks SET status='FAILED', error=? WHERE id=?",
                (f"Dependency task {failed_id} failed: {error[:200]}", row["id"])
            )


def validate_dag(task_ids: list) -> tuple:
    """Validate a set of tasks form a valid DAG (no cycles, no missing deps).

    Returns (valid, error_message).
    """
    with get_conn() as conn:
        # Build adjacency from depends_on
        graph = {}  # task_id -> [dependency_ids]
        for tid in task_ids:
            row = conn.execute("SELECT depends_on FROM tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                return False, f"Task {tid} not found"
            deps = []
            if row["depends_on"]:
                try:
                    deps = json.loads(row["depends_on"])
                except (json.JSONDecodeError, TypeError):
                    pass
            graph[tid] = deps

        # Check for missing dependencies (deps referencing tasks outside the set)
        all_ids = set(task_ids)
        for tid, deps in graph.items():
            for dep in deps:
                if dep not in all_ids:
                    # Check if it exists in DB at all
                    exists = conn.execute("SELECT id FROM tasks WHERE id=?", (dep,)).fetchone()
                    if not exists:
                        return False, f"Task {tid} depends on non-existent task {dep}"

        # Cycle detection using DFS
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in task_ids}

        def has_cycle(node):
            color[node] = GRAY
            for dep in graph.get(node, []):
                if dep not in color:
                    continue  # external dep, skip
                if color[dep] == GRAY:
                    return True  # back edge = cycle
                if color[dep] == WHITE and has_cycle(dep):
                    return True
            color[node] = BLACK
            return False

        for tid in task_ids:
            if color[tid] == WHITE:
                if has_cycle(tid):
                    return False, f"Cycle detected involving task {tid}"

        return True, "DAG valid"


def post_task_chain(tasks, priority=5):
    """Post a sequence of tasks where each depends on the previous.

    Args:
        tasks: list of dicts with keys: type, payload (dict), assigned_to (optional)
        priority: shared priority for all tasks

    Returns:
        list of task IDs in order
    """
    task_ids = []
    for i, t in enumerate(tasks):
        depends = [task_ids[-1]] if task_ids else None
        payload_json = json.dumps(t.get("payload", {}))
        tid = post_task(
            t["type"], payload_json,
            priority=priority,
            assigned_to=t.get("assigned_to"),
            parent_id=task_ids[0] if task_ids else None,
            depends_on=depends
        )
        task_ids.append(tid)

    # Validate the chain forms a valid DAG
    valid, msg = validate_dag(task_ids)
    if not valid:
        import logging
        logging.getLogger("db").warning(f"Task chain DAG validation: {msg}")

    return task_ids


def checkpoint_chain(parent_id: int) -> dict:
    """Save checkpoint of a task chain's progress. Returns checkpoint data."""
    with get_conn() as conn:
        tasks = conn.execute(
            "SELECT id, type, status, result_json, depends_on FROM tasks WHERE parent_id=? OR id=?",
            (parent_id, parent_id)
        ).fetchall()
        checkpoint = {
            "parent_id": parent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tasks": [dict(t) for t in tasks],
            "completed": [t["id"] for t in tasks if t["status"] == "DONE"],
            "failed": [t["id"] for t in tasks if t["status"] == "FAILED"],
            "pending": [t["id"] for t in tasks if t["status"] in ("PENDING", "WAITING", "RUNNING")],
        }
        return checkpoint


def resume_chain(parent_id: int) -> list:
    """Resume a failed chain from the last checkpoint. Requeues failed tasks."""
    resumed = []
    def _do():
        with get_conn() as conn:
            # Find failed tasks in this chain
            failed = conn.execute(
                "SELECT id, type FROM tasks WHERE (parent_id=? OR id=?) AND status='FAILED'",
                (parent_id, parent_id)
            ).fetchall()
            for t in failed:
                conn.execute(
                    "UPDATE tasks SET status='PENDING', error=NULL, assigned_to=NULL WHERE id=?",
                    (t["id"],)
                )
                resumed.append({"id": t["id"], "type": t["type"]})
            # Also re-promote any WAITING tasks whose deps are now DONE
            _promote_waiting_tasks(conn)
    _retry_write(_do)
    return resumed


def requeue_task(task_id):
    """Put a task back into the PENDING queue (e.g. on temporary overload)."""
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='PENDING', assigned_to=NULL WHERE id=?",
                (task_id,)
            )
    _retry_write(_do)


def review_task(task_id, result_json):
    """Transition task to REVIEW status — output awaits adversarial review."""
    if result_json and not isinstance(result_json, str):
        result_json = json.dumps(result_json)
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='REVIEW', result_json=? WHERE id=?",
                (result_json, task_id)
            )
    _retry_write(_do)


def reject_task(task_id, critique):
    """Review rejected — requeue with critique appended to payload for retry.

    Increments review_rounds. Returns the new review_rounds count.
    """
    result = [0]
    def _do():
        with get_conn() as conn:
            row = conn.execute(
                "SELECT payload_json, review_rounds FROM tasks WHERE id=?",
                (task_id,)
            ).fetchone()
            if not row:
                return
            rounds = (row["review_rounds"] or 0) + 1
            result[0] = rounds
            # Append critique to payload so the worker can see it on retry
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            payload["_review_critique"] = critique
            payload["_review_round"] = rounds
            conn.execute("""
                UPDATE tasks SET status='PENDING', assigned_to=NULL,
                    result_json=NULL, error=NULL,
                    payload_json=?, review_rounds=?
                WHERE id=?
            """, (json.dumps(payload), rounds, task_id))
    _retry_write(_do)
    return result[0]


def post_task(type_, payload_json, priority=5, assigned_to=None,
              parent_id=None, depends_on=None, conditions=None,
              classification="internal"):
    """Post a task to the queue.

    Args:
        type_: skill name (e.g. "summarize", "web_search")
        payload_json: JSON string payload for the skill
        priority: 1-10, higher = claimed first
        assigned_to: optional agent name to assign to
        parent_id: optional parent task ID (for sub-tasks)
        depends_on: optional list of task IDs that must complete first
        conditions: optional dict mapping dep_task_id (str) -> substring.
            The dependency's result_json must contain the substring for the
            waiting task to be promoted.  None means any completion suffices.
            Example: {"1": "approved", "2": None}
        classification: data classification label (default "internal").
            Common values: "public", "internal", "confidential", "restricted".
    """
    # Validate payload is valid JSON
    if payload_json:
        try:
            json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(f"payload_json must be valid JSON, got: {repr(payload_json)[:100]}")
    # Clamp priority
    priority = max(1, min(10, int(priority)))
    # Determine initial status
    deps_json = None
    conds_json = None
    status = "PENDING"
    if depends_on:
        deps_json = json.dumps(depends_on) if isinstance(depends_on, list) else depends_on
        status = "WAITING"
    if conditions:
        conds_json = json.dumps(conditions) if isinstance(conditions, dict) else conditions

    result = [None]
    def _do():
        with get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO tasks (type, payload_json, priority, assigned_to, status,
                                   parent_id, depends_on, conditions, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (type_, payload_json, priority, assigned_to, status,
                  parent_id, deps_json, conds_json, classification))
            result[0] = cur.lastrowid
    _retry_write(_do)
    return result[0]


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
                    acquired = datetime.fromisoformat(row["acquired_at"]).replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - acquired).total_seconds()
                    if age < timeout_secs:
                        return False  # held by someone else, not stale
                except Exception:
                    return False
                # Stale — remove and acquire
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
            for s in ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'WAITING', 'REVIEW', 'WAITING_HUMAN')
        }
        return {'agents': [dict(a) for a in agents], 'tasks': counts}


# ── Human-in-the-Loop Functions ───────────────────────────────────────────────

def request_human_input(task_id, agent_name, question):
    """Agent pauses task and requests operator input. Sets status to WAITING_HUMAN."""
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='WAITING_HUMAN' WHERE id=?", (task_id,))
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json, channel)
                VALUES (?, 'operator', ?, 'fleet')
            """, (agent_name, json.dumps({
                "type": "human_input_request",
                "task_id": task_id,
                "question": question,
            })))
    _retry_write(_do)


def respond_to_agent(task_id, response):
    """Operator responds to agent question. Resumes task to RUNNING."""
    def _do():
        with get_conn() as conn:
            row = conn.execute(
                "SELECT assigned_to, payload_json FROM tasks WHERE id=?",
                (task_id,)).fetchone()
            if not row:
                return
            agent = row["assigned_to"]
            # Append response to payload
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            payload["_human_response"] = response
            conn.execute("""
                UPDATE tasks SET status='PENDING', payload_json=?
                WHERE id=? AND status='WAITING_HUMAN'
            """, (json.dumps(payload), task_id))
            # Notify agent
            if agent:
                conn.execute("""
                    INSERT INTO messages (from_agent, to_agent, body_json, channel)
                    VALUES ('operator', ?, ?, 'fleet')
                """, (agent, json.dumps({
                    "type": "human_response",
                    "task_id": task_id,
                    "response": response,
                })))
    _retry_write(_do)


def get_waiting_human_tasks():
    """Get all tasks awaiting human input, with the agent's question."""
    with get_conn() as conn:
        tasks = conn.execute("""
            SELECT t.id, t.type, t.assigned_to, t.created_at, t.payload_json
            FROM tasks t
            WHERE t.status = 'WAITING_HUMAN'
            ORDER BY t.created_at ASC
        """).fetchall()
        result = []
        for t in tasks:
            task_dict = dict(t)
            # Find the question from the agent's message
            msg = conn.execute("""
                SELECT body_json FROM messages
                WHERE from_agent = ? AND to_agent = 'operator'
                AND body_json LIKE '%human_input_request%'
                AND body_json LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (t["assigned_to"] or "", f'%"task_id": {t["id"]}%')).fetchone()
            if msg:
                try:
                    body = json.loads(msg["body_json"])
                    task_dict["question"] = body.get("question", "")
                except Exception:
                    task_dict["question"] = ""
            else:
                task_dict["question"] = ""
            result.append(task_dict)
        return result


# ── Diagnostics (extracted to diagnostics.py) ────────────────────────────────
from diagnostics import quarantine_agent, clear_quarantine, get_failure_streaks, get_stuck_reviews


# ── Cost Tracking (extracted to cost_tracking.py) ────────────────────────────
from cost_tracking import log_usage, get_usage_summary, get_usage_delta


# ── Idle Evolution (extracted to idle_evolution.py) ───────────────────────────
from idle_evolution import log_idle_run, get_idle_stats, get_least_evolved_skill


# ── DAG Visualization ─────────────────────────────────────────────────────────

def get_dag_graph(parent_id: int) -> dict:
    """Build a DAG graph structure for visualization."""
    with get_conn() as conn:
        tasks = conn.execute(
            "SELECT id, type, status, depends_on, parent_id, result_json FROM tasks "
            "WHERE parent_id=? OR id=?",
            (parent_id, parent_id)
        ).fetchall()
        nodes = []
        edges = []
        for t in tasks:
            nodes.append({
                "id": t["id"], "type": t["type"], "status": t["status"],
                "has_result": bool(t["result_json"]),
            })
            deps = json.loads(t["depends_on"]) if t["depends_on"] else []
            for dep in deps:
                edges.append({"from": dep, "to": t["id"]})
        return {"nodes": nodes, "edges": edges, "parent_id": parent_id}
