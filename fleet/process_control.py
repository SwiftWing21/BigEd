"""Process Control + Fleet Health REST API endpoints."""
import json
import os
import sqlite3
import sys
import signal
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, jsonify, request

FLEET_DIR = Path(__file__).parent
DB_PATH = FLEET_DIR / "fleet.db"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"

fleet_bp = Blueprint('fleet', __name__)


# ── DB helpers (duplicated from dashboard — trivial, avoids circular import) ─

def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def query(sql, params=()):
    with _get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Process Control API (extracted from dashboard.py, TECH_DEBT 4.3) ────────
# REST endpoints for process lifecycle — replaces raw bash pkill/pgrep strings.

@fleet_bp.route("/api/fleet/start", methods=["POST"])
def api_fleet_start():
    """Start fleet workers. Body: {roles: [...]} or empty for all."""
    import subprocess
    try:
        data = request.get_json(silent=True) or {}
        roles = data.get("roles")
        cmd = [sys.executable, str(FLEET_DIR / "supervisor.py")]
        proc = subprocess.Popen(
            cmd, cwd=str(FLEET_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return jsonify({"status": "started", "pid": proc.pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/stop", methods=["POST"])
def api_fleet_stop():
    """Stop fleet by signaling supervisor. Graceful SIGTERM then SIGKILL."""
    try:
        agents = query("SELECT name, pid FROM agents WHERE role='supervisor' AND pid IS NOT NULL")
        killed = []
        for a in agents:
            pid = a.get("pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append({"name": a["name"], "pid": pid})
                except (OSError, ProcessLookupError):
                    pass
        return jsonify({"status": "stopping", "signaled": killed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/workers")
def api_fleet_workers():
    """List all workers with PID and alive status."""
    try:
        agents = query(
            "SELECT name, role, status, pid, last_heartbeat FROM agents WHERE role != 'supervisor'"
        )
        result = []
        for a in agents:
            alive = False
            if a.get("pid"):
                try:
                    os.kill(a["pid"], 0)  # signal 0 = check if alive
                    alive = True
                except (OSError, ProcessLookupError):
                    pass
            result.append({**a, "alive": alive})
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/worker/<name>/restart", methods=["POST"])
def api_fleet_worker_restart(name):
    """Restart a specific worker by name. Kills old PID, supervisor respawns."""
    try:
        rows = query("SELECT pid FROM agents WHERE name=?", (name,))
        if not rows:
            return jsonify({"error": f"Agent '{name}' not found"}), 404
        pid = rows[0].get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        # Mark agent as needing restart — supervisor will respawn on next cycle
        with _get_conn() as conn:
            conn.execute("UPDATE agents SET status='IDLE', pid=NULL WHERE name=?", (name,))
        return jsonify({"status": "restarting", "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/health")
def api_fleet_health():
    """Overall fleet health check — supervisors, workers, Ollama, thermal."""
    try:
        sup_agents = query("SELECT name, status, pid, last_heartbeat FROM agents WHERE role='supervisor'")
        worker_count = query("SELECT COUNT(*) as n FROM agents WHERE role != 'supervisor'")[0]["n"]
        active_workers = query(
            "SELECT COUNT(*) as n FROM agents WHERE role != 'supervisor' AND status IN ('IDLE', 'BUSY')"
        )[0]["n"]
        pending = query("SELECT COUNT(*) as n FROM tasks WHERE status='PENDING'")[0]["n"]

        # Ollama status
        ollama_ok = False
        try:
            import urllib.request
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2):
                ollama_ok = True
        except Exception:
            pass

        # Thermal
        thermal = None
        try:
            if HW_STATE_JSON.exists():
                thermal = json.loads(HW_STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass

        return jsonify({
            "supervisors": [dict(s) for s in sup_agents],
            "workers": {"total": worker_count, "active": active_workers},
            "tasks_pending": pending,
            "ollama": ollama_ok,
            "thermal": thermal,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/uptime")
def api_fleet_uptime():
    """v0.42: Fleet uptime since supervisor started."""
    try:
        agents = query("SELECT name, last_heartbeat FROM agents WHERE role='supervisor' ORDER BY name")
        if not agents:
            return jsonify({"uptime_seconds": 0, "status": "not running"})
        # Use oldest supervisor heartbeat as start proxy
        oldest = None
        for a in agents:
            hb = a.get("last_heartbeat")
            if hb:
                try:
                    dt = datetime.fromisoformat(hb).replace(tzinfo=timezone.utc)
                    if oldest is None or dt < oldest:
                        oldest = dt
                except Exception:
                    pass
        if oldest:
            uptime = (datetime.now(timezone.utc) - oldest).total_seconds()
            return jsonify({"uptime_seconds": int(uptime), "status": "running"})
        return jsonify({"uptime_seconds": 0, "status": "unknown"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/idle")
def api_fleet_idle():
    """v0.42: Idle evolution statistics."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        import db
        stats = db.get_idle_stats(period=request.args.get("period", "week"))
        total_runs = sum(r.get("runs", 0) for r in stats)
        total_cost = sum(r.get("total_cost", 0) for r in stats)
        return jsonify({
            "total_runs": total_runs,
            "total_cost": round(total_cost, 4),
            "skills": stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/marathon")
def api_fleet_marathon():
    """v0.43: Marathon session status — active sessions and recent snapshots."""
    try:
        import re
        marathon_dir = FLEET_DIR / "knowledge" / "marathon"
        if not marathon_dir.exists():
            return jsonify({"sessions": []})

        sessions = []
        for f in sorted(marathon_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]:
            content = f.read_text(encoding="utf-8")
            snapshot_count = content.count("## Snapshot")
            dates = re.findall(r"## Snapshot \d+ — (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", content)
            sessions.append({
                "session_id": f.stem,
                "snapshots": snapshot_count,
                "last_snapshot": dates[-1] if dates else None,
                "size_bytes": f.stat().st_size,
            })
        return jsonify({"sessions": sessions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fleet_bp.route("/api/fleet/checkpoints")
def api_fleet_checkpoints():
    """v0.43: Training checkpoint status from autoresearch."""
    try:
        checkpoint_dir = FLEET_DIR.parent / "autoresearch" / "checkpoints"
        if not checkpoint_dir.exists():
            return jsonify({"checkpoints": [], "count": 0})

        checkpoints = []
        for cp in sorted(checkpoint_dir.glob("*.pt"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]:
            checkpoints.append({
                "name": cp.name,
                "size_mb": round(cp.stat().st_size / 1e6, 1),
                "modified": cp.stat().st_mtime,
            })
        return jsonify({"checkpoints": checkpoints, "count": len(list(checkpoint_dir.glob("*.pt")))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
