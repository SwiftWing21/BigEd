"""SQLite data layer — all DB access goes through this module."""
import json
import os
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


def get_tenant_db_path(tenant_id: str = None) -> Path:
    """Get DB path for a tenant. None = default (single-tenant mode).

    In multi-tenant mode (enterprise.multi_tenant = true in fleet.toml),
    each tenant gets an isolated database under fleet/tenants/<tenant_id>/.
    The directory is auto-created if it doesn't exist.

    Returns fleet/fleet.db for single-tenant (default) mode.
    """
    base = Path(__file__).parent
    if not tenant_id:
        return base / "fleet.db"
    tenant_dir = base / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir / "fleet.db"

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
    depends_on   TEXT,
    FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE SET NULL
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


def get_conn(db_path=None):
    """Get DB connection, with SQLCipher if available and configured.

    SQLCipher provides transparent AES-256 encryption for the fleet database.
    On fresh installs with sqlcipher3 installed, set BIGED_DB_KEY env var to
    enable encryption. Falls back to plain sqlite3 when sqlcipher3 is absent.
    """
    path = db_path or DB_PATH
    try:
        import sqlcipher3 as sqlite3_mod
        conn = sqlite3_mod.connect(str(path), check_same_thread=False, timeout=30)
        key = os.environ.get("BIGED_DB_KEY", "")
        if key:
            # Escape single quotes to prevent SQL injection
            safe_key = key.replace("'", "''")
            conn.execute(f"PRAGMA key = '{safe_key}'")
        conn.row_factory = sqlite3_mod.Row
    except ImportError:
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
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


def acquire_fleet_lock(conn, timeout_ms=5000):
    """Acquire an exclusive SQLite advisory lock for federation write serialization.

    Issues BEGIN EXCLUSIVE which prevents any other reader or writer from accessing
    the database until the transaction is committed or rolled back. When multiple
    fleet nodes share the same SQLite file on a network mount, this serializes
    conflicting write operations.

    Retries with exponential backoff until timeout_ms is exhausted.
    Returns True on success, False if timeout exceeded without acquiring the lock.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    attempt = 0
    while True:
        try:
            conn.execute("BEGIN EXCLUSIVE")
            return True
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            wait = min(0.05 * (2 ** attempt) + random.uniform(0, 0.01), remaining)
            time.sleep(wait)
            attempt += 1


def release_fleet_lock(conn, commit=True):
    """Release the exclusive fleet lock — commit writes on success, roll back on error."""
    try:
        conn.execute("COMMIT" if commit else "ROLLBACK")
    except sqlite3.OperationalError:
        pass  # Transaction already completed


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
        if "intelligence_score" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN intelligence_score REAL DEFAULT NULL")
        if "trace_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN trace_id TEXT DEFAULT NULL")
        # Migrate messages: add channel column if missing
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "channel" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN channel TEXT DEFAULT 'fleet'")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_messages_inbox
            ON messages (to_agent, channel, read_at)""")
        # Migrate usage: add speed tracking columns
        usage_cols = {r[1] for r in conn.execute("PRAGMA table_info(usage)").fetchall()}
        if "eval_duration_ms" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN eval_duration_ms REAL DEFAULT NULL")
        if "prompt_duration_ms" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN prompt_duration_ms REAL DEFAULT NULL")
        if "tokens_per_sec" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN tokens_per_sec REAL DEFAULT NULL")
        if "provider" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN provider TEXT DEFAULT NULL")
            conn.execute("UPDATE usage SET provider='claude' WHERE provider IS NULL AND model LIKE 'claude-%'")
            conn.execute("UPDATE usage SET provider='gemini' WHERE provider IS NULL AND model LIKE 'gemini-%'")
            conn.execute("UPDATE usage SET provider='local' WHERE provider IS NULL AND model NOT LIKE 'claude-%' AND model NOT LIKE 'gemini-%' AND model IS NOT NULL")
        # P2-1: Indexes for common query patterns
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
        # DAG promotion: index for dependency lookups and status+type queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_depends ON tasks(depends_on) WHERE depends_on IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type)")
        # PHI audit table — DITL Phase 2 compliance tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS phi_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                action TEXT NOT NULL,
                data_scope TEXT,
                model_used TEXT,
                phi_detected BOOLEAN DEFAULT 0,
                deidentified BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_phi_audit_date ON phi_audit(created_at)")
        # Structured audit log — enhanced audit trail (fleet/audit.py)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
                actor         TEXT NOT NULL,
                action        TEXT NOT NULL,
                resource      TEXT,
                detail        TEXT,
                cost_usd      REAL NOT NULL DEFAULT 0.0,
                metadata_json TEXT,
                ip_address    TEXT,
                role          TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
        # Human feedback on agent outputs (Outputs module)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS output_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                output_path TEXT NOT NULL,
                verdict TEXT NOT NULL,
                feedback_text TEXT,
                operator TEXT DEFAULT 'human',
                agent_name TEXT,
                skill_type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_path ON output_feedback(output_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_verdict ON output_feedback(verdict)")


def update_intelligence_score(task_id, score):
    """Store intelligence quality score (0.0-1.0) for a completed task."""
    def _do():
        with get_conn() as conn:
            conn.execute("UPDATE tasks SET intelligence_score=? WHERE id=?", (score, task_id))
    _retry_write(_do)


def get_skill_quality_stats(hours=24):
    """Return avg intelligence_score per skill over recent window."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT type as skill,
                   ROUND(AVG(intelligence_score), 3) as avg_score,
                   COUNT(*) as sample_count
            FROM tasks
            WHERE intelligence_score IS NOT NULL
              AND created_at >= datetime('now', ?)
            GROUP BY type ORDER BY avg_score DESC
        """, (f"-{hours} hours",)).fetchall()
        return [dict(r) for r in rows]


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


def queue_depth():
    """Return the number of PENDING tasks."""
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()
        return row[0] if row else 0


def claim_tasks(agent_name, n: int = 1, affinity_skills=None):
    """Claim up to N pending tasks atomically. Returns a list (may be empty)."""
    claimed = []
    for _ in range(n):
        task = claim_task(agent_name, affinity_skills=affinity_skills)
        if task is None:
            break
        claimed.append(task)
    return claimed


def claim_task(agent_name, affinity_skills=None):
    """Atomically claim the highest-priority pending task for this agent.

    Uses atomic UPDATE...WHERE(SELECT) to eliminate race conditions between
    the SELECT and UPDATE steps. If affinity_skills is provided, prefer tasks
    matching those skills first. Falls back to any unassigned task.
    """
    with get_conn() as conn:
        # Try affinity-matched tasks first (atomic claim)
        if affinity_skills:
            placeholders = ','.join('?' * len(affinity_skills))
            conn.execute(f"""
                UPDATE tasks SET status='RUNNING', assigned_to=?
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE status='PENDING' AND (assigned_to=? OR assigned_to IS NULL)
                      AND type IN ({placeholders})
                    ORDER BY priority DESC, created_at ASC LIMIT 1
                )
            """, (agent_name, agent_name, *affinity_skills))
            row = conn.execute(
                "SELECT id, type, payload_json FROM tasks WHERE status='RUNNING' AND assigned_to=? ORDER BY id DESC LIMIT 1",
                (agent_name,)
            ).fetchone()
            if row:
                return dict(row)

        # Fall back to any available task (atomic claim)
        conn.execute("""
            UPDATE tasks SET status='RUNNING', assigned_to=?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status='PENDING' AND (assigned_to=? OR assigned_to IS NULL)
                ORDER BY priority DESC, created_at ASC LIMIT 1
            )
        """, (agent_name, agent_name))

        row = conn.execute(
            "SELECT id, type, payload_json FROM tasks WHERE status='RUNNING' AND assigned_to=? ORDER BY id DESC LIMIT 1",
            (agent_name,)
        ).fetchone()
        return dict(row) if row else None


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


def post_task_chain(tasks, priority=5, trace_id=None):
    """Post a sequence of tasks where each depends on the previous.

    Args:
        tasks: list of dicts with keys: type, payload (dict), assigned_to (optional)
        priority: shared priority for all tasks
        trace_id: optional shared trace_id for the entire chain (auto-generated if None)

    Returns:
        list of task IDs in order
    """
    import uuid
    # v0.23 S3: Shared trace_id across the entire chain
    if trace_id is None:
        trace_id = str(uuid.uuid4())[:8]

    task_ids = []
    for i, t in enumerate(tasks):
        depends = [task_ids[-1]] if task_ids else None
        payload_json = json.dumps(t.get("payload", {}))
        tid = post_task(
            t["type"], payload_json,
            priority=priority,
            assigned_to=t.get("assigned_to"),
            parent_id=task_ids[0] if task_ids else None,
            depends_on=depends,
            trace_id=trace_id,
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
              classification="internal", trace_id=None):
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
        trace_id: optional distributed trace ID for request correlation.
            Auto-generated if not provided. DAG child tasks inherit parent's
            trace_id for end-to-end tracing.
    """
    import uuid

    # Validate payload is valid JSON
    if payload_json:
        try:
            json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(f"payload_json must be valid JSON, got: {repr(payload_json)[:100]}")
    # Validate parent_id references an existing task
    if parent_id:
        with get_conn() as conn:
            parent = conn.execute("SELECT id FROM tasks WHERE id=?", (parent_id,)).fetchone()
            if not parent:
                raise ValueError(f"Parent task {parent_id} does not exist")
    # Clamp priority
    priority = max(1, min(10, int(priority)))

    # v0.23 S3: For DAG child tasks, inherit parent's trace_id
    if trace_id is None and parent_id:
        try:
            with get_conn() as conn:
                parent_row = conn.execute(
                    "SELECT trace_id FROM tasks WHERE id=?", (parent_id,)
                ).fetchone()
                if parent_row and parent_row["trace_id"]:
                    trace_id = parent_row["trace_id"]
        except Exception:
            pass

    # Auto-generate trace_id if still not set
    if trace_id is None:
        trace_id = str(uuid.uuid4())[:8]

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
                                   parent_id, depends_on, conditions, classification,
                                   trace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (type_, payload_json, priority, assigned_to, status,
                  parent_id, deps_json, conds_json, classification, trace_id))
            result[0] = cur.lastrowid
    _retry_write(_do)
    return result[0]


def recover_stale_tasks(timeout_secs=900):
    """Requeue RUNNING tasks whose assigned agent has gone stale (no heartbeat).

    Also verifies the assigned agent's PID is actually dead via psutil
    before requeuing — avoids false recovery when heartbeat is merely delayed.

    Uses BEGIN EXCLUSIVE for federation-mode safety: when multiple fleet nodes share
    the same SQLite file, this prevents two nodes from both recovering the same task.
    """
    recovered = []
    def _do():
        conn = get_conn()
        try:
            if not acquire_fleet_lock(conn, timeout_ms=5000):
                raise sqlite3.OperationalError("database is locked")
            try:
                rows = conn.execute("""
                    SELECT t.id, t.assigned_to, t.type
                    FROM tasks t
                    LEFT JOIN agents a ON t.assigned_to = a.name
                    WHERE t.status = 'RUNNING'
                      AND (a.last_heartbeat IS NULL
                           OR (julianday('now') - julianday(a.last_heartbeat)) * 86400 > ?)
                """, (timeout_secs,)).fetchall()
                for r in rows:
                    # Verify the assigned agent's PID is dead before requeuing
                    try:
                        import psutil
                        agent_row = conn.execute(
                            "SELECT pid FROM agents WHERE name=?",
                            (r["assigned_to"],)
                        ).fetchone()
                        if agent_row and agent_row["pid"]:
                            if psutil.pid_exists(agent_row["pid"]):
                                continue  # Agent is alive, task isn't stale
                    except Exception:
                        pass
                    conn.execute(
                        "UPDATE tasks SET status='PENDING', assigned_to=NULL WHERE id=?",
                        (r['id'],)
                    )
                    recovered.append(dict(r))
                release_fleet_lock(conn, commit=True)
            except Exception:
                release_fleet_lock(conn, commit=False)
                raise
        finally:
            conn.close()
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


def get_waiting_human_details():
    """Return detailed HITL requests with agent info, question, task type, and age.

    Richer version of get_waiting_human_tasks() — includes age_minutes and
    waiting_since for the launcher UI's HITL panel.
    """
    with get_conn() as conn:
        tasks = conn.execute("""
            SELECT t.id, t.type, t.assigned_to, t.created_at, t.payload_json
            FROM tasks t
            WHERE t.status = 'WAITING_HUMAN'
            ORDER BY t.created_at ASC
        """).fetchall()
        result = []
        now = datetime.now(timezone.utc)
        for t in tasks:
            # Find the question from the agent's HITL request message
            question = ""
            waiting_since = t["created_at"] or ""
            msg = conn.execute("""
                SELECT body_json, created_at FROM messages
                WHERE from_agent = ? AND to_agent = 'operator'
                AND body_json LIKE '%human_input_request%'
                AND body_json LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (t["assigned_to"] or "", f'%"task_id": {t["id"]}%')).fetchone()
            if msg:
                try:
                    body = json.loads(msg["body_json"])
                    question = body.get("question", "")
                except Exception:
                    pass
                if msg["created_at"]:
                    waiting_since = msg["created_at"]

            # Calculate age in minutes
            age_minutes = 0
            if waiting_since:
                try:
                    dt = datetime.fromisoformat(waiting_since).replace(tzinfo=timezone.utc)
                    age_minutes = int((now - dt).total_seconds() / 60)
                except Exception:
                    pass

            result.append({
                "task_id": t["id"],
                "agent": t["assigned_to"] or "unknown",
                "question": question,
                "task_type": t["type"] or "",
                "waiting_since": utc_to_local(waiting_since),
                "age_minutes": age_minutes,
            })
        return result


def get_pending_advisories():
    """Return list of pending security advisories with metadata.

    Reads advisory_*.md files from fleet/knowledge/security/pending/ and
    enriches with JSON sidecar data when available.
    """
    pending_dir = Path(__file__).parent / "knowledge" / "security" / "pending"
    if not pending_dir.exists():
        return []
    result = []
    for md_path in sorted(pending_dir.glob("advisory_*.md")):
        # Extract advisory ID from filename: advisory_<id>.md
        stem = md_path.stem  # e.g. "advisory_a1b2c3d4"
        advisory_id = stem.replace("advisory_", "", 1)

        # Read title from first non-empty line
        title = ""
        try:
            for line in md_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    title = stripped
                    break
        except Exception:
            title = advisory_id

        # Check for JSON sidecar
        json_path = md_path.with_suffix(".json")
        severity = "UNKNOWN"
        if json_path.exists():
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
                severity = meta.get("severity", "UNKNOWN").upper()
            except Exception:
                pass

        # File creation/modification date
        try:
            mtime = datetime.fromtimestamp(md_path.stat().st_mtime)
            created = mtime.strftime("%Y-%m-%d")
        except Exception:
            created = ""

        result.append({
            "id": advisory_id,
            "path": str(md_path),
            "json_path": str(json_path) if json_path.exists() else None,
            "severity": severity,
            "title": title,
            "created": created,
        })
    return result


def dismiss_advisory(advisory_id):
    """Archive an advisory by moving its files to archived/ subfolder.

    Moves both the .md and .json (if present) from pending/ to pending/archived/.
    Creates the archived/ directory if it doesn't exist.

    Returns:
        dict with 'moved' count and list of 'files' moved, or 'error' string.
    """
    pending_dir = Path(__file__).parent / "knowledge" / "security" / "pending"
    archive_dir = pending_dir / "archived"

    if not pending_dir.exists():
        return {"error": "pending directory not found", "moved": 0, "files": []}

    # Find matching files
    candidates = list(pending_dir.glob(f"advisory_{advisory_id}.*"))
    if not candidates:
        return {"error": f"no advisory found with id '{advisory_id}'", "moved": 0, "files": []}

    archive_dir.mkdir(parents=True, exist_ok=True)
    moved_files = []
    for src in candidates:
        if src.is_file():
            dst = archive_dir / src.name
            try:
                src.rename(dst)
                moved_files.append(str(dst))
            except Exception as e:
                return {"error": str(e), "moved": len(moved_files), "files": moved_files}

    return {"moved": len(moved_files), "files": moved_files}


# ── Diagnostics (extracted to diagnostics.py) ────────────────────────────────
from diagnostics import quarantine_agent, clear_quarantine, get_failure_streaks, get_stuck_reviews


# ── Cost Tracking (extracted to cost_tracking.py) ────────────────────────────
from cost_tracking import log_usage, get_usage_summary, get_usage_delta


def get_model_speed_stats(hours=24):
    """Return avg/p50/p95 tokens_per_sec per model over recent window."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT model, tokens_per_sec
            FROM usage
            WHERE tokens_per_sec IS NOT NULL
              AND created_at >= datetime('now', ?)
            ORDER BY model, tokens_per_sec
        """, (f"-{hours} hours",)).fetchall()

    if not rows:
        return []

    from collections import defaultdict
    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r["tokens_per_sec"])

    results = []
    for model, speeds in sorted(by_model.items()):
        n = len(speeds)
        avg = sum(speeds) / n
        p50 = speeds[n // 2]
        p95_idx = min(int(n * 0.95), n - 1)
        p95 = speeds[p95_idx]
        results.append({
            "model": model,
            "avg_tok_sec": round(avg, 1),
            "p50_tok_sec": round(p50, 1),
            "p95_tok_sec": round(p95, 1),
            "sample_count": n,
        })
    return results


# ── Idle Evolution (extracted to idle_evolution.py) ───────────────────────────
from idle_evolution import log_idle_run, get_idle_stats, get_least_evolved_skill


# ── DAG Visualization ─────────────────────────────────────────────────────────

def delete_user_data(identifier: str, scope: str = "agent") -> dict:
    """GDPR Art. 17: Right to erasure — purge all data for an agent or task submitter.

    Args:
        identifier: agent name or submitter identifier
        scope: "agent" (purge agent data) or "all" (purge everything matching identifier)
    Returns:
        dict with counts of deleted records per table
    """
    deleted = {}
    def _do():
        with get_conn() as conn:
            # Tasks assigned to or created for this agent
            r = conn.execute("DELETE FROM tasks WHERE assigned_to=?", (identifier,))
            deleted["tasks"] = r.rowcount
            # Messages from/to
            r = conn.execute("DELETE FROM messages WHERE from_agent=? OR to_agent=?", (identifier, identifier))
            deleted["messages"] = r.rowcount
            # Notes from
            r = conn.execute("DELETE FROM notes WHERE from_agent=?", (identifier,))
            deleted["notes"] = r.rowcount
            # Usage records
            r = conn.execute("DELETE FROM usage WHERE agent=?", (identifier,))
            deleted["usage"] = r.rowcount
            # Idle runs
            r = conn.execute("DELETE FROM idle_runs WHERE agent=?", (identifier,))
            deleted["idle_runs"] = r.rowcount
            # Agent record itself
            r = conn.execute("DELETE FROM agents WHERE name=?", (identifier,))
            deleted["agents"] = r.rowcount
    _retry_write(_do)

    # Also purge knowledge files mentioning this agent
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


# ── Alert Escalation Pipeline (0.22.00) ──────────────────────────────────────

def log_alert(severity, source, message, details=None):
    """Log an alert to the audit trail for escalation.

    Args:
        severity: "info", "warning", "critical"
        source: subsystem name (e.g. "supervisor", "ollama", "thermal")
        message: human-readable alert message
        details: optional dict with structured context
    """
    def _do():
        with get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alerts ("
                "id INTEGER PRIMARY KEY, severity TEXT, source TEXT, "
                "message TEXT, details TEXT, created_at TEXT DEFAULT (datetime('now')), "
                "acknowledged_at TEXT)",
            )
            conn.execute(
                "INSERT INTO alerts (severity, source, message, details) VALUES (?, ?, ?, ?)",
                (severity, source, message, json.dumps(details) if details else None)
            )
    _retry_write(_do)


def get_alerts(hours=24, severity=None):
    """Retrieve recent alerts from the persistent alert table.

    Args:
        hours: lookback window (default 24)
        severity: optional filter ("info", "warning", "critical")

    Returns:
        list of alert dicts, newest first (max 100)
    """
    with get_conn() as conn:
        # Ensure table exists (safe for first call)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alerts ("
            "id INTEGER PRIMARY KEY, severity TEXT, source TEXT, "
            "message TEXT, details TEXT, created_at TEXT DEFAULT (datetime('now')), "
            "acknowledged_at TEXT)",
        )
        q = "SELECT * FROM alerts WHERE created_at > datetime('now', ?)"
        params = [f'-{hours} hours']
        if severity:
            q += " AND severity = ?"
            params.append(severity)
        q += " ORDER BY created_at DESC LIMIT 100"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def acknowledge_alert(alert_id):
    """Mark a persistent alert as acknowledged."""
    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE alerts SET acknowledged_at = datetime('now') WHERE id = ?",
                (alert_id,)
            )
    _retry_write(_do)


# ── Manual Mode Audit Runs ────────────────────────────────────────────────────

_AUDIT_RUNS_DDL = (
    "CREATE TABLE IF NOT EXISTS audit_runs ("
    "id            INTEGER PRIMARY KEY AUTOINCREMENT,"
    "created_at    TEXT    NOT NULL DEFAULT (datetime('now')),"
    "prompt_count  INTEGER NOT NULL DEFAULT 0,"
    "total_tokens  INTEGER NOT NULL DEFAULT 0,"
    "total_cost    REAL    NOT NULL DEFAULT 0.0,"
    "status        TEXT    NOT NULL DEFAULT 'done',"
    "prompts_json  TEXT,"
    "results_json  TEXT)"
)


def log_audit_run(prompts: list, results: list, total_tokens: int, total_cost: float) -> int:
    """Persist a completed audit run. Returns new run ID."""
    def _do():
        with get_conn() as conn:
            conn.execute(_AUDIT_RUNS_DDL)
            cur = conn.execute(
                "INSERT INTO audit_runs"
                " (prompt_count, total_tokens, total_cost, status, prompts_json, results_json)"
                " VALUES (?, ?, ?, 'done', ?, ?)",
                (len(prompts), total_tokens, round(total_cost, 6),
                 json.dumps(prompts), json.dumps(results)),
            )
            return cur.lastrowid
    return _retry_write(_do)


def get_audit_runs(limit: int = 20) -> list:
    """Return recent audit runs, newest first."""
    with get_conn() as conn:
        conn.execute(_AUDIT_RUNS_DDL)
        rows = conn.execute(
            "SELECT * FROM audit_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Human Feedback on Agent Outputs ──────────────────────────────────────────

def submit_feedback(output_path, verdict, feedback_text="", agent_name="", skill_type=""):
    """Store human feedback on an agent output.

    verdict must be 'approved', 'rejected', or 'neutral'.
    Upserts: a new review on the same path replaces the previous one.
    """
    if verdict not in ("approved", "rejected", "neutral"):
        raise ValueError(f"Invalid verdict: {verdict!r}")
    def _do():
        with get_conn() as conn:
            # Delete any prior feedback for this path, then insert fresh
            conn.execute("DELETE FROM output_feedback WHERE output_path = ?", (output_path,))
            conn.execute(
                """INSERT INTO output_feedback
                   (output_path, verdict, feedback_text, operator, agent_name, skill_type)
                   VALUES (?, ?, ?, 'human', ?, ?)""",
                (output_path, verdict, feedback_text, agent_name, skill_type),
            )
    _retry_write(_do)


def get_feedback(output_path):
    """Get feedback for a specific output. Returns dict or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM output_feedback WHERE output_path = ? ORDER BY id DESC LIMIT 1",
            (output_path,),
        ).fetchone()
        return dict(row) if row else None


def get_feedback_stats(days=7):
    """Get approval/rejection stats by agent and skill over recent window."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT agent_name, skill_type, verdict, COUNT(*) as cnt
               FROM output_feedback
               WHERE created_at >= datetime('now', ?)
               GROUP BY agent_name, skill_type, verdict
               ORDER BY cnt DESC""",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_feedback_bulk(output_paths):
    """Get feedback verdicts for multiple paths in one query.

    Returns a dict mapping output_path -> verdict string.
    Paths without feedback are omitted from the result.
    """
    if not output_paths:
        return {}
    with get_conn() as conn:
        placeholders = ",".join("?" * len(output_paths))
        rows = conn.execute(
            f"""SELECT output_path, verdict FROM output_feedback
                WHERE output_path IN ({placeholders})""",
            list(output_paths),
        ).fetchall()
        return {r["output_path"]: r["verdict"] for r in rows}
