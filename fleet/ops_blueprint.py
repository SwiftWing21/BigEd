"""Operational endpoints — cache, audit, GDPR, filesystem audit, event triggers.

Extracted from dashboard.py (Phase 5 of dashboard decomposition).
"""
import json
import logging
import re

from flask import Blueprint, Response, jsonify, request

from dashboard_utils import (
    FLEET_DIR, _load_config, get_conn, query,
    _get_request_role, _require_role,
    _check_rate_limit, _safe_error,
    _broadcast_sse,
)

log = logging.getLogger("dashboard.ops")

ops_bp = Blueprint("ops", __name__)


# ── Cache Management ────────────────────────────────────────────────────────

@ops_bp.route("/api/cache/stats")
def api_cache_stats():
    """List all registered caches with age, TTL, and staleness."""
    try:
        from cache_manager import get_cache_stats, get_cache_count
        stats = get_cache_stats()
        return jsonify({
            "caches": stats,
            "total": get_cache_count(),
            "stale": sum(1 for s in stats if s["is_stale"]),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@ops_bp.route("/api/cache/invalidate", methods=["POST"])
def api_cache_invalidate():
    """Invalidate all caches, or a specific one via ?name=X or JSON body."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from cache_manager import invalidate, invalidate_all
        # Check for specific cache name in query param or JSON body
        name = request.args.get("name")
        if not name:
            body = request.get_json(silent=True) or {}
            name = body.get("name")

        if name:
            ok = invalidate(name)
            if not ok:
                return jsonify({"error": f"Unknown cache: {name}"}), 404
            return jsonify({"invalidated": name, "success": True})
        else:
            count = invalidate_all()
            return jsonify({"invalidated": "all", "count": count, "success": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@ops_bp.route("/api/cache/invalidate/<name>", methods=["POST"])
def api_cache_invalidate_named(name):
    """Invalidate a specific cache by name."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from cache_manager import invalidate
        ok = invalidate(name)
        if not ok:
            return jsonify({"error": f"Unknown cache: {name}"}), 404
        return jsonify({"invalidated": name, "success": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Audit Log ───────────────────────────────────────────────────────────────

@ops_bp.route("/api/audit")
def api_audit():
    """Paginated audit trail with filter params.

    Query params:
        actor   -- filter by actor (exact)
        action  -- filter by action (exact)
        from    -- events after this ISO timestamp
        to      -- events before this ISO timestamp
        resource -- filter by resource (contains)
        limit   -- max rows (default 100, max 1000)
        offset  -- pagination offset
        summary -- if truthy, return legacy audit_log.py summary instead
        legacy  -- if truthy, return legacy file-based events
    """
    # Legacy compat: ?summary=1 or ?legacy=1 still use the old audit_log.py
    if request.args.get("summary") or request.args.get("legacy"):
        try:
            from audit_log import read_events, get_audit_summary
            if request.args.get("summary"):
                return jsonify(get_audit_summary())
            return jsonify(read_events(
                last_n=int(request.args.get("limit", 50)),
                event_type=request.args.get("type"),
            ))
        except ImportError:
            return jsonify({"error": "audit_log module not available"}), 500

    try:
        from audit import query_audit, count_audit, get_audit_actors, get_audit_actions
        filters = {}
        if request.args.get("actor"):
            filters["actor"] = request.args["actor"]
        if request.args.get("action"):
            filters["action"] = request.args["action"]
        if request.args.get("from"):
            filters["from_ts"] = request.args["from"]
        if request.args.get("to"):
            filters["to_ts"] = request.args["to"]
        if request.args.get("resource"):
            filters["resource"] = request.args["resource"]

        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))

        rows = query_audit(filters=filters, limit=limit, offset=offset)
        total = count_audit(filters=filters)
        actors = get_audit_actors()
        actions = get_audit_actions()

        return jsonify({
            "events": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {"actors": actors, "actions": actions},
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@ops_bp.route("/api/audit/export")
def api_audit_export():
    """Download audit export as JSON or CSV.

    Query params:
        fmt    -- "json" (default) or "csv"
        actor  -- filter by actor
        action -- filter by action
        from   -- events after this ISO timestamp
        to     -- events before this ISO timestamp
    """
    try:
        from audit import export_audit
        fmt = request.args.get("fmt", "json")
        if fmt not in ("json", "csv"):
            fmt = "json"

        filters = {}
        if request.args.get("actor"):
            filters["actor"] = request.args["actor"]
        if request.args.get("action"):
            filters["action"] = request.args["action"]
        if request.args.get("from"):
            filters["from_ts"] = request.args["from"]
        if request.args.get("to"):
            filters["to_ts"] = request.args["to"]

        content, content_type, filename = export_audit(fmt=fmt, filters=filters)
        return Response(
            content,
            mimetype=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@ops_bp.route("/api/audit/purge", methods=["POST"])
def api_audit_purge():
    """Trigger retention purge -- admin only.

    JSON body:
        older_than_days -- retention window (default 365, minimum 1)
    """
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from audit import purge_audit, log_audit
        data = request.get_json(silent=True) or {}
        days = int(data.get("older_than_days", 365))
        result = purge_audit(older_than_days=days)
        # Self-audit the purge action
        log_audit(
            actor=_get_request_role() or "admin",
            action="audit.purge",
            resource="audit_log",
            detail=f"Purged {result['purged']} entries older than {days} days",
            role=_get_request_role(),
            ip_address=request.remote_addr,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── GDPR Erasure ────────────────────────────────────────────────────────────

@ops_bp.route("/api/gdpr/erasure", methods=["POST"])
def api_gdpr_erasure():
    """GDPR Art. 17: Right to erasure."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        data = request.get_json()
        identifier = data.get("identifier")
        if not identifier:
            return jsonify({"error": "identifier required"}), 400
        import db
        result = db.delete_user_data(identifier, scope=data.get("scope", "agent"))
        # Log to both audit trails (legacy file + new DB)
        try:
            from audit_log import log_event
            log_event("gdpr_erasure", "dashboard", {"identifier": identifier, "deleted": result}, severity="warning")
        except Exception:
            pass
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "admin",
                action="gdpr.erasure",
                resource=f"user:{identifier}",
                detail=f"GDPR erasure for '{identifier}', deleted: {result}",
                role=_get_request_role(),
                ip_address=request.remote_addr,
                metadata={"identifier": identifier, "deleted": result},
            )
        except Exception:
            pass
        return jsonify({"status": "erased", "deleted": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Filesystem Audit ────────────────────────────────────────────────────────

@ops_bp.route("/api/filesystem/audit")
def api_filesystem_audit():
    """Recent FileSystemGuard audit log entries (last 20 by default).

    v0.051.07b: SOC 2 file access audit trail viewer.
    Query params:
        limit  int  Max entries to return (default 20, max 200).
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 200)
    except (ValueError, TypeError):
        limit = 20

    log_path = FLEET_DIR / "logs" / "fs_access.log"
    entries = []

    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                # Parse log format: "TIMESTAMP [ALLOW|DENY] agent=... action=... path=..."
                entry = {"raw": line}
                m = re.match(
                    r"^(\S+)\s+\[(ALLOW|DENY)\]\s+agent=(\S+)(.*?)\s+action=(\S+)\s+path=(.+)$",
                    line,
                )
                if m:
                    entry = {
                        "timestamp": m.group(1),
                        "status": m.group(2),
                        "agent": m.group(3),
                        "action": m.group(5),
                        "path": m.group(6),
                    }
                    # Extract optional skill= tag
                    skill_m = re.search(r"skill=(\S+)", m.group(4))
                    if skill_m:
                        entry["skill"] = skill_m.group(1)
                entries.append(entry)
        except OSError:
            pass

    return jsonify({
        "entries": list(reversed(entries)),  # newest first
        "total": len(entries),
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
    })


# ── Event Triggers ──────────────────────────────────────────────────────────

@ops_bp.route("/api/trigger", methods=["POST"])
def api_trigger():
    """Webhook: receive external event and dispatch a fleet task.

    Required: type (skill name).
    Optional: payload (dict), priority (1-10), assigned_to (agent name).
    Returns: {"task_id": N} on success.
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        from event_triggers import handle_webhook

        result = handle_webhook(data)
        status_code = result.pop("status", 200)

        # Broadcast via SSE so dashboard updates live
        if "task_id" in result:
            try:
                _broadcast_sse({"type": "trigger", "data": result})
            except Exception:
                pass

        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@ops_bp.route("/api/trigger/status")
def api_trigger_status():
    """Return current event trigger configuration and state."""
    try:
        cfg = _load_config()
        triggers = cfg.get("triggers", {})
        schedules = cfg.get("schedules", {})

        # Load schedule state if available
        schedule_state = {}
        state_file = FLEET_DIR / "data" / "schedule_state.json"
        if state_file.exists():
            try:
                schedule_state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        return jsonify({
            "triggers": triggers,
            "schedules": {
                name: {
                    **spec,
                    "last_run": schedule_state.get(name, 0),
                }
                for name, spec in schedules.items()
                if isinstance(spec, dict)
            },
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500
