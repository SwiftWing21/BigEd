#!/usr/bin/env python3
"""
Fleet Dashboard v2 — localhost web UI for activity tracking, metrics, and live monitoring.

v0.27: New endpoints (/api/thermal, /api/training, /api/modules, /api/data_stats),
       Server-Sent Events for live updates, alert system.
CT-2:  Cost intelligence endpoints (/api/usage, /api/usage/delta).

Usage:
    python dashboard.py                # http://localhost:5555
    python dashboard.py --port 8080    # custom port
"""
import argparse
import importlib.util
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
import threading
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("dashboard")

from flask import Flask, jsonify, Response, request

from security import (
    ensure_tls_cert as _ensure_tls_cert,
    get_request_role,
    require_role as _require_role_raw,
    safe_error as _safe_error,
    generate_csrf_token as _generate_csrf_token,
    cors_origins as _cors_origins,
    register_hooks as _register_security_hooks,
)

from dashboard_utils import (
    FLEET_DIR, DB_PATH, KNOWLEDGE_DIR, HW_STATE_JSON, VALID_AGENT,
    _load_config, get_conn, query,
    _get_request_role, _require_role,
    _check_rate_limit, _is_recent, safe_error,
    _alerts, _alert_lock, _sse_clients, _sse_lock,
    _add_alert, _broadcast_sse,
)

_start_time = time.time()  # dashboard boot timestamp for /api/health uptime

# Background CPU sampler — psutil.cpu_percent(interval=0) returns 0 on first call.
# This thread samples every 2s so the /api/thermal endpoint always has a fresh value.
_cpu_pct_cache = {"value": 0.0}

def _cpu_sampler():
    import psutil
    psutil.cpu_percent(interval=0)  # prime
    while True:
        time.sleep(2)
        try:
            _cpu_pct_cache["value"] = psutil.cpu_percent(interval=0)
        except Exception:
            pass

try:
    _cpu_sampler_thread = threading.Thread(target=_cpu_sampler, daemon=True)
    _cpu_sampler_thread.start()
except Exception:
    pass


def _get_version() -> str:
    """Read version from fleet.toml [meta] or fall back to hardcoded beta tag."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("meta", {}).get("version", "0.400.00b")
    except Exception:
        return "0.400.00b"


app = Flask(__name__)

# ── Security hooks (CORS, auth, rate-limit, CSRF) ────────────────────────
# register_hooks wires all before_request / after_request handlers.
# _load_config is defined below — forward-ref is fine because hooks run at
# request time, not import time.
_register_security_hooks(app, lambda: _load_config())


# ── Blueprint Registry ──────────────────────────────────────────────────────
# Each tuple: (module_name, blueprint_var, required)
# required=True → ImportError raises and halts startup
# required=False → logged at DEBUG and skipped gracefully
_BLUEPRINTS = [
    ("auth_blueprint",        "auth_bp",          True),
    ("mode_blueprint",        "mode_bp",          True),
    ("sse_blueprint",         "sse_bp",            True),
    ("process_control",       "fleet_bp",          True),
    ("health_api",            "health_bp",         True),
    ("geo_api",               "geo_bp",            True),
    ("a2a",                   "a2a_bp",            True),
    ("tenant_crypto_api",     "tenant_crypto_bp",  True),
    ("tenant_admin",          "tenant_bp",         True),
    ("modules_blueprint",     "modules_bp",        True),
    ("outputs_blueprint",     "outputs_bp",        True),
    ("ollama_blueprint",      "ollama_bp",         True),
    ("settings_blueprint",    "settings_bp",       True),
    ("federation_blueprint",  "federation_bp",     True),
    ("deploy_blueprint",      "deploy_bp",         True),
    ("tasks_blueprint",       "tasks_bp",          True),
    ("monitoring_blueprint",  "monitoring_bp",     True),
    ("metering_blueprint",    "metering_bp",       True),
    ("ops_blueprint",         "ops_bp",            True),
    ("knowledge_blueprint",   "knowledge_bp",      True),
    ("marketplace",           "marketplace_bp",    False),
    ("control_plane",         "platform_bp",       False),
    ("self_service",          "self_service_bp",   False),
    ("ingest_blueprint",      "ingest_bp",         False),
    ("audit_blueprint",       "audit_bp",          True),
    ("update_blueprint",      "update_bp",         False),
]


def _register_blueprints(flask_app):
    """Register all blueprints with consistent error handling."""
    registered = 0
    for module_name, bp_var, required in _BLUEPRINTS:
        try:
            mod = importlib.import_module(module_name)
            bp = getattr(mod, bp_var)
            flask_app.register_blueprint(bp)
            registered += 1
        except ImportError as exc:
            if required:
                log.error("Required blueprint '%s' failed to import: %s", module_name, exc)
                raise
            log.debug("Optional blueprint '%s' not available: %s", module_name, exc)
        except Exception as exc:
            log.error("Failed to register blueprint '%s': %s", module_name, exc)
            if required:
                raise
    log.info("Registered %d/%d blueprints", registered, len(_BLUEPRINTS))


def _post_registration_setup(flask_app):
    """One-time setup that must run after specific blueprints are registered."""
    # monitoring_blueprint: share CPU cache dict reference
    try:
        from monitoring_blueprint import set_cpu_cache_ref
        set_cpu_cache_ref(_cpu_pct_cache)
    except Exception as exc:
        log.warning("monitoring_blueprint post-setup failed: %s", exc)

    # SSO — uses register_sso(app) rather than a Blueprint object
    try:
        from sso import register_sso as _register_sso
        _register_sso(flask_app)
    except Exception as exc:
        log.debug("SSO module not loaded: %s", exc)

    # compliance — factory pattern: create_compliance_blueprint(_require_role)
    try:
        from compliance import create_compliance_blueprint
        _compliance_bp = create_compliance_blueprint(_require_role)
        flask_app.register_blueprint(_compliance_bp)
    except ImportError:
        pass

    # payments — uses register_payment_routes(app) rather than a Blueprint object
    try:
        from payments import register_payment_routes
        register_payment_routes(flask_app)
    except ImportError:
        pass

    # views_blueprint — requires view_registry discover + source registration after blueprint load
    try:
        from views_blueprint import views_bp
        flask_app.register_blueprint(views_bp)
        import view_registry
        view_registry.discover_and_register()
        view_registry.register_source(
            name="knowledge",
            category="knowledge",
            node_types=["skill", "folder", "agent"],
            edge_types=["reads", "writes", "produces"],
            data_endpoint="/api/views/graph/knowledge-graph",
            icon="book",
            layout_hint="tree",
            metrics=["file_count", "total_size_mb"],
        )
        view_registry.register_source(
            name="wiki",
            category="knowledge",
            node_types=["wiki_page", "wiki_section"],
            edge_types=["links_to", "summarizes"],
            data_endpoint="/api/knowledge/wiki/graph",
            icon="book-open",
            layout_hint="tree",
            metrics=["page_count"],
        )
        view_registry.register_source(
            name="universe",
            category="fleet",
            node_types=["agent", "skill", "task", "folder", "model", "config", "message"],
            edge_types=["runs", "writes", "reads", "assigned", "uses_model", "communicates", "costs"],
            data_endpoint="/api/views/graph/universe",
            icon="globe",
            color="#7c3aed",
            layout_hint="cluster",
            metrics=["node_count", "edge_count"],
        )
    except ImportError:
        pass

    # update_manager — start background update checker (24h interval)
    try:
        import update_manager
        update_manager.start_background_checker(interval_hours=24, sse_broadcast=_broadcast_sse)
    except Exception as e:
        log.warning("Failed to start update checker: %s", e)

    # Mark disabled agents in DB so they don't count as active
    try:
        from config import load_config
        from db_agents import mark_disabled_agents
        disabled = load_config().get("fleet", {}).get("disabled_agents", [])
        if disabled:
            mark_disabled_agents(disabled)
    except Exception as e:
        log.warning("Failed to mark disabled agents: %s", e)


_register_blueprints(app)
_post_registration_setup(app)

# Extra symbols needed from blueprints for inline route handlers below
from mode_blueprint import (
    restore_mode as _restore_mode,
    _get_effective_mode, _get_modifier_states,
)
from monitoring_blueprint import api_thermal, api_alerts
from knowledge_blueprint import api_discussions as _api_discussions


# SSE/alert state now lives in dashboard_utils.py (Phase 3 extraction)
# _alerts, _alert_lock, _sse_clients, _sse_lock imported above
# _add_alert, _broadcast_sse imported above

# ── API call attribution logging ──────────────────────────────────────────

@app.after_request
def _log_api_attribution(response):
    """Log API call attribution for audit trail.

    Samples 10% of GET requests but logs 100% of write requests (POST/PUT/DELETE)
    to avoid DB bloat while maintaining full write audit coverage.
    """
    if not request.path.startswith("/api/"):
        return response
    # Skip 90% of GET requests to avoid DB bloat
    if request.method == "GET" and random.random() > 0.1:
        return response
    try:
        role = _get_request_role()
        # Legacy file-based audit
        try:
            from audit_log import log_event
            log_event(
                event_type="api_call",
                source="dashboard",
                details={
                    "method": request.method,
                    "path": request.path,
                    "role": role,
                    "status": response.status_code,
                    "remote": request.remote_addr,
                },
                severity="info",
            )
        except (ImportError, AttributeError):
            pass
        # Enhanced DB-backed audit (write operations only — GETs already sampled above)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            try:
                from audit import log_audit
                log_audit(
                    actor=role or "unknown",
                    action=f"api.{request.method.lower()}",
                    resource=request.path,
                    detail=f"{request.method} {request.path} -> {response.status_code}",
                    ip_address=request.remote_addr,
                    role=role,
                )
            except Exception:
                pass
            # Broadcast SSE so the audit panel auto-refreshes
            try:
                _broadcast_sse({"type": "audit", "data": {
                    "actor": role or "unknown",
                    "action": f"api.{request.method.lower()}",
                    "resource": request.path,
                }})
            except Exception:
                pass
    except Exception:
        pass  # Never let logging break the response
    return response


@app.after_request
def _add_security_headers(response):
    """Add Content-Security-Policy and other security headers to all responses."""
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    # Prevent browser from caching HTML pages — ensures template changes
    # appear without manual hard-refresh during development
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response


# _add_alert, _broadcast_sse: now in dashboard_utils.py
# _alert_monitor: now in alerts.py


# ── Original API endpoints ───────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    # Show ALL registered agents with computed display_status
    try:
        from config import load_config
        disabled = load_config().get("fleet", {}).get("disabled_agents", [])
    except Exception:
        disabled = []

    agents = query("""
        SELECT a.name, a.role, a.status, a.last_heartbeat, a.current_task_id,
               t.type as current_task
        FROM agents a
        LEFT JOIN tasks t ON a.current_task_id = t.id
        ORDER BY
            CASE WHEN a.name IN ('dr_ders', 'hw_supervisor') THEN 0 ELSE 1 END,
            CASE a.status WHEN 'BUSY' THEN 0 WHEN 'ACTIVE' THEN 1 ELSE 2 END,
            a.name
    """)
    for a in agents:
        if a["name"] in disabled:
            a["display_status"] = "DISABLED"
        elif a.get("last_heartbeat") and _is_recent(a["last_heartbeat"], 120):
            a["display_status"] = a["status"]  # BUSY / ACTIVE / IDLE
        else:
            a["display_status"] = "OFFLINE"

    counts = {}
    for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
        row = query("SELECT COUNT(*) as n FROM tasks WHERE status=? AND classification != 'synthetic_prefix'", (s,))
        counts[s] = row[0]["n"] if row else 0
    return jsonify({"agents": agents, "tasks": counts})


# ── Live Activity Feed ────────────────────────────────────────────────────────

@app.route("/api/activity/live")
def api_activity_live():
    """Return recent task activity: running + recently completed, with agent info."""
    try:
        rows = query("""
            SELECT t.id, t.type, t.status, t.assigned_to, t.classification,
                   t.created_at, t.error, t.intelligence_score, t.priority,
                   t.trace_id,
                   substr(t.payload_json, 1, 100) as payload_preview,
                   u.model, u.eval_duration_ms, u.input_tokens, u.output_tokens,
                   u.tokens_per_sec, u.cost_usd, u.provider
            FROM tasks t
            LEFT JOIN usage u ON u.task_id = t.id
            WHERE t.classification != 'synthetic_prefix'
              AND (t.status = 'RUNNING'
                   OR (t.status IN ('DONE', 'FAILED') AND t.created_at >= datetime('now', '-10 minutes')))
            ORDER BY
                CASE t.status WHEN 'RUNNING' THEN 0 WHEN 'DONE' THEN 1 ELSE 2 END,
                t.id DESC
            LIMIT 30
        """)
        items = []
        for r in rows:
            duration_ms = r["eval_duration_ms"]
            items.append({
                "id": r["id"],
                "type": r["type"],
                "status": r["status"],
                "agent": r["assigned_to"] or "unassigned",
                "classification": r["classification"],
                "created_at": r["created_at"],
                "error": (r["error"] or "")[:80],
                "payload_preview": r["payload_preview"] or "",
                "model": r["model"] or "",
                "duration_s": round(duration_ms / 1000, 1) if duration_ms else None,
                "in_tokens": r["input_tokens"] or 0,
                "out_tokens": r["output_tokens"] or 0,
                "tok_per_sec": round(r["tokens_per_sec"], 1) if r["tokens_per_sec"] else None,
                "cost_usd": round(r["cost_usd"], 4) if r["cost_usd"] else 0,
                "provider": r["provider"] or "",
                "iq_score": round(r["intelligence_score"], 2) if r["intelligence_score"] is not None else None,
                "priority": r["priority"] or 3,
                "trace_id": r["trace_id"] or "",
            })
        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        return jsonify({"items": [], "error": str(e)}), 500


# ── v0.22.00: Unified Health Endpoint ─────────────────────────────────────────

@app.route("/api/health")
def api_health():
    """Unified health check — aggregates all subsystem status in one call."""
    subsystems = {}
    overall = "healthy"

    # 1. Fleet DB connectivity
    try:
        conn = get_conn()
        conn.execute("SELECT 1")
        subsystems["fleet_db"] = {"status": "ok", "detail": "connected"}
    except Exception as e:
        subsystems["fleet_db"] = {"status": "unavailable", "detail": _safe_error(e)}
        overall = "unhealthy"

    # 2. Ollama status + available models + current loaded model
    try:
        import urllib.request
        from config import get_ollama_host
        req = urllib.request.Request(f"{get_ollama_host()}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            available_models = [m.get("name", "") for m in data.get("models", [])]
            models_loaded = len(available_models)
        # Determine current loaded model from hw_state or fleet.toml
        current_model = ""
        try:
            if HW_STATE_JSON.exists():
                hw_data = json.loads(HW_STATE_JSON.read_text())
                current_model = hw_data.get("model", "")
        except Exception:
            pass
        if not current_model:
            try:
                current_model = _load_config().get("models", {}).get("tiers", {}).get("default", "")
            except Exception:
                pass
        subsystems["ollama"] = {
            "status": "ok",
            "models_loaded": models_loaded,
            "available_models": available_models,
            "current_model": current_model,
        }
    except Exception:
        subsystems["ollama"] = {"status": "unavailable", "models_loaded": 0, "available_models": [], "current_model": ""}
        if overall == "healthy":
            overall = "degraded"

    # 3. Supervisor status (from hw_state.json)
    try:
        if HW_STATE_JSON.exists():
            hw = json.loads(HW_STATE_JSON.read_text())
            hw_status = hw.get("status", "unknown")
            # Count live workers from agents table
            try:
                workers = query(
                    "SELECT COUNT(*) as n FROM agents WHERE status != 'OFFLINE' "
                    "AND last_heartbeat > datetime('now', '-5 minutes')"
                )
                worker_count = workers[0]["n"] if workers else 0
            except Exception:
                worker_count = 0
            subsystems["supervisor"] = {"status": "running", "workers": worker_count}
        else:
            subsystems["supervisor"] = {"status": "unknown", "workers": 0}
            if overall == "healthy":
                overall = "degraded"
    except Exception:
        subsystems["supervisor"] = {"status": "unknown", "workers": 0}
        if overall == "healthy":
            overall = "degraded"

    # 4. Dashboard self-check
    try:
        # Count registered endpoints
        endpoint_count = len([r for r in app.url_map.iter_rules() if r.endpoint != 'static'])
        subsystems["dashboard"] = {"status": "ok", "endpoints": endpoint_count}
    except Exception:
        subsystems["dashboard"] = {"status": "ok", "endpoints": 0}

    # 5. RAG DB
    rag_db = FLEET_DIR / "rag.db"
    try:
        if rag_db.exists():
            # rag.db has no DAL get_conn() — intentional raw sqlite3 for read-only probe
            with sqlite3.connect(str(rag_db), timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                chunks = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]
            subsystems["rag_db"] = {"status": "ok", "chunks": chunks}
        else:
            subsystems["rag_db"] = {"status": "missing", "chunks": 0}
    except Exception:
        subsystems["rag_db"] = {"status": "unavailable", "chunks": 0}
        if overall == "healthy":
            overall = "degraded"

    return jsonify({
        "status": overall,
        "uptime_seconds": int(time.time() - _start_time),
        "subsystems": subsystems,
        "version": _get_version(),
    })


# ── Ollama process status ─────────────────────────────────────────────────────

@app.route("/api/ollama/ps")
def api_ollama_ps():
    """Proxy to Ollama /api/ps — returns currently loaded models."""
    try:
        import urllib.request
        from config import get_ollama_host
        req = urllib.request.Request(f"{get_ollama_host()}/api/ps")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return jsonify(data)
    except Exception:
        return jsonify({"models": []})


# ── Walkthrough (first-run wizard) ────────────────────────────────────────────

_WALKTHROUGH_FLAG = FLEET_DIR / ".walkthrough_done"


@app.route("/api/walkthrough/needed")
def api_walkthrough_needed():
    """Check if the first-run walkthrough should be shown."""
    try:
        # Show walkthrough if flag file doesn't exist and DB has no completed tasks
        if _WALKTHROUGH_FLAG.exists():
            return jsonify({"needed": False})
        # Also skip if fleet has been used (has completed tasks)
        try:
            rows = query("SELECT COUNT(*) as n FROM tasks WHERE status='DONE'")
            if rows and rows[0]["n"] > 5:
                return jsonify({"needed": False})
        except Exception:
            pass
        return jsonify({"needed": True})
    except Exception:
        return jsonify({"needed": False})


@app.route("/api/walkthrough/complete", methods=["POST"])
def api_walkthrough_complete():
    """Mark the first-run walkthrough as completed."""
    try:
        _WALKTHROUGH_FLAG.write_text(
            json.dumps({"completed_at": datetime.utcnow().isoformat()}),
            encoding="utf-8",
        )
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Boot Status ──────────────────────────────────────────────────────────────

@app.route("/api/boot/status")
def api_boot_status():
    """Return current boot stage progress for the boot overlay."""
    try:
        import boot_status
        return jsonify(boot_status.read())
    except Exception as e:
        return jsonify({"stage": "unknown", "error": str(e), "stages": {}}), 500


# ── v0.22.00: Per-Agent Performance ──────────────────────────────────────────

@app.route("/api/agents/performance")
def api_agents_performance():
    """Per-agent performance metrics over the last hour."""
    try:
        rows = query("""
            SELECT
                assigned_to,
                COUNT(*) as total,
                SUM(CASE WHEN status = 'DONE' THEN 1 ELSE 0 END) as done,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                AVG(CASE WHEN status = 'DONE' THEN intelligence_score ELSE NULL END) as avg_iq,
                AVG(CASE
                    WHEN status IN ('DONE', 'FAILED')
                    THEN (julianday('now') - julianday(created_at)) * 86400000
                    ELSE NULL
                END) as avg_latency
            FROM tasks
            WHERE created_at >= datetime('now', '-1 hour')
              AND assigned_to IS NOT NULL
            GROUP BY assigned_to
            ORDER BY done DESC
        """)
        agents = []
        for r in rows:
            total = r["total"] or 0
            done = r["done"] or 0
            agents.append({
                "name": r["assigned_to"],
                "tasks_completed_1h": done,
                "success_rate": round(done / total, 2) if total > 0 else 0.0,
                "avg_latency_ms": round(r["avg_latency"] or 0, 0),
                "avg_intelligence_score": round(r["avg_iq"] or 0, 2),
                "tasks_per_hour": float(done),
            })
        return jsonify({"agents": agents})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "agents": []}), 500


@app.route("/api/activity")
def api_activity():
    rows = query("""
        SELECT date(created_at) as day, status, COUNT(*) as n
        FROM tasks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY day, status
        ORDER BY day
    """)
    days = defaultdict(lambda: {"DONE": 0, "FAILED": 0, "PENDING": 0, "RUNNING": 0})
    for r in rows:
        if r["day"]:
            days[r["day"]][r["status"]] = r["n"]
    result = []
    today = datetime.utcnow().date()
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        result.append({"day": d, **days[d]})
    return jsonify(result)


@app.route("/api/activity/lanes")
def api_activity_lanes():
    """Universe-level activity data for neural lane graph visualization.

    Returns agent lanes, skill nodes, model nodes, knowledge folders,
    message channels, and usage cost — matching the universe graph view.
    """
    hours = request.args.get("hours", 24, type=int)

    # ── 1. AGENT LANES (tasks by agent) ─────────────────────────────
    agent_rows = query("""
        SELECT assigned_to, type, status, COUNT(*) as n,
               MAX(created_at) as last_active
        FROM tasks
        WHERE created_at >= datetime('now', ? || ' hours')
          AND assigned_to IS NOT NULL
        GROUP BY assigned_to, type, status
        ORDER BY assigned_to, n DESC
    """, (f"-{hours}",))
    lanes = {}
    for r in agent_rows:
        agent = r["assigned_to"]
        if agent not in lanes:
            lanes[agent] = {"agent": agent, "kind": "agent", "skills": {},
                            "total": 0, "done": 0, "failed": 0, "running": 0,
                            "last_active": r["last_active"]}
        skill = r["type"] or "unknown"
        lanes[agent]["skills"].setdefault(skill, 0)
        lanes[agent]["skills"][skill] += r["n"]
        lanes[agent]["total"] += r["n"]
        st = r["status"].lower()
        if st in ("done", "failed", "running"):
            lanes[agent][st] += r["n"]

    # ── 2. MODEL LANES (usage by model) ─────────────────────────────
    model_rows = query("""
        SELECT model, SUM(input_tokens + output_tokens) as tokens,
               SUM(cost_usd) as cost, COUNT(*) as calls
        FROM usage
        WHERE created_at >= datetime('now', ? || ' hours')
        GROUP BY model ORDER BY calls DESC
    """, (f"-{hours}",))
    for r in model_rows:
        mid = "model:" + (r["model"] or "unknown")
        lanes[mid] = {
            "agent": r["model"] or "unknown", "kind": "model",
            "total": r["calls"], "done": r["calls"], "failed": 0, "running": 0,
            "tokens": r["tokens"] or 0,
            "cost": round(r["cost"] or 0, 4),
            "skills": {}, "last_active": None,
        }

    # ── 3. SKILL ACTIVITY (top skills by volume) ────────────────────
    skill_rows = query("""
        SELECT type, status, COUNT(*) as n
        FROM tasks
        WHERE created_at >= datetime('now', ? || ' hours')
          AND type IS NOT NULL
        GROUP BY type, status ORDER BY n DESC LIMIT 30
    """, (f"-{hours}",))
    skill_summary = {}
    for r in skill_rows:
        s = r["type"]
        skill_summary.setdefault(s, {"skill": s, "done": 0, "failed": 0, "total": 0})
        skill_summary[s]["total"] += r["n"]
        if r["status"] == "DONE":
            skill_summary[s]["done"] += r["n"]
        elif r["status"] == "FAILED":
            skill_summary[s]["failed"] += r["n"]

    # ── 4. KNOWLEDGE FOLDERS ────────────────────────────────────────
    knowledge_dir = Path(__file__).parent / "knowledge"
    folders = []
    if knowledge_dir.exists():
        for entry in sorted(knowledge_dir.iterdir()):
            if entry.is_dir() and entry.name != "__pycache__":
                try:
                    count = sum(1 for f in entry.rglob("*") if f.is_file())
                except Exception:
                    count = 0
                if count > 0:
                    folders.append({"name": entry.name, "files": count})

    # ── 5. MESSAGE CHANNELS ─────────────────────────────────────────
    msg_rows = query("""
        SELECT channel, COUNT(*) as n
        FROM messages
        WHERE created_at >= datetime('now', ? || ' hours')
        GROUP BY channel ORDER BY n DESC LIMIT 10
    """, (f"-{hours}",))
    channels = [{"channel": r["channel"] or "fleet", "count": r["n"]} for r in msg_rows]

    # ── 6. NEURAL EDGES (agent↔skill, skill↔model) ─────────────────
    edges = []
    skill_agents = {}
    for agent, data in lanes.items():
        if data.get("kind") != "agent":
            continue
        for skill in data["skills"]:
            skill_agents.setdefault(skill, []).append(agent)
    for skill, agents in skill_agents.items():
        if len(agents) > 1:
            for i in range(len(agents) - 1):
                edges.append({"source": agents[i], "target": agents[i + 1],
                              "skill": skill, "type": "shared_skill"})

    # ── 7. TIMELINE (recent pulses) ─────────────────────────────────
    recent = query("""
        SELECT id, assigned_to, type, status, created_at
        FROM tasks
        WHERE created_at >= datetime('now', '-2 hours')
          AND assigned_to IS NOT NULL
        ORDER BY created_at DESC LIMIT 50
    """)
    timeline = [{"id": r["id"], "agent": r["assigned_to"], "skill": r["type"],
                 "status": r["status"], "time": r["created_at"]} for r in recent]

    return jsonify({
        "lanes": sorted(lanes.values(), key=lambda x: -x["total"]),
        "skills": sorted(skill_summary.values(), key=lambda x: -x["total"]),
        "folders": folders,
        "channels": channels,
        "edges": edges,
        "timeline": timeline,
        "hours": hours,
    })


@app.route("/api/skills")
def api_skills():
    rows = query("""
        SELECT type, status, COUNT(*) as n
        FROM tasks
        GROUP BY type, status
        ORDER BY type
    """)
    skills = defaultdict(lambda: {"DONE": 0, "FAILED": 0, "PENDING": 0, "RUNNING": 0, "total": 0})
    for r in rows:
        skills[r["type"]][r["status"]] = r["n"]
        skills[r["type"]]["total"] += r["n"]
    return jsonify(dict(skills))


# /api/discussions, /api/knowledge: now in knowledge_blueprint.py (Phase 5)


@app.route("/api/code_stats")
def api_code_stats():
    workspace = KNOWLEDGE_DIR / "code_writes" / "workspace"
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return jsonify({"commits": 0, "lines_added": 0, "lines_deleted": 0, "files_changed": 0})

    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--numstat", "--pretty=format:"],
            cwd=str(workspace), capture_output=True, text=True, timeout=10,
        )
        added = deleted = 0
        files = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3:
                try:
                    a, d = int(parts[0]), int(parts[1])
                    added += a
                    deleted += d
                    files.add(parts[2])
                except ValueError:
                    pass

        commits = subprocess.run(
            ["git", "rev-list", "--count", "--all"],
            cwd=str(workspace), capture_output=True, text=True, timeout=5,
        )
        commit_count = int(commits.stdout.strip()) if commits.returncode == 0 else 0
    except Exception:
        return jsonify({"commits": 0, "lines_added": 0, "lines_deleted": 0, "files_changed": 0})

    return jsonify({
        "commits": commit_count,
        "lines_added": added,
        "lines_deleted": deleted,
        "files_changed": len(files),
    })


# /api/reviews: now in knowledge_blueprint.py (Phase 5)


@app.route("/api/timeline")
def api_timeline():
    events = []
    for row in query("""
        SELECT id, type, status, assigned_to, created_at
        FROM tasks WHERE status IN ('DONE','FAILED')
        AND created_at >= date('now','-7 days')
        ORDER BY created_at DESC LIMIT 50
    """):
        events.append({
            "time": row["created_at"],
            "type": "task",
            "detail": f"Task #{row['id']} ({row['type']}) -> {row['status']}",
            "agent": row["assigned_to"] or "",
            "status": row["status"],
        })

    for row in query("""
        SELECT from_agent, body_json, created_at
        FROM messages WHERE created_at >= date('now','-7 days')
        ORDER BY created_at DESC LIMIT 30
    """):
        try:
            body = json.loads(row["body_json"])
            topic = body.get("topic", "message")
        except Exception:
            topic = "message"
        events.append({
            "time": row["created_at"],
            "type": "discussion",
            "detail": f"Discussion: {topic}",
            "agent": row["from_agent"],
            "status": "INFO",
        })

    events.sort(key=lambda x: x.get("time", ""), reverse=True)
    return jsonify(events[:80])


# /api/rag: now in knowledge_blueprint.py (Phase 5)


# ── v0.27 New API endpoints ──────────────────────────────────────────────────

# /api/thermal, /api/fleet/provider-health: now in monitoring_blueprint.py (Phase 5)


@app.route("/api/training")
def api_training():
    """Training lock status, active run info."""
    result = {"locked": False, "holder": None, "elapsed_s": 0, "timeout_s": 7200}

    try:
        locks = query("SELECT * FROM locks WHERE name='training'")
        if locks:
            lock = locks[0]
            result["locked"] = True
            result["holder"] = lock.get("holder", "unknown")
            acquired = lock.get("acquired_at", "")
            if acquired:
                try:
                    acq_time = datetime.fromisoformat(acquired)
                    result["elapsed_s"] = int((datetime.utcnow() - acq_time).total_seconds())
                except Exception:
                    pass
    except Exception:
        pass

    cfg = _load_config()
    result["timeout_s"] = cfg.get("training", {}).get("lock_timeout_secs", 7200)
    result["exclusive"] = cfg.get("training", {}).get("exclusive_lock", True)

    # Recent training logs
    training_dir = KNOWLEDGE_DIR / "skill_training"
    logs = []
    if training_dir.exists():
        for f in sorted(training_dir.glob("*.json"), reverse=True)[:10]:
            try:
                data = json.loads(f.read_text())
                logs.append({
                    "file": f.name,
                    "skill": data.get("skill", ""),
                    "improved": data.get("improved", False),
                    "before": data.get("before_score", 0),
                    "after": data.get("after_score", 0),
                    "iterations": data.get("iterations_run", 0),
                })
            except Exception:
                pass
    result["recent_logs"] = logs

    return jsonify(result)


@app.route("/api/dashboard/batch")
def api_dashboard_batch():
    """Combined endpoint -- returns status, thermal, and training in one call.

    Reduces launcher round-trips from 3 sequential requests to 1.
    """
    return jsonify({
        "status": api_status().get_json(),
        "thermal": api_thermal().get_json(),
        "training": api_training().get_json(),
    })


@app.route("/api/dashboard")
def api_dashboard_aggregate():
    """Aggregate endpoint — returns all core dashboard data in a single request.

    Replaces 15 separate JS fetch calls with one, reducing connection overhead.
    Keys match the individual endpoint paths for easy JS destructuring.
    """
    def _safe(fn):
        try:
            return fn().get_json()
        except Exception:
            return {}

    return jsonify({
        "status":     _safe(api_status),
        "thermal":    _safe(api_thermal),
        "training":   _safe(api_training),
        "activity":   _safe(api_activity),
        "skills":     _safe(api_skills),
        "timeline":   _safe(api_timeline),
        "alerts":     _safe(api_alerts),
        "code_stats": _safe(api_code_stats),
        "modules":    _safe(api_modules_legacy),
        "data_stats": _safe(api_data_stats),
        "evolution":  _safe(api_evolution),
    })


# ── SSE broadcaster (needed for SSE background thread below) ─────────────────
from sse_blueprint import _sse_broadcaster


@app.route("/api/modules/legacy")
def api_modules_legacy():
    """Enabled modules, versions, deprecation status (legacy manifest reader)."""
    modules_dir = Path(__file__).parent.parent / "BigEd" / "launcher" / "modules"
    manifest_path = modules_dir / "manifest.json"

    if not manifest_path.exists():
        return jsonify({"modules": [], "profile": "unknown"})

    try:
        manifest = json.loads(manifest_path.read_text())
        modules = manifest.get("modules", [])
    except Exception:
        modules = []

    cfg = _load_config()
    profile = cfg.get("launcher", {}).get("profile", "research")
    tab_cfg = cfg.get("launcher", {}).get("tabs", {})

    for mod in modules:
        mod["enabled"] = tab_cfg.get(mod["name"], mod.get("default_enabled", False))

    return jsonify({"modules": modules, "profile": profile})


# /api/filesystem/audit: now in ops_blueprint.py (Phase 5)


@app.route("/api/data_stats")
def api_data_stats():
    """Per-module data size and growth metrics."""
    if not _check_rate_limit("data_stats", 5):
        return jsonify({"error": "Rate limited"}), 429
    stats = {}

    # Fleet DB tables
    ALLOWED_FLEET_TABLES = frozenset({"tasks", "agents", "messages", "locks", "notes", "usage"})
    try:
        conn = get_conn()
        for table in ["tasks", "agents", "messages", "locks", "notes"]:
            if table not in ALLOWED_FLEET_TABLES:
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[f"fleet.{table}"] = {"count": count}
            except Exception:
                pass
    except Exception:
        pass

    # Tools DB (launcher data)
    ALLOWED_TOOLS_TABLES = frozenset({"crm", "accounts", "onboarding", "customers", "agents"})
    tools_db = Path(__file__).parent.parent / "BigEd" / "launcher" / "data" / "tools.db"
    if tools_db.exists():
        try:
            # tools.db is the launcher DB — no DAL exists for it; intentional raw sqlite3
            with sqlite3.connect(str(tools_db), timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                for table in ["crm", "accounts", "onboarding", "customers", "agents"]:
                    if table not in ALLOWED_TOOLS_TABLES:
                        continue
                    try:
                        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        stats[f"tools.{table}"] = {"count": count}
                    except Exception:
                        pass
        except Exception:
            pass

    # Knowledge directory sizes
    if KNOWLEDGE_DIR.exists():
        for subdir in KNOWLEDGE_DIR.iterdir():
            if subdir.is_dir():
                files = list(subdir.rglob("*"))
                file_count = sum(1 for f in files if f.is_file())
                total_size = sum(f.stat().st_size for f in files if f.is_file())
                stats[f"knowledge.{subdir.name}"] = {
                    "count": file_count,
                    "size_mb": round(total_size / (1024 * 1024), 2),
                }

    return jsonify(stats)


@app.route("/api/comms")
def api_comms():
    """Per-channel message/note counts + recent activity."""
    channels = ["sup", "agent", "fleet", "pool"]
    result = {}
    try:
        conn = get_conn()
        for ch in channels:
            msg_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE channel=?", (ch,)
            ).fetchone()[0]
            msg_unread = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE channel=? AND read_at IS NULL", (ch,)
            ).fetchone()[0]
            note_count = 0
            try:
                note_count = conn.execute(
                    "SELECT COUNT(*) FROM notes WHERE channel=?", (ch,)
                ).fetchone()[0]
            except Exception:
                pass
            recent = [dict(r) for r in conn.execute("""
                SELECT from_agent, body_json, created_at FROM messages
                WHERE channel=? ORDER BY created_at DESC LIMIT 3
            """, (ch,)).fetchall()]
            result[ch] = {
                "messages": msg_count,
                "unread": msg_unread,
                "notes": note_count,
                "recent": recent,
            }
    except Exception as e:
        result["error"] = _safe_error(e)
    return jsonify(result)


@app.route("/api/comms/<channel>/read", methods=["POST"])
def api_comms_mark_read(channel):
    """Mark all unread messages in a channel as read."""
    if channel not in ("sup", "agent", "fleet", "pool"):
        return jsonify({"error": "invalid channel"}), 400
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE messages SET read_at=datetime('now') "
                "WHERE channel=? AND read_at IS NULL",
                (channel,),
            )
            count = cur.rowcount
        return jsonify({"ok": True, "channel": channel, "marked": count})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/comms/history/<channel>")
def api_comms_history(channel):
    """Paginated message + note history for a channel."""
    if channel not in ("sup", "agent", "fleet", "pool", "chat"):
        return jsonify({"error": "invalid channel"}), 400
    limit = min(int(request.args.get("limit", 50)), 200)
    before = request.args.get("before", "")  # ISO datetime for pagination
    try:
        conn = get_conn()
        # Messages
        if before:
            msgs = [dict(r) for r in conn.execute(
                "SELECT id, from_agent, to_agent, body_json, created_at, read_at, channel "
                "FROM messages WHERE channel=? AND created_at < ? ORDER BY created_at DESC LIMIT ?",
                (channel, before, limit),
            ).fetchall()]
        else:
            msgs = [dict(r) for r in conn.execute(
                "SELECT id, from_agent, to_agent, body_json, created_at, read_at, channel "
                "FROM messages WHERE channel=? ORDER BY created_at DESC LIMIT ?",
                (channel, limit),
            ).fetchall()]
        # Notes
        if before:
            notes = [dict(r) for r in conn.execute(
                "SELECT id, channel, from_agent, body_json, created_at "
                "FROM notes WHERE channel=? AND created_at < ? ORDER BY created_at DESC LIMIT ?",
                (channel, before, limit),
            ).fetchall()]
        else:
            notes = [dict(r) for r in conn.execute(
                "SELECT id, channel, from_agent, body_json, created_at "
                "FROM notes WHERE channel=? ORDER BY created_at DESC LIMIT ?",
                (channel, limit),
            ).fetchall()]
        return jsonify({"messages": msgs, "notes": notes})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "messages": [], "notes": []}), 500


@app.route("/api/comms/send", methods=["POST"])
def api_comms_send():
    """Send a message or note from the dashboard UI."""
    data = request.get_json(silent=True) or {}
    channel = data.get("channel", "fleet")
    msg_type = data.get("type", "note")  # "message" or "note"
    body = data.get("body", "")
    from_agent = data.get("from", "dashboard")
    to_agent = data.get("to", "")

    if not body:
        return jsonify({"error": "body is required"}), 400
    if channel not in ("sup", "agent", "fleet", "pool", "chat"):
        return jsonify({"error": "invalid channel"}), 400

    try:
        import comms
        body_json = json.dumps({"text": body, "source": "dashboard"})
        if msg_type == "message" and to_agent:
            comms.post_message(from_agent, to_agent, body_json, channel=channel)
        else:
            comms.post_note(channel, from_agent, body_json)
        # Broadcast via SSE so other clients see it immediately
        _broadcast_sse({
            "type": "comms_activity",
            "channel": channel,
            "from": from_agent,
            "body": body,
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/comms/chat", methods=["POST"])
def api_comms_chat():
    """User chat — posts a task to the fleet and returns the task ID."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    try:
        import db as _db
        task_id = _db.post_task(
            skill="chat_response",
            payload=json.dumps({"user_message": message, "source": "dashboard_chat"}),
            priority=8,
        )
        # Also post as a note to the chat channel for history
        import comms
        comms.post_note("chat", "user", json.dumps({"text": message, "source": "dashboard_chat"}))
        _broadcast_sse({
            "type": "comms_activity",
            "channel": "chat",
            "from": "user",
            "body": message,
        })
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/comms/launch-vscode", methods=["POST"])
def api_comms_launch_vscode():
    """Launch VS Code in the BigEd workspace."""
    try:
        from manual_mode import launch_vscode
        workspace = str(Path(__file__).resolve().parent.parent)
        ok = launch_vscode(workspace)
        if ok:
            return jsonify({"ok": True, "workspace": workspace})
        return jsonify({"error": "VS Code not found — install it or add 'code' to PATH"}), 404
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# /api/alerts, /api/alerts/ack: now in monitoring_blueprint.py (Phase 5)


@app.route("/api/csrf")
def api_csrf_token():
    """Generate a CSRF token for form submissions."""
    return jsonify({"token": _generate_csrf_token()})


@app.route("/api/resolutions")
def api_resolutions():
    """Resolution tracking — read data/resolutions.jsonl."""
    resolutions_file = FLEET_DIR / "data" / "resolutions.jsonl"
    if not resolutions_file.exists():
        return jsonify([])
    try:
        entries = []
        for line in resolutions_file.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return jsonify(entries[-50:])
    except Exception:
        return jsonify([])


# /api/usage/*, /api/gate/*, /api/billing/*, /api/scaling/*: now in metering_blueprint.py (Phase 5)


# ── Evolution Leaderboard & Quality Metrics ───────────────────────────────────

@app.route("/api/evolution")
def api_evolution():
    """Evolution leaderboard — skill improvement rates and agent contributions."""
    try:
        conn = get_conn()
        # Top evolved skills (most improved)
        skills = conn.execute("""
            SELECT type as skill, COUNT(*) as evolutions,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as successful
            FROM tasks
            WHERE type IN ('skill_evolve', 'evolution_coordinator', 'skill_test')
            AND created_at >= datetime('now', '-30 days')
            GROUP BY type ORDER BY evolutions DESC
        """).fetchall()

        # Agent contributions
        agents = conn.execute("""
            SELECT assigned_to as agent, COUNT(*) as tasks,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                   ROUND(AVG(intelligence_score), 3) as avg_iq
            FROM tasks
            WHERE created_at >= datetime('now', '-7 days')
            AND assigned_to IS NOT NULL
            GROUP BY assigned_to ORDER BY tasks DESC
        """).fetchall()

        # Quality scores trend
        quality = conn.execute("""
            SELECT DATE(created_at) as day,
                   ROUND(AVG(intelligence_score), 3) as avg_score,
                   COUNT(*) as scored_tasks
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-14 days')
            GROUP BY DATE(created_at) ORDER BY day
        """).fetchall()

        return jsonify({
            "skills": [dict(r) for r in skills],
            "agents": [dict(r) for r in agents],
            "quality_trend": [dict(r) for r in quality],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Agent Cards ───────────────────────────────────────────────────────────────

@app.route("/api/fleet/agent-cards")
def api_agent_cards():
    """Agent Card metadata for all roles."""
    try:
        from agent_cards import generate_all_cards
        config = _load_config()
        return jsonify(generate_all_cards(config))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── DAG Visualization ─────────────────────────────────────────────────────────

@app.route("/api/dag/<int:parent_id>")
def api_dag(parent_id):
    """DAG visualization data for a task chain."""
    try:
        import db
        return jsonify(db.get_dag_graph(parent_id))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Autonomous DAG Builder (v0.200.00b) ──────────────────────────────────────

@app.route("/api/dag/create", methods=["POST"])
def api_dag_create():
    """Parse a natural-language description into a DAG preview (no submission)."""
    try:
        from dag_builder import build_dag_from_description
        data = request.get_json(silent=True) or {}
        description = data.get("description", "").strip()
        if not description:
            return jsonify({"error": "description is required"}), 400
        dag = build_dag_from_description(description)
        return jsonify({"ok": True, "tasks": dag, "count": len(dag)})
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/dag/submit", methods=["POST"])
def api_dag_submit():
    """Submit a DAG for execution. Accepts output from /api/dag/create."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from dag_builder import submit_dag, build_dag_from_description
        data = request.get_json(silent=True) or {}

        # Accept either pre-built tasks or a description to parse
        tasks = data.get("tasks")
        if not tasks:
            description = data.get("description", "").strip()
            if not description:
                return jsonify({"error": "tasks or description required"}), 400
            tasks = build_dag_from_description(description)

        priority = data.get("priority", 5)
        task_ids = submit_dag(tasks, priority=priority)
        return jsonify({"ok": True, "task_ids": task_ids, "root_id": task_ids[0] if task_ids else None})
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/dag/<int:root_id>/status")
def api_dag_status(root_id):
    """DAG execution tree — task statuses and progress."""
    try:
        from dag_builder import get_dag_status
        return jsonify(get_dag_status(root_id))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/dag/<int:root_id>/visualize")
def api_dag_visualize(root_id):
    """DAG nodes + edges for dashboard rendering with levels."""
    try:
        from dag_builder import visualize_dag
        return jsonify(visualize_dag(root_id))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Predictive Scaling (v0.200.00b) ──────────────────────────────────────────

# /api/scaling/*: now in metering_blueprint.py (Phase 5)




# ── MCP Server Status (v0.31.00) ─────────────────────────────────────────────

@app.route("/api/mcp/status")
def api_mcp_status():
    """MCP server status — configured servers with health probes."""
    try:
        from mcp_manager import get_all_server_status, get_skill_mcp_mapping
        servers = get_all_server_status()
        routing = get_skill_mcp_mapping()
        return jsonify({
            "servers": servers,
            "routing": routing,
            "total": len(servers),
            "online": sum(1 for s in servers if s.get("status") == "online"),
            "configured": sum(1 for s in servers if s.get("status") == "configured"),
        })
    except Exception as e:
        return jsonify({"error": str(e), "servers": []}), 500


@app.route("/api/mcp/server/<name>/enable", methods=["POST"])
def api_mcp_enable(name):
    """Enable a default or integration MCP server."""
    try:
        from mcp_manager import enable_default, MCP_INTEGRATIONS, add_server
        enabled = False
        if enable_default(name):
            enabled = True
        elif name in MCP_INTEGRATIONS:
            # Try as integration
            server_def = MCP_INTEGRATIONS[name]
            config = {"type": server_def.get("type", "stdio")}
            if config["type"] == "stdio":
                config["command"] = server_def.get("command", "npx")
                config["args"] = server_def.get("args", [])
            add_server(name, config)
            enabled = True
        if not enabled:
            return jsonify({"error": f"Unknown server: {name}"}), 404
        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="mcp.server.enable",
                resource=f"mcp:{name}",
                detail=f"Enabled MCP server '{name}'",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass
        return jsonify({"status": "enabled", "server": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mcp/server/<name>/disable", methods=["POST"])
def api_mcp_disable(name):
    """Disable (remove) an MCP server."""
    try:
        from mcp_manager import disable_server
        if disable_server(name):
            # Audit log
            try:
                from audit import log_audit
                log_audit(
                    actor=_get_request_role() or "operator",
                    action="mcp.server.disable",
                    resource=f"mcp:{name}",
                    detail=f"Disabled MCP server '{name}'",
                    role=_get_request_role(),
                    ip_address=request.remote_addr,
                )
            except Exception:
                pass
            return jsonify({"status": "disabled", "server": name})
        return jsonify({"error": f"Server not found: {name}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# /api/audit/*, /api/gdpr/*, /api/integrity/*: now in ops_blueprint.py (Phase 5)


# ── OpenAI-Compatible API (v0.25.00) ───────────────────────────────────────


@app.route("/v1/chat/completions", methods=["POST"])
def openai_chat_completions():
    """OpenAI-compatible API adapter for fleet models."""
    data = request.get_json()
    model = data.get("model", "qwen3:8b")
    messages = data.get("messages", [])
    max_tokens = data.get("max_tokens", 2048)
    temperature = data.get("temperature", 0.7)

    system = ""
    prompt = ""
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        elif msg["role"] == "user":
            prompt = msg["content"]

    try:
        from providers import get_backend
        backend = get_backend()
        result = backend.generate(model, prompt, system=system,
                                  max_tokens=max_tokens, temperature=temperature)

        return jsonify({
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result["text"]},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(result["text"].split()),
                "total_tokens": len(prompt.split()) + len(result["text"].split())
            }
        })
    except Exception as e:
        return jsonify({"error": {"message": _safe_error(e), "type": "server_error"}}), 500


# /api/stream route + _sse_broadcaster: now in sse_blueprint.py (Phase 3)


# ── Main page ────────────────────────────────────────────────────────────────
# Template extracted to templates/dashboard.html (TECH_DEBT 4.2)

_TEMPLATE_PATH = FLEET_DIR / "templates" / "dashboard.html"
DASHBOARD_HTML = _TEMPLATE_PATH.read_text(encoding="utf-8") if _TEMPLATE_PATH.exists() else "<h1>Dashboard template missing</h1>"


@app.route("/")
def index():
    from flask import render_template
    try:
        return render_template("dashboard.html")
    except Exception:
        log.warning("Failed to render dashboard template", exc_info=True)
        return Response("<h1>Dashboard template error</h1>", mimetype="text/html")


# ── Agent Disable/Enable ──────────────────────────────────────────────────────

@app.route("/api/fleet/worker/<name>/disable", methods=["POST"])
def worker_disable(name):
    """Disable a worker — adds to disabled_agents list in fleet.toml."""
    if not VALID_AGENT.match(name):
        return jsonify({"error": "Invalid agent name"}), 400
    try:
        cfg = _load_config()
        disabled = cfg.get("fleet", {}).get("disabled_agents", [])
        if name not in disabled:
            disabled.append(name)
            _update_fleet_toml_disabled(disabled)
            _add_alert("info", f"Agent '{name}' disabled by operator", "fleet")
        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="fleet.worker.disable",
                resource=f"worker:{name}",
                detail=f"Disabled agent '{name}'",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass
        return jsonify({"status": "disabled", "agent": name, "disabled_agents": disabled})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fleet/worker/<name>/enable", methods=["POST"])
def worker_enable(name):
    """Enable a worker — removes from disabled_agents list in fleet.toml."""
    if not VALID_AGENT.match(name):
        return jsonify({"error": "Invalid agent name"}), 400
    try:
        cfg = _load_config()
        disabled = cfg.get("fleet", {}).get("disabled_agents", [])
        if name in disabled:
            disabled.remove(name)
            _update_fleet_toml_disabled(disabled)
            _add_alert("info", f"Agent '{name}' enabled by operator", "fleet")
        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="fleet.worker.enable",
                resource=f"worker:{name}",
                detail=f"Enabled agent '{name}'",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass
        return jsonify({"status": "enabled", "agent": name, "disabled_agents": disabled})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Process Control: Cosmo Bot + Dr. Ders ─────────────────────────────────────

def _find_fleet_process(script_name):
    """Find a running fleet process by script name using psutil."""
    import psutil
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if any(script_name in (c or "") for c in cmdline):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


@app.route("/api/fleet/services")
def api_fleet_services():
    """Status of Cosmo Bot (supervisor) and Dr. Ders (hw_supervisor)."""
    services = {}
    # Supervisor (Cosmo Bot)
    sup = _find_fleet_process("supervisor.py")
    if sup:
        try:
            import time
            uptime_s = int(time.time() - sup.create_time())
            services["cosmo_bot"] = {"status": "running", "pid": sup.pid, "uptime_s": uptime_s}
        except Exception:
            services["cosmo_bot"] = {"status": "running", "pid": sup.pid, "uptime_s": 0}
    else:
        services["cosmo_bot"] = {"status": "offline", "pid": None, "uptime_s": 0}

    # Dr. Ders (hw_supervisor)
    ders = _find_fleet_process("hw_supervisor.py")
    if ders:
        try:
            import time
            uptime_s = int(time.time() - ders.create_time())
            services["dr_ders"] = {"status": "running", "pid": ders.pid, "uptime_s": uptime_s}
        except Exception:
            services["dr_ders"] = {"status": "running", "pid": ders.pid, "uptime_s": 0}
    else:
        services["dr_ders"] = {"status": "offline", "pid": None, "uptime_s": 0}

    return jsonify(services)


@app.route("/api/fleet/services/<name>/restart", methods=["POST"])
def api_fleet_service_restart(name):
    """Restart Cosmo Bot or Dr. Ders by terminating — supervisor auto-respawns."""
    import psutil
    script_map = {"cosmo_bot": "supervisor.py", "dr_ders": "hw_supervisor.py"}
    if name not in script_map:
        return jsonify({"error": f"Unknown service: {name}"}), 400

    proc = _find_fleet_process(script_map[name])
    if not proc:
        return jsonify({"error": f"{name} is not running"}), 404

    try:
        # For Dr. Ders: terminate and supervisor.check_alive() respawns it
        # For Cosmo Bot: terminate children first, then supervisor re-execs
        if name == "dr_ders":
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            _add_alert("info", "Dr. Ders restarted by operator", "fleet")
        else:
            # Supervisor restart: signal graceful restart via flag file
            restart_flag = FLEET_DIR / ".restart_requested"
            restart_flag.write_text("1")
            _add_alert("info", "Cosmo Bot restart requested", "fleet")

        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action=f"fleet.service.restart",
                resource=f"service:{name}",
                detail=f"Restarted {name}",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass

        return jsonify({"status": "restarting", "service": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── System Recommendations (0.052.00b) ────────────────────────────────────────

@app.route("/api/recommendations")
def api_system_recommendations():
    """System optimization recommendations (never auto-applied)."""
    recs = []
    try:
        conn = get_conn()
        # Rec 1: Cost optimization — flag expensive skills
        try:
            expensive = conn.execute("""
                SELECT skill, AVG(cost_usd) as avg_cost, COUNT(*) as calls
                FROM usage WHERE created_at >= datetime('now', '-7 days')
                GROUP BY skill HAVING avg_cost > 0.01 ORDER BY avg_cost DESC LIMIT 3
            """).fetchall()
            for r in expensive:
                recs.append({
                    "type": "cost", "skill": r["skill"],
                    "message": f"'{r['skill']}' costs ${r['avg_cost']:.3f}/call ({r['calls']} calls/week). Consider routing to cheaper model.",
                    "action": "review_model_tier",
                })
        except Exception:
            pass

        # Rec 2: Idle agent optimization — too many agents sitting idle
        try:
            idle = conn.execute("""
                SELECT name, last_heartbeat FROM agents
                WHERE status='IDLE' AND last_heartbeat < datetime('now', '-1 hour')
            """).fetchall()
            if len(idle) > 3:
                recs.append({
                    "type": "scaling",
                    "message": f"{len(idle)} agents idle >1 hour. Consider scaling down.",
                    "action": "scale_down",
                })
        except Exception:
            pass

        # Rec 3: Stale prompts — skills with no usage in 30 days
        try:
            stale = conn.execute("""
                SELECT skill, MAX(created_at) as last_used
                FROM usage
                GROUP BY skill
                HAVING last_used < datetime('now', '-30 days')
                ORDER BY last_used ASC LIMIT 5
            """).fetchall()
            for r in stale:
                recs.append({
                    "type": "frequency",
                    "message": f"'{r['skill']}' hasn't been used since {r['last_used'][:10]}. Review if still needed.",
                    "action": "review_frequency",
                })
        except Exception:
            pass
    except Exception:
        pass
    return jsonify({"recommendations": recs, "auto_apply": False})


def _update_fleet_toml_disabled(disabled_list):
    """Update the disabled_agents list in fleet.toml."""
    toml_path = FLEET_DIR / "fleet.toml"
    content = toml_path.read_text(encoding="utf-8")
    arr = "[" + ", ".join(f'"{a}"' for a in disabled_list) + "]"
    new_content = re.sub(
        r'^disabled_agents\s*=\s*\[.*\].*$',
        f'disabled_agents = {arr}  # agents excluded from fleet boot',
        content, count=1, flags=re.MULTILINE
    )
    toml_path.write_text(new_content, encoding="utf-8")


# ── Cluster Data (0.100.00b — Unified Dashboard Hooks) ──────────────────────


@app.route("/api/cluster/agents")
def api_cluster_agents():
    """All agents across all federated peers."""
    try:
        from federation_data import get_cluster_agents
        return jsonify(get_cluster_agents())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/cluster/tasks")
def api_cluster_tasks():
    """All tasks across all federated peers, optionally filtered by status."""
    try:
        status_filter = request.args.get("status")
        from federation_data import get_cluster_tasks
        return jsonify(get_cluster_tasks(status=status_filter))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# /api/cluster/metrics, /api/sla: now in monitoring_blueprint.py (Phase 5)
# /api/cache/*, /api/trigger/*: now in ops_blueprint.py (Phase 5)


# /api/feedback: now in knowledge_blueprint.py (Phase 5)


@app.route("/api/skills/available")
def api_skills_available():
    """List all registered skills with descriptions — for task dispatch picker.

    Scans fleet/skills/*.py for SKILL_NAME and DESCRIPTION module-level constants.
    Results are cached for 60 seconds.
    """
    try:
        if not _check_rate_limit("skills_available", max_per_min=20):
            return jsonify({"error": "Rate limited"}), 429

        # Simple cache to avoid re-scanning on every call
        now = time.time()
        cache = getattr(api_skills_available, '_cache', None)
        if cache and (now - cache['ts']) < 60:
            return jsonify(cache['data'])

        skills_dir = FLEET_DIR / "skills"
        skills = []
        if skills_dir.exists():
            for f in sorted(skills_dir.glob("*.py")):
                if f.name.startswith("_"):
                    continue
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    skill_name = None
                    description = None
                    requires_network = False
                    suite = None
                    tags = None
                    complexity = None
                    for line in content.splitlines()[:40]:
                        line_s = line.strip()
                        if line_s.startswith("SKILL_NAME"):
                            m = re.match(r'^SKILL_NAME\s*=\s*["\'](.+?)["\']', line_s)
                            if m:
                                skill_name = m.group(1)
                        elif line_s.startswith("DESCRIPTION"):
                            m = re.match(r'^DESCRIPTION\s*=\s*["\'](.+?)["\']', line_s)
                            if m:
                                description = m.group(1)
                        elif line_s.startswith("REQUIRES_NETWORK"):
                            requires_network = "True" in line_s
                        elif line_s.startswith("SUITE"):
                            m = re.match(r'^SUITE\s*=\s*["\'](.+?)["\']', line_s)
                            if m:
                                suite = m.group(1)
                        elif line_s.startswith("TAGS"):
                            m = re.match(r'^TAGS\s*=\s*\[(.+?)\]', line_s)
                            if m:
                                tags = [t.strip().strip('"\'') for t in m.group(1).split(",")]
                        elif line_s.startswith("COMPLEXITY"):
                            m = re.match(r'^COMPLEXITY\s*=\s*["\'](.+?)["\']', line_s)
                            if m:
                                complexity = m.group(1)
                    if skill_name:
                        entry = {
                            "name": skill_name,
                            "description": description or "",
                            "requires_network": requires_network,
                            "file": f.name,
                        }
                        if suite:
                            entry["suite"] = suite
                        if tags:
                            entry["tags"] = tags
                        if complexity:
                            entry["complexity"] = complexity
                        skills.append(entry)
                except Exception:
                    pass

        result = {"skills": skills, "total": len(skills)}
        api_skills_available._cache = {'ts': now, 'data': result}
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e), "skills": []}), 500


# ── v0.200: ML Task Routing ──────────────────────────────────────────────────

@app.route("/api/routing/model-status")
def api_routing_model_status():
    """Return ML routing model status: age, accuracy, feature importances."""
    try:
        from ml_router import get_model_status
        return jsonify(get_model_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/routing/retrain", methods=["POST"])
def api_routing_retrain():
    """Trigger manual retrain of the ML routing model."""
    try:
        from ml_router import train_routing_model
        result = train_routing_model()
        if result.get("error"):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/benchmarks/compare")
def api_benchmarks_compare():
    models_param = request.args.get("models", "")
    if not models_param:
        return jsonify({"error": "models parameter required"}), 400
    models_list = [m.strip() for m in models_param.split(",")]
    from skills.benchmark_model import compare_models
    rows = compare_models(models_list)
    return jsonify(rows)


# /api/logs/*, /api/recommendations/*, /api/experiments/*: now in monitoring/knowledge blueprints (Phase 5)


# ── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Refuse to start in air-gap mode (no listening sockets)
    try:
        from config import is_air_gap, load_config
        if is_air_gap(load_config()):
            print("Dashboard disabled — air-gap mode active")
            sys.exit(0)
    except Exception:
        pass

    import logging
    _log = logging.getLogger("dashboard")

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # ── Read bind_address + CORS from config ────────────────────────────────
    cfg = _load_config()
    dash_cfg = cfg.get("dashboard", {})
    bind_addr = dash_cfg.get("bind_address", "127.0.0.1")
    cors_origins_cfg = dash_cfg.get("cors_origins", [])

    # --host flag overrides config when explicitly provided
    if args.host != "127.0.0.1":
        bind_addr = args.host

    # ── Safety gate: remote bind requires auth + TLS ────────────────────────
    if bind_addr not in ("127.0.0.1", "localhost"):
        sec_cfg = cfg.get("security", {})
        token = sec_cfg.get("dashboard_token", "")
        cert_dir = FLEET_DIR / "certs"
        has_tls = (cert_dir / "cert.pem").exists() and (cert_dir / "key.pem").exists()
        safe = True
        if not token:
            _log.error("Remote bind (%s) requires dashboard_token in [security] — falling back to 127.0.0.1", bind_addr)
            safe = False
        if not has_tls:
            _log.error("Remote bind (%s) requires TLS certs (fleet/certs/cert.pem + key.pem) — falling back to 127.0.0.1", bind_addr)
            safe = False
        if not safe:
            bind_addr = "127.0.0.1"

    # Populate module-level CORS list for the after_request handler
    _cors_origins.extend(cors_origins_cfg)

    # Start background threads
    from alerts import start_alert_monitor
    start_alert_monitor()
    threading.Thread(target=_sse_broadcaster, daemon=True).start()

    # Wire api_gate events into SSE stream
    try:
        import api_gate
        def _gate_to_sse(event_type, detail):
            _sse_broadcast({"type": event_type, "data": detail})
        api_gate._event_subscribers.append(_gate_to_sse)
    except Exception:
        pass

    # Fleet mTLS: auto-setup certs if federation TLS enabled
    try:
        from fleet_tls import auto_setup as _fleet_tls_auto_setup
        _fleet_tls_auto_setup()
    except Exception as _ftls_exc:
        _log.debug("Fleet TLS auto-setup skipped: %s", _ftls_exc)

    # TLS: prefer fleet mTLS context (mutual auth), fall back to self-signed cert
    ssl_ctx = None
    try:
        from fleet_tls import is_tls_enabled as _fleet_tls_enabled, get_ssl_context as _fleet_ssl_ctx
        if _fleet_tls_enabled():
            ssl_ctx = _fleet_ssl_ctx("server")
            print(f"Fleet Dashboard v2: https://{bind_addr}:{args.port} (mTLS — fleet CA)")
    except Exception as _mtls_exc:
        _log.debug("Fleet mTLS context not available: %s", _mtls_exc)

    if ssl_ctx is None:
        # Fall back to existing self-signed cert (openssl-based)
        cert, key = _ensure_tls_cert()
        if cert and key:
            ssl_ctx = (cert, key)
            print(f"Fleet Dashboard v2: https://{bind_addr}:{args.port} (TLS)")
        else:
            print(f"Fleet Dashboard v2: http://{bind_addr}:{args.port} (no TLS — openssl not found)")

    # Restore last active mode from fleet.toml
    _restore_mode(app)

    app.run(host=bind_addr, port=args.port, debug=False, threaded=True,
            ssl_context=ssl_ctx)
