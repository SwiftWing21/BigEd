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
import sqlite3
import sys
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, Response, request

FLEET_DIR = Path(__file__).parent
DB_PATH = FLEET_DIR / "fleet.db"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"

app = Flask(__name__)

# Alert state — tracked in memory, broadcast via SSE
_alerts = []
_alert_lock = threading.Lock()
_sse_clients = []


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
    """Add an alert (info/warning/critical) and broadcast via SSE."""
    alert = {
        "id": int(time.time() * 1000),
        "level": level,
        "message": message,
        "source": source,
        "time": datetime.utcnow().isoformat(),
        "acknowledged": False,
    }
    with _alert_lock:
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
            agents = query("""
                SELECT name, last_heartbeat FROM agents
                WHERE last_heartbeat < datetime('now', '-5 minutes')
                AND status != 'OFFLINE'
            """)
            for a in agents:
                _add_alert("warning", f"Agent '{a['name']}' no heartbeat for >5min", "fleet")

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

        except Exception:
            pass

        time.sleep(30)  # Check every 30s


# ── Original API endpoints ───────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    agents = query("SELECT name, role, status, last_heartbeat FROM agents ORDER BY name")
    counts = {}
    for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
        row = query("SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,))
        counts[s] = row[0]["n"] if row else 0
    return jsonify({"agents": agents, "tasks": counts})


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
        return jsonify({"error": str(e), "files": 0, "chunks": 0, "sources": []})


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

    # Read from hw_state.json (written by hw_supervisor)
    if HW_STATE_JSON.exists():
        try:
            hw = json.loads(HW_STATE_JSON.read_text())
            result.update({
                "gpu_temp_c": hw.get("gpu_temp_c", 0),
                "gpu_power_w": hw.get("gpu_power_w", 0),
                "gpu_fan_pct": hw.get("gpu_fan_pct", 0),
                "gpu_vram_used_gb": round(hw.get("gpu_vram_used_bytes", 0) / (1024**3), 2),
                "gpu_vram_total_gb": round(hw.get("gpu_vram_total_bytes", 0) / (1024**3), 2),
                "cpu_temp_c": hw.get("cpu_temp_c", 0),
                "ambient_estimate_c": hw.get("ambient_estimate_c", 0),
                "thermal_state": hw.get("state", "unknown"),
                "model_tier": hw.get("current_tier", "unknown"),
            })
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
        return jsonify({"error": str(e)}), 500


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
    stats = {}

    # Fleet DB tables
    try:
        conn = get_conn()
        for table in ["tasks", "agents", "messages", "locks", "notes"]:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[f"fleet.{table}"] = {"count": count}
            except Exception:
                pass
        conn.close()
    except Exception:
        pass

    # Tools DB (launcher data)
    tools_db = Path(__file__).parent.parent / "BigEd" / "launcher" / "data" / "tools.db"
    if tools_db.exists():
        try:
            conn = sqlite3.connect(str(tools_db), timeout=5)
            conn.row_factory = sqlite3.Row
            for table in ["crm", "accounts", "onboarding", "customers", "agents"]:
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
        result["error"] = str(e)
    return jsonify(result)


@app.route("/api/alerts")
def api_alerts():
    """Return current alerts."""
    with _alert_lock:
        return jsonify(_alerts[-50:])


@app.route("/api/alerts/ack/<int:alert_id>", methods=["POST"])
def api_ack_alert(alert_id):
    """Acknowledge an alert."""
    with _alert_lock:
        for a in _alerts:
            if a["id"] == alert_id:
                a["acknowledged"] = True
                return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


# ── DAG Visualization ─────────────────────────────────────────────────────────

@app.route("/api/dag/<int:parent_id>")
def api_dag(parent_id):
    """DAG visualization data for a task chain."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        import db
        return jsonify(db.get_dag_graph(parent_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Process Control (extracted to process_control.py) ─────────────────────────
from process_control import fleet_bp
app.register_blueprint(fleet_bp)


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

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Dashboard v2</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #111; --bg2: #1a1a1a; --bg3: #242424; --border: #333;
    --text: #e2e2e2; --dim: #888; --accent: #b22222; --gold: #c8a84b;
    --green: #4caf50; --red: #f44336; --orange: #ff9800; --blue: #42a5f5;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; }
  a { color: var(--gold); text-decoration: none; }

  .header {
    background: var(--bg3); padding: 16px 24px; border-bottom: 2px solid var(--accent);
    display: flex; align-items: center; gap: 12px;
  }
  .header h1 { font-size: 20px; color: var(--gold); }
  .header .status { margin-left: auto; font-size: 13px; color: var(--dim); display: flex; gap: 8px; align-items: center; }
  .header .live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  .alert-bar { padding: 0 16px; }
  .alert-item {
    padding: 8px 16px; margin: 4px 0; border-radius: 4px; font-size: 13px;
    display: flex; align-items: center; gap: 8px;
  }
  .alert-item.critical { background: #3d1b1b; border-left: 3px solid var(--red); }
  .alert-item.warning { background: #3d2e0e; border-left: 3px solid var(--orange); }
  .alert-item.info { background: #1b2a3d; border-left: 3px solid var(--blue); }
  .alert-item .dismiss { cursor: pointer; margin-left: auto; color: var(--dim); }

  .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 16px; padding: 16px; max-width: 1600px; margin: 0 auto;
  }

  .card {
    background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; min-height: 200px;
  }
  .card h2 { font-size: 14px; color: var(--gold); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
  .card.wide { grid-column: span 2; }

  .stat-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
  .stat {
    background: var(--bg3); border-radius: 6px; padding: 12px 16px; flex: 1; min-width: 120px; text-align: center;
  }
  .stat .value { font-size: 28px; font-weight: bold; }
  .stat .label { font-size: 11px; color: var(--dim); margin-top: 4px; }
  .stat.green .value { color: var(--green); }
  .stat.red .value { color: var(--red); }
  .stat.gold .value { color: var(--gold); }
  .stat.blue .value { color: var(--blue); }
  .stat.orange .value { color: var(--orange); }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--dim); font-weight: normal; padding: 6px 8px; border-bottom: 1px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid var(--bg3); }
  tr:hover td { background: var(--bg3); }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;
  }
  .badge-done { background: #1b3d1b; color: var(--green); }
  .badge-failed { background: #3d1b1b; color: var(--red); }
  .badge-running { background: #3d2e0e; color: var(--orange); }
  .badge-pending { background: #1b2a3d; color: var(--blue); }
  .badge-idle { background: #1b2a3d; color: var(--blue); }
  .badge-busy { background: #3d2e0e; color: var(--orange); }
  .badge-offline { background: #2d2d2d; color: var(--dim); }
  .badge-info { background: #1b2a3d; color: var(--blue); }

  .chart-container { position: relative; height: 260px; }

  .timeline { max-height: 400px; overflow-y: auto; }
  .timeline-item {
    display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--bg3);
    font-size: 12px;
  }
  .timeline-item .time { color: var(--dim); min-width: 60px; }
  .timeline-item .agent { color: var(--gold); min-width: 80px; }

  .file-list { max-height: 300px; overflow-y: auto; font-size: 12px; }
  .file-item { padding: 4px 0; border-bottom: 1px solid var(--bg3); display: flex; justify-content: space-between; }
  .file-item .name { color: var(--text); }
  .file-item .meta { color: var(--dim); }

  .refresh-btn {
    background: var(--bg3); border: 1px solid var(--border); color: var(--dim);
    padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
  }
  .refresh-btn:hover { color: var(--text); border-color: var(--gold); }

  .thermal-gauge {
    display: flex; gap: 12px; margin-top: 8px;
  }
  .gauge {
    flex: 1; background: var(--bg3); border-radius: 6px; padding: 10px; text-align: center;
  }
  .gauge .temp { font-size: 24px; font-weight: bold; }
  .gauge .name { font-size: 11px; color: var(--dim); margin-top: 4px; }

  @media (max-width: 800px) {
    .grid { grid-template-columns: 1fr; }
    .card.wide { grid-column: span 1; }
  }
</style>
</head>
<body>

<div class="header">
  <span style="font-size:24px">&#x1f9f1;</span>
  <h1>FLEET DASHBOARD v2</h1>
  <div class="status">
    <div class="live-dot" id="liveDot"></div>
    <span id="connectionStatus">Connecting...</span>
    <button class="refresh-btn" onclick="loadAll()">Refresh</button>
    <span id="lastUpdate"></span>
  </div>
</div>

<div class="alert-bar" id="alertBar"></div>

<div class="grid">
  <div class="card">
    <h2>Task Summary</h2>
    <div class="stat-row" id="taskStats"></div>
  </div>

  <div class="card">
    <h2>Thermal</h2>
    <div class="thermal-gauge" id="thermalGauge"></div>
    <div class="stat-row" id="thermalStats" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2>Agents</h2>
    <table><thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Last Seen</th></tr></thead>
    <tbody id="agentTable"></tbody></table>
  </div>

  <div class="card">
    <h2>Training</h2>
    <div id="trainingStatus"></div>
    <div class="file-list" id="trainingLogs" style="margin-top:8px"></div>
  </div>

  <div class="card">
    <h2>Activity — Last 30 Days</h2>
    <div class="chart-container"><canvas id="activityChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Skills Used</h2>
    <div class="chart-container"><canvas id="skillsChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Discussions / Meetings</h2>
    <table><thead><tr><th>Topic</th><th>Agents</th><th>Rounds</th><th>Posts</th></tr></thead>
    <tbody id="discussionTable"></tbody></table>
  </div>

  <div class="card">
    <h2>Modules</h2>
    <div id="modulesList"></div>
  </div>

  <div class="card">
    <h2>Code Reviews</h2>
    <div class="file-list" id="reviewList"></div>
  </div>

  <div class="card">
    <h2>Code Output</h2>
    <div class="stat-row" id="codeStats"></div>
  </div>

  <div class="card">
    <h2>Knowledge Base</h2>
    <div class="stat-row" id="knowledgeStats"></div>
    <div class="file-list" id="knowledgeList"></div>
  </div>

  <div class="card">
    <h2>RAG Index</h2>
    <div class="stat-row" id="ragStats"></div>
    <div class="file-list" id="ragSources"></div>
  </div>

  <div class="card">
    <h2>Data Stats</h2>
    <div class="file-list" id="dataStats"></div>
  </div>

  <div class="card wide">
    <h2>Recent Activity</h2>
    <div class="timeline" id="timeline"></div>
  </div>
</div>

<script>
let activityChart = null;
let skillsChart = null;
let eventSource = null;

async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function badge(status) {
  const s = (status || '').toLowerCase();
  return `<span class="badge badge-${s}">${status}</span>`;
}

function timeAgo(dateStr) {
  if (!dateStr) return 'never';
  const d = new Date(dateStr + 'Z');
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

function shortTime(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'Z');
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}

function tempColor(temp, sustained, burst) {
  if (temp >= burst) return 'var(--red)';
  if (temp >= sustained) return 'var(--orange)';
  if (temp >= sustained - 10) return 'var(--gold)';
  return 'var(--green)';
}

// ── SSE Connection ──────────────────────────────────────────────────────────

function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/stream');

  eventSource.onopen = () => {
    document.getElementById('connectionStatus').textContent = 'Live';
    document.getElementById('liveDot').style.background = 'var(--green)';
  };

  eventSource.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'status') {
        updateStatusFromSSE(msg.data);
      } else if (msg.type === 'alert') {
        addAlertToBar(msg.data);
      }
    } catch (err) {}
  };

  eventSource.onerror = () => {
    document.getElementById('connectionStatus').textContent = 'Reconnecting...';
    document.getElementById('liveDot').style.background = 'var(--red)';
    setTimeout(connectSSE, 5000);
  };
}

function updateStatusFromSSE(data) {
  const t = data.tasks;
  const total = t.DONE + t.FAILED + t.PENDING + t.RUNNING;
  document.getElementById('taskStats').innerHTML = `
    <div class="stat green"><div class="value">${t.DONE}</div><div class="label">Done</div></div>
    <div class="stat red"><div class="value">${t.FAILED}</div><div class="label">Failed</div></div>
    <div class="stat orange"><div class="value">${t.RUNNING}</div><div class="label">Running</div></div>
    <div class="stat blue"><div class="value">${t.PENDING}</div><div class="label">Pending</div></div>
    <div class="stat gold"><div class="value">${total}</div><div class="label">Total</div></div>
  `;
  document.getElementById('agentTable').innerHTML = data.agents.map(a => `
    <tr><td>${a.name}</td><td style="color:var(--dim)">${a.role}</td>
    <td>${badge(a.status)}</td><td style="color:var(--dim)">${timeAgo(a.last_heartbeat)}</td></tr>
  `).join('');
  document.getElementById('lastUpdate').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

function addAlertToBar(alert) {
  if (alert.acknowledged) return;
  const bar = document.getElementById('alertBar');
  const div = document.createElement('div');
  div.className = `alert-item ${alert.level}`;
  div.innerHTML = `
    <strong>${alert.level.toUpperCase()}</strong>
    <span>${alert.message}</span>
    <span class="dismiss" onclick="ackAlert(${alert.id}, this.parentElement)">&times;</span>
  `;
  bar.prepend(div);
  // Keep only 5 visible
  while (bar.children.length > 5) bar.lastChild.remove();
}

async function ackAlert(id, el) {
  await fetch(`/api/alerts/ack/${id}`, {method: 'POST'});
  el.remove();
}

// ── Load functions ──────────────────────────────────────────────────────────

async function loadStatus() {
  const data = await fetchJSON('/api/status');
  updateStatusFromSSE(data);
}

async function loadCodeStats() {
  const data = await fetchJSON('/api/code_stats');
  document.getElementById('codeStats').innerHTML = `
    <div class="stat green"><div class="value">${data.lines_added}</div><div class="label">Lines Added</div></div>
    <div class="stat red"><div class="value">${data.lines_deleted}</div><div class="label">Lines Deleted</div></div>
    <div class="stat blue"><div class="value">${data.files_changed}</div><div class="label">Files Changed</div></div>
    <div class="stat gold"><div class="value">${data.commits}</div><div class="label">Commits</div></div>
  `;
}

async function loadThermal() {
  const data = await fetchJSON('/api/thermal');
  const th = data.thresholds || {};
  document.getElementById('thermalGauge').innerHTML = `
    <div class="gauge"><div class="temp" style="color:${tempColor(data.gpu_temp_c, th.gpu_sustained||75, th.gpu_burst||78)}">${data.gpu_temp_c}&deg;C</div><div class="name">GPU</div></div>
    <div class="gauge"><div class="temp" style="color:${tempColor(data.cpu_temp_c, th.cpu_sustained||80, 90)}">${data.cpu_temp_c}&deg;C</div><div class="name">CPU</div></div>
    <div class="gauge"><div class="temp" style="color:var(--blue)">${data.ambient_estimate_c}&deg;C</div><div class="name">Ambient (est)</div></div>
  `;
  document.getElementById('thermalStats').innerHTML = `
    <div class="stat"><div class="value">${data.gpu_power_w}W</div><div class="label">GPU Power</div></div>
    <div class="stat"><div class="value">${data.gpu_fan_pct}%</div><div class="label">Fan</div></div>
    <div class="stat"><div class="value">${data.gpu_vram_used_gb}/${data.gpu_vram_total_gb}GB</div><div class="label">VRAM</div></div>
    <div class="stat"><div class="value">${data.model_tier}</div><div class="label">Model Tier</div></div>
  `;
}

async function loadTraining() {
  const data = await fetchJSON('/api/training');
  let html = '';
  if (data.locked) {
    const pct = Math.round(data.elapsed_s / data.timeout_s * 100);
    html = `<div style="color:var(--orange)">Training active: ${data.holder} (${Math.round(data.elapsed_s/60)}min / ${Math.round(data.timeout_s/60)}min)</div>`;
  } else {
    html = '<div style="color:var(--dim)">No training in progress</div>';
  }
  document.getElementById('trainingStatus').innerHTML = html;

  document.getElementById('trainingLogs').innerHTML = (data.recent_logs || []).map(l => `
    <div class="file-item">
      <span class="name">${l.skill} ${l.improved ? '<span style="color:var(--green)">improved</span>' : '<span style="color:var(--dim)">no change</span>'}</span>
      <span class="meta">${l.before.toFixed(2)} -> ${l.after.toFixed(2)} (${l.iterations} iter)</span>
    </div>
  `).join('') || '<div style="color:var(--dim)">No training logs</div>';
}

async function loadModules() {
  const data = await fetchJSON('/api/modules');
  document.getElementById('modulesList').innerHTML = `
    <div style="color:var(--dim);margin-bottom:8px">Profile: <strong>${data.profile}</strong></div>
    ${(data.modules || []).map(m => `
      <div class="file-item">
        <span class="name">${m.name} <span style="color:var(--dim)">v${m.version}</span></span>
        <span class="meta">
          ${m.enabled ? '<span style="color:var(--green)">enabled</span>' : '<span style="color:var(--dim)">disabled</span>'}
          ${m.deprecated ? '<span style="color:var(--orange)"> DEPRECATED</span>' : ''}
        </span>
      </div>
    `).join('')}
  `;
}

async function loadDataStats() {
  const data = await fetchJSON('/api/data_stats');
  document.getElementById('dataStats').innerHTML = Object.entries(data).map(([k, v]) => `
    <div class="file-item">
      <span class="name">${k}</span>
      <span class="meta">${v.count} records${v.size_mb ? ` / ${v.size_mb}MB` : ''}</span>
    </div>
  `).join('') || '<div style="color:var(--dim)">No data</div>';
}

async function loadActivity() {
  const data = await fetchJSON('/api/activity');
  const labels = data.map(d => d.day.slice(5));
  const done = data.map(d => d.DONE);
  const failed = data.map(d => d.FAILED);

  if (activityChart) activityChart.destroy();
  activityChart = new Chart(document.getElementById('activityChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Done', data: done, backgroundColor: '#4caf50', borderRadius: 3 },
        { label: 'Failed', data: failed, backgroundColor: '#f44336', borderRadius: 3 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#888', font: { size: 11 } } } },
      scales: {
        x: { stacked: true, ticks: { color: '#666', font: { size: 10 } }, grid: { color: '#222' } },
        y: { stacked: true, ticks: { color: '#666' }, grid: { color: '#222' } },
      }
    }
  });
}

async function loadSkills() {
  const data = await fetchJSON('/api/skills');
  const entries = Object.entries(data).sort((a,b) => b[1].total - a[1].total);
  const labels = entries.map(e => e[0]);
  const done = entries.map(e => e[1].DONE);
  const failed = entries.map(e => e[1].FAILED);

  if (skillsChart) skillsChart.destroy();
  skillsChart = new Chart(document.getElementById('skillsChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Done', data: done, backgroundColor: '#4caf50' },
        { label: 'Failed', data: failed, backgroundColor: '#f44336' },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#888', font: { size: 11 } } } },
      scales: {
        x: { stacked: true, ticks: { color: '#666' }, grid: { color: '#222' } },
        y: { stacked: true, ticks: { color: '#aaa', font: { size: 11 } }, grid: { display: false } },
      }
    }
  });
}

async function loadDiscussions() {
  const data = await fetchJSON('/api/discussions');
  document.getElementById('discussionTable').innerHTML = data.slice(0, 15).map(d => `
    <tr><td>${d.topic}</td><td style="color:var(--dim)">${d.agents.join(', ')}</td>
    <td>${d.rounds}</td><td>${d.contributions}</td></tr>
  `).join('') || '<tr><td colspan="4" style="color:var(--dim)">No discussions yet</td></tr>';
}

async function loadReviews() {
  const data = await fetchJSON('/api/reviews');
  document.getElementById('reviewList').innerHTML = data.slice(0, 20).map(r => `
    <div class="file-item">
      <span class="name">${r.file}</span>
      <span class="meta">${r.category} / ${timeAgo(r.modified)}</span>
    </div>
  `).join('') || '<div style="color:var(--dim)">No reviews yet</div>';
}

async function loadKnowledge() {
  const data = await fetchJSON('/api/knowledge');
  const entries = Object.entries(data);
  const totalFiles = entries.reduce((s, [,v]) => s + v.count, 0);
  document.getElementById('knowledgeStats').innerHTML = `
    <div class="stat gold"><div class="value">${totalFiles}</div><div class="label">Total Files</div></div>
    <div class="stat blue"><div class="value">${entries.length}</div><div class="label">Categories</div></div>
  `;
  document.getElementById('knowledgeList').innerHTML = entries
    .sort((a,b) => b[1].count - a[1].count)
    .map(([cat, v]) => `
      <div class="file-item"><span class="name">${cat}/</span><span class="meta">${v.count} files</span></div>
    `).join('');
}

async function loadRAG() {
  const data = await fetchJSON('/api/rag');
  document.getElementById('ragStats').innerHTML = `
    <div class="stat gold"><div class="value">${data.files}</div><div class="label">Files Indexed</div></div>
    <div class="stat blue"><div class="value">${data.chunks}</div><div class="label">Chunks</div></div>
  `;
  document.getElementById('ragSources').innerHTML = (data.sources || []).slice(0, 15).map(s => `
    <div class="file-item"><span class="name">${s.path}</span><span class="meta">${s.chunks} chunks</span></div>
  `).join('') || '<div style="color:var(--dim)">Not indexed yet</div>';
}

async function loadTimeline() {
  const data = await fetchJSON('/api/timeline');
  document.getElementById('timeline').innerHTML = data.map(e => `
    <div class="timeline-item">
      <span class="time">${shortTime(e.time)}</span>
      <span class="agent">${e.agent}</span>
      <span>${badge(e.status)} ${e.detail}</span>
    </div>
  `).join('') || '<div style="color:var(--dim);padding:12px">No recent activity</div>';
}

async function loadAlerts() {
  const data = await fetchJSON('/api/alerts');
  const bar = document.getElementById('alertBar');
  bar.innerHTML = '';
  data.filter(a => !a.acknowledged).slice(0, 5).forEach(addAlertToBar);
}

async function loadAll() {
  await Promise.all([
    loadStatus(), loadCodeStats(), loadThermal(), loadTraining(),
    loadActivity(), loadSkills(), loadDiscussions(), loadModules(),
    loadReviews(), loadKnowledge(), loadRAG(), loadTimeline(),
    loadDataStats(), loadAlerts(),
  ]);
  document.getElementById('lastUpdate').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

// Initial load + SSE connection
loadAll();
connectSSE();
// Fallback polling for non-SSE data (charts, knowledge, etc) every 30s
setInterval(async () => {
  await Promise.all([
    loadThermal(), loadTraining(), loadActivity(), loadSkills(),
    loadModules(), loadReviews(), loadKnowledge(), loadRAG(),
    loadTimeline(), loadDataStats(), loadCodeStats(), loadAlerts(),
  ]);
}, 30000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


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

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # Start background threads
    threading.Thread(target=_alert_monitor, daemon=True).start()
    threading.Thread(target=_sse_broadcaster, daemon=True).start()

    print(f"Fleet Dashboard v2: http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
