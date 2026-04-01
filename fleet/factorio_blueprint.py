"""Factorio sandbox proxy endpoints.

Extracted from dashboard.py (Phase 2 of dashboard decomposition).
All /api/factorio/* routes live here.
"""
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

from flask import Blueprint, jsonify, request

from dashboard_utils import _load_config, FLEET_DIR, _safe_error

log = logging.getLogger("dashboard.factorio")

factorio_bp = Blueprint("factorio", __name__)


# ── Generic bridge proxy ────────────────────────────────────────────────────

def _proxy_bridge(path: str, method: str = "GET", timeout: int = 5,
                  error_status: int = 502, fallback: dict | None = None):
    """Proxy a request to the Factorio bridge API.

    Returns (response_dict, status_code) or raw bytes for pass-through.
    """
    port = _load_config().get("factorio", {}).get("bridge_port", 27016)
    url = f"http://127.0.0.1:{port}{path}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return json.loads(data), 200
    except Exception:
        if fallback is not None:
            return fallback, 200
        return {"error": "Bridge unreachable"}, error_status


def _proxy_bridge_raw(path: str, timeout: int = 5):
    """Proxy and return raw bytes + JSON content type (avoids re-encoding)."""
    port = _load_config().get("factorio", {}).get("bridge_port", 27016)
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read(), 200, {"Content-Type": "application/json"}
    except Exception:
        return jsonify({"error": "Bridge unreachable"}), 502


# ── Process helpers ─────────────────────────────────────────────────────────

_factorio_procs: dict = {}  # track {"server": Popen, "bridge": Popen}


def _factorio_kill_all():
    """Kill all Factorio processes (server, bridge, setup_and_launch). Returns list of what was stopped."""
    import psutil
    stopped = []
    # 1. Graceful bridge shutdown via API
    try:
        port = _load_config().get("factorio", {}).get("bridge_port", 27016)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=2)
        stopped.append("bridge (via API)")
        import time as _t; _t.sleep(1)
    except Exception:
        pass
    # 2. Kill by process scan — terminate then kill
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(p.info.get("cmdline") or [])
            pname = (p.info.get("name") or "").lower()
            is_bridge = "factorio.bridge" in cmdline
            is_launcher = "factorio.setup_and_launch" in cmdline
            is_server = pname.startswith("factorio") and "--start-server" in cmdline
            if is_bridge or is_launcher or is_server:
                label = "bridge" if is_bridge else "launcher" if is_launcher else "server"
                p.terminate()
                try:
                    p.wait(timeout=3)
                except psutil.TimeoutExpired:
                    p.kill()
                stopped.append(f"{label} (PID {p.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _factorio_procs.clear()
    # 3. Clean PID file
    pid_file = os.path.join(str(FLEET_DIR), "factorio", "server_data", "pids.json")
    try:
        os.remove(pid_file)
    except FileNotFoundError:
        pass
    return stopped


def _factorio_wait_for_rcon(port=27015, password="", timeout=30):
    """Block until RCON accepts auth or timeout. Returns True if ready."""
    import struct, socket, time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect(("127.0.0.1", port))
            body = password.encode("utf-8")
            payload = struct.pack("<ii", 1, 3) + body + b"\x00\x00"
            pkt = struct.pack("<i", len(payload)) + payload
            sock.sendall(pkt)
            data = sock.recv(4096)
            if data and len(data) >= 12:
                _, rid, _ = struct.unpack("<iii", data[:12])
                if rid >= 0:
                    return True
        except Exception:
            pass
        finally:
            try:
                if sock: sock.close()
            except Exception:
                pass
        _t.sleep(2)
    return False


# ── Route handlers ──────────────────────────────────────────────────────────

@factorio_bp.route("/api/factorio/bridge-status")
def api_factorio_bridge_status():
    """Proxy bridge status — avoids CORS issues from browser."""
    data, status = _proxy_bridge("/api/status", timeout=3,
                                 fallback={"running": False, "error": "bridge unreachable"})
    return jsonify(data), status


@factorio_bp.route("/api/factorio/bridge-state")
def api_factorio_bridge_state():
    """Proxy bridge game state — avoids CORS issues from browser."""
    data, status = _proxy_bridge("/api/state", timeout=3,
                                 fallback={"error": "bridge unreachable"})
    return jsonify(data), status


@factorio_bp.route("/api/factorio/spectator", methods=["POST"])
def api_factorio_spectator():
    """Launch Factorio client as spectator."""
    import subprocess as sp
    try:
        from factorio.lua_installer import detect_factorio_path
        from factorio.bridge_config import load_factorio_config
        cfg = load_factorio_config()
        fpath = detect_factorio_path()
        if not fpath:
            return jsonify({"success": False, "error": "Factorio install not found"})
        exe = fpath / "bin" / "x64" / "factorio.exe"
        if not exe.exists():
            exe = fpath / "factorio"
        if not exe.exists():
            return jsonify({"success": False, "error": f"Executable not found at {exe}"})
        sp.Popen(
            [str(exe), "--mp-connect", f"localhost:{cfg.rcon_port}"],
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )
        return jsonify({"success": True})
    except Exception as e:
        log.warning("Factorio spectator launch failed: %s", e)
        return jsonify({"success": False, "error": _safe_error(e)})


@factorio_bp.route("/api/factorio/fpm", methods=["POST"])
def api_factorio_fpm():
    """Launch Factorio Process Manager GUI."""
    import subprocess as sp
    try:
        fpm_script = os.path.join(str(FLEET_DIR), "factorio", "process_manager.py")
        sp.Popen(
            [sys.executable, fpm_script],
            cwd=str(FLEET_DIR),
            stdout=sp.DEVNULL, stderr=sp.DEVNULL,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )
        return jsonify({"success": True})
    except Exception as e:
        log.warning("FPM launch failed: %s", e)
        return jsonify({"success": False, "error": _safe_error(e)})


@factorio_bp.route("/api/factorio/start", methods=["POST"])
def api_factorio_start():
    """Start Factorio headless server + bridge. setup_and_launch exits after starting."""
    import subprocess as sp
    try:
        # Check if bridge is already running and healthy
        port = _load_config().get("factorio", {}).get("bridge_port", 27016)
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2)
            data = json.loads(resp.read())
            if data.get("running"):
                return jsonify({"success": True, "already_running": True})
        except Exception:
            pass

        # Launch setup_and_launch — it starts server + bridge, writes PIDs, then exits
        fleet_dir = str(FLEET_DIR)
        proc = sp.Popen(
            [sys.executable, "-m", "factorio.setup_and_launch"],
            cwd=fleet_dir,
            stdout=sp.DEVNULL, stderr=sp.DEVNULL,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        )
        try:
            proc.wait(timeout=30)
        except sp.TimeoutExpired:
            log.warning("setup_and_launch timed out after 30s")
        log.info("Factorio setup_and_launch completed (exit %s)", proc.returncode)
        return jsonify({"success": True, "launcher_exit": proc.returncode})
    except Exception as e:
        log.warning("Factorio start failed: %s", e)
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@factorio_bp.route("/api/factorio/stop", methods=["POST"])
def api_factorio_stop():
    """Stop all Factorio processes cleanly."""
    stopped = _factorio_kill_all()
    log.info("Factorio stopped: %s", stopped or "nothing running")
    return jsonify({"success": True, "stopped": stopped})


@factorio_bp.route("/api/factorio/restart", methods=["POST"])
def api_factorio_restart():
    """Full restart: stop everything, start fresh."""
    stopped = _factorio_kill_all()
    import time as _t; _t.sleep(2)
    resp = api_factorio_start()
    resp_data = resp.get_json() if hasattr(resp, "get_json") else {}
    log.info("Factorio restarted: stopped=%s, start=%s", stopped, resp_data)
    return jsonify({"success": True, "stopped": stopped, **resp_data})


@factorio_bp.route("/api/factorio/restart-bridge", methods=["POST"])
def api_factorio_restart_bridge():
    """Restart just the bridge (server stays up). For code reloads."""
    import subprocess as sp
    import psutil
    stopped = []
    # Kill bridge only
    try:
        port = _load_config().get("factorio", {}).get("bridge_port", 27016)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=2)
        stopped.append("bridge (via API)")
    except Exception:
        pass
    import time as _t; _t.sleep(1)
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(p.info.get("cmdline") or [])
            if "factorio.bridge" in cmdline:
                p.kill()
                stopped.append(f"bridge (PID {p.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _t.sleep(1)
    # Wait for RCON to be available (server should still be up)
    cfg = _load_config().get("factorio", {})
    rcon_port = cfg.get("rcon_port", 27015)
    rcon_pw = cfg.get("rcon_password", "")
    rcon_ready = _factorio_wait_for_rcon(rcon_port, rcon_pw, timeout=10)
    if not rcon_ready:
        return jsonify({"success": False, "error": "RCON not responding — server may be down",
                        "stopped": stopped}), 503
    # Start bridge
    fleet_dir = str(FLEET_DIR)
    bridge_proc = sp.Popen(
        [sys.executable, "-m", "factorio.bridge"],
        cwd=fleet_dir,
        stdout=sp.DEVNULL, stderr=sp.DEVNULL,
        creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
    )
    log.info("Bridge restarted (PID %d), RCON ready", bridge_proc.pid)
    return jsonify({"success": True, "stopped": stopped, "bridge_pid": bridge_proc.pid})


@factorio_bp.route("/api/factorio/pause", methods=["POST"])
def api_factorio_pause():
    """Proxy pause request to Factorio bridge."""
    data, status = _proxy_bridge("/api/pause", method="POST")
    if status == 200:
        return jsonify(data)
    log.warning("Factorio pause failed")
    return jsonify(data), status


@factorio_bp.route("/api/factorio/resume", methods=["POST"])
def api_factorio_resume():
    """Proxy resume request to Factorio bridge."""
    data, status = _proxy_bridge("/api/resume", method="POST")
    if status == 200:
        return jsonify(data)
    log.warning("Factorio resume failed")
    return jsonify(data), status


@factorio_bp.route("/api/factorio/focus", methods=["POST"])
def api_factorio_focus_toggle():
    """Toggle Factorio focus mode on/off and pick dedicated workers."""
    data = request.get_json(force=True) or {}
    on = bool(data.get("on", False))
    worker_count = int(data.get("workers", 2))
    focus_file = os.path.join(str(FLEET_DIR), ".factorio_focus.json")

    if on:
        import db as _db
        fleet_status = _db.get_fleet_status()
        agents = fleet_status.get("agents", [])
        idle = [a for a in agents if a.get("status") == "IDLE"]
        busy = [a for a in agents if a.get("status") != "IDLE"]
        candidates = idle + sorted(busy, key=lambda a: a.get("last_heartbeat", 0), reverse=True)
        selected = [a["name"] for a in candidates[:worker_count]]
        state = {"on": True, "workers": selected}
    else:
        state = {"on": False, "workers": []}

    try:
        with open(focus_file, "w") as f:
            json.dump(state, f)
    except Exception:
        log.warning("Failed to write factorio focus file", exc_info=True)
    return jsonify(state)


@factorio_bp.route("/api/factorio/focus", methods=["GET"])
def api_factorio_focus_state():
    """Return current Factorio focus mode state."""
    focus_file = os.path.join(str(FLEET_DIR), ".factorio_focus.json")
    try:
        with open(focus_file) as f:
            return jsonify(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify({"on": False, "workers": []})


@factorio_bp.route("/api/factorio/plans", methods=["GET"])
def api_factorio_plans_proxy():
    """Proxy plan queue from Factorio bridge."""
    data, status = _proxy_bridge("/api/plan/queue", timeout=10)
    if status == 200:
        return jsonify(data)
    log.warning("Factorio plans proxy failed", exc_info=True)
    return jsonify(data), status


@factorio_bp.route("/api/factorio/plan-history", methods=["GET"])
def api_factorio_plan_history_proxy():
    """Proxy plan history from Factorio bridge."""
    data, status = _proxy_bridge("/api/plan/history", timeout=10)
    if status == 200:
        return jsonify(data)
    log.warning("Factorio plan-history proxy failed", exc_info=True)
    return jsonify(data), status


@factorio_bp.route("/api/factorio/training-status", methods=["GET"])
def api_factorio_training_status_proxy():
    """Proxy ML training metrics from Factorio bridge."""
    data, status = _proxy_bridge("/api/training/status", timeout=5)
    if status == 200:
        return jsonify(data)
    log.warning("Factorio training-status proxy failed", exc_info=True)
    return jsonify(data), status


@factorio_bp.route("/api/factorio/spatial-map")
def api_factorio_spatial_map_proxy():
    """Proxy spatial memory data for dashboard map visualization."""
    return _proxy_bridge_raw("/api/spatial-map", timeout=5)


@factorio_bp.route("/api/factorio/reward-history")
def api_factorio_reward_history_proxy():
    """Proxy reward history for dashboard chart."""
    return _proxy_bridge_raw("/api/reward-history", timeout=5)


# ── Training Visualization ──────────────────────────────────────────────────

@factorio_bp.route("/api/factorio/training-viz")
def api_factorio_training_viz():
    """Composite training visualization data.

    Returns reward history, current lesson/phase, spatial memory summary,
    and steps per episode — all sourced from the Factorio bridge.
    """
    result = {
        "reward_history": [],
        "lesson": None,
        "phase": None,
        "spatial_summary": {},
        "steps_per_episode": [],
    }

    # 1. Training status (lesson, phase)
    training, status = _proxy_bridge("/api/training/status", timeout=5)
    if status == 200:
        result["lesson"] = training.get("current_lesson") or training.get("lesson")
        result["phase"] = training.get("current_phase") or training.get("phase")
        result["steps_per_episode"] = training.get("steps_per_episode", [])

    # 2. Reward history (last 1000 steps)
    rewards, status = _proxy_bridge("/api/reward-history", timeout=5,
                                    fallback={"history": []})
    if status == 200:
        history = rewards.get("history", rewards.get("rewards", []))
        result["reward_history"] = history[-1000:]

    # 3. Spatial memory summary
    spatial, status = _proxy_bridge("/api/spatial-map", timeout=5,
                                    fallback={})
    if status == 200:
        result["spatial_summary"] = {
            "resource_count": len(spatial.get("resources", [])),
            "entity_count": len(spatial.get("entities", [])),
            "explored_chunks": spatial.get("explored_chunks", 0),
        }

    return jsonify(result)


# ── Game Speed Control ──────────────────────────────────────────────────────

@factorio_bp.route("/api/factorio/game-speed", methods=["GET"])
def api_factorio_game_speed_get():
    """Return current game speed from the Factorio bridge."""
    data, status = _proxy_bridge("/api/game-speed", timeout=5,
                                 fallback={"speed": 1.0})
    return jsonify(data), status


@factorio_bp.route("/api/factorio/game-speed", methods=["POST"])
def api_factorio_game_speed_set():
    """Set game speed via bridge RCON.

    JSON body: {"speed": 1.0}  — valid range 0.5 to 10.0
    """
    data = request.get_json(silent=True) or {}
    speed = data.get("speed")
    if speed is None:
        return jsonify({"error": "speed is required"}), 400

    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return jsonify({"error": "speed must be a number"}), 400

    if not (0.5 <= speed <= 10.0):
        return jsonify({"error": "speed must be between 0.5 and 10.0"}), 400

    # Proxy to bridge which sends RCON command
    port = _load_config().get("factorio", {}).get("bridge_port", 27016)
    url = f"http://127.0.0.1:{port}/api/game-speed"
    try:
        import json as _json
        payload = _json.dumps({"speed": speed}).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            return jsonify(result)
    except Exception:
        return jsonify({"error": "Bridge unreachable"}), 502
