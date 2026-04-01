"""Alerts, audit runs — split from db.py (TD-04)."""
import json
import logging

log = logging.getLogger(__name__)

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


def log_alert(severity, source, message, details=None):
    """Log an alert to the audit trail for escalation.

    Args:
        severity: "info", "warning", "critical"
        source: subsystem name (e.g. "supervisor", "ollama", "thermal")
        message: human-readable alert message
        details: optional dict with structured context
    """
    from db import get_conn, _retry_write

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
    from db import get_conn

    with get_conn() as conn:
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
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE alerts SET acknowledged_at = datetime('now') WHERE id = ?",
                (alert_id,)
            )
    _retry_write(_do)


def log_audit_run(prompts: list, results: list, total_tokens: int, total_cost: float) -> int:
    """Persist a completed audit run. Returns new run ID."""
    from db import get_conn, _retry_write

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
    from db import get_conn

    with get_conn() as conn:
        conn.execute(_AUDIT_RUNS_DDL)
        rows = conn.execute(
            "SELECT * FROM audit_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
