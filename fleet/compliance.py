"""Compliance Reporting — automated SOC 2, HIPAA, SLA, and audit summary reports.

Generates compliance reports from the audit trail (audit.py, audit_log.py),
RBAC state (security.py), and SLA metrics (fleet.db tasks table).  Reports
are stored in knowledge/compliance/ and the compliance_reports DB table.

Public API:
    generate_soc2_report(period)         -> dict
    generate_hipaa_report(period)        -> dict
    generate_audit_summary(period)       -> dict
    generate_sla_report(period)          -> dict
    collect_access_logs(period)          -> list
    collect_change_logs(period)          -> list
    collect_incident_logs(period)        -> list
    collect_encryption_status()          -> dict
    export_report(report, fmt)           -> str (file path)
    schedule_reports(interval)           -> dict
    get_compliance_status()              -> dict
    init_compliance_table()              -> None
"""
import csv
import io
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("compliance")

FLEET_DIR = Path(__file__).parent
REPORT_DIR = FLEET_DIR / "knowledge" / "compliance"

# ── Lazy DB helpers (avoid circular imports) ────────────────────────────────

def _get_conn():
    import db
    return db.get_conn()


def _retry_write(fn, retries=8):
    import db
    return db._retry_write(fn, retries)


# ── Table bootstrap ─────────────────────────────────────────────────────────

_table_ready = False
_table_lock = threading.Lock()


def init_compliance_table():
    """Create the compliance_reports table if it doesn't exist."""
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return

        def _do():
            with _get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS compliance_reports (
                        id          TEXT PRIMARY KEY,
                        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                        report_type TEXT NOT NULL,
                        period_from TEXT NOT NULL,
                        period_to   TEXT NOT NULL,
                        status      TEXT NOT NULL DEFAULT 'generated',
                        summary     TEXT,
                        file_path   TEXT,
                        metadata_json TEXT
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_compliance_type "
                    "ON compliance_reports(report_type)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_compliance_created "
                    "ON compliance_reports(created_at)"
                )
        _retry_write(_do)
        _table_ready = True


# ── Period helpers ──────────────────────────────────────────────────────────

def _parse_period(period):
    """Parse a period string into (from_ts, to_ts) ISO strings.

    Accepted formats:
        "7d"  / "30d" / "90d" / "365d"  — last N days
        "monthly"                        — last calendar month
        "2026-01"                        — specific YYYY-MM
        dict with "from" and "to" keys  — explicit range
    """
    now = datetime.now(timezone.utc)
    if isinstance(period, dict):
        return period.get("from", ""), period.get("to", now.isoformat())

    if isinstance(period, str):
        if period.endswith("d"):
            try:
                days = int(period[:-1])
            except ValueError:
                days = 30
            from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            from_dt = from_dt - timedelta(days=days)
            return from_dt.isoformat(), now.isoformat()

        if period == "monthly":
            # Last full calendar month
            first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            last_month_end = first_of_month - timedelta(seconds=1)
            last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return last_month_start.isoformat(), last_month_end.isoformat()

        if len(period) == 7 and period[4] == "-":
            # YYYY-MM format
            import calendar
            year, month = int(period[:4]), int(period[5:7])
            last_day = calendar.monthrange(year, month)[1]
            from_dt = datetime(year, month, 1, tzinfo=timezone.utc)
            to_dt = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
            return from_dt.isoformat(), to_dt.isoformat()

    # Default: last 30 days
    from datetime import timedelta
    from_dt = now - timedelta(days=30)
    return from_dt.isoformat(), now.isoformat()


def _redact_secrets(text):
    """Strip anything that looks like a secret/key/token from report text."""
    import re
    if not isinstance(text, str):
        return text
    # Redact API keys, tokens, passwords
    text = re.sub(r'(?i)(key|token|password|secret|credential)["\s:=]+["\']?[\w\-\.]{8,}["\']?',
                  r'\1=[REDACTED]', text)
    # Redact long hex strings (likely keys)
    text = re.sub(r'\b[0-9a-fA-F]{32,}\b', '[REDACTED]', text)
    return text


# ── Evidence collection ─────────────────────────────────────────────────────

def collect_access_logs(period):
    """Collect who accessed what, when — from audit_log table."""
    from_ts, to_ts = _parse_period(period)
    try:
        from audit import query_audit
        rows = query_audit(
            filters={"from_ts": from_ts, "to_ts": to_ts},
            limit=1000,
        )
        return [
            {
                "timestamp": r.get("timestamp"),
                "actor": r.get("actor"),
                "action": r.get("action"),
                "resource": r.get("resource"),
                "role": r.get("role"),
                "ip_address": r.get("ip_address"),
            }
            for r in rows
        ]
    except Exception:
        log.warning("collect_access_logs failed", exc_info=True)
        return []


def collect_change_logs(period):
    """Collect config changes and skill deployments."""
    from_ts, to_ts = _parse_period(period)
    try:
        from audit import query_audit
        change_actions = ("config.change", "skill.deploy", "skill.rollback",
                          "fleet.start", "fleet.stop", "config.update")
        all_changes = []
        for action in change_actions:
            rows = query_audit(
                filters={"action": action, "from_ts": from_ts, "to_ts": to_ts},
                limit=500,
            )
            all_changes.extend([
                {
                    "timestamp": r.get("timestamp"),
                    "actor": r.get("actor"),
                    "action": r.get("action"),
                    "resource": r.get("resource"),
                    "detail": _redact_secrets(r.get("detail", "")),
                }
                for r in rows
            ])
        all_changes.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return all_changes[:1000]
    except Exception:
        log.warning("collect_change_logs failed", exc_info=True)
        return []


def collect_incident_logs(period):
    """Collect errors, circuit breaker trips, and rollbacks."""
    from_ts, to_ts = _parse_period(period)
    incidents = []
    # From DB-backed audit
    try:
        from audit import query_audit
        for action in ("error", "circuit_breaker.trip", "skill.rollback",
                       "quarantine", "budget_exceeded", "dlp_alert"):
            rows = query_audit(
                filters={"action": action, "from_ts": from_ts, "to_ts": to_ts},
                limit=200,
            )
            incidents.extend([
                {
                    "timestamp": r.get("timestamp"),
                    "type": r.get("action"),
                    "source": r.get("actor"),
                    "detail": _redact_secrets(r.get("detail", "")),
                    "resource": r.get("resource"),
                }
                for r in rows
            ])
    except Exception:
        log.warning("collect_incident_logs: DB audit query failed", exc_info=True)

    # From file-based audit_log (HMAC-signed events)
    try:
        from audit_log import read_events
        events = read_events(last_n=500, since=from_ts)
        for e in events:
            if e.get("severity") in ("error", "critical"):
                incidents.append({
                    "timestamp": e.get("timestamp"),
                    "type": e.get("type"),
                    "source": e.get("source"),
                    "detail": _redact_secrets(json.dumps(e.get("data", {}))),
                    "severity": e.get("severity"),
                })
    except Exception:
        log.warning("collect_incident_logs: file audit query failed", exc_info=True)

    incidents.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return incidents[:1000]


def collect_encryption_status():
    """Check which tenants have encryption enabled."""
    status = {
        "db_encryption": False,
        "tls_enabled": False,
        "fleet_tls_enabled": False,
        "tenants": {},
    }
    # Check SQLCipher
    try:
        import sqlcipher3  # noqa: F401
        import os
        status["db_encryption"] = bool(os.environ.get("BIGED_DB_KEY"))
    except ImportError:
        status["db_encryption"] = False

    # Check TLS cert
    try:
        from security import ensure_tls_cert
        cert, key = ensure_tls_cert()
        status["tls_enabled"] = cert is not None
    except Exception:
        log.warning("collect_encryption_status: TLS check failed", exc_info=True)

    # Check fleet mTLS
    try:
        from fleet_tls import is_tls_enabled
        status["fleet_tls_enabled"] = is_tls_enabled()
    except Exception:
        status["fleet_tls_enabled"] = False

    # Multi-tenant encryption check
    try:
        from config import load_config
        cfg = load_config()
        if cfg.get("enterprise", {}).get("multi_tenant"):
            tenant_dir = FLEET_DIR / "tenants"
            if tenant_dir.exists():
                for td in tenant_dir.iterdir():
                    if td.is_dir():
                        status["tenants"][td.name] = {
                            "db_exists": (td / "fleet.db").exists(),
                            "encrypted": status["db_encryption"],
                        }
    except Exception:
        log.warning("collect_encryption_status: tenant check failed", exc_info=True)

    return status


# ── Report generators ───────────────────────────────────────────────────────

def generate_soc2_report(period="30d"):
    """SOC 2 Type II compliance report.

    Covers: access controls, change management, system monitoring,
    incident response, data encryption status.
    """
    from_ts, to_ts = _parse_period(period)
    report_id = str(uuid.uuid4())[:12]

    access_logs = collect_access_logs(period)
    change_logs = collect_change_logs(period)
    incident_logs = collect_incident_logs(period)
    encryption = collect_encryption_status()

    # RBAC summary
    try:
        from security import PERMISSIONS
        rbac_summary = {role: sorted(perms) for role, perms in PERMISSIONS.items()}
    except Exception:
        rbac_summary = {}

    # Unique actors
    actors = set(e.get("actor", "") for e in access_logs if e.get("actor"))

    report = {
        "id": report_id,
        "type": "soc2",
        "title": "SOC 2 Type II Compliance Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": from_ts, "to": to_ts},
        "sections": {
            "access_controls": {
                "description": "Access control policies and enforcement",
                "rbac_roles": rbac_summary,
                "unique_actors": len(actors),
                "total_access_events": len(access_logs),
                "failed_auth_attempts": sum(
                    1 for e in access_logs if "denied" in (e.get("action") or "")
                    or "unauthorized" in (e.get("action") or "")
                ),
            },
            "change_management": {
                "description": "Configuration and deployment change tracking",
                "total_changes": len(change_logs),
                "config_changes": sum(
                    1 for e in change_logs if "config" in (e.get("action") or "")
                ),
                "skill_deployments": sum(
                    1 for e in change_logs if "deploy" in (e.get("action") or "")
                ),
                "rollbacks": sum(
                    1 for e in change_logs if "rollback" in (e.get("action") or "")
                ),
            },
            "system_monitoring": {
                "description": "System health and availability monitoring",
                "incident_count": len(incident_logs),
                "critical_incidents": sum(
                    1 for e in incident_logs if e.get("severity") == "critical"
                ),
                "circuit_breaker_trips": sum(
                    1 for e in incident_logs
                    if "circuit_breaker" in (e.get("type") or "")
                ),
            },
            "incident_response": {
                "description": "Incident detection and response log",
                "incidents": incident_logs[:50],
            },
            "data_encryption": {
                "description": "Encryption status for data at rest and in transit",
                "db_encrypted": encryption.get("db_encryption", False),
                "tls_enabled": encryption.get("tls_enabled", False),
                "fleet_mtls": encryption.get("fleet_tls_enabled", False),
                "tenant_encryption": encryption.get("tenants", {}),
            },
        },
    }

    _save_report(report)
    return report


def generate_hipaa_report(period="30d"):
    """HIPAA compliance report.

    Covers: PHI access audit, encryption status, breach log,
    minimum necessary access checks.
    """
    from_ts, to_ts = _parse_period(period)
    report_id = str(uuid.uuid4())[:12]

    access_logs = collect_access_logs(period)
    incident_logs = collect_incident_logs(period)
    encryption = collect_encryption_status()

    # DITL (Doctor in the Loop) config
    ditl_config = {}
    try:
        from config import load_config
        cfg = load_config()
        ditl = cfg.get("ditl", {})
        ditl_config = {
            "enabled": ditl.get("enabled", False),
            "compliance_level": ditl.get("compliance_level", "none"),
            "force_local_phi": ditl.get("force_local_phi", True),
            "data_retention_days": ditl.get("data_retention_days", 2555),
            "auto_purge": ditl.get("auto_purge", True),
            "require_baa": ditl.get("require_baa", True),
            "audit_all_phi_access": ditl.get("audit_all_phi_access", True),
            "deidentification_method": ditl.get("deidentification", {}).get("method", "safe_harbor"),
        }
    except Exception:
        log.warning("generate_hipaa_report: DITL config read failed", exc_info=True)

    # PHI access events (filter by DITL-related actions)
    phi_access = [
        e for e in access_logs
        if any(kw in (e.get("action") or "") for kw in ("phi", "ditl", "patient", "health"))
    ]

    # Breach-like incidents (DLP alerts, quarantines)
    breaches = [
        e for e in incident_logs
        if any(kw in (e.get("type") or "") for kw in ("dlp_alert", "quarantine", "breach"))
    ]

    report = {
        "id": report_id,
        "type": "hipaa",
        "title": "HIPAA Compliance Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": from_ts, "to": to_ts},
        "sections": {
            "phi_access_audit": {
                "description": "Protected Health Information access log",
                "total_phi_access_events": len(phi_access),
                "unique_phi_actors": len(set(e.get("actor", "") for e in phi_access)),
                "events": phi_access[:100],
            },
            "encryption_status": {
                "description": "Encryption for PHI at rest and in transit",
                "db_encrypted": encryption.get("db_encryption", False),
                "tls_enabled": encryption.get("tls_enabled", False),
                "fleet_mtls": encryption.get("fleet_tls_enabled", False),
                "compliant": encryption.get("db_encryption", False) and encryption.get("tls_enabled", False),
            },
            "breach_log": {
                "description": "Security breach and DLP alert log",
                "total_breaches": len(breaches),
                "incidents": breaches[:50],
            },
            "minimum_necessary": {
                "description": "Minimum necessary access checks",
                "ditl_config": ditl_config,
                "force_local_phi": ditl_config.get("force_local_phi", True),
                "deidentification_active": ditl_config.get("deidentification_method") == "safe_harbor",
            },
            "baa_status": {
                "description": "Business Associate Agreement status",
                "baa_required": ditl_config.get("require_baa", True),
            },
        },
    }

    _save_report(report)
    return report


def generate_audit_summary(period="30d"):
    """General audit summary report.

    Covers: user actions, permission changes, failed auth, data exports.
    """
    from_ts, to_ts = _parse_period(period)
    report_id = str(uuid.uuid4())[:12]

    access_logs = collect_access_logs(period)

    # Categorize events
    actions_by_type = {}
    actors_by_count = {}
    failed_auth = []
    for e in access_logs:
        action = e.get("action", "unknown")
        actor = e.get("actor", "unknown")
        actions_by_type[action] = actions_by_type.get(action, 0) + 1
        actors_by_count[actor] = actors_by_count.get(actor, 0) + 1
        if "denied" in action or "unauthorized" in action or "auth.fail" in action:
            failed_auth.append(e)

    # Permission change events
    change_logs = collect_change_logs(period)
    perm_changes = [
        e for e in change_logs
        if any(kw in (e.get("action") or "")
               for kw in ("permission", "role", "token", "rbac"))
    ]

    # Data export events
    export_events = [
        e for e in access_logs
        if "export" in (e.get("action") or "")
    ]

    report = {
        "id": report_id,
        "type": "audit_summary",
        "title": "Audit Summary Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": from_ts, "to": to_ts},
        "sections": {
            "overview": {
                "total_events": len(access_logs),
                "unique_actors": len(actors_by_count),
                "unique_action_types": len(actions_by_type),
            },
            "actions_by_type": dict(
                sorted(actions_by_type.items(), key=lambda x: x[1], reverse=True)[:50]
            ),
            "top_actors": dict(
                sorted(actors_by_count.items(), key=lambda x: x[1], reverse=True)[:20]
            ),
            "failed_auth_attempts": {
                "count": len(failed_auth),
                "events": failed_auth[:50],
            },
            "permission_changes": {
                "count": len(perm_changes),
                "events": perm_changes[:50],
            },
            "data_exports": {
                "count": len(export_events),
                "events": export_events[:50],
            },
        },
    }

    _save_report(report)
    return report


def generate_sla_report(period="30d"):
    """SLA compliance report — uptime, task completion times, breach count."""
    from_ts, to_ts = _parse_period(period)
    report_id = str(uuid.uuid4())[:12]

    # Query task completion metrics from DB
    skill_metrics = []
    overall = {"total": 0, "done": 0, "failed": 0}
    try:
        conn = _get_conn()
        rows = conn.execute("""
            SELECT type as skill,
                   COUNT(*) as tasks,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                   SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed,
                   AVG(CASE WHEN status='DONE' THEN
                       CAST((julianday(created_at) - julianday(created_at)) * 86400
                            AS INTEGER) END) as avg_completion_secs
            FROM tasks
            WHERE created_at >= ? AND created_at <= ?
            GROUP BY type
            ORDER BY tasks DESC
        """, (from_ts, to_ts)).fetchall()
        for r in rows:
            d = dict(r)
            d["success_rate"] = round(
                (d["done"] or 0) / max(d["tasks"], 1) * 100, 1
            )
            skill_metrics.append(d)

        ov = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                   SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
            FROM tasks
            WHERE created_at >= ? AND created_at <= ?
        """, (from_ts, to_ts)).fetchone()
        if ov:
            overall = {"total": ov["total"], "done": ov["done"] or 0, "failed": ov["failed"] or 0}
        conn.close()
    except Exception:
        log.warning("generate_sla_report: task query failed", exc_info=True)

    # SLA targets (configurable defaults)
    sla_targets = {
        "uptime_pct": 99.0,
        "max_task_completion_secs": 300,
        "max_failure_rate_pct": 10.0,
    }

    success_rate = round(overall["done"] / max(overall["total"], 1) * 100, 1)
    failure_rate = round(overall["failed"] / max(overall["total"], 1) * 100, 1)

    # Count SLA breaches (skills with >10% failure rate)
    breach_count = sum(
        1 for m in skill_metrics if m.get("success_rate", 100) < (100 - sla_targets["max_failure_rate_pct"])
    )

    report = {
        "id": report_id,
        "type": "sla",
        "title": "SLA Compliance Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": from_ts, "to": to_ts},
        "sections": {
            "overall": {
                "total_tasks": overall["total"],
                "completed": overall["done"],
                "failed": overall["failed"],
                "success_rate_pct": success_rate,
                "failure_rate_pct": failure_rate,
                "sla_target_success_pct": 100 - sla_targets["max_failure_rate_pct"],
                "sla_met": failure_rate <= sla_targets["max_failure_rate_pct"],
            },
            "by_skill": skill_metrics[:50],
            "sla_breaches": {
                "count": breach_count,
                "skills_below_target": [
                    m["skill"] for m in skill_metrics
                    if m.get("success_rate", 100) < (100 - sla_targets["max_failure_rate_pct"])
                ],
            },
            "sla_targets": sla_targets,
        },
    }

    _save_report(report)
    return report


# ── Report persistence ──────────────────────────────────────────────────────

def _save_report(report):
    """Save report to DB and filesystem."""
    init_compliance_table()
    report_id = report["id"]
    report_type = report["type"]
    period = report.get("period", {})

    # Save JSON file
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{report_type}_{ts}_{report_id}.json"
    file_path = REPORT_DIR / filename
    try:
        file_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    except Exception:
        log.warning("Failed to write compliance report to disk", exc_info=True)

    # Compute summary line
    sections = report.get("sections", {})
    summary_parts = []
    if "overall" in sections:
        ov = sections["overall"]
        if "success_rate_pct" in ov:
            summary_parts.append(f"success={ov['success_rate_pct']}%")
        if "total_tasks" in ov:
            summary_parts.append(f"tasks={ov['total_tasks']}")
    if "access_controls" in sections:
        ac = sections["access_controls"]
        summary_parts.append(f"events={ac.get('total_access_events', 0)}")
    summary = "; ".join(summary_parts) if summary_parts else report_type

    # Save to DB
    def _do():
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO compliance_reports
                   (id, report_type, period_from, period_to, summary, file_path)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (report_id, report_type,
                 period.get("from", ""), period.get("to", ""),
                 summary, str(file_path)),
            )
    try:
        _retry_write(_do)
    except Exception:
        log.warning("Failed to save compliance report to DB", exc_info=True)

    # Audit log the generation
    try:
        from audit import log_audit
        log_audit(
            actor="system",
            action="compliance.generate",
            resource=f"report:{report_id}",
            detail=f"Generated {report_type} report",
            metadata={"report_type": report_type, "period": period},
        )
    except Exception:
        log.warning("Failed to audit-log compliance report generation", exc_info=True)


# ── Report formatting & export ──────────────────────────────────────────────

def export_report(report, fmt="json"):
    """Export a report dict to a file in the given format.

    Args:
        report: report dict (from generate_* functions)
        fmt: "json", "csv", or "md"

    Returns:
        str — file path of the exported report
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_type = report.get("type", "report")
    report_id = report.get("id", "unknown")

    if fmt == "csv":
        filename = f"{report_type}_{ts}_{report_id}.csv"
        file_path = REPORT_DIR / filename
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["section", "key", "value"])
        for section_name, section_data in report.get("sections", {}).items():
            if isinstance(section_data, dict):
                for k, v in section_data.items():
                    if isinstance(v, (list, dict)):
                        writer.writerow([section_name, k, json.dumps(v, default=str)])
                    else:
                        writer.writerow([section_name, k, str(v)])
        file_path.write_text(buf.getvalue(), encoding="utf-8")
        return str(file_path)

    if fmt == "md":
        filename = f"{report_type}_{ts}_{report_id}.md"
        file_path = REPORT_DIR / filename
        lines = [f"# {report.get('title', report_type)}", ""]
        lines.append(f"**Generated:** {report.get('generated_at', '')}")
        period = report.get("period", {})
        lines.append(f"**Period:** {period.get('from', '')} to {period.get('to', '')}")
        lines.append("")
        for section_name, section_data in report.get("sections", {}).items():
            lines.append(f"## {section_name.replace('_', ' ').title()}")
            lines.append("")
            if isinstance(section_data, dict):
                for k, v in section_data.items():
                    if isinstance(v, list):
                        lines.append(f"- **{k}:** {len(v)} items")
                    elif isinstance(v, dict):
                        lines.append(f"- **{k}:**")
                        for sk, sv in v.items():
                            lines.append(f"  - {sk}: {sv}")
                    else:
                        lines.append(f"- **{k}:** {v}")
            lines.append("")
        file_path.write_text("\n".join(lines), encoding="utf-8")
        return str(file_path)

    # Default: JSON
    filename = f"{report_type}_{ts}_{report_id}.json"
    file_path = REPORT_DIR / filename
    file_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return str(file_path)


# ── List / fetch stored reports ─────────────────────────────────────────────

def list_reports(report_type=None, limit=50, offset=0):
    """List stored compliance reports from DB."""
    init_compliance_table()
    try:
        conn = _get_conn()
        if report_type:
            rows = conn.execute(
                "SELECT * FROM compliance_reports WHERE report_type = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (report_type, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM compliance_reports "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("list_reports failed", exc_info=True)
        return []


def get_report(report_id):
    """Fetch a specific report by ID — returns the full JSON from disk."""
    init_compliance_table()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM compliance_reports WHERE id = ?", (report_id,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        file_path = d.get("file_path")
        if file_path and Path(file_path).exists():
            d["report"] = json.loads(Path(file_path).read_text(encoding="utf-8"))
        return d
    except Exception:
        log.warning("get_report failed for %s", report_id, exc_info=True)
        return None


# ── Compliance posture ──────────────────────────────────────────────────────

def get_compliance_status():
    """Overall compliance posture — green / yellow / red.

    Checks:
        - Audit logging active
        - RBAC configured
        - TLS enabled
        - Recent reports generated
        - No critical incidents in last 24h
    """
    checks = {}

    # Audit logging active
    try:
        from audit import query_audit
        recent = query_audit(limit=1)
        checks["audit_logging"] = len(recent) > 0
    except Exception:
        checks["audit_logging"] = False

    # RBAC configured
    try:
        from config import load_config
        cfg = load_config()
        sec = cfg.get("security", {})
        checks["rbac_configured"] = bool(
            sec.get("admin_token") or sec.get("operator_token")
        )
    except Exception:
        checks["rbac_configured"] = False

    # TLS
    encryption = collect_encryption_status()
    checks["tls_enabled"] = encryption.get("tls_enabled", False)
    checks["db_encrypted"] = encryption.get("db_encryption", False)

    # Recent reports
    try:
        recent_reports = list_reports(limit=5)
        if recent_reports:
            latest = recent_reports[0].get("created_at", "")
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
            checks["recent_reports"] = latest >= cutoff
        else:
            checks["recent_reports"] = False
    except Exception:
        checks["recent_reports"] = False

    # Critical incidents in last 24h
    try:
        incidents = collect_incident_logs("1d")
        critical = [i for i in incidents if i.get("severity") == "critical"]
        checks["no_critical_incidents"] = len(critical) == 0
    except Exception:
        checks["no_critical_incidents"] = True  # assume ok on failure

    # Determine overall posture
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    if passed == total:
        posture = "green"
    elif passed >= total - 2:
        posture = "yellow"
    else:
        posture = "red"

    return {
        "posture": posture,
        "checks": checks,
        "passed": passed,
        "total": total,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Scheduled generation ────────────────────────────────────────────────────

_schedule_thread = None
_schedule_lock = threading.Lock()


def schedule_reports(interval="monthly"):
    """Start a background thread that auto-generates reports on schedule.

    Args:
        interval: "monthly", "weekly", or "daily"

    Returns:
        dict with schedule status
    """
    global _schedule_thread
    with _schedule_lock:
        if _schedule_thread and _schedule_thread.is_alive():
            return {"status": "already_running", "interval": interval}

    interval_map = {
        "daily": 86400,
        "weekly": 604800,
        "monthly": 2592000,  # ~30 days
    }
    sleep_secs = interval_map.get(interval, 2592000)

    # Read config to determine which reports to generate
    report_types = ["soc2", "audit_summary", "sla"]
    try:
        from config import load_config
        cfg = load_config()
        comp_cfg = cfg.get("compliance", {})
        if not comp_cfg.get("enabled", False):
            return {"status": "disabled", "detail": "compliance.enabled=false in fleet.toml"}
        if not comp_cfg.get("auto_generate", True):
            return {"status": "disabled", "detail": "compliance.auto_generate=false"}
        report_types = comp_cfg.get("report_types", report_types)
    except Exception:
        log.warning("schedule_reports: config load failed, using defaults", exc_info=True)

    generators = {
        "soc2": generate_soc2_report,
        "hipaa": generate_hipaa_report,
        "audit_summary": generate_audit_summary,
        "sla": generate_sla_report,
    }

    def _run():
        while True:
            time.sleep(sleep_secs)
            for rt in report_types:
                gen = generators.get(rt)
                if gen:
                    try:
                        gen(interval)
                        log.info("Scheduled compliance report generated: %s", rt)
                    except Exception:
                        log.warning("Scheduled report generation failed: %s", rt, exc_info=True)

    _schedule_thread = threading.Thread(target=_run, daemon=True, name="compliance-scheduler")
    _schedule_thread.start()
    return {"status": "started", "interval": interval, "report_types": report_types}


# ── Flask Blueprint for dashboard endpoints ─────────────────────────────────

def create_compliance_blueprint(require_role_fn):
    """Create Flask blueprint with compliance endpoints.

    Args:
        require_role_fn: decorator function for RBAC (e.g., dashboard._require_role)

    Returns:
        Flask Blueprint
    """
    from flask import Blueprint, jsonify, request, Response

    bp = Blueprint("compliance", __name__)

    @bp.route("/api/compliance/reports")
    def api_compliance_reports():
        """List available compliance reports."""
        try:
            report_type = request.args.get("type")
            limit = int(request.args.get("limit", 50))
            offset = int(request.args.get("offset", 0))
            reports = list_reports(report_type=report_type, limit=limit, offset=offset)
            return jsonify({"reports": reports, "total": len(reports)})
        except Exception as e:
            log.warning("api_compliance_reports failed", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/compliance/generate", methods=["POST"])
    @require_role_fn("operator")
    def api_compliance_generate():
        """Trigger compliance report generation.

        JSON body: { "type": "soc2"|"hipaa"|"audit_summary"|"sla", "period": "30d" }
        """
        try:
            body = request.get_json(silent=True) or {}
            report_type = body.get("type", "audit_summary")
            period = body.get("period", "30d")

            generators = {
                "soc2": generate_soc2_report,
                "hipaa": generate_hipaa_report,
                "audit_summary": generate_audit_summary,
                "sla": generate_sla_report,
            }
            gen = generators.get(report_type)
            if not gen:
                return jsonify({"error": f"Unknown report type: {report_type}"}), 400

            report = gen(period)
            return jsonify({"status": "generated", "report": report})
        except Exception as e:
            log.warning("api_compliance_generate failed", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/compliance/reports/<report_id>")
    def api_compliance_report_detail(report_id):
        """Download a specific compliance report."""
        try:
            report_data = get_report(report_id)
            if not report_data:
                return jsonify({"error": "Report not found"}), 404

            fmt = request.args.get("fmt", "json")
            if fmt == "json":
                return jsonify(report_data)

            # Re-export in requested format
            report_content = report_data.get("report", report_data)
            file_path = export_report(report_content, fmt=fmt)
            content = Path(file_path).read_text(encoding="utf-8")
            mime = "text/csv" if fmt == "csv" else "text/markdown" if fmt == "md" else "application/json"
            return Response(
                content, mimetype=mime,
                headers={"Content-Disposition": f"attachment; filename={Path(file_path).name}"},
            )
        except Exception as e:
            log.warning("api_compliance_report_detail failed", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/compliance/status")
    def api_compliance_status():
        """Overall compliance posture (green/yellow/red)."""
        try:
            status = get_compliance_status()
            return jsonify(status)
        except Exception as e:
            log.warning("api_compliance_status failed", exc_info=True)
            return jsonify({"error": str(e)}), 500

    return bp
