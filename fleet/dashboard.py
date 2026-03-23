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
import json
import os
import random
import re
import sqlite3
import sys
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

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

FLEET_DIR = Path(__file__).parent
_start_time = time.time()  # dashboard boot timestamp for /api/health uptime
DB_PATH = FLEET_DIR / "fleet.db"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"

app = Flask(__name__)

# ── Security hooks (CORS, auth, rate-limit, CSRF) ────────────────────────
# register_hooks wires all before_request / after_request handlers.
# _load_config is defined below — forward-ref is fine because hooks run at
# request time, not import time.
_register_security_hooks(app, lambda: _load_config())


def _get_request_role(req=None):
    """Convenience wrapper — delegates to security.get_request_role."""
    return get_request_role(_load_config, req)


def _require_role(role):
    """Convenience wrapper — delegates to security.require_role."""
    return _require_role_raw(role, _load_config)


# Alert state — tracked in memory, broadcast via SSE
_alerts = []
_alert_lock = threading.Lock()
_sse_clients = []
_monitor_start_time = time.time()

# Federation state — peer heartbeats tracked in memory
_federation_peers = {}

# ── In-memory rate limiter for expensive endpoints ────────────────────────
_rate_limits = {}  # endpoint -> (last_call_time, count)

def _check_rate_limit(endpoint, max_per_min=10):
    """Simple in-memory rate limit. Returns True if allowed."""
    now = time.time()
    if endpoint not in _rate_limits:
        _rate_limits[endpoint] = (now, 1)
        return True
    last, count = _rate_limits[endpoint]
    if now - last > 60:
        _rate_limits[endpoint] = (now, 1)
        return True
    if count >= max_per_min:
        return False
    _rate_limits[endpoint] = (last, count + 1)
    return True


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
            sys.path.insert(0, str(FLEET_DIR))
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
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return response


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Config loader ────────────────────────────────────────────────────────────

def _load_config():
    """Load fleet.toml for thermal/training/module config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    toml_path = FLEET_DIR / "fleet.toml"
    if not toml_path.exists():
        return {}
    return tomllib.loads(toml_path.read_text(encoding="utf-8"))


# ── Alerts ───────────────────────────────────────────────────────────────────

def _add_alert(level: str, message: str, source: str = "system"):
    """Add an alert (info/warning/critical) and broadcast via SSE. Deduplicates."""
    with _alert_lock:
        # Deduplicate: skip if same message already exists and isn't acknowledged
        for existing in _alerts[-20:]:
            if existing["message"] == message and not existing["acknowledged"]:
                return
        alert = {
            "id": int(time.time() * 1000),
            "level": level,
            "message": message,
            "source": source,
            "time": datetime.utcnow().isoformat(),
            "acknowledged": False,
        }
        _alerts.append(alert)
        # Keep only last 100 alerts
        if len(_alerts) > 100:
            _alerts.pop(0)
    _broadcast_sse({"type": "alert", "data": alert})


def _broadcast_sse(data: dict):
    """Send data to all connected SSE clients."""
    msg = f"data: {json.dumps(data)}\n\n"
    dead = []
    for client in _sse_clients:
        try:
            client.put(msg)
        except Exception:
            dead.append(client)
    for c in dead:
        _sse_clients.remove(c)


# ── Alert monitoring thread ──────────────────────────────────────────────────

def _alert_monitor():
    """Background thread checking for alert-worthy conditions."""
    while True:
        try:
            # Check thermal
            if HW_STATE_JSON.exists():
                hw = json.loads(HW_STATE_JSON.read_text())
                gpu_temp = hw.get("gpu_temp_c", 0)
                cfg = _load_config()
                thermal = cfg.get("thermal", {})
                sustained = thermal.get("gpu_max_sustained_c", 75)
                burst = thermal.get("gpu_max_burst_c", 78)

                if gpu_temp > burst:
                    _add_alert("critical", f"GPU temp {gpu_temp}C exceeds burst limit {burst}C", "thermal")
                elif gpu_temp > sustained:
                    _add_alert("warning", f"GPU temp {gpu_temp}C above sustained limit {sustained}C", "thermal")

            # Check for crashed workers (stale heartbeats)
            # Skip disabled/quarantined agents and allow 5min grace after startup
            cfg = cfg if 'cfg' in dir() else _load_config()
            disabled = set(cfg.get("fleet", {}).get("disabled_agents", []))
            agents = query("""
                SELECT name, last_heartbeat, status FROM agents
                WHERE last_heartbeat < datetime('now', '-5 minutes')
                AND status NOT IN ('OFFLINE', 'QUARANTINED', 'SLEEPING')
            """)
            stale = [a for a in agents if a["name"] not in disabled]
            if stale and (time.time() - _monitor_start_time) > 300:
                names = ", ".join(a["name"] for a in stale)
                _add_alert("warning", f"{len(stale)} agent(s) stale: {names}", "fleet")

            # Check disk space
            import shutil
            total, used, free = shutil.disk_usage(str(FLEET_DIR))
            free_gb = free / (1024**3)
            if free_gb < 5:
                _add_alert("warning", f"Low disk space: {free_gb:.1f}GB free", "system")

            # Check training lock timeout
            locks = query("SELECT * FROM locks WHERE name='training'")
            if locks:
                acquired = locks[0].get("acquired_at", "")
                if acquired:
                    try:
                        acq_time = datetime.fromisoformat(acquired)
                        elapsed = (datetime.utcnow() - acq_time).total_seconds()
                        cfg = _load_config()
                        timeout = cfg.get("training", {}).get("lock_timeout_secs", 7200)
                        if elapsed > timeout * 0.9:
                            _add_alert("warning",
                                       f"Training lock held for {elapsed/3600:.1f}h (timeout: {timeout/3600:.1f}h)",
                                       "training")
                    except Exception:
                        pass

            # Check for anomalous API spend (v0.170.04b: uses detect_cost_anomaly)
            try:
                from cost_tracking import detect_cost_anomaly
                anomaly = detect_cost_anomaly()
                if anomaly:
                    throttle_active = (FLEET_DIR / ".cost_anomaly_throttle").exists()
                    throttle_label = " [idle evolution paused]" if throttle_active else ""
                    _add_alert("warning",
                        f"Cost anomaly: ${anomaly['today_cost']:.2f} today "
                        f"({anomaly['multiplier']}x avg ${anomaly['avg_cost']:.2f})"
                        f"{throttle_label}", "cost")
            except Exception:
                pass

            # Check for high-scoring skill drafts pending review
            try:
                drafts = query("""
                    SELECT t.id, t.type, t.intelligence_score, t.assigned_to
                    FROM tasks t
                    WHERE t.type IN ('skill_evolve', 'evolution_coordinator')
                    AND t.status = 'DONE'
                    AND t.intelligence_score > 0.7
                    AND t.created_at >= datetime('now', '-24 hours')
                    AND t.id NOT IN (
                        SELECT CAST(json_extract(body_json, '$.task_id') AS INTEGER)
                        FROM messages WHERE json_extract(body_json, '$.type') = 'draft_reviewed'
                    )
                    LIMIT 3
                """)
                if drafts:
                    _add_alert("info", f"{len(drafts)} high-quality skill draft(s) ready for review", "evolution")
            except Exception:
                pass

        except Exception as e:
            _alert_failure_count = getattr(_alert_monitor, '_failures', 0) + 1
            _alert_monitor._failures = _alert_failure_count
            if _alert_failure_count <= 3:
                import logging
                logging.warning(f"Alert monitor error: {e}")

        time.sleep(30)  # Check every 30s


# ── Original API endpoints ───────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    # Only show agents with a heartbeat in the last 60s (currently running)
    agents = query("""
        SELECT a.name, a.role, a.status, a.last_heartbeat, a.current_task_id,
               t.type as current_task
        FROM agents a
        LEFT JOIN tasks t ON a.current_task_id = t.id
        WHERE a.last_heartbeat >= datetime('now', '-60 seconds')
        ORDER BY
            CASE WHEN a.name IN ('dr_ders', 'hw_supervisor') THEN 0 ELSE 1 END,
            CASE a.status WHEN 'BUSY' THEN 0 WHEN 'ACTIVE' THEN 1 ELSE 2 END,
            a.name
    """)
    counts = {}
    for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
        row = query("SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,))
        counts[s] = row[0]["n"] if row else 0
    return jsonify({"agents": agents, "tasks": counts})


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
        conn.close()
        subsystems["fleet_db"] = {"status": "ok", "detail": "connected"}
    except Exception as e:
        subsystems["fleet_db"] = {"status": "unavailable", "detail": _safe_error(e)}
        overall = "unhealthy"

    # 2. Ollama status
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            models_loaded = len(data.get("models", []))
        subsystems["ollama"] = {"status": "ok", "models_loaded": models_loaded}
    except Exception:
        subsystems["ollama"] = {"status": "unavailable", "models_loaded": 0}
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
        "version": "0.22.00",
    })


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
        "cpu_temp_c": 0, "ambient_estimate_c": 0,
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

            result.update({
                "gpu_temp_c": th.get("gpu_temp_c", 0),
                "gpu_power_w": th.get("gpu_power_w", 0),
                "gpu_fan_pct": th.get("gpu_fan_pct", 0),
                "gpu_vram_used_gb": round(th.get("vram_used_gb", 0), 2),
                "gpu_vram_total_gb": round(th.get("vram_total_gb", 0), 2),
                "cpu_temp_c": th.get("cpu_temp_c", 0),
                "ambient_estimate_c": th.get("ambient_est_c", 0),
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
    if result["cpu_temp_c"] == 0:
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
            "cpu_pct": psutil.cpu_percent(interval=0),
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
        "modules":    _safe(api_modules),
        "data_stats": _safe(api_data_stats),
        "evolution":  _safe(api_evolution),
    })


@app.route("/api/modules")
def api_modules():
    """Enabled modules, versions, deprecation status."""
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
        conn.close()
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
        conn.close()
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
        sys.path.insert(0, str(FLEET_DIR))
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
        sys.path.insert(0, str(FLEET_DIR))
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
        sys.path.insert(0, str(FLEET_DIR))
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

        sys.path.insert(0, str(FLEET_DIR))
        import db
        summary = db.get_usage_summary(period="day", group_by="skill")
        spent_map = {r["skill"]: r.get("total_cost", 0) or 0 for r in summary}

        result = []
        for skill, limit_usd in sorted(budgets.items()):
            spent = spent_map.get(skill, 0)
            result.append({
                "skill": skill,
                "budget_usd": limit_usd,
                "spent_usd": round(spent, 6),
                "remaining_usd": round(limit_usd - spent, 6),
                "exceeded": spent >= limit_usd,
                "pct_used": round(spent / limit_usd * 100, 1) if limit_usd > 0 else 0,
            })
        return jsonify(result)
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

        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/usage/regression")
def api_usage_regression():
    """CT-3: Flag skills with >20% token increase vs previous period."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
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

        conn.close()
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
        sys.path.insert(0, str(FLEET_DIR))
        import db
        return jsonify(db.get_dag_graph(parent_id))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Process Control (extracted to process_control.py) ─────────────────────────
from process_control import fleet_bp
app.register_blueprint(fleet_bp)

# ── A2A Protocol (Agent-to-Agent interoperability) ────────────────────────────
from a2a import a2a_bp
app.register_blueprint(a2a_bp)


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
        sys.path.insert(0, str(FLEET_DIR))
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


# ── Server-Sent Events ──────────────────────────────────────────────────────

@app.route("/api/stream")
def api_stream():
    """SSE endpoint for live updates (replaces 30s polling)."""
    import queue

    q = queue.Queue()
    _sse_clients.append(q)

    def generate():
        try:
            # Send initial heartbeat
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield msg
                except queue.Empty:
                    # Send keepalive
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            if q in _sse_clients:
                _sse_clients.remove(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ── SSE broadcast thread ────────────────────────────────────────────────────

def _sse_broadcaster():
    """Periodically push status updates to all SSE clients."""
    while True:
        if _sse_clients:
            try:
                agents = query("SELECT name, role, status, last_heartbeat FROM agents ORDER BY name")
                counts = {}
                for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
                    row = query("SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,))
                    counts[s] = row[0]["n"] if row else 0
                _broadcast_sse({
                    "type": "status",
                    "data": {"agents": agents, "tasks": counts},
                })
            except Exception:
                pass
        time.sleep(5)


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


# ── Theme Settings ────────────────────────────────────────────────────────────

_VALID_THEMES = {"classic", "modern", "figma"}


@app.route("/api/settings/theme", methods=["GET"])
def api_settings_theme_get():
    """Return current dashboard theme from fleet.toml."""
    try:
        cfg = _load_config()
        theme = cfg.get("dashboard", {}).get("theme", "figma")
        if theme not in _VALID_THEMES:
            theme = "figma"
        return jsonify({"theme": theme})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/settings/theme", methods=["POST"])
@_require_role("operator")
def api_settings_theme_set():
    """Update dashboard theme in fleet.toml.

    Accepts JSON body: {"theme": "classic"|"modern"|"figma"}
    Writes to [dashboard] theme key using tomlkit to preserve formatting.
    """
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "").strip().lower()
    if theme not in _VALID_THEMES:
        return jsonify({"error": f"Invalid theme '{theme}'. Valid: {sorted(_VALID_THEMES)}"}), 400

    try:
        import tomlkit
        toml_path = FLEET_DIR / "fleet.toml"
        doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
        if "dashboard" not in doc:
            doc["dashboard"] = tomlkit.table()
        doc["dashboard"]["theme"] = theme
        toml_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        return jsonify({"ok": True, "theme": theme})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Agent Disable/Enable ──────────────────────────────────────────────────────

VALID_AGENT = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

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


# ── System Recommendations (0.052.00b) ────────────────────────────────────────

@app.route("/api/recommendations")
def api_recommendations():
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

        conn.close()
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


# ── Federation (0.085.00b) ─────────────────────────────────────────────────

@app.route("/api/federation/heartbeat", methods=["POST"])
def api_federation_heartbeat():
    """Receive heartbeat from peer fleet."""
    data = request.get_json(silent=True) or {}
    fleet_id = data.get("fleet_id") or "unknown"
    _federation_peers[fleet_id] = {
        "agents": data.get("agents", 0),
        "pending": data.get("pending", 0),
        "last_seen": time.time(),
    }
    return jsonify({"ok": True})


@app.route("/api/federation/peers")
def api_federation_peers():
    """List known federation peers and their online status."""
    now = time.time()
    peers = {k: {**v, "online": now - v["last_seen"] < 120}
             for k, v in _federation_peers.items()}
    return jsonify(peers)


@app.route("/api/federation/discovered")
def api_federation_discovered():
    """List auto-discovered peers (separate from manually configured).

    Returns peers found via UDP broadcast and/or mDNS, with online status.
    """
    try:
        import discovery
        discovered = discovery.get_discovered_peers()
        all_peers = discovery.get_all_peers()
        return jsonify({
            "discovered": discovered,
            "all_peers": all_peers,
            "discovery_running": discovery._running,
            "fleet_id": discovery._fleet_id,
        })
    except ImportError:
        return jsonify({"discovered": [], "all_peers": [], "discovery_running": False,
                        "fleet_id": "", "error": "discovery module not available"})
    except Exception as e:
        return jsonify({"discovered": [], "all_peers": [], "discovery_running": False,
                        "fleet_id": "", "error": str(e)})

# ── Federation Routing (v0.100.00b — Cross-Fleet Task Routing) ──────────────

@app.route("/api/federation/capacity")
def api_federation_capacity():
    """Aggregated cluster capacity — local + all reachable peers."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from federation_router import get_aggregated_capacity
        return jsonify(get_aggregated_capacity())
    except ImportError:
        return jsonify({"error": "federation_router not available"}), 501

# ── Federation HITL (0.100.00b) ──────────────────────────────────────────────


@app.route("/api/federation/hitl")
def api_federation_hitl():
    """Aggregated HITL tasks from local fleet and all federation peers."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from federation_hitl import get_all_hitl_tasks
        tasks = get_all_hitl_tasks()
        return jsonify(tasks)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/federation/routing-stats")
def api_federation_routing_stats():
    """Routing statistics — how many tasks routed locally vs remotely."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from federation_router import get_routing_stats
        return jsonify(get_routing_stats())
    except ImportError:
        return jsonify({"error": "federation_router not available"}), 501
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/federation/route", methods=["POST"])
@_require_role("operator")
def api_federation_route():
    """Manually route a task to a specific peer fleet.

    Body JSON: {"peer_url": "http://...", "type": "skill_name",
                "payload": {...}, "priority": 5}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        peer_url = data.get("peer_url")
        task_type = data.get("type")
        if not peer_url or not task_type:
            return jsonify({"error": "peer_url and type are required"}), 400

        sys.path.insert(0, str(FLEET_DIR))
        from federation_router import route_to_peer

        peer = {"url": peer_url}
        task_dict = {
            "type": task_type,
            "payload": data.get("payload", {}),
            "priority": data.get("priority", 5),
        }
        result = route_to_peer(peer, task_dict)

        if result.get("ok"):
            _broadcast_sse({"type": "federation_route", "data": result})
            return jsonify(result)
        else:
            return jsonify(result), 502
    except ImportError:
        return jsonify({"error": "federation_router not available"}), 501

# ── Federation mTLS (0.100.00b) ────────────────────────────────────────────


@app.route("/api/federation/cert-status")
def api_federation_cert_status():
    """Certificate health info for the dashboard."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from fleet_tls import get_cert_info
        return jsonify(get_cert_info())
    except ImportError:
        return jsonify({"tls_enabled": False, "warning": "fleet_tls module not available"})

@app.route("/api/federation/hitl/respond", methods=["POST"])
@_require_role("operator")
def api_federation_hitl_respond():
    """Respond to a HITL task on a remote peer fleet.

    Body: {"peer_url": "http://...", "task_id": 123, "response": "approved"}
    If peer_url is "local" or omitted, routes to the local fleet.
    """
    try:
        data = request.get_json(silent=True) or {}
        peer_url = data.get("peer_url", "local")
        task_id = data.get("task_id")
        response_text = data.get("response", "").strip()

        if not task_id:
            return jsonify({"error": "task_id is required"}), 400
        if not response_text:
            return jsonify({"error": "response is required"}), 400

        sys.path.insert(0, str(FLEET_DIR))

        if peer_url == "local" or not peer_url:
            # Local response
            import db
            db.respond_to_agent(int(task_id), response_text)
            _broadcast_sse({
                "type": "hitl_response",
                "data": {"task_id": task_id, "responded": True, "source": "local"},
            })
            return jsonify({"ok": True, "task_id": task_id, "source": "local"})
        else:
            # Remote response — forward to peer
            from federation_hitl import respond_to_remote_hitl
            result = respond_to_remote_hitl(peer_url, int(task_id), response_text)
            if "error" in result:
                return jsonify(result), 502
            _broadcast_sse({
                "type": "hitl_response",
                "data": {"task_id": task_id, "responded": True, "source": f"peer:{peer_url}"},
            })
            return jsonify({"ok": True, "task_id": task_id, "source": f"peer:{peer_url}"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/federation/exchange-cert", methods=["POST"])
def api_federation_exchange_cert():
    """Peer sends its cert, receives local cert.

    Request body: {"peer_id": "...", "cert_pem": "-----BEGIN CERTIFICATE..."}
    Response: {"ok": true, "cert_pem": "-----BEGIN CERTIFICATE..."}
    """
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from fleet_tls import store_trusted_cert, get_local_cert_pem, is_tls_enabled
        if not is_tls_enabled():
            return jsonify({"error": "Federation TLS not enabled"}), 400
        data = request.get_json(silent=True) or {}
        peer_id = data.get("peer_id")
        cert_pem = data.get("cert_pem")
        if not peer_id or not cert_pem:
            return jsonify({"error": "peer_id and cert_pem required"}), 400
        # Store the incoming peer cert
        store_trusted_cert(peer_id, cert_pem)
        # Return our cert for mutual trust
        local_cert = get_local_cert_pem()
        return jsonify({"ok": True, "cert_pem": local_cert})
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500

# ── Remote Deployment (0.100.00b) ─────────────────────────────────────────

@app.route("/api/deploy/prepare", methods=["POST"])
def api_deploy_prepare():
    """Create a deployment package for pushing to peers."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from remote_deploy import prepare_deployment
        data = request.get_json(silent=True) or {}
        pkg_path = prepare_deployment(
            include_skills=data.get("include_skills", True),
            include_config=data.get("include_config", True),
            include_models=data.get("include_models", False),
        )
        size_mb = pkg_path.stat().st_size / (1024 * 1024)
        return jsonify({"ok": True, "package": str(pkg_path), "size_mb": round(size_mb, 2)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/push", methods=["POST"])
def api_deploy_push():
    """Push a deployment package to a peer fleet."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from remote_deploy import push_to_peer
        data = request.get_json(silent=True) or {}
        peer_url = data.get("peer_url")
        package_path = data.get("package")
        if not peer_url or not package_path:
            return jsonify({"ok": False, "error": "peer_url and package required"}), 400
        result = push_to_peer(peer_url, package_path, timeout=data.get("timeout", 60))
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/status/<deploy_id>")
def api_deploy_status(deploy_id):
    """Check deployment status (local or remote via peer_url query param)."""
    try:
        peer_url = request.args.get("peer_url")
        if peer_url:
            from remote_deploy import deploy_status
            return jsonify(deploy_status(peer_url, deploy_id))
        else:
            from remote_deploy import get_local_deploy_status
            return jsonify(get_local_deploy_status(deploy_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/rollback/<deploy_id>", methods=["POST"])
def api_deploy_rollback(deploy_id):
    """Rollback a deployment (local or remote via peer_url in body)."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        peer_url = data.get("peer_url")
        if peer_url:
            from remote_deploy import rollback_peer
            return jsonify(rollback_peer(peer_url, deploy_id))
        else:
            # Local rollback — restore from pre-deploy backup
            from backup_manager import BackupManager
            cfg = _load_config()
            bm = BackupManager(cfg)
            backups = bm.list_backups()
            pre_deploy = [b for b in backups if b.get("trigger") == "pre-deploy"]
            if not pre_deploy:
                return jsonify({"ok": False, "error": "No pre-deploy backup found"}), 404
            return jsonify({"ok": True, "backup_id": pre_deploy[0]["id"],
                           "note": "Use backup --restore to apply"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/history")
def api_deploy_history():
    """List recent deployments."""
    try:
        from remote_deploy import get_deployment_history
        limit = request.args.get("limit", 20, type=int)
        return jsonify({"deployments": get_deployment_history(limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Receiving side (peer receives a deployment push) ──────────────────────

@app.route("/api/deploy/receive", methods=["POST"])
def api_deploy_receive():
    """Receive a deployment package from a peer fleet."""
    try:
        from remote_deploy import receive_deployment
        pkg_data = request.get_data()
        if not pkg_data:
            return jsonify({"ok": False, "error": "Empty package"}), 400
        result = receive_deployment(pkg_data)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/pending")
def api_deploy_pending():
    """List pending deployments awaiting operator approval."""
    try:
        from remote_deploy import get_pending_deployments
        return jsonify({"pending": get_pending_deployments()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/approve/<deploy_id>", methods=["POST"])
def api_deploy_approve(deploy_id):
    """Approve and apply a pending deployment (HITL gate)."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from remote_deploy import approve_deployment
        result = approve_deployment(deploy_id)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/deploy/reject/<deploy_id>", methods=["POST"])
def api_deploy_reject(deploy_id):
    """Reject a pending deployment."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from remote_deploy import reject_deployment
        result = reject_deployment(deploy_id)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/federation/hitl/notify", methods=["POST"])
def api_federation_hitl_notify():
    """Receive notification from a peer about a new HITL task.

    Broadcasts an SSE event so connected dashboards update live.
    Body: task_info dict with _source_fleet field.
    """
    try:
        data = request.get_json(silent=True) or {}
        source_fleet = data.get("_source_fleet", "unknown")
        _broadcast_sse({
            "type": "remote_hitl_waiting",
            "data": {
                "task_id": data.get("task_id") or data.get("id"),
                "type": data.get("type", ""),
                "question": data.get("question", ""),
                "agent": data.get("agent", ""),
                "source_fleet": source_fleet,
            },
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Cluster Data (0.100.00b — Unified Dashboard Hooks) ──────────────────────


@app.route("/api/cluster/agents")
def api_cluster_agents():
    """All agents across all federated peers."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from federation_data import get_cluster_agents
        return jsonify(get_cluster_agents())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/cluster/tasks")
def api_cluster_tasks():
    """All tasks across all federated peers, optionally filtered by status."""
    try:
        status_filter = request.args.get("status")
        sys.path.insert(0, str(FLEET_DIR))
        from federation_data import get_cluster_tasks
        return jsonify(get_cluster_tasks(status=status_filter))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/cluster/metrics")
def api_cluster_metrics():
    """Aggregated metrics across all federated peers."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
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

        conn.close()
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

        sys.path.insert(0, str(FLEET_DIR))
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


# ── HITL Response Endpoints ───────────────────────────────────────────────────


@app.route("/api/tasks/waiting-human")
def api_waiting_human():
    """List all tasks awaiting human input.

    Query params:
        include_remote=true — include HITL tasks from federation peers
                              (default: false, local only for backward compat)
    """
    try:
        include_remote = request.args.get("include_remote", "false").lower() == "true"
        sys.path.insert(0, str(FLEET_DIR))

        if include_remote:
            from federation_hitl import get_all_hitl_tasks
            all_tasks = get_all_hitl_tasks()
            result = []
            for t in all_tasks:
                result.append({
                    "id": t.get("id"),
                    "type": t.get("type", ""),
                    "question": t.get("question", ""),
                    "agent": t.get("assigned_to", ""),
                    "created_at": t.get("created_at", ""),
                    "source_fleet": t.get("source_fleet", "local"),
                    "source": t.get("source", "local"),
                })
            return jsonify(result)

        # Default: local only (backward compatible)
        import db
        tasks = db.get_waiting_human_tasks()
        result = []
        for t in tasks:
            result.append({
                "id": t["id"],
                "type": t.get("type", ""),
                "question": t.get("question", ""),
                "agent": t.get("assigned_to", ""),
                "created_at": t.get("created_at", ""),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/tasks/<int:task_id>/respond", methods=["POST"])
@_require_role("operator")
def api_task_respond(task_id):
    """Submit human response to a WAITING_HUMAN task."""
    try:
        data = request.get_json(silent=True) or {}
        response_text = data.get("response", "").strip()
        if not response_text:
            return jsonify({"error": "response is required"}), 400

        sys.path.insert(0, str(FLEET_DIR))
        import db
        db.respond_to_agent(task_id, response_text)

        _broadcast_sse({
            "type": "hitl_response",
            "data": {"task_id": task_id, "responded": True},
        })
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/tasks/<int:task_id>/question")
def api_task_question(task_id):
    """Get the question asked by an agent for a specific task."""
    try:
        row = query(
            "SELECT id, type, assigned_to, status, payload_json, created_at "
            "FROM tasks WHERE id=?", (task_id,)
        )
        if not row:
            return jsonify({"error": "Task not found"}), 404
        task = row[0]
        # Extract question from the agent's message to operator
        question = ""
        try:
            msgs = query(
                "SELECT body_json FROM messages "
                "WHERE from_agent=? AND to_agent='operator' "
                "AND body_json LIKE '%human_input_request%' "
                "AND body_json LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (task.get("assigned_to") or "", f'%"task_id": {task_id}%'),
            )
            if msgs:
                body = json.loads(msgs[0]["body_json"])
                question = body.get("question", "")
        except Exception:
            pass
        return jsonify({
            "task_id": task_id,
            "type": task.get("type", ""),
            "agent": task.get("assigned_to", ""),
            "status": task.get("status", ""),
            "question": question,
            "created_at": task.get("created_at", ""),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Queue Management Endpoints ───────────────────────────────────────────────


@app.route("/api/tasks/queue")
def api_task_queue():
    """List pending and running tasks with priority and order."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        total_row = query(
            "SELECT COUNT(*) as n FROM tasks WHERE status IN ('PENDING','RUNNING')"
        )
        total = total_row[0]["n"] if total_row else 0

        tasks = query(
            "SELECT id, type, status, priority, assigned_to, created_at, payload_json "
            "FROM tasks WHERE status IN ('PENDING','RUNNING') "
            "ORDER BY priority DESC, created_at ASC "
            "LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        return jsonify({
            "tasks": tasks,
            "page": page,
            "per_page": per_page,
            "total": total,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/tasks/<int:task_id>/priority", methods=["PUT"])
@_require_role("operator")
def api_task_priority(task_id):
    """Change task priority (1-10). Only PENDING tasks can be re-prioritised."""
    try:
        data = request.get_json(silent=True) or {}
        new_priority = data.get("priority", 5)
        try:
            new_priority = int(new_priority)
        except (TypeError, ValueError):
            return jsonify({"error": "priority must be an integer"}), 400
        if not 1 <= new_priority <= 10:
            return jsonify({"error": "priority must be between 1 and 10"}), 400

        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "PENDING":
            return jsonify({"error": "Only PENDING tasks can be re-prioritised"}), 409

        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET priority=? WHERE id=? AND status='PENDING'",
                (new_priority, task_id),
            )

        _broadcast_sse({
            "type": "task_priority",
            "data": {"task_id": task_id, "priority": new_priority},
        })
        return jsonify({"ok": True, "task_id": task_id, "priority": new_priority})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@_require_role("operator")
def api_task_cancel(task_id):
    """Cancel a pending task."""
    try:
        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "PENDING":
            return jsonify({"error": "Only PENDING tasks can be cancelled"}), 409

        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='FAILED', result_json=? WHERE id=? AND status='PENDING'",
                (json.dumps({"error": "Cancelled by operator"}), task_id),
            )

        _broadcast_sse({
            "type": "task_cancelled",
            "data": {"task_id": task_id},
        })
        return jsonify({"ok": True, "task_id": task_id, "status": "FAILED"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/tasks/<int:task_id>/requeue", methods=["POST"])
@_require_role("operator")
def api_task_requeue(task_id):
    """Requeue a failed task — resets it to PENDING."""
    try:
        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "FAILED":
            return jsonify({"error": "Only FAILED tasks can be requeued"}), 409

        sys.path.insert(0, str(FLEET_DIR))
        import db
        db.requeue_task(task_id)

        _broadcast_sse({
            "type": "task_requeued",
            "data": {"task_id": task_id},
        })
        return jsonify({"ok": True, "task_id": task_id, "status": "PENDING"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Task Dispatch ────────────────────────────────────────────────────────────

@app.route("/api/tasks/dispatch", methods=["POST"])
@_require_role("operator")
def api_task_dispatch():
    """Submit a task to the fleet queue.

    Body JSON:
        skill (str):       required — skill name (e.g. "summarize", "code_review")
        payload (dict):    optional — JSON payload for the skill
        priority (int):    optional — 1-10, default 5
        assigned_to (str): optional — target agent name
    """
    try:
        if not _check_rate_limit("task_dispatch", max_per_min=30):
            return jsonify({"error": "Rate limited"}), 429

        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        skill = (data.get("skill") or "").strip()
        if not skill:
            return jsonify({"error": "skill is required"}), 400

        # Validate skill name format
        if not re.match(r'^[a-zA-Z0-9_]{1,64}$', skill):
            return jsonify({"error": "Invalid skill name format"}), 400

        payload = data.get("payload", {})
        priority = data.get("priority", 5)
        assigned_to = (data.get("assigned_to") or "").strip() or None

        try:
            priority = max(1, min(10, int(priority)))
        except (TypeError, ValueError):
            priority = 5

        payload_json = json.dumps(payload) if isinstance(payload, dict) else str(payload)

        sys.path.insert(0, str(FLEET_DIR))
        import db
        task_id = db.post_task(
            type_=skill,
            payload_json=payload_json,
            priority=priority,
            assigned_to=assigned_to,
        )

        _broadcast_sse({
            "type": "task_dispatched",
            "data": {"task_id": task_id, "skill": skill, "priority": priority},
        })

        return jsonify({
            "status": "ok",
            "task_id": task_id,
            "skill": skill,
            "priority": priority,
            "assigned_to": assigned_to,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


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
                    for line in content.splitlines()[:30]:
                        line_s = line.strip()
                        if line_s.startswith("SKILL_NAME"):
                            # Extract value from: SKILL_NAME = "foo"
                            m = re.match(r'^SKILL_NAME\s*=\s*["\'](.+?)["\']', line_s)
                            if m:
                                skill_name = m.group(1)
                        elif line_s.startswith("DESCRIPTION"):
                            m = re.match(r'^DESCRIPTION\s*=\s*["\'](.+?)["\']', line_s)
                            if m:
                                description = m.group(1)
                        elif line_s.startswith("REQUIRES_NETWORK"):
                            requires_network = "True" in line_s
                    if skill_name:
                        skills.append({
                            "name": skill_name,
                            "description": description or "",
                            "requires_network": requires_network,
                            "file": f.name,
                        })
                except Exception:
                    pass

        result = {"skills": skills, "total": len(skills)}
        api_skills_available._cache = {'ts': now, 'data': result}
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e), "skills": []}), 500


# ── Queue Management (extended) ──────────────────────────────────────────────

# Queue pause state — in-memory flag checked by workers
_queue_paused = False

@app.route("/api/queue")
def api_queue():
    """Full pending/running queue with ordering and pause state."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 100))))
        offset = (page - 1) * per_page

        total_row = query(
            "SELECT COUNT(*) as n FROM tasks WHERE status IN ('PENDING','RUNNING','WAITING')"
        )
        total = total_row[0]["n"] if total_row else 0

        tasks = query(
            "SELECT id, type, status, priority, assigned_to, created_at, payload_json "
            "FROM tasks WHERE status IN ('PENDING','RUNNING','WAITING') "
            "ORDER BY "
            "  CASE status WHEN 'RUNNING' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END, "
            "  priority DESC, created_at ASC "
            "LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        return jsonify({
            "tasks": tasks,
            "page": page,
            "per_page": per_page,
            "total": total,
            "paused": _queue_paused,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/queue/reorder", methods=["POST"])
@_require_role("operator")
def api_queue_reorder():
    """Reorder queue by setting priorities based on position.

    Body JSON:
        task_ids (list[int]): ordered list of task IDs — first = highest priority
    """
    try:
        data = request.get_json(silent=True) or {}
        task_ids = data.get("task_ids", [])
        if not task_ids or not isinstance(task_ids, list):
            return jsonify({"error": "task_ids must be a non-empty list of integers"}), 400

        # Validate all are integers
        try:
            task_ids = [int(tid) for tid in task_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "All task_ids must be integers"}), 400

        if len(task_ids) > 200:
            return jsonify({"error": "Maximum 200 tasks per reorder"}), 400

        # Assign decreasing priorities: first = 10, last = 1
        # Scale across the range
        updated = []

        sys.path.insert(0, str(FLEET_DIR))
        import db

        def _do():
            with db.get_conn() as conn:
                for i, tid in enumerate(task_ids):
                    # Priority: 10 for first, scales down to 1
                    prio = max(1, 10 - int(i * 9 / max(len(task_ids) - 1, 1)))
                    result = conn.execute(
                        "UPDATE tasks SET priority=? WHERE id=? AND status='PENDING'",
                        (prio, tid),
                    )
                    if result.rowcount > 0:
                        updated.append({"task_id": tid, "priority": prio})
        db._retry_write(_do)

        _broadcast_sse({
            "type": "queue_reordered",
            "data": {"updated_count": len(updated)},
        })

        return jsonify({
            "status": "ok",
            "updated": updated,
            "total_updated": len(updated),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/queue/<int:task_id>", methods=["DELETE"])
@_require_role("operator")
def api_queue_remove(task_id):
    """Remove a task from the queue — cancels a PENDING task."""
    try:
        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "PENDING":
            return jsonify({"error": "Only PENDING tasks can be removed from queue"}), 409

        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='FAILED', result_json=? WHERE id=? AND status='PENDING'",
                (json.dumps({"error": "Removed from queue by operator"}), task_id),
            )

        _broadcast_sse({
            "type": "queue_removed",
            "data": {"task_id": task_id},
        })
        return jsonify({"status": "ok", "task_id": task_id, "removed": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/queue/pause", methods=["POST"])
@_require_role("operator")
def api_queue_pause():
    """Pause queue processing — workers stop claiming new tasks."""
    global _queue_paused
    _queue_paused = True
    _broadcast_sse({"type": "queue_paused", "data": {"paused": True}})
    return jsonify({"status": "ok", "paused": True})


@app.route("/api/queue/resume", methods=["POST"])
@_require_role("operator")
def api_queue_resume():
    """Resume queue processing after a pause."""
    global _queue_paused
    _queue_paused = False
    _broadcast_sse({"type": "queue_resumed", "data": {"paused": False}})
    return jsonify({"status": "ok", "paused": False})


@app.route("/api/queue/status")
def api_queue_status():
    """Return queue processing state (paused/active)."""
    return jsonify({"paused": _queue_paused})


# ── Settings Editor ──────────────────────────────────────────────────────────

# Sections that can be edited via the API (safety: never expose security tokens)
_EDITABLE_SECTIONS = {
    "fleet", "models", "thermal", "training", "dashboard", "workers",
    "idle", "backup", "review", "gpu", "naming", "affinity", "context",
    "budgets", "triggers", "schedules", "assistant", "boot",
}

# Sections that are read-only via the API
_READONLY_SECTIONS = {
    "security", "ditl", "walkthrough", "enterprise", "filesystem",
}

# Schema descriptions for the settings editor UI
_SETTINGS_SCHEMA = {
    "fleet": {
        "eco_mode": {"type": "bool", "description": "Reduce resource usage"},
        "idle_enabled": {"type": "bool", "description": "Workers self-improve when idle"},
        "idle_timeout_secs": {"type": "int", "description": "Seconds before idle mode activates"},
        "max_workers": {"type": "int", "description": "Maximum active workers at boot"},
        "offline_mode": {"type": "bool", "description": "Disable external API calls"},
        "hitl_evolution": {"type": "bool", "description": "Require human approval for evolution"},
    },
    "models": {
        "local": {"type": "str", "description": "Default local model (Ollama)"},
        "complex": {"type": "str", "description": "Complex task model"},
        "complex_provider": {"type": "str", "description": "Provider for complex tasks: claude | gemini | local"},
        "conductor_model": {"type": "str", "description": "CPU-pinned chat model"},
        "keep_alive_mins": {"type": "int", "description": "Minutes to keep models loaded"},
    },
    "thermal": {
        "gpu_max_sustained_c": {"type": "int", "description": "Max sustained GPU temp (C)"},
        "gpu_max_burst_c": {"type": "int", "description": "Hard GPU temp ceiling (C)"},
        "cpu_max_sustained_c": {"type": "int", "description": "Max sustained CPU temp (C)"},
        "cooldown_target_c": {"type": "int", "description": "Resume GPU below this temp (C)"},
        "poll_interval_secs": {"type": "int", "description": "Temp check interval (seconds)"},
    },
    "dashboard": {
        "enabled": {"type": "bool", "description": "Enable web dashboard"},
        "port": {"type": "int", "description": "Dashboard port"},
        "auto_open": {"type": "bool", "description": "Open browser on fleet boot"},
        "bind_address": {"type": "str", "description": "Listen address (127.0.0.1 or 0.0.0.0)"},
    },
    "workers": {
        "nice_level": {"type": "int", "description": "OS priority level"},
        "cpu_limit_percent": {"type": "int", "description": "CPU limit per worker (%)"},
        "coder_count": {"type": "int", "description": "Number of coder instances"},
        "memory_limit_mb": {"type": "int", "description": "Max memory per worker (MB)"},
    },
    "backup": {
        "enabled": {"type": "bool", "description": "Enable auto-save backups"},
        "interval_secs": {"type": "int", "description": "Backup interval (seconds, min 180)"},
        "depth": {"type": "int", "description": "Max backups to keep"},
        "location": {"type": "str", "description": "Backup directory path"},
    },
    "training": {
        "exclusive_lock": {"type": "bool", "description": "Only 1 training process at a time"},
        "auto_pause_gpu_tasks": {"type": "bool", "description": "Pause GPU skills during training"},
        "default_profile": {"type": "str", "description": "Training profile: conservative | aggressive | exploratory"},
    },
    "review": {
        "enabled": {"type": "bool", "description": "Enable evaluator-optimizer review pass"},
        "max_rounds": {"type": "int", "description": "Max review-reject cycles per task"},
        "provider": {"type": "str", "description": "Review provider: api | subscription | local"},
    },
    "idle": {
        "enabled": {"type": "bool", "description": "Enable idle self-improvement"},
        "threshold_polls": {"type": "int", "description": "Idle polls before activation"},
        "cooldown_secs": {"type": "int", "description": "Min seconds between idle runs"},
    },
    "gpu": {
        "mode": {"type": "str", "description": "GPU mode: eco | full"},
        "multi_gpu": {"type": "bool", "description": "Enable multi-GPU splitting"},
    },
    "context": {
        "max_turns": {"type": "int", "description": "Sliding window context turns"},
        "max_tokens": {"type": "int", "description": "Token budget for context"},
        "stale_hours": {"type": "int", "description": "Clear contexts older than this"},
    },
}

# In-memory theme preference (persists for session, not to TOML)
_dashboard_theme = "dark"


@app.route("/api/settings")
def api_settings():
    """Return fleet.toml as JSON with read-only sections marked."""
    try:
        cfg = _load_config()
        result = {}
        for section, values in cfg.items():
            if isinstance(values, dict):
                result[section] = {
                    "values": values,
                    "readonly": section in _READONLY_SECTIONS,
                    "editable": section in _EDITABLE_SECTIONS,
                }
            else:
                # Top-level scalar (rare in fleet.toml but handle gracefully)
                result[section] = {
                    "values": values,
                    "readonly": True,
                    "editable": False,
                }
        return jsonify({"status": "ok", "settings": result})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/settings/<section>", methods=["PUT"])
@_require_role("operator")
def api_settings_update(section):
    """Update a TOML section. Body: {key: value, ...}

    Only editable sections can be modified. Security/DITL/enterprise are read-only.
    """
    if not _check_rate_limit("settings_update", max_per_min=10):
        return jsonify({"error": "Rate limited"}), 429

    if section not in _EDITABLE_SECTIONS:
        return jsonify({"error": f"Section '{section}' is read-only or does not exist"}), 403

    # Validate section name format
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', section):
        return jsonify({"error": "Invalid section name"}), 400

    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Body must be a JSON object with key-value pairs"}), 400

        # Read current TOML
        toml_path = FLEET_DIR / "fleet.toml"
        content = toml_path.read_text(encoding="utf-8")

        # Apply updates line by line within the section
        updated_keys = []
        for key, value in data.items():
            # Validate key format
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', key):
                continue

            # Format value for TOML
            if isinstance(value, bool):
                toml_val = "true" if value else "false"
            elif isinstance(value, int):
                toml_val = str(value)
            elif isinstance(value, float):
                toml_val = str(value)
            elif isinstance(value, str):
                toml_val = f'"{value}"'
            elif isinstance(value, list):
                # Format list items
                items = []
                for item in value:
                    if isinstance(item, str):
                        items.append(f'"{item}"')
                    else:
                        items.append(str(item))
                toml_val = "[" + ", ".join(items) + "]"
            else:
                continue  # Skip unsupported types

            # Try to replace existing key in the content
            # Match: key = value (with optional comment)
            pattern = rf'^({re.escape(key)}\s*=\s*).*$'
            new_line = f'{key} = {toml_val}'
            new_content = re.sub(pattern, new_line, content, count=1, flags=re.MULTILINE)

            if new_content != content:
                content = new_content
                updated_keys.append(key)

        if updated_keys:
            toml_path.write_text(content, encoding="utf-8")

            # Reload config cache
            try:
                from config import reload_config
                reload_config()
            except Exception:
                pass

        return jsonify({
            "status": "ok",
            "section": section,
            "updated_keys": updated_keys,
            "total_updated": len(updated_keys),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/settings/theme", methods=["GET", "POST"])
def api_settings_theme():
    """Get or update dashboard theme preference (in-memory, session-scoped).

    GET: returns current theme
    POST body JSON: {theme: "dark" | "light" | "system"}
    """
    global _dashboard_theme
    if request.method == "GET":
        return jsonify({"theme": _dashboard_theme})
    try:
        data = request.get_json(silent=True) or {}
        theme = (data.get("theme") or "dark").strip().lower()
        if theme not in ("dark", "light", "system"):
            return jsonify({"error": "theme must be 'dark', 'light', or 'system'"}), 400
        _dashboard_theme = theme
        return jsonify({"status": "ok", "theme": _dashboard_theme})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@app.route("/api/settings/schema")
def api_settings_schema():
    """Return editable sections with types and descriptions for UI form generation."""
    return jsonify({
        "status": "ok",
        "schema": _SETTINGS_SCHEMA,
        "editable_sections": sorted(_EDITABLE_SECTIONS),
        "readonly_sections": sorted(_READONLY_SECTIONS),
    })


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
    threading.Thread(target=_alert_monitor, daemon=True).start()
    threading.Thread(target=_sse_broadcaster, daemon=True).start()

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

    app.run(host=bind_addr, port=args.port, debug=False, threaded=True,
            ssl_context=ssl_ctx)
