"""v0.49: System service management — verify, repair, monitor auto-boot and fleet services."""
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "service_manager"
DESCRIPTION = "Verify and manage system services (auto-boot, Ollama, fleet processes)"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent

# Platform-specific service identifiers (mirroring fleet/services.py)
_WIN_TASK_NAME = "BigEdFleet"
_MAC_PLIST_NAME = "com.biged.fleet"
_LINUX_SERVICE_NAME = "biged-fleet"

# Services that _repair supports (whitelist to prevent arbitrary restarts)
_REPAIRABLE = {"ollama", "supervisor", "hw_supervisor", "dr_ders", "autoboot"}


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "status")

    actions = {
        "status": _check_all_services,
        "verify_autoboot": _verify_autoboot,
        "verify_ollama": _verify_ollama,
        "verify_fleet": _verify_fleet,
        "repair": _repair_service,
    }
    fn = actions.get(action)
    if not fn:
        return json.dumps({"error": f"Unknown action: {action}", "valid_actions": list(actions)})
    return fn(payload, config)


# ── status ───────────────────────────────────────────────────────────────────

def _check_all_services(payload: dict, config: dict) -> str:
    """Cross-platform check of autoboot, Ollama, supervisor, hw_supervisor."""
    autoboot = json.loads(_verify_autoboot(payload, config))
    ollama = json.loads(_verify_ollama(payload, config))
    fleet = json.loads(_verify_fleet(payload, config))

    # Derive overall health
    all_ok = (
        autoboot.get("installed", False)
        and ollama.get("reachable", False)
        and fleet.get("supervisor", {}).get("running", False)
        and fleet.get("hw_supervisor", {}).get("running", False)
    )

    return json.dumps({
        "status": "ok" if all_ok else "degraded",
        "platform": sys.platform,
        "autoboot": autoboot,
        "ollama": ollama,
        "fleet": fleet,
    })


# ── verify_autoboot ──────────────────────────────────────────────────────────

def _verify_autoboot(payload: dict, config: dict) -> str:
    """Check if the auto-boot service is installed and enabled."""
    result = {"installed": False, "enabled": False, "detail": ""}

    # Check fleet.toml flag
    toml_enabled = config.get("autoboot", {}).get("enabled", False)
    result["toml_enabled"] = toml_enabled

    try:
        if sys.platform == "win32":
            result.update(_check_autoboot_windows())
        elif sys.platform == "darwin":
            result.update(_check_autoboot_macos())
        else:
            result.update(_check_autoboot_linux())
    except Exception as e:
        result["error"] = str(e)

    return json.dumps(result)


def _check_autoboot_windows() -> dict:
    """Query Task Scheduler for the BigEdFleet task."""
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", _WIN_TASK_NAME, "/fo", "CSV", "/nh"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and _WIN_TASK_NAME in r.stdout:
            enabled = "Disabled" not in r.stdout
            return {"installed": True, "enabled": enabled, "detail": r.stdout.strip()}
        return {"installed": False, "enabled": False, "detail": r.stderr.strip() or "Task not found"}
    except subprocess.TimeoutExpired:
        return {"installed": False, "error": "schtasks query timed out"}
    except FileNotFoundError:
        return {"installed": False, "error": "schtasks not found"}


def _check_autoboot_macos() -> dict:
    """Check launchd for the fleet plist."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{_MAC_PLIST_NAME}.plist"
    if not plist_path.exists():
        return {"installed": False, "enabled": False, "detail": f"No plist at {plist_path}"}
    try:
        r = subprocess.run(
            ["launchctl", "list", _MAC_PLIST_NAME],
            capture_output=True, text=True, timeout=10,
        )
        loaded = r.returncode == 0
        return {"installed": True, "enabled": loaded, "detail": r.stdout.strip() or r.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"installed": True, "error": "launchctl query timed out"}
    except FileNotFoundError:
        return {"installed": True, "error": "launchctl not found"}


def _check_autoboot_linux() -> dict:
    """Check systemd --user for biged-fleet service."""
    service_path = (
        Path.home() / ".config" / "systemd" / "user" / f"{_LINUX_SERVICE_NAME}.service"
    )
    if not service_path.exists():
        return {"installed": False, "enabled": False, "detail": f"No unit file at {service_path}"}
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", _LINUX_SERVICE_NAME],
            capture_output=True, text=True, timeout=10,
        )
        enabled = r.stdout.strip() == "enabled"
        # Also check if active
        r2 = subprocess.run(
            ["systemctl", "--user", "is-active", _LINUX_SERVICE_NAME],
            capture_output=True, text=True, timeout=10,
        )
        active = r2.stdout.strip() == "active"
        return {
            "installed": True,
            "enabled": enabled,
            "active": active,
            "detail": f"enabled={enabled}, active={active}",
        }
    except subprocess.TimeoutExpired:
        return {"installed": True, "error": "systemctl query timed out"}
    except FileNotFoundError:
        return {"installed": True, "error": "systemctl not found"}


# ── verify_ollama ────────────────────────────────────────────────────────────

def _verify_ollama(payload: dict, config: dict) -> str:
    """Check if Ollama is reachable at the configured host."""
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    result = {"host": host, "reachable": False}

    try:
        import urllib.request
        url = f"{host.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            models = [m.get("name", "") for m in data.get("models", [])]
            result["reachable"] = True
            result["models_loaded"] = models
            result["model_count"] = len(models)
    except urllib.error.URLError as e:
        result["error"] = f"Connection failed: {e.reason}"
    except Exception as e:
        result["error"] = str(e)

    return json.dumps(result)


# ── verify_fleet ─────────────────────────────────────────────────────────────

def _verify_fleet(payload: dict, config: dict) -> str:
    """Check if supervisor and Dr. Ders PIDs are running."""
    result = {
        "supervisor": {"running": False, "pid": None},
        "dr_ders": {"running": False, "pid": None},
    }

    # Try DB first — agents table tracks supervisor PIDs
    try:
        import sqlite3
        db_path = FLEET_DIR / "fleet.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT name, pid, status, last_heartbeat FROM agents WHERE role='supervisor'"
            ).fetchall()
            conn.close()
            for row in rows:
                name = row["name"]
                pid = row["pid"]
                key = "dr_ders" if "hw" in name or "dr_ders" in name else "supervisor"
                if pid and _is_pid_alive(pid):
                    result[key] = {
                        "running": True,
                        "pid": pid,
                        "status": row["status"],
                        "last_heartbeat": row["last_heartbeat"],
                    }
                else:
                    result[key] = {
                        "running": False,
                        "pid": pid,
                        "status": row["status"] if row["status"] else "not registered",
                        "detail": "PID not alive" if pid else "no PID recorded",
                    }
    except Exception as e:
        result["db_error"] = str(e)

    # Fallback: check hw_state.json for hw_supervisor liveness
    hw_state_path = FLEET_DIR / "hw_state.json"
    if hw_state_path.exists():
        try:
            hw_state = json.loads(hw_state_path.read_text(encoding="utf-8"))
            result["hw_state"] = {
                "status": hw_state.get("status"),
                "model": hw_state.get("model"),
                "thermal": hw_state.get("thermal"),
            }
        except Exception:
            pass

    return json.dumps(result)


def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is running (cross-platform)."""
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            # os.kill(pid, 0) works on Windows for owned processes;
            # fall back to tasklist for broader coverage
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            return str(pid) in r.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


# ── repair ───────────────────────────────────────────────────────────────────

def _repair_service(payload: dict, config: dict) -> str:
    """Attempt to restart a named service (with safety checks)."""
    service = payload.get("service", "")
    if not service:
        return json.dumps({"error": "No service specified", "repairable": sorted(_REPAIRABLE)})
    if service not in _REPAIRABLE:
        return json.dumps({
            "error": f"Service '{service}' not in allowed list",
            "repairable": sorted(_REPAIRABLE),
        })

    try:
        if service == "ollama":
            return _repair_ollama(config)
        elif service == "supervisor":
            return _repair_supervisor()
        elif service in ("hw_supervisor", "dr_ders"):
            return _repair_hw_supervisor()
        elif service == "autoboot":
            return _repair_autoboot()
    except Exception as e:
        return json.dumps({"error": str(e), "service": service})

    return json.dumps({"error": "Unhandled service", "service": service})


def _repair_ollama(config: dict) -> str:
    """Start Ollama serve if not already running."""
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")

    # Check if already running
    try:
        import urllib.request
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=5):
            return json.dumps({"service": "ollama", "action": "none", "detail": "Already running"})
    except Exception:
        pass

    # Attempt to start
    try:
        env = os.environ.copy()
        # Respect eco mode — CPU-only Ollama
        gpu_mode = config.get("gpu", {}).get("mode", "eco")
        if gpu_mode == "eco":
            env["CUDA_VISIBLE_DEVICES"] = "-1"

        if sys.platform == "win32":
            proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        return json.dumps({
            "service": "ollama",
            "action": "started",
            "pid": proc.pid,
            "gpu_mode": gpu_mode,
        })
    except FileNotFoundError:
        return json.dumps({"service": "ollama", "error": "ollama binary not found in PATH"})
    except Exception as e:
        return json.dumps({"service": "ollama", "error": str(e)})


def _repair_supervisor() -> str:
    """Start supervisor.py if not running."""
    # Safety: check if already running via DB
    check = json.loads(_verify_fleet({}, {}))
    if check.get("supervisor", {}).get("running"):
        return json.dumps({
            "service": "supervisor",
            "action": "none",
            "detail": "Already running",
            "pid": check["supervisor"].get("pid"),
        })

    script = FLEET_DIR / "supervisor.py"
    if not script.exists():
        return json.dumps({"service": "supervisor", "error": f"Not found: {script}"})

    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(FLEET_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(FLEET_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return json.dumps({"service": "supervisor", "action": "started", "pid": proc.pid})
    except Exception as e:
        return json.dumps({"service": "supervisor", "error": str(e)})


def _repair_hw_supervisor() -> str:
    """Start Dr. Ders (hw_supervisor.py) if not running."""
    check = json.loads(_verify_fleet({}, {}))
    if check.get("dr_ders", {}).get("running"):
        return json.dumps({
            "service": "dr_ders",
            "action": "none",
            "detail": "Already running",
            "pid": check["dr_ders"].get("pid"),
        })

    script = FLEET_DIR / "hw_supervisor.py"
    if not script.exists():
        return json.dumps({"service": "dr_ders", "error": f"Not found: {script}"})

    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(FLEET_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(FLEET_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return json.dumps({"service": "dr_ders", "action": "started", "pid": proc.pid})
    except Exception as e:
        return json.dumps({"service": "dr_ders", "error": str(e)})


def _repair_autoboot() -> str:
    """Re-install the auto-boot service using fleet/services.py."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        import services
        services.install_service(FLEET_DIR)
        return json.dumps({"service": "autoboot", "action": "reinstalled"})
    except Exception as e:
        return json.dumps({"service": "autoboot", "error": str(e)})
