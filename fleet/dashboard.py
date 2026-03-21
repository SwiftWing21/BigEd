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
        # Use db.log_alert if available, otherwise fall back to audit_log
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
            pass  # audit_log not available — skip silently
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
        if enable_default(name):
            return jsonify({"status": "enabled", "server": name})
        # Try as integration
        if name in MCP_INTEGRATIONS:
            server_def = MCP_INTEGRATIONS[name]
            config = {"type": server_def.get("type", "stdio")}
            if config["type"] == "stdio":
                config["command"] = server_def.get("command", "npx")
                config["args"] = server_def.get("args", [])
            add_server(name, config)
            return jsonify({"status": "enabled", "server": name})
        return jsonify({"error": f"Unknown server: {name}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mcp/server/<name>/disable", methods=["POST"])
def api_mcp_disable(name):
    """Disable (remove) an MCP server."""
    try:
        from mcp_manager import disable_server
        if disable_server(name):
            return jsonify({"status": "disabled", "server": name})
        return jsonify({"error": f"Server not found: {name}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Audit Log ──────────────────────────────────────────────────────────────

@app.route("/api/audit")
def api_audit():
    from audit_log import read_events, get_audit_summary
    if request.args.get("summary"):
        return jsonify(get_audit_summary())
    return jsonify(read_events(
        last_n=int(request.args.get("limit", 50)),
        event_type=request.args.get("type"),
    ))


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
        # Log to audit trail
        try:
            from audit_log import log_event
            log_event("gdpr_erasure", "dashboard", {"identifier": identifier, "deleted": result}, severity="warning")
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
        return jsonify({"status": "enabled", "agent": name, "disabled_agents": disabled})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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

    # TLS: auto-generate self-signed cert for HTTPS by default
    cert, key = _ensure_tls_cert()
    ssl_ctx = None
    if cert and key:
        ssl_ctx = (cert, key)
        print(f"Fleet Dashboard v2: https://{bind_addr}:{args.port} (TLS)")
    else:
        print(f"Fleet Dashboard v2: http://{bind_addr}:{args.port} (no TLS — openssl not found)")
    app.run(host=bind_addr, port=args.port, debug=False, threaded=True,
            ssl_context=ssl_ctx)
