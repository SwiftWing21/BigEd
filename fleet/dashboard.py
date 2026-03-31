#!/usr/bin/env python3
"""
Fleet Dashboard v2 — localhost web UI for activity tracking, metrics, and live monitoring.

v0.27: New endpoints (/api/thermal, /api/training, /api/modules, /api/data_stats),
       Server-Sent Events for live updates, alert system.
CT-2:  Cost intelligence endpoints (/api/usage, /api/usage/delta).

31 endpoints total (25 data + 6 process control).

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
_cpu_pct_cache = 0.0

def _cpu_sampler():
    global _cpu_pct_cache
    import psutil
    psutil.cpu_percent(interval=0)  # prime
    while True:
        time.sleep(2)
        try:
            _cpu_pct_cache = psutil.cpu_percent(interval=0)
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
    return response


# ── Mode Control (extracted to mode_blueprint.py) ────────────────────────────
from mode_blueprint import (
    mode_bp, restore_mode as _restore_mode,
    _get_effective_mode, _get_modifier_states,
)
app.register_blueprint(mode_bp)


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
        req = urllib.request.Request("http://localhost:11434/api/tags")
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
            conn = sqlite3.connect(str(rag_db), timeout=2)
            conn.row_factory = sqlite3.Row
            chunks = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]
            conn.close()
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
        req = urllib.request.Request("http://localhost:11434/api/ps")
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

    # ── 4b. FACTORIO LANE (live bridge probe) ────────────────────────
    try:
        import urllib.request as _ur
        from config import load_config as _lc
        _fport = _lc().get("factorio", {}).get("bridge_port", 27016)
        _fresp = _ur.urlopen(f"http://127.0.0.1:{_fport}/api/status", timeout=2)
        _fdata = json.loads(_fresp.read())
        if _fdata.get("running"):
            _ftick = _fdata.get("tick", 0)
            _fpaused = _fdata.get("paused", False)
            lanes["factorio:bridge"] = {
                "agent": "Factorio",
                "kind": "factorio",
                "skills": {"bridge": 1},
                "total": _ftick,
                "done": _ftick if not _fpaused else 0,
                "failed": 0,
                "running": 0 if _fpaused else 1,
                "last_active": None,
                "tick": _ftick,
                "paused": _fpaused,
                "cadence": _fdata.get("cadence", "unknown"),
            }
    except Exception:
        pass

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


@app.route("/api/discussions")
def api_discussions():
    rows = query("""
        SELECT from_agent, body_json, created_at
        FROM messages
        WHERE body_json IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 200
    """)
    topics = defaultdict(lambda: {"agents": set(), "rounds": set(), "count": 0, "last": ""})
    for r in rows:
        try:
            body = json.loads(r["body_json"])
            topic = body.get("topic", "unknown")
            topics[topic]["agents"].add(r["from_agent"])
            topics[topic]["rounds"].add(body.get("round", 1))
            topics[topic]["count"] += 1
            if not topics[topic]["last"] or r["created_at"] > topics[topic]["last"]:
                topics[topic]["last"] = r["created_at"]
        except Exception:
            pass
    result = []
    for topic, data in sorted(topics.items(), key=lambda x: x[1]["last"], reverse=True):
        result.append({
            "topic": topic,
            "agents": sorted(data["agents"]),
            "rounds": max(data["rounds"]) if data["rounds"] else 0,
            "contributions": data["count"],
            "last_activity": data["last"],
        })
    return jsonify(result)


@app.route("/api/knowledge")
def api_knowledge():
    if not _check_rate_limit("knowledge", 5):
        return jsonify({"error": "Rate limited"}), 429
    categories = {}
    if not KNOWLEDGE_DIR.exists():
        return jsonify(categories)
    for subdir in sorted(KNOWLEDGE_DIR.iterdir()):
        if subdir.is_dir():
            files = list(subdir.rglob("*"))
            file_list = [
                {"name": str(f.relative_to(KNOWLEDGE_DIR)), "size": f.stat().st_size,
                 "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
                for f in files if f.is_file()
            ]
            categories[subdir.name] = {
                "count": len(file_list),
                "files": sorted(file_list, key=lambda x: x["modified"], reverse=True)[:20],
            }
        elif subdir.is_file():
            categories[subdir.name] = {
                "count": 1,
                "files": [{"name": subdir.name, "size": subdir.stat().st_size,
                           "modified": datetime.fromtimestamp(subdir.stat().st_mtime).isoformat()}],
            }
    return jsonify(categories)


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


@app.route("/api/reviews")
def api_reviews():
    reviews = []
    for review_dir in [KNOWLEDGE_DIR / "code_reviews", KNOWLEDGE_DIR / "fma_reviews"]:
        if not review_dir.exists():
            continue
        for f in sorted(review_dir.glob("*_review_*.md"), reverse=True)[:30]:
            try:
                content = f.read_text(errors="ignore")
                lines = content.splitlines()[:6]
                reviews.append({
                    "file": f.name,
                    "category": review_dir.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "header": "\n".join(lines),
                })
            except Exception:
                pass
    return jsonify(reviews)


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


@app.route("/api/rag")
def api_rag():
    if not _check_rate_limit("rag", 5):
        return jsonify({"error": "Rate limited"}), 429
    rag_db = FLEET_DIR / "rag.db"
    if not rag_db.exists():
        return jsonify({"files": 0, "chunks": 0, "sources": []})
    try:
        conn = sqlite3.connect(rag_db, timeout=5)
        conn.row_factory = sqlite3.Row
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]
        sources = [
            dict(r) for r in conn.execute(
                "SELECT path, chunks, indexed FROM files ORDER BY indexed DESC LIMIT 30"
            ).fetchall()
        ]
        conn.close()
        return jsonify({"files": files, "chunks": chunks, "sources": sources})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "files": 0, "chunks": 0, "sources": []})


# ── v0.27 New API endpoints ──────────────────────────────────────────────────

@app.route("/api/thermal")
def api_thermal():
    """Live GPU/CPU temps, fan speed, power, ambient estimate."""
    result = {
        "gpu_temp_c": 0, "gpu_power_w": 0, "gpu_fan_pct": 0,
        "gpu_vram_used_gb": 0, "gpu_vram_total_gb": 0,
        "cpu_temp_c": None, "ambient_estimate_c": None,
        "thermal_state": "unknown", "model_tier": "unknown",
    }

    # Read from hw_state.json (written by hw_supervisor.write_state())
    # Thermal data is nested under hw["thermal"], not top-level
    if HW_STATE_JSON.exists():
        try:
            hw = json.loads(HW_STATE_JSON.read_text())
            th = hw.get("thermal", {})
            model = hw.get("model", "unknown")

            # Determine model tier from model name
            tier_map = {"qwen3:8b": "default", "qwen3:4b": "mid",
                        "qwen3:1.7b": "low", "qwen3:0.6b": "critical"}
            model_tier = tier_map.get(model, model or "unknown")

            cpu_t = th.get("cpu_temp_c", 0)
            ambient_t = th.get("ambient_est_c", 0)
            result.update({
                "gpu_temp_c": th.get("gpu_temp_c", 0),
                "gpu_power_w": th.get("gpu_power_w", 0),
                "gpu_fan_pct": th.get("gpu_fan_pct", 0),
                "gpu_vram_used_gb": round(th.get("vram_used_gb", 0), 2),
                "gpu_vram_total_gb": round(th.get("vram_total_gb", 0), 2),
                "cpu_temp_c": cpu_t if cpu_t and cpu_t > 0 else None,
                "ambient_estimate_c": ambient_t if ambient_t and ambient_t > 0 else None,
                "thermal_state": hw.get("status", "unknown"),
                "model_tier": model_tier,
            })
        except Exception:
            pass

    # Fallback: read GPU directly if hw_state.json has no thermal data
    if result["gpu_temp_c"] == 0:
        try:
            from gpu import detect_gpu, read_telemetry
            backend, has_gpu = detect_gpu()
            if has_gpu:
                gpu_data = read_telemetry(backend)
                if gpu_data:
                    result["gpu_temp_c"] = gpu_data.get("gpu_temp_c", 0)
                    result["gpu_power_w"] = gpu_data.get("gpu_power_w", 0)
                    result["gpu_fan_pct"] = gpu_data.get("gpu_fan_pct", 0)
                    result["gpu_vram_used_gb"] = round(gpu_data.get("vram_used_gb", 0), 2)
                    result["gpu_vram_total_gb"] = round(gpu_data.get("vram_total_gb", 0), 2)
        except Exception:
            pass

    # Fallback: read CPU temp directly if hw_state.json has no CPU data
    if not result["cpu_temp_c"]:
        try:
            from cpu_temp import read_cpu_temp
            val = read_cpu_temp()
            if val > 0:
                result["cpu_temp_c"] = val
        except Exception:
            pass

    # Annotate if GPU sensor is still unreadable after fallbacks
    if result["gpu_temp_c"] == 0:
        result["_note"] = "0\u00b0C = unable to access GPU sensor"

    # System resources (always available, even without GPU)
    try:
        import psutil
        ram = psutil.virtual_memory()
        result["system"] = {
            "ram_total_gb": round(ram.total / (1024**3), 1),
            "ram_used_gb": round(ram.used / (1024**3), 1),
            "ram_pct": ram.percent,
            "cpu_pct": _cpu_pct_cache,
            "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
        }
    except Exception:
        pass

    # Add config thresholds
    cfg = _load_config()
    thermal = cfg.get("thermal", {})
    result["thresholds"] = {
        "gpu_sustained": thermal.get("gpu_max_sustained_c", 75),
        "gpu_burst": thermal.get("gpu_max_burst_c", 78),
        "cpu_sustained": thermal.get("cpu_max_sustained_c", 80),
        "cooldown_target": thermal.get("cooldown_target_c", 72),
    }

    return jsonify(result)


@app.route("/api/fleet/provider-health")
def api_provider_health():
    try:
        from providers import get_provider_health
        return jsonify(get_provider_health())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


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


# ── Factorio endpoints (extracted to factorio_blueprint.py) ─────────────────
from factorio_blueprint import (
    factorio_bp,
    _factorio_kill_all,
    api_factorio_start,
    api_factorio_stop,
)
app.register_blueprint(factorio_bp)

# ── SSE endpoint (extracted to sse_blueprint.py) ─────────────────────────────
from sse_blueprint import sse_bp, _sse_broadcaster
app.register_blueprint(sse_bp)


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


@app.route("/api/filesystem/audit")
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
                import re as _re
                m = _re.match(
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
                    skill_m = _re.search(r"skill=(\S+)", m.group(4))
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
            conn = sqlite3.connect(str(tools_db), timeout=5)
            conn.row_factory = sqlite3.Row
            for table in ["crm", "accounts", "onboarding", "customers", "agents"]:
                if table not in ALLOWED_TOOLS_TABLES:
                    continue
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    stats[f"tools.{table}"] = {"count": count}
                except Exception:
                    pass
            conn.close()
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


@app.route("/api/alerts")
def api_alerts():
    """Return current alerts — in-memory + persistent DB alerts."""
    hours = int(request.args.get("hours", 24))
    severity = request.args.get("severity")
    # In-memory alerts (legacy SSE-based)
    with _alert_lock:
        mem_alerts = list(_alerts[-50:])
    # Persistent DB alerts (0.22.00)
    try:
        import db
        db_alerts = db.get_alerts(hours=hours, severity=severity)
    except Exception:
        db_alerts = []
    return jsonify({"memory": mem_alerts, "persistent": db_alerts})


@app.route("/api/alerts/ack/<int:alert_id>", methods=["POST"])
@_require_role("operator")
def api_ack_alert(alert_id):
    """Acknowledge an alert."""
    with _alert_lock:
        for a in _alerts:
            if a["id"] == alert_id:
                a["acknowledged"] = True
                return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


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


# ── CT-2: Cost Intelligence endpoints ─────────────────────────────────────

@app.route("/api/usage")
def api_usage():
    """CT-2: Token usage aggregates by skill/model/agent."""
    try:
        import db
        period = request.args.get("period", "week")
        group = request.args.get("group", "skill")
        return jsonify(db.get_usage_summary(period, group))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/usage/delta")
def api_usage_delta():
    """CT-2: Compare usage between two date ranges."""
    try:
        import db
        from_start = request.args.get("from_start", "")
        from_end = request.args.get("from_end", "")
        to_start = request.args.get("to_start", "")
        to_end = request.args.get("to_end", "")
        if not all([from_start, from_end, to_start, to_end]):
            return jsonify({"error": "Required params: from_start, from_end, to_start, to_end"}), 400
        return jsonify(db.get_usage_delta(from_start, from_end, to_start, to_end))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/usage/budgets")
def api_usage_budgets():
    """CT-4: Token budget status — daily spend vs configured limits."""
    try:
        config = _load_config()
        budgets = config.get("budgets", {})
        if not budgets:
            return jsonify({"budgets": [], "message": "No budgets configured"})

        import db
        summary = db.get_usage_summary(period="day", group_by="skill")
        spent_map = {r["skill"]: r.get("total_cost", 0) or 0 for r in summary}

        # Filter out non-budget config keys (period, enforcement, etc.)
        enforcement = budgets.get("enforcement", "block")
        period = budgets.get("period", "day")
        result = []
        for skill, limit_usd in sorted(budgets.items()):
            if not isinstance(limit_usd, (int, float)):
                continue  # skip config keys like "period", "enforcement"
            spent = spent_map.get(skill, 0)
            result.append({
                "skill": skill,
                "budget_usd": limit_usd,
                "spent_usd": round(spent, 6),
                "remaining_usd": round(max(0, limit_usd - spent), 6),
                "exceeded": spent >= limit_usd,
                "pct_used": round(spent / limit_usd * 100, 1) if limit_usd > 0 else 0,
            })
        return jsonify({"budgets": result, "enforcement": enforcement, "period": period})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/usage/dashboard")
def api_usage_dashboard():
    """Cost intelligence dashboard — live spend, projections, per-provider breakdown."""
    try:
        conn = get_conn()
        result = {"providers": {}, "today": {}, "week": {}, "month": {}, "projection": {}}

        # Per-provider totals (today)
        for period, label, interval in [
            ("today", "Today", "-1 day"),
            ("week", "7 days", "-7 days"),
            ("month", "30 days", "-30 days"),
        ]:
            rows = conn.execute(f"""
                SELECT provider,
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cost_usd), 0) as cost_usd,
                       COUNT(*) as calls
                FROM usage
                WHERE created_at >= datetime('now', '{interval}')
                GROUP BY provider
            """).fetchall()
            period_data = {}
            total_cost = 0
            total_tokens = 0
            for r in rows:
                p = r["provider"] or "local"
                period_data[p] = {
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                    "cost_usd": round(r["cost_usd"], 4),
                    "calls": r["calls"],
                }
                total_cost += r["cost_usd"]
                total_tokens += r["input_tokens"] + r["output_tokens"]
            period_data["_total"] = {
                "cost_usd": round(total_cost, 4),
                "tokens": total_tokens,
                "calls": sum(d["calls"] for d in period_data.values() if isinstance(d, dict) and "calls" in d),
            }
            result[period] = period_data

        # Top skills by cost (last 7 days)
        top_skills = conn.execute("""
            SELECT skill, provider,
                   COALESCE(SUM(cost_usd), 0) as cost_usd,
                   COALESCE(SUM(input_tokens + output_tokens), 0) as tokens,
                   COUNT(*) as calls
            FROM usage
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY skill, provider
            ORDER BY cost_usd DESC
            LIMIT 20
        """).fetchall()
        result["top_skills"] = [dict(r) for r in top_skills]

        # Daily cost trend (last 14 days)
        daily = conn.execute("""
            SELECT DATE(created_at) as day,
                   COALESCE(SUM(cost_usd), 0) as cost_usd,
                   COALESCE(SUM(input_tokens + output_tokens), 0) as tokens
            FROM usage
            WHERE created_at >= datetime('now', '-14 days')
            GROUP BY DATE(created_at)
            ORDER BY day
        """).fetchall()
        result["daily_trend"] = [dict(r) for r in daily]

        # Projection: based on 7-day average
        if result["week"].get("_total", {}).get("cost_usd", 0) > 0:
            weekly_cost = result["week"]["_total"]["cost_usd"]
            result["projection"] = {
                "monthly_usd": round(weekly_cost * 4.3, 2),
                "yearly_usd": round(weekly_cost * 52, 2),
                "daily_avg_usd": round(weekly_cost / 7, 4),
            }

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/usage/regression")
def api_usage_regression():
    """CT-3: Flag skills with >20% token increase vs previous period."""
    try:
        import db
        from datetime import datetime, timedelta

        now = datetime.now()
        # Compare last 7 days vs previous 7 days
        to_end = now.strftime("%Y-%m-%d %H:%M:%S")
        to_start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        from_end = to_start
        from_start = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

        deltas = db.get_usage_delta(from_start, from_end, to_start, to_end)
        regressions = [d for d in deltas if d.get("delta_pct", 0) > 20]
        return jsonify({
            "period": {"from": f"{from_start} to {from_end}", "to": f"{to_start} to {to_end}"},
            "regressions": regressions,
            "total_skills_checked": len(deltas),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# -- API Gate -----------------------------------------------------------------

@app.route("/api/gate/status")
def api_gate_status():
    import api_gate
    return jsonify(api_gate.status())

@app.route("/api/gate/enable", methods=["POST"])
def api_gate_enable():
    import api_gate
    data = request.get_json(silent=True) or {}
    budget = float(data.get("budget", 0))
    providers = data.get("providers", [])
    ttl = data.get("ttl_hours")
    drain = data.get("drain_mode", "graceful")
    if budget <= 0:
        return jsonify({"error": "budget must be > 0"}), 400
    if not providers:
        return jsonify({"error": "at least one provider required"}), 400
    cfg = _load_config()
    from config import is_offline, is_air_gap
    if is_offline(cfg) or is_air_gap(cfg):
        return jsonify({"error": "Cannot enable API gate — offline_mode or air_gap_mode is active"}), 409
    result = api_gate.enable(budget, providers, ttl, drain)
    return jsonify(result)

@app.route("/api/gate/disable", methods=["POST"])
def api_gate_disable():
    import api_gate
    return jsonify(api_gate.disable())

@app.route("/api/gate/drain-mode", methods=["PUT"])
def api_gate_drain_mode():
    import api_gate
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "graceful")
    if mode not in ("graceful", "hard"):
        return jsonify({"error": "mode must be 'graceful' or 'hard'"}), 400
    return jsonify(api_gate.set_drain_mode(mode))

@app.route("/api/gate/ring")
def api_gate_ring():
    import api_gate
    limit = request.args.get("limit", 200, type=int)
    return jsonify(api_gate.get_ring(limit))


# ── Billing / Metering per Tenant (v0.300.00b) ───────────────────────────────

@app.route("/api/billing/<tenant_id>/usage")
def api_billing_usage(tenant_id):
    """Per-tenant usage summary for a billing period."""
    try:
        from billing import get_tenant_usage
        period = request.args.get("period", "month")
        return jsonify(get_tenant_usage(tenant_id, period))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/billing/<tenant_id>/invoice")
def api_billing_invoice(tenant_id):
    """Itemized invoice for a tenant."""
    try:
        from billing import calculate_invoice, export_invoice_csv
        period = request.args.get("period", "month")
        fmt = request.args.get("format", "json")
        if fmt == "csv":
            csv_data = export_invoice_csv(tenant_id, period)
            return Response(csv_data, mimetype="text/csv",
                            headers={"Content-Disposition":
                                     f"attachment; filename=invoice_{tenant_id}_{period}.csv"})
        return jsonify(calculate_invoice(tenant_id, period))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/billing/<tenant_id>/quota")
def api_billing_quota(tenant_id):
    """Quota status — current usage vs limits."""
    try:
        from billing import get_quota_usage
        return jsonify(get_quota_usage(tenant_id))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/billing/<tenant_id>/quota", methods=["PUT"])
@_require_role("admin")
def api_billing_quota_update(tenant_id):
    """Update quota limits for a tenant (admin only)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        from billing import set_quota, get_quota
        set_quota(tenant_id, data)
        return jsonify({"status": "updated", "quota": get_quota(tenant_id)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/billing/overview")
@_require_role("admin")
def api_billing_overview():
    """Admin view — usage across all tenants."""
    try:
        from billing import get_all_tenant_usage
        period = request.args.get("period", "month")
        return jsonify({"period": period, "tenants": get_all_tenant_usage(period)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/billing/pricing")
def api_billing_pricing():
    """Current pricing tiers from config."""
    try:
        from billing import get_pricing
        return jsonify(get_pricing())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


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

@app.route("/api/scaling/prediction")
def api_scaling_prediction():
    """Current ML prediction vs actual agent count."""
    try:
        from predictive_scaler import get_prediction_summary
        return jsonify(get_prediction_summary())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/scaling/retrain", methods=["POST"])
def api_scaling_retrain():
    """Trigger scaler model retrain from historical data."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from predictive_scaler import train_scaler_model
        result = train_scaler_model()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Process Control (extracted to process_control.py) ─────────────────────────
from process_control import fleet_bp
app.register_blueprint(fleet_bp)

# ── Self-Healing Fleet Health API (v0.200.00b) ────────────────────────────────
from health_api import health_bp
app.register_blueprint(health_bp)

# ── Geo-Distributed Fleets (v0.400.00b) ───────────────────────────────────────
from geo_api import geo_bp
app.register_blueprint(geo_bp)

# ── A2A Protocol (Agent-to-Agent interoperability) ────────────────────────────
from a2a import a2a_bp
app.register_blueprint(a2a_bp)

# ── Tenant Key Management (v0.300.00b — Enterprise Encryption) ────────────────
from tenant_crypto_api import tenant_crypto_bp
app.register_blueprint(tenant_crypto_bp)


# ── Tenant Admin (v0.300.00b) ─────────────────────────────────────────────────
from tenant_admin import tenant_bp
app.register_blueprint(tenant_bp)

# ── SSO / OIDC / SAML Authentication (v0.300.00b) ────────────────────────────
try:
    from sso import register_sso as _register_sso
    _register_sso(app)
except Exception as _sso_exc:
    import logging as _sso_logging
    _sso_logging.getLogger("dashboard").debug("SSO module not loaded: %s", _sso_exc)

# ── Compliance Reporting (v0.300.00b) ─────────────────────────────────────────
try:
    from compliance import create_compliance_blueprint
    _compliance_bp = create_compliance_blueprint(_require_role)
    app.register_blueprint(_compliance_bp)
except ImportError:
    pass  # compliance module optional

# ── Marketplace with Reviews (v0.400.00b) ─────────────────────────────────────
try:
    from marketplace import marketplace_bp
    app.register_blueprint(marketplace_bp)
except ImportError:
    pass  # marketplace module optional

try:
    from control_plane import platform_bp
    app.register_blueprint(platform_bp)
except ImportError:
    pass  # control_plane module optional

try:
    from self_service import self_service_bp
    app.register_blueprint(self_service_bp)
except ImportError:
    pass  # self_service module optional

try:
    from payments import register_payment_routes
    register_payment_routes(app)
except ImportError:
    pass  # payments module optional

# ── Hybrid ViewPort — Views API (Phase 2) ────────────────────────────────────
try:
    from views_blueprint import views_bp
    app.register_blueprint(views_bp)
    # Auto-discover and register view data sources at startup
    import view_registry
    view_registry.discover_and_register()
    # Register knowledge graph source (scans skills for I/O folder mappings)
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
    pass  # views module optional

# ── Module Manager API (v1.0) ──────────────────────────────────────────────
from modules_blueprint import modules_bp
app.register_blueprint(modules_bp)

# ── Ingestion Hub (v0.900.00b) ─────────────────────────────────────────
try:
    from ingest_blueprint import ingest_bp
    app.register_blueprint(ingest_bp)
except ImportError:
    pass  # ingest module optional


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


# ── Audit Log (enhanced — DB-backed structured audit trail) ───────────────

@app.route("/api/audit")
def api_audit():
    """Paginated audit trail with filter params.

    Query params:
        actor   — filter by actor (exact)
        action  — filter by action (exact)
        from    — events after this ISO timestamp
        to      — events before this ISO timestamp
        resource — filter by resource (contains)
        limit   — max rows (default 100, max 1000)
        offset  — pagination offset
        summary — if truthy, return legacy audit_log.py summary instead
        legacy  — if truthy, return legacy file-based events
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


@app.route("/api/audit/export")
def api_audit_export():
    """Download audit export as JSON or CSV.

    Query params:
        fmt    — "json" (default) or "csv"
        actor  — filter by actor
        action — filter by action
        from   — events after this ISO timestamp
        to     — events before this ISO timestamp
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


@app.route("/api/audit/purge", methods=["POST"])
@_require_role("admin")
def api_audit_purge():
    """Trigger retention purge — admin only.

    JSON body:
        older_than_days — retention window (default 365, minimum 1)
    """
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


@app.route("/api/gdpr/erasure", methods=["POST"])
@_require_role("admin")
def api_gdpr_erasure():
    """GDPR Art. 17: Right to erasure."""
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
# ── Knowledge Integrity ─────────────────────────────────────────────────────

@app.route("/api/integrity")
def api_integrity():
    try:
        from integrity import verify_integrity
        return jsonify(verify_integrity())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/integrity/refresh", methods=["POST"])
@_require_role("operator")
def api_integrity_refresh():
    try:
        from integrity import save_manifest
        path = save_manifest()
        return jsonify({"status": "manifest_saved", "path": str(path)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


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
    template = FLEET_DIR / "templates" / "dashboard.html"
    if template.exists():
        return Response(template.read_text(encoding="utf-8"), mimetype="text/html")
    return Response(DASHBOARD_HTML, mimetype="text/html")  # fallback to cached


# ── Settings (extracted to settings_blueprint.py) ─────────────────────────
from settings_blueprint import settings_bp
app.register_blueprint(settings_bp)


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


# ── Federation (extracted to federation_blueprint.py) ─────────────────────
from federation_blueprint import federation_bp
app.register_blueprint(federation_bp)


# ── Deploy (extracted to deploy_blueprint.py) ─────────────────────────────
from deploy_blueprint import deploy_bp
app.register_blueprint(deploy_bp)


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


@app.route("/api/cluster/metrics")
def api_cluster_metrics():
    """Aggregated metrics across all federated peers."""
    try:
        from federation_data import get_cluster_metrics
        return jsonify(get_cluster_metrics())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── SLA Monitoring (0.135.00b — Enterprise & Multi-Tenant) ──────────────────

@app.route("/api/sla")
def api_sla():
    """SLA monitoring -- task completion time guarantees."""
    try:
        conn = get_conn()
        # Average completion time by skill (last 7 days)
        metrics = conn.execute("""
            SELECT type as skill,
                   COUNT(*) as tasks,
                   AVG(CAST((julianday(
                       CASE WHEN status='DONE' THEN created_at END
                   ) - julianday(created_at)) * 86400 AS INTEGER)) as avg_secs,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate
            FROM tasks
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY type
            ORDER BY tasks DESC LIMIT 20
        """).fetchall()

        # Overall fleet SLA
        overall = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                   SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
            FROM tasks WHERE created_at >= datetime('now', '-24 hours')
        """).fetchone()

        return jsonify({
            "skills": [dict(r) for r in metrics],
            "overall_24h": {
                "total": overall["total"],
                "success_rate": round(overall["done"] / max(overall["total"], 1) * 100, 1),
                "failure_rate": round(overall["failed"] / max(overall["total"], 1) * 100, 1),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Cache Management (fleet-wide invalidation) ───────────────────────────────

@app.route("/api/cache/stats")
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


@app.route("/api/cache/invalidate", methods=["POST"])
@_require_role("operator")
def api_cache_invalidate():
    """Invalidate all caches, or a specific one via ?name=X or JSON body."""
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


@app.route("/api/cache/invalidate/<name>", methods=["POST"])
@_require_role("operator")
def api_cache_invalidate_named(name):
    """Invalidate a specific cache by name."""
    try:
        from cache_manager import invalidate
        ok = invalidate(name)
        if not ok:
            return jsonify({"error": f"Unknown cache: {name}"}), 404
        return jsonify({"invalidated": name, "success": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Event Triggers: Webhook endpoint ──────────────────────────────────────────

@app.route("/api/trigger", methods=["POST"])
@_require_role("operator")
def api_trigger():
    """Webhook: receive external event and dispatch a fleet task.

    Required: type (skill name).
    Optional: payload (dict), priority (1-10), assigned_to (agent name).
    Returns: {"task_id": N} on success.
    """
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


@app.route("/api/trigger/status")
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
                import json as _json
                schedule_state = _json.loads(state_file.read_text(encoding="utf-8"))
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


# ── Human Feedback ────────────────────────────────────────────────────────────

@app.route("/api/feedback", methods=["POST"])
def api_submit_feedback():
    """Submit human feedback on an agent output.

    Body JSON:
        output_path (str):    path or 'task:<id>' identifying the output
        verdict (str):        'approved' or 'rejected'
        feedback_text (str):  optional free-text explanation
        agent_name (str):     optional agent that produced the output
        skill_type (str):     optional skill that produced the output
    """
    try:
        if not _check_rate_limit("feedback_submit", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        data = request.get_json(silent=True) or {}
        output_path = (data.get("output_path") or "").strip()
        verdict = (data.get("verdict") or "").strip().lower()
        feedback_text = (data.get("feedback_text") or "").strip()
        agent_name = (data.get("agent_name") or "").strip()
        skill_type = (data.get("skill_type") or "").strip()

        if not output_path:
            return jsonify({"error": "output_path required"}), 400
        if verdict not in ("approved", "rejected"):
            return jsonify({"error": "verdict must be 'approved' or 'rejected'"}), 400

        # Store feedback
        import db
        db.submit_feedback(output_path, verdict, feedback_text, agent_name, skill_type)

        # Process reinforcement (IQ adjustments + re-review dispatch)
        result = {"output_path": output_path, "verdict": verdict}
        try:
            from reinforcement import process_approved, process_rejected, process_ditl_rejection

            if verdict == "approved":
                new_score = process_approved(output_path, agent_name, skill_type)
                if new_score is not None:
                    result["new_iq"] = new_score

            elif verdict == "rejected":
                # Dispatch re-review task
                re_task = process_rejected(output_path, agent_name, skill_type, feedback_text)
                if re_task is not None:
                    result["re_review_task_id"] = re_task

                # DITL: if enabled and rejected, also log PHI audit + clinical review
                try:
                    cfg = _load_config()
                    if cfg.get("ditl", {}).get("enabled", False):
                        ditl_result = process_ditl_rejection(output_path, agent_name, feedback_text)
                        if ditl_result:
                            result["ditl_audit_id"] = ditl_result.get("audit_id")
                            result["ditl_task_id"] = ditl_result.get("task_id")
                except Exception:
                    pass  # DITL is optional — never block feedback on it

        except Exception:
            pass  # reinforcement is enhancement — never block feedback storage

        # Broadcast SSE event so dashboard updates live
        _broadcast_sse({
            "type": "feedback",
            "data": {
                "output_path": output_path,
                "verdict": verdict,
                "agent_name": agent_name,
                "skill_type": skill_type,
            },
        })

        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/feedback", methods=["GET"])
def api_get_feedback():
    """Query feedback with filters.

    Query params:
        output_path (str):  exact match on output path
        agent (str):        filter by agent_name
        skill (str):        filter by skill_type
        verdict (str):      filter by verdict (approved/rejected/neutral)
        days (int):         lookback window in days (default 30)
        limit (int):        max rows (default 100, max 500)
    """
    try:
        if not _check_rate_limit("feedback_get", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        output_path = request.args.get("output_path", "").strip()
        agent = request.args.get("agent", "").strip()
        skill = request.args.get("skill", "").strip()
        verdict = request.args.get("verdict", "").strip()
        days = min(365, max(1, int(request.args.get("days", 30))))
        limit = min(500, max(1, int(request.args.get("limit", 100))))

        # If output_path is given, return single feedback
        if output_path:
            import db
            fb = db.get_feedback(output_path)
            return jsonify({"feedback": fb})

        # Otherwise, query with filters
        import db
        clauses = ["created_at >= datetime('now', ?)"]
        params = [f"-{days} days"]

        if agent:
            clauses.append("agent_name = ?")
            params.append(agent)
        if skill:
            clauses.append("skill_type = ?")
            params.append(skill)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)

        where = " AND ".join(clauses)
        params.append(limit)

        with db.get_conn() as conn:
            rows = conn.execute(
                f"""SELECT id, output_path, verdict, feedback_text, operator,
                           agent_name, skill_type, created_at
                    FROM output_feedback
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()

        return jsonify({"feedback": [dict(r) for r in rows], "count": len(rows)})

    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/feedback/stats")
def api_feedback_stats():
    """Feedback stats: approval rate by agent, by skill, trend.

    Query params:
        days (int): lookback window in days (default 7)
    """
    try:
        if not _check_rate_limit("feedback_stats", max_per_min=20):
            return jsonify({"error": "rate limited"}), 429

        days = min(365, max(1, int(request.args.get("days", 7))))

        import db
        raw = db.get_feedback_stats(days=days)

        # Pivot into by-agent and by-skill summaries
        by_agent = {}
        by_skill = {}
        totals = {"approved": 0, "rejected": 0, "neutral": 0}

        for row in raw:
            agent = row.get("agent_name") or "unknown"
            skill = row.get("skill_type") or "unknown"
            v = row.get("verdict", "neutral")
            cnt = row.get("cnt", 0)

            totals[v] = totals.get(v, 0) + cnt

            if agent not in by_agent:
                by_agent[agent] = {"approved": 0, "rejected": 0, "neutral": 0}
            by_agent[agent][v] = by_agent[agent].get(v, 0) + cnt

            if skill not in by_skill:
                by_skill[skill] = {"approved": 0, "rejected": 0, "neutral": 0}
            by_skill[skill][v] = by_skill[skill].get(v, 0) + cnt

        # Compute approval rates
        total_reviewed = totals["approved"] + totals["rejected"]
        approval_rate = round(totals["approved"] / total_reviewed, 3) if total_reviewed else None

        for d in list(by_agent.values()) + list(by_skill.values()):
            reviewed = d["approved"] + d["rejected"]
            d["approval_rate"] = round(d["approved"] / reviewed, 3) if reviewed else None

        # Daily trend (last N days)
        trend = []
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    """SELECT DATE(created_at) as day, verdict, COUNT(*) as cnt
                       FROM output_feedback
                       WHERE created_at >= datetime('now', ?)
                       GROUP BY day, verdict
                       ORDER BY day""",
                    (f"-{days} days",),
                ).fetchall()
            trend_map = {}
            for r in rows:
                day = r["day"]
                if day not in trend_map:
                    trend_map[day] = {"day": day, "approved": 0, "rejected": 0, "neutral": 0}
                trend_map[day][r["verdict"]] = r["cnt"]
            trend = list(trend_map.values())
        except Exception:
            pass

        return jsonify({
            "days": days,
            "totals": totals,
            "approval_rate": approval_rate,
            "by_agent": by_agent,
            "by_skill": by_skill,
            "trend": trend,
        })

    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Tasks & Queue (extracted to tasks_blueprint.py) ───────────────────────
from tasks_blueprint import tasks_bp
app.register_blueprint(tasks_bp)


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


# ── Log Viewer ───────────────────────────────────────────────────────────────

@app.route("/api/logs/stream")
def api_logs_stream():
    """SSE endpoint streaming log lines (tail -f style).

    Reads from fleet/logs/supervisor.log and streams new lines.
    Query params:
        source: "supervisor" (default), "dashboard", or "worker"
    """
    import queue as queue_mod

    source = request.args.get("source", "supervisor").strip()
    allowed_sources = {
        "supervisor": FLEET_DIR / "logs" / "supervisor.log",
        "dashboard": FLEET_DIR / "logs" / "dashboard.log",
    }

    log_path = allowed_sources.get(source)
    if log_path is None:
        # Also allow worker logs: worker_<name>.log
        if source.startswith("worker_") and re.match(r'^worker_[a-zA-Z0-9_-]+$', source):
            log_path = FLEET_DIR / "logs" / f"{source}.log"
        else:
            return jsonify({"error": f"Unknown log source: {source}"}), 400

    def generate():
        try:
            yield f"data: {{\"type\": \"connected\", \"source\": \"{source}\"}}\n\n"

            if not log_path.exists():
                yield f"data: {{\"type\": \"info\", \"line\": \"Log file not found: {log_path.name}\"}}\n\n"
                return

            # Start by sending last 50 lines
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    for line in lines[-50:]:
                        line = line.rstrip()
                        if line:
                            escaped = json.dumps(line)
                            yield f"data: {{\"type\": \"log\", \"line\": {escaped}}}\n\n"
            except Exception:
                pass

            # Then tail for new lines
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)  # Seek to end
                    while True:
                        line = f.readline()
                        if line:
                            line = line.rstrip()
                            if line:
                                escaped = json.dumps(line)
                                yield f"data: {{\"type\": \"log\", \"line\": {escaped}}}\n\n"
                        else:
                            # No new data — send keepalive
                            yield ": keepalive\n\n"
                            time.sleep(1)
            except GeneratorExit:
                pass
        except GeneratorExit:
            pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/api/logs/recent")
def api_logs_recent():
    """Last N log lines as JSON array.

    Query params:
        n (int):      number of lines (default 100, max 1000)
        source (str): "supervisor" (default), "dashboard", or "worker_<name>"
        filter (str): optional substring filter
    """
    try:
        n = min(1000, max(1, int(request.args.get("n", 100))))
        source = request.args.get("source", "supervisor").strip()
        line_filter = request.args.get("filter", "").strip()

        allowed_sources = {
            "supervisor": FLEET_DIR / "logs" / "supervisor.log",
            "dashboard": FLEET_DIR / "logs" / "dashboard.log",
        }

        log_path = allowed_sources.get(source)
        if log_path is None:
            if source.startswith("worker_") and re.match(r'^worker_[a-zA-Z0-9_-]+$', source):
                log_path = FLEET_DIR / "logs" / f"{source}.log"
            else:
                return jsonify({"error": f"Unknown log source: {source}"}), 400

        if not log_path.exists():
            return jsonify({"lines": [], "total": 0, "source": source})

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception as e:
            return jsonify({"error": _safe_error(e), "lines": []}), 500

        # Apply filter if provided
        if line_filter:
            all_lines = [l for l in all_lines if line_filter.lower() in l.lower()]

        # Return last N lines
        recent = [l.rstrip() for l in all_lines[-n:] if l.strip()]

        return jsonify({
            "lines": recent,
            "total": len(recent),
            "source": source,
            "log_path": str(log_path),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e), "lines": []}), 500


@app.route("/api/logs/sources")
def api_logs_sources():
    """List available log sources (files in fleet/logs/)."""
    try:
        logs_dir = FLEET_DIR / "logs"
        sources = []
        if logs_dir.exists():
            for f in sorted(logs_dir.glob("*.log")):
                sources.append({
                    "name": f.stem,
                    "file": f.name,
                    "size_bytes": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
        return jsonify({"sources": sources, "total": len(sources)})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "sources": []}), 500


# ── Skill Recommendations (v0.200.00b) ────────────────────────────────────────

@app.route("/api/recommendations/<skill>")
def api_skill_recommendations(skill):
    """Skill recommendations after completing a task — co-occurrence based."""
    try:
        if not _check_rate_limit("recommendations", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from skill_recommender import get_recommendations, get_skill_chain

        n = min(20, max(1, int(request.args.get("n", 5))))
        depth = min(10, max(1, int(request.args.get("depth", 3))))

        recs = get_recommendations(skill, n=n)
        chain = get_skill_chain(skill, depth=depth)

        return jsonify({
            "skill": skill,
            "recommendations": recs,
            "chain": chain,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e), "recommendations": []}), 500


@app.route("/api/recommendations/popular")
def api_popular_skills():
    """Most-used skills by task count over the last 30 days."""
    try:
        if not _check_rate_limit("popular_skills", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from skill_recommender import get_popular_skills

        n = min(50, max(1, int(request.args.get("n", 10))))
        skills = get_popular_skills(n=n)
        return jsonify({"skills": skills, "total": len(skills)})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "skills": []}), 500


# ── A/B Testing Experiments (v0.200.00b) ──────────────────────────────────────

@app.route("/api/experiments")
def api_experiments_list():
    """List active A/B experiments."""
    try:
        if not _check_rate_limit("experiments_list", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from ab_testing import get_active_experiments

        experiments = get_active_experiments()
        return jsonify({"experiments": experiments, "total": len(experiments)})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "experiments": []}), 500


@app.route("/api/experiments", methods=["POST"])
def api_experiments_create():
    """Create a new A/B experiment.

    Body JSON:
        skill (str):        skill name to experiment on
        variant_path (str): Python module path for the variant skill
    """
    try:
        if not _check_rate_limit("experiments_create", max_per_min=10):
            return jsonify({"error": "rate limited"}), 429

        data = request.get_json(silent=True) or {}
        skill = (data.get("skill") or "").strip()
        variant_path = (data.get("variant_path") or "").strip()

        if not skill:
            return jsonify({"error": "skill required"}), 400
        if not variant_path:
            return jsonify({"error": "variant_path required"}), 400

        from ab_testing import create_experiment

        exp_id = create_experiment(skill, variant_path)
        if not exp_id:
            return jsonify({"error": "failed to create experiment"}), 500

        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="experiment.create",
                resource=f"experiment:{exp_id}",
                detail=f"A/B test: {skill} vs {variant_path}",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass

        return jsonify({"experiment_id": exp_id, "skill": skill, "variant_path": variant_path})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/experiments/<exp_id>/results")
def api_experiment_results(exp_id):
    """Evaluate an experiment: compare control vs variant with p-value."""
    try:
        if not _check_rate_limit("experiment_results", max_per_min=30):
            return jsonify({"error": "rate limited"}), 429

        from ab_testing import evaluate_experiment

        result = evaluate_experiment(exp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/experiments/<exp_id>/promote", methods=["POST"])
def api_experiment_promote(exp_id):
    """Promote the winner of an experiment (marks as completed).

    Does NOT auto-deploy the variant file — operator must review and
    copy from code_drafts/ to skills/ per project conventions.
    """
    try:
        if not _check_rate_limit("experiment_promote", max_per_min=5):
            return jsonify({"error": "rate limited"}), 429

        from ab_testing import promote_winner

        result = promote_winner(exp_id)

        # Audit log
        try:
            from audit import log_audit
            log_audit(
                actor=_get_request_role() or "operator",
                action="experiment.promote",
                resource=f"experiment:{exp_id}",
                detail=f"Winner: {result.get('winner', 'unknown')}",
                role=_get_request_role(),
                ip_address=request.remote_addr,
            )
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


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
