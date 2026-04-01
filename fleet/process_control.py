"""Process Control + Fleet Health REST API endpoints."""
import json
import logging
import os
import re
import sys
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, jsonify, request

from security import safe_error as _safe_error

from dashboard_utils import (
    FLEET_DIR, DB_PATH, HW_STATE_JSON, VALID_AGENT,
    _load_config, get_conn, query, _require_role, _check_rate_limit,
)

fleet_bp = Blueprint('fleet', __name__)

log = logging.getLogger("process_control")


# ── Process Control API (extracted from dashboard.py, TECH_DEBT 4.3) ────────
# REST endpoints for process lifecycle — replaces raw bash pkill/pgrep strings.

@fleet_bp.route("/api/fleet/start", methods=["POST"])
@_require_role("operator")
def api_fleet_start():
    """Start fleet workers. Body: {roles: [...]} or empty for all.

    Rate-limited to 3/min to prevent accidental multi-launch.
    """
    if not _check_rate_limit("fleet_start", max_per_min=3):
        return jsonify({"error": "Rate limited — fleet start can only be called 3 times per minute"}), 429
    import subprocess
    try:
        data = request.get_json(silent=True) or {}
        cmd = [sys.executable, str(FLEET_DIR / "supervisor.py")]
        proc = subprocess.Popen(
            cmd, cwd=str(FLEET_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("Fleet start requested via API, supervisor PID=%d", proc.pid)
        return jsonify({"status": "started", "pid": proc.pid})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/fleet/stop", methods=["POST"])
@_require_role("operator")
def api_fleet_stop():
    """Stop fleet gracefully — kill all fleet processes (workers, supervisor, dashboard).

    Finds processes by scanning cmdlines (psutil) rather than relying solely on
    DB PIDs, which can be stale. Responds before self-terminating the dashboard.
    Rate-limited to 3/min to prevent accidental repeated stops.
    """
    if not _check_rate_limit("fleet_stop", max_per_min=3):
        return jsonify({"error": "Rate limited — fleet stop can only be called 3 times per minute"}), 429
    try:
        terminated = []
        my_pid = os.getpid()

        # Phase 1: Find ALL fleet processes via psutil cmdline scan
        try:
            import psutil
            fleet_patterns = [
                "supervisor.py", "hw_supervisor.py", "web_app.py",
                "worker.py", "fleet_bridge.py",
            ]
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                if p.pid == my_pid:
                    continue  # don't kill ourselves yet
                cmdline = " ".join(p.info.get("cmdline") or [])
                for pattern in fleet_patterns:
                    if pattern in cmdline:
                        try:
                            p.terminate()
                            terminated.append({"name": pattern, "pid": p.pid})
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        break
        except ImportError:
            # Fallback: kill by DB PIDs only
            agents = query("SELECT name, pid FROM agents WHERE pid IS NOT NULL")
            for a in agents:
                pid = a.get("pid")
                if pid and pid != my_pid:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        terminated.append({"name": a["name"], "pid": pid})
                    except (OSError, ProcessLookupError):
                        pass

        # Phase 2: Mark all agents as OFFLINE in DB
        try:
            with get_conn() as conn:
                conn.execute("UPDATE agents SET status='OFFLINE', pid=NULL")
        except Exception:
            pass

        # Phase 3: Checkpoint DB
        try:
            import db
            db.shutdown()
        except Exception:
            pass

        log.info("Fleet stop: terminated %d processes", len(terminated))

        # Phase 4: Schedule self-termination AFTER sending the response
        import threading
        def _self_terminate():
            time.sleep(0.5)  # let response flush
            os._exit(0)
        threading.Thread(target=_self_terminate, daemon=True).start()

        return jsonify({
            "status": "stopping",
            "terminated": terminated,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/fleet/restart", methods=["POST"])
@_require_role("operator")
def api_fleet_restart():
    """Restart fleet — stop all processes then re-launch supervisor + dashboard.

    Rate-limited to 3/min.
    """
    if not _check_rate_limit("fleet_restart", max_per_min=3):
        return jsonify({"error": "Rate limited"}), 429
    import subprocess
    import threading

    try:
        my_pid = os.getpid()
        terminated = []

        # Kill all fleet processes except ourselves
        try:
            import psutil
            fleet_patterns = [
                "supervisor.py", "hw_supervisor.py",
                "worker.py", "fleet_bridge.py",
            ]
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                if p.pid == my_pid:
                    continue
                cmdline = " ".join(p.info.get("cmdline") or [])
                for pattern in fleet_patterns:
                    if pattern in cmdline:
                        try:
                            p.terminate()
                            terminated.append(pattern)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        break
            # Also kill other web_app.py instances
            for p in psutil.process_iter(["pid", "cmdline"]):
                if p.pid == my_pid:
                    continue
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "web_app.py" in cmdline:
                    try:
                        p.terminate()
                        terminated.append("web_app.py")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except ImportError:
            pass

        # Mark agents offline
        try:
            with get_conn() as conn:
                conn.execute("UPDATE agents SET status='OFFLINE', pid=NULL")
        except Exception:
            pass

        log.info("Fleet restart: killed %d processes, relaunching", len(terminated))

        # Relaunch supervisor (it will start a new dashboard)
        def _relaunch():
            time.sleep(1)
            subprocess.Popen(
                [sys.executable, str(FLEET_DIR / "supervisor.py")],
                cwd=str(FLEET_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            time.sleep(0.5)
            os._exit(0)  # kill this dashboard process
        threading.Thread(target=_relaunch, daemon=True).start()

        return jsonify({"status": "restarting", "killed": terminated})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/fleet/model-switch", methods=["POST"])
@_require_role("operator")
def api_fleet_model_switch():
    """Switch the active Ollama model. Body: {model: "qwen3:8b"}."""
    if not _check_rate_limit("model_switch", max_per_min=5):
        return jsonify({"error": "Rate limited — model switch can only be called 5 times per minute"}), 429
    try:
        data = request.get_json(silent=True) or {}
        model = data.get("model", "").strip()
        if not model or not re.match(r'^[a-zA-Z0-9_.:\-/]{1,128}$', model):
            return jsonify({"error": "Invalid model name"}), 400

        import urllib.request
        from config import get_ollama_host as _goh
        # Send a keepalive ping to Ollama with the new model to load it
        payload = json.dumps({"model": model, "keep_alive": "10m"}).encode()
        req = urllib.request.Request(
            f"{_goh()}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()

        # Update fleet.toml default model
        toml_path = FLEET_DIR / "fleet.toml"
        if toml_path.exists():
            content = toml_path.read_text(encoding="utf-8")
            new_content = re.sub(
                r'^(default\s*=\s*)"[^"]*"',
                f'\\1"{model}"',
                content, count=1, flags=re.MULTILINE,
            )
            if new_content != content:
                toml_path.write_text(new_content, encoding="utf-8")

        log.info("Model switch requested via API: %s", model)
        return jsonify({"status": "switched", "model": model})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


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
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/fleet/worker/<name>/restart", methods=["POST"])
@_require_role("operator")
def api_fleet_worker_restart(name):
    """Restart a specific worker by name. Kills old PID, supervisor respawns."""
    if not VALID_AGENT.match(name):
        return jsonify({"error": "Invalid agent name"}), 400
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
        with get_conn() as conn:
            conn.execute("UPDATE agents SET status='IDLE', pid=NULL WHERE name=?", (name,))
        log.info("Agent restart requested via API: %s", name)
        return jsonify({"status": "restarting", "name": name})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/fleet/health")
def api_fleet_health():
    """Detailed fleet health — uptime, memory, CPU, model status, workers, Ollama."""
    try:
        sup_agents = query("SELECT name, status, pid, last_heartbeat FROM agents WHERE role='supervisor'")
        worker_count = query("SELECT COUNT(*) as n FROM agents WHERE role != 'supervisor'")[0]["n"]
        active_workers = query(
            "SELECT COUNT(*) as n FROM agents WHERE role != 'supervisor' AND status IN ('IDLE', 'BUSY')"
        )[0]["n"]
        pending = query("SELECT COUNT(*) as n FROM tasks WHERE status='PENDING'")[0]["n"]
        running = query("SELECT COUNT(*) as n FROM tasks WHERE status='RUNNING'")[0]["n"]

        # Ollama status + loaded models
        ollama_ok = False
        ollama_models = []
        try:
            import urllib.request
            from config import get_ollama_host as _goh
            req = urllib.request.Request(f"{_goh()}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                ollama_ok = True
                ollama_models = [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            pass

        # Thermal
        thermal = None
        model_status = "unknown"
        try:
            if HW_STATE_JSON.exists():
                hw = json.loads(HW_STATE_JSON.read_text(encoding="utf-8"))
                thermal = hw.get("thermal", hw)
                model_status = hw.get("model", hw.get("status", "unknown"))
        except Exception:
            pass

        # System resources
        system = {}
        try:
            import psutil
            ram = psutil.virtual_memory()
            system = {
                "ram_total_gb": round(ram.total / (1024**3), 1),
                "ram_used_gb": round(ram.used / (1024**3), 1),
                "ram_pct": ram.percent,
                "cpu_pct": psutil.cpu_percent(interval=0),
                "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
            }
        except ImportError:
            pass

        # Uptime from oldest supervisor heartbeat
        uptime_seconds = 0
        for a in sup_agents:
            hb = a.get("last_heartbeat")
            if hb:
                try:
                    dt = datetime.fromisoformat(hb).replace(tzinfo=timezone.utc)
                    up = (datetime.now(timezone.utc) - dt).total_seconds()
                    if up > uptime_seconds:
                        uptime_seconds = int(up)
                except Exception:
                    pass

        return jsonify({
            "status": "ok",
            "uptime_seconds": uptime_seconds,
            "supervisors": [dict(s) for s in sup_agents],
            "workers": {"total": worker_count, "active": active_workers},
            "tasks_pending": pending,
            "tasks_running": running,
            "ollama": {"online": ollama_ok, "models": ollama_models},
            "model_status": model_status,
            "thermal": thermal,
            "system": system,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Agent List + Enable/Disable/Restart ──────────────────────────────────────

@fleet_bp.route("/api/agents/list")
def api_agents_list():
    """All agents with status, current task, role, and alive check."""
    try:
        agents = query("""
            SELECT a.name, a.role, a.status, a.pid, a.last_heartbeat,
                   a.current_task_id,
                   t.type as current_task_type,
                   t.status as current_task_status
            FROM agents a
            LEFT JOIN tasks t ON a.current_task_id = t.id
            ORDER BY a.name
        """)

        cfg = _load_config()
        disabled = set(cfg.get("fleet", {}).get("disabled_agents", []))

        result = []
        for a in agents:
            alive = False
            if a.get("pid"):
                try:
                    os.kill(a["pid"], 0)
                    alive = True
                except (OSError, ProcessLookupError):
                    pass
            result.append({
                "name": a["name"],
                "role": a["role"],
                "status": a["status"],
                "pid": a["pid"],
                "alive": alive,
                "disabled": a["name"] in disabled,
                "last_heartbeat": a["last_heartbeat"],
                "current_task": {
                    "id": a["current_task_id"],
                    "type": a.get("current_task_type"),
                    "status": a.get("current_task_status"),
                } if a.get("current_task_id") else None,
            })
        return jsonify({"agents": result, "total": len(result)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/agents/<name>/enable", methods=["POST"])
@_require_role("operator")
def api_agent_enable(name):
    """Enable a disabled agent — removes from disabled_agents in fleet.toml."""
    if not VALID_AGENT.match(name):
        return jsonify({"error": "Invalid agent name"}), 400
    try:
        cfg = _load_config()
        disabled = cfg.get("fleet", {}).get("disabled_agents", [])
        if name in disabled:
            disabled.remove(name)
            _update_fleet_toml_disabled(disabled)
            log.info("Agent '%s' enabled via API", name)
        return jsonify({"status": "enabled", "agent": name, "disabled_agents": disabled})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/agents/<name>/disable", methods=["POST"])
@_require_role("operator")
def api_agent_disable(name):
    """Disable an agent — adds to disabled_agents in fleet.toml.

    The agent will finish its current task before being removed from the pool.
    """
    if not VALID_AGENT.match(name):
        return jsonify({"error": "Invalid agent name"}), 400
    try:
        cfg = _load_config()
        disabled = cfg.get("fleet", {}).get("disabled_agents", [])
        if name not in disabled:
            disabled.append(name)
            _update_fleet_toml_disabled(disabled)
            log.info("Agent '%s' disabled via API", name)
        return jsonify({"status": "disabled", "agent": name, "disabled_agents": disabled})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@fleet_bp.route("/api/agents/<name>/restart", methods=["POST"])
@_require_role("operator")
def api_agent_restart(name):
    """Restart a specific agent. Sends SIGTERM, supervisor auto-respawns."""
    if not VALID_AGENT.match(name):
        return jsonify({"error": "Invalid agent name"}), 400
    try:
        rows = query("SELECT pid, status FROM agents WHERE name=?", (name,))
        if not rows:
            return jsonify({"error": f"Agent '{name}' not found"}), 404
        pid = rows[0].get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        with get_conn() as conn:
            conn.execute("UPDATE agents SET status='IDLE', pid=NULL WHERE name=?", (name,))
        log.info("Agent '%s' restart requested via API", name)
        return jsonify({"status": "restarting", "agent": name})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


def _update_fleet_toml_disabled(disabled_list):
    """Update the disabled_agents list in fleet.toml (process_control copy)."""
    toml_path = FLEET_DIR / "fleet.toml"
    content = toml_path.read_text(encoding="utf-8")
    arr = "[" + ", ".join(f'"{a}"' for a in disabled_list) + "]"
    new_content = re.sub(
        r'^disabled_agents\s*=\s*\[.*\].*$',
        f'disabled_agents = {arr}  # agents excluded from fleet boot',
        content, count=1, flags=re.MULTILINE,
    )
    toml_path.write_text(new_content, encoding="utf-8")


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
