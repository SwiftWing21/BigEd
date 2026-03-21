"""Enhanced audit logging — structured event trail backed by SQLite (fleet.db).

Uses an async queue + background flush thread (same pattern as cost_tracking.py)
for non-blocking writes.  The audit_log table lives in fleet.db alongside tasks,
agents, and usage.

Public API:
    log_audit(actor, action, resource, ...)   # non-blocking enqueue
    query_audit(filters, limit, offset)       # paginated query
    export_audit(fmt, filters)                # JSON or CSV export
    purge_audit(older_than_days)              # retention enforcement
    init_audit_table()                        # CREATE TABLE IF NOT EXISTS
"""
import csv
import io
import json
import queue
import threading
import time
from datetime import datetime, timezone

# ── Async write queue (mirrors cost_tracking.py pattern) ─────────────────────

_audit_queue: queue.Queue = queue.Queue()
_audit_thread_started = False
_audit_thread_lock = threading.Lock()


def _get_conn():
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db.get_conn()


def _retry_write(fn, retries=8):
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db._retry_write(fn, retries)


# ── Table bootstrap ──────────────────────────────────────────────────────────

_table_ready = False
_table_lock = threading.Lock()


def init_audit_table():
    """Create the audit_log table if it doesn't exist.

    Safe to call repeatedly — uses IF NOT EXISTS and caches the result.
    """
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return

        def _do():
            with _get_conn() as conn:
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
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)"
                )
        _retry_write(_do)
        _table_ready = True


# ── Background flush thread ──────────────────────────────────────────────────

def _start_audit_logger():
    global _audit_thread_started
    with _audit_thread_lock:
        if _audit_thread_started:
            return
        _audit_thread_started = True

    def _flush_loop():
        while True:
            batch = []
            try:
                item = _audit_queue.get(timeout=5)
                batch.append(item)
                # Drain up to 20 items per batch
                while len(batch) < 20:
                    try:
                        batch.append(_audit_queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                continue
            for entry in batch:
                try:
                    _log_audit_sync(**entry)
                except Exception:
                    pass  # Never crash the flush thread

    t = threading.Thread(target=_flush_loop, daemon=True, name="audit-flush")
    t.start()


def flush_audit_queue(timeout=5):
    """Block until the audit queue is empty (for testing / shutdown)."""
    deadline = time.time() + timeout
    while not _audit_queue.empty() and time.time() < deadline:
        time.sleep(0.1)


# ── Synchronous insert (called from background thread) ───────────────────────

def _log_audit_sync(actor, action, resource=None, detail=None,
                    cost_usd=0.0, metadata=None, ip_address=None, role=None):
    """Insert a single audit row — called from the flush thread."""
    init_audit_table()
    metadata_json = json.dumps(metadata) if metadata else None

    def _do():
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, actor, action, resource, detail, cost_usd,
                    metadata_json, ip_address, role)
                   VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)""",
                (actor, action, resource, detail, cost_usd,
                 metadata_json, ip_address, role),
            )
    _retry_write(_do)


# ── Public API ───────────────────────────────────────────────────────────────

def log_audit(actor, action, resource=None, detail=None,
              cost_usd=0.0, metadata=None, ip_address=None, role=None):
    """Non-blocking audit logging — buffers entries and flushes in background.

    Args:
        actor:      Who performed the action (e.g., "operator", "coder_1", "system")
        action:     What happened (e.g., "fleet.stop", "task.dispatch", "config.change")
        resource:   What was acted upon (e.g., "worker:coder_1", "task:42", "/api/audit")
        detail:     Human-readable detail string
        cost_usd:   Associated cost (0.0 if none)
        metadata:   Arbitrary dict — serialized to JSON
        ip_address: Request origin IP (when available)
        role:       Caller's auth role (admin/operator/viewer)
    """
    _start_audit_logger()
    _audit_queue.put({
        "actor": str(actor),
        "action": str(action),
        "resource": str(resource) if resource else None,
        "detail": str(detail) if detail else None,
        "cost_usd": float(cost_usd) if cost_usd else 0.0,
        "metadata": metadata,
        "ip_address": str(ip_address) if ip_address else None,
        "role": str(role) if role else None,
    })


def query_audit(filters=None, limit=100, offset=0):
    """Query audit_log with optional filters.

    Args:
        filters: dict with optional keys:
            actor   — exact match
            action  — exact match
            resource — LIKE match (contains)
            from_ts — events >= this ISO timestamp
            to_ts   — events <= this ISO timestamp
            role    — exact match
        limit:  max rows (capped at 1000)
        offset: pagination offset

    Returns:
        list of dicts with all audit_log columns + parsed metadata.
    """
    init_audit_table()
    filters = filters or {}
    limit = min(int(limit), 1000)
    offset = max(int(offset), 0)

    clauses = []
    params = []

    if filters.get("actor"):
        clauses.append("actor = ?")
        params.append(filters["actor"])
    if filters.get("action"):
        clauses.append("action = ?")
        params.append(filters["action"])
    if filters.get("resource"):
        clauses.append("resource LIKE ?")
        params.append(f"%{filters['resource']}%")
    if filters.get("from_ts"):
        clauses.append("timestamp >= ?")
        params.append(filters["from_ts"])
    if filters.get("to_ts"):
        clauses.append("timestamp <= ?")
        params.append(filters["to_ts"])
    if filters.get("role"):
        clauses.append("role = ?")
        params.append(filters["role"])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT * FROM audit_log{where}"
        f" ORDER BY id DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            # Parse metadata_json for convenience
            if d.get("metadata_json"):
                try:
                    d["metadata"] = json.loads(d["metadata_json"])
                except (json.JSONDecodeError, TypeError):
                    d["metadata"] = None
            else:
                d["metadata"] = None
            results.append(d)
        return results


def count_audit(filters=None):
    """Return total count of matching audit rows (for pagination headers)."""
    init_audit_table()
    filters = filters or {}

    clauses = []
    params = []
    if filters.get("actor"):
        clauses.append("actor = ?")
        params.append(filters["actor"])
    if filters.get("action"):
        clauses.append("action = ?")
        params.append(filters["action"])
    if filters.get("resource"):
        clauses.append("resource LIKE ?")
        params.append(f"%{filters['resource']}%")
    if filters.get("from_ts"):
        clauses.append("timestamp >= ?")
        params.append(filters["from_ts"])
    if filters.get("to_ts"):
        clauses.append("timestamp <= ?")
        params.append(filters["to_ts"])
    if filters.get("role"):
        clauses.append("role = ?")
        params.append(filters["role"])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT COUNT(*) as n FROM audit_log{where}"

    with _get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
        return row["n"] if row else 0


def export_audit(fmt="json", filters=None):
    """Export audit log as JSON string or CSV string.

    Args:
        fmt:     "json" or "csv"
        filters: same as query_audit filters (no limit applied — exports all matches)

    Returns:
        (content_string, content_type, filename)
    """
    init_audit_table()
    filters = filters or {}

    clauses = []
    params = []
    if filters.get("actor"):
        clauses.append("actor = ?")
        params.append(filters["actor"])
    if filters.get("action"):
        clauses.append("action = ?")
        params.append(filters["action"])
    if filters.get("resource"):
        clauses.append("resource LIKE ?")
        params.append(f"%{filters['resource']}%")
    if filters.get("from_ts"):
        clauses.append("timestamp >= ?")
        params.append(filters["from_ts"])
    if filters.get("to_ts"):
        clauses.append("timestamp <= ?")
        params.append(filters["to_ts"])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT 10000"

    with _get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if fmt == "csv":
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return buf.getvalue(), "text/csv", f"audit_export_{ts}.csv"

    # Default: JSON
    return json.dumps(rows, indent=2, default=str), "application/json", f"audit_export_{ts}.json"


def purge_audit(older_than_days=365):
    """Delete audit entries older than N days.  Returns count of purged rows.

    Args:
        older_than_days: retention window (default 365)

    Returns:
        dict with keys: purged (int), remaining (int)
    """
    init_audit_table()
    older_than_days = max(int(older_than_days), 1)  # Minimum 1 day

    purged = 0

    def _do():
        nonlocal purged
        with _get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM audit_log WHERE timestamp < datetime('now', ?)",
                (f"-{older_than_days} days",),
            )
            purged = cur.rowcount

    _retry_write(_do)

    remaining = 0
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as n FROM audit_log").fetchone()
            remaining = row["n"] if row else 0
    except Exception:
        pass

    return {"purged": purged, "remaining": remaining, "retention_days": older_than_days}


def get_audit_actors():
    """Return distinct actor values for filter dropdowns."""
    init_audit_table()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT actor FROM audit_log ORDER BY actor"
        ).fetchall()
        return [r["actor"] for r in rows]


def get_audit_actions():
    """Return distinct action values for filter dropdowns."""
    init_audit_table()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action"
        ).fetchall()
        return [r["action"] for r in rows]
