"""Task CRUD, DAG operations, and queue management — split from db.py (TD-04)."""
import json
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def update_intelligence_score(task_id, score):
    """Store intelligence quality score (0.0-1.0) for a completed task."""
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            conn.execute("UPDATE tasks SET intelligence_score=? WHERE id=?", (score, task_id))
    _retry_write(_do)


def get_skill_quality_stats(hours=24):
    """Return avg intelligence_score per skill over recent window."""
    from db import get_conn

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


def queue_depth():
    """Return the number of PENDING tasks."""
    from db import get_conn

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
    the SELECT and UPDATE steps. After UPDATE, checks rowcount and retrieves
    the exact claimed task by its subquery criteria to avoid returning a
    different RUNNING task belonging to this agent.
    """
    from db import get_conn

    with get_conn() as conn:
        # Try affinity-matched tasks first (atomic claim)
        if affinity_skills:
            placeholders = ','.join('?' * len(affinity_skills))
            cursor = conn.execute(f"""
                UPDATE tasks SET status='RUNNING', assigned_to=?
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE status='PENDING' AND (assigned_to=? OR assigned_to IS NULL)
                      AND type IN ({placeholders})
                    ORDER BY priority DESC, created_at ASC LIMIT 1
                )
            """, (agent_name, agent_name, *affinity_skills))
            if cursor.rowcount > 0:
                row = conn.execute(
                    "SELECT id, type, payload_json FROM tasks WHERE status='RUNNING' AND assigned_to=? AND type IN ({}) ORDER BY id DESC LIMIT 1".format(placeholders),
                    (agent_name, *affinity_skills)
                ).fetchone()
                if row:
                    return dict(row)

        # Fall back to any available task (atomic claim)
        cursor = conn.execute("""
            UPDATE tasks SET status='RUNNING', assigned_to=?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status='PENDING' AND (assigned_to=? OR assigned_to IS NULL)
                ORDER BY priority DESC, created_at ASC LIMIT 1
            )
        """, (agent_name, agent_name))

        if cursor.rowcount > 0:
            row = conn.execute(
                "SELECT id, type, payload_json FROM tasks WHERE status='RUNNING' AND assigned_to=? ORDER BY id DESC LIMIT 1",
                (agent_name,)
            ).fetchone()
            return dict(row) if row else None
        return None


def complete_task(task_id, result_json):
    """Mark a task as DONE and promote any WAITING dependents."""
    from db import get_conn, _retry_write

    # Validate result is valid JSON
    if result_json:
        try:
            parsed = json.loads(result_json) if isinstance(result_json, str) else result_json
            if isinstance(parsed, dict) and parsed.get("error"):
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
    from db import get_conn, _retry_write

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
    from db import get_conn

    with get_conn() as conn:
        graph = {}
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

        all_ids = set(task_ids)
        for tid, deps in graph.items():
            for dep in deps:
                if dep not in all_ids:
                    exists = conn.execute("SELECT id FROM tasks WHERE id=?", (dep,)).fetchone()
                    if not exists:
                        return False, f"Task {tid} depends on non-existent task {dep}"

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in task_ids}

        def has_cycle(node):
            color[node] = GRAY
            for dep in graph.get(node, []):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    return True
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

    valid, msg = validate_dag(task_ids)
    if not valid:
        logging.getLogger("db").warning(f"Task chain DAG validation: {msg}")

    return task_ids


def checkpoint_chain(parent_id: int) -> dict:
    """Save checkpoint of a task chain's progress. Returns checkpoint data."""
    from db import get_conn

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
    from db import get_conn, _retry_write

    resumed = []
    def _do():
        with get_conn() as conn:
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
            _promote_waiting_tasks(conn)
    _retry_write(_do)
    return resumed


def requeue_task(task_id):
    """Put a task back into the PENDING queue (e.g. on temporary overload)."""
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='PENDING', assigned_to=NULL WHERE id=?",
                (task_id,)
            )
    _retry_write(_do)


def review_task(task_id, result_json):
    """Transition task to REVIEW status — output awaits adversarial review."""
    from db import get_conn, _retry_write

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
    from db import get_conn, _retry_write

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
    from db import get_conn, _retry_write

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

    Uses BEGIN EXCLUSIVE for federation-mode safety.
    """
    import sqlite3
    from db import get_conn, _retry_write, acquire_fleet_lock, release_fleet_lock

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
                    try:
                        import psutil
                        agent_row = conn.execute(
                            "SELECT pid FROM agents WHERE name=?",
                            (r["assigned_to"],)
                        ).fetchone()
                        if agent_row and agent_row["pid"]:
                            if psutil.pid_exists(agent_row["pid"]):
                                continue
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
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def get_pending_count():
    from db import get_conn

    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) as n FROM tasks WHERE status='PENDING'").fetchone()['n']


def get_fleet_status():
    from db import get_conn

    with get_conn() as conn:
        agents = conn.execute(
            "SELECT name, role, status, current_task_id, last_heartbeat, pid FROM agents ORDER BY name"
        ).fetchall()
        counts = {
            s: conn.execute(
                "SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,)
            ).fetchone()['n']
            for s in ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'WAITING', 'REVIEW', 'WAITING_HUMAN', 'FORWARDED')
        }
        return {'agents': [dict(a) for a in agents], 'tasks': counts}


def cancel_task(task_id):
    """Cancel a PENDING or WAITING_HUMAN task. Returns True if cancelled."""
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status, payload_json FROM tasks WHERE id=?",
                (task_id,)).fetchone()
            if not row:
                return False
            status = row["status"]
            if status not in ("PENDING", "WAITING_HUMAN"):
                return False
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            payload["_cancel_reason"] = "user_cancelled"
            conn.execute(
                "UPDATE tasks SET status='FAILED', payload_json=? WHERE id=?",
                (json.dumps(payload), task_id),
            )
            return True
    return _retry_write(_do)


def get_dag_graph(parent_id: int) -> dict:
    """Build a DAG graph structure for visualization."""
    from db import get_conn

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
