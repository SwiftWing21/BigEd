"""Ollama Management REST API — status, start, stop, models, pull, switch."""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from flask import Blueprint, jsonify, request

from dashboard_utils import _require_role, _check_rate_limit

log = logging.getLogger("ollama_api")

ollama_bp = Blueprint("ollama", __name__)

OLLAMA_HOST = "http://localhost:11434"


def _ollama_url(path: str) -> str:
    return f"{OLLAMA_HOST}{path}"


def _ollama_request(path: str, method: str = "GET", data: dict | None = None,
                    timeout: int = 10) -> dict | None:
    """Make a request to the Ollama HTTP API. Returns parsed JSON or None."""
    url = _ollama_url(path)
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def _find_ollama_binary() -> str | None:
    """Auto-detect Ollama binary path."""
    # Check PATH first
    found = shutil.which("ollama")
    if found:
        return found
    # Windows: check default install location
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidate = Path(local_app) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.exists():
                return str(candidate)
    return None


def _get_ollama_pid() -> int | None:
    """Find the running Ollama server process PID via psutil."""
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "ollama" in name and proc.is_running():
                return proc.pid
        except Exception:
            pass
    return None


# ── GET /api/ollama/status ───────────────────────────────────────────────────

@ollama_bp.route("/api/ollama/status", methods=["GET"])
@_require_role("viewer")
def api_ollama_status():
    """Ollama process status + loaded models."""
    if not _check_rate_limit("ollama_status", max_per_min=30):
        return jsonify({"error": "Rate limited"}), 429

    pid = _get_ollama_pid()
    running = pid is not None

    models_loaded = []
    version = None

    if running:
        # Get loaded models
        ps_data = _ollama_request("/api/ps", timeout=10)
        if ps_data and "models" in ps_data:
            for m in ps_data["models"]:
                models_loaded.append({
                    "name": m.get("name", ""),
                    "size_vram": m.get("size_vram", 0),
                })

        # Get version
        ver_data = _ollama_request("/api/version", timeout=10)
        if ver_data:
            version = ver_data.get("version")

    return jsonify({
        "running": running,
        "pid": pid,
        "models_loaded": models_loaded,
        "version": version,
    })


# ── POST /api/ollama/start ──────────────────────────────────────────────────

@ollama_bp.route("/api/ollama/start", methods=["POST"])
@_require_role("operator")
def api_ollama_start():
    """Start Ollama if not running."""
    if not _check_rate_limit("ollama_start", max_per_min=3):
        return jsonify({"error": "Rate limited"}), 429

    # Check if already running
    pid = _get_ollama_pid()
    if pid:
        return jsonify({"ok": True, "pid": pid, "already_running": True})

    binary = _find_ollama_binary()
    if not binary:
        return jsonify({"error": "Ollama binary not found"}), 404

    try:
        proc = subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # Wait briefly for it to become responsive
        for _ in range(10):
            time.sleep(0.5)
            if _ollama_request("/api/version", timeout=3):
                break
        log.info("Ollama started, PID=%d", proc.pid)
        return jsonify({"ok": True, "pid": proc.pid})
    except Exception as e:
        log.warning("Failed to start Ollama: %s", e)
        return jsonify({"error": str(e)}), 500


# ── POST /api/ollama/stop ───────────────────────────────────────────────────

@ollama_bp.route("/api/ollama/stop", methods=["POST"])
@_require_role("operator")
def api_ollama_stop():
    """Unload all models and terminate Ollama."""
    if not _check_rate_limit("ollama_stop", max_per_min=3):
        return jsonify({"error": "Rate limited"}), 429

    # Unload all loaded models
    ps_data = _ollama_request("/api/ps", timeout=10)
    if ps_data and "models" in ps_data:
        for m in ps_data["models"]:
            model_name = m.get("name", "")
            if model_name:
                _ollama_request("/api/generate", method="POST", data={
                    "model": model_name, "keep_alive": 0,
                }, timeout=10)

    # Terminate process
    pid = _get_ollama_pid()
    if pid:
        try:
            import psutil
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            log.info("Ollama stopped (PID=%d)", pid)
        except Exception as e:
            log.warning("Failed to stop Ollama: %s", e)
            return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


# ── GET /api/ollama/models ──────────────────────────────────────────────────

@ollama_bp.route("/api/ollama/models", methods=["GET"])
@_require_role("viewer")
def api_ollama_models():
    """List available (downloaded) models."""
    if not _check_rate_limit("ollama_models", max_per_min=30):
        return jsonify({"error": "Rate limited"}), 429

    data = _ollama_request("/api/tags", timeout=10)
    if data is None:
        return jsonify({"error": "Ollama not responding"}), 503

    models = []
    for m in data.get("models", []):
        size_bytes = m.get("size", 0)
        size_gb = f"{size_bytes / 1e9:.1f}GB" if size_bytes else "unknown"
        modified = m.get("modified_at", "")
        if modified:
            modified = modified[:10]  # YYYY-MM-DD
        models.append({
            "name": m.get("name", ""),
            "size": size_gb,
            "modified": modified,
        })

    return jsonify({"models": models})


# ── POST /api/ollama/pull ───────────────────────────────────────────────────

@ollama_bp.route("/api/ollama/pull", methods=["POST"])
@_require_role("admin")
def api_ollama_pull():
    """Pull a model from Ollama registry. Body: {"model": "qwen3:8b"}."""
    if not _check_rate_limit("ollama_pull", max_per_min=3):
        return jsonify({"error": "Rate limited"}), 429

    body = request.get_json(silent=True) or {}
    model = body.get("model", "").strip()
    if not model:
        return jsonify({"error": "model is required"}), 400

    result = _ollama_request("/api/pull", method="POST", data={
        "model": model, "stream": False,
    }, timeout=300)

    if result is None:
        return jsonify({"error": "Ollama not responding or pull failed"}), 503

    log.info("Pulled model: %s", model)
    return jsonify({"ok": True, "model": model, "status": result.get("status", "success")})


# ── POST /api/ollama/switch ─────────────────────────────────────────────────

@ollama_bp.route("/api/ollama/switch", methods=["POST"])
@_require_role("operator")
def api_ollama_switch():
    """Switch active model: unload current, load new. Body: {"model": "qwen3:4b"}."""
    if not _check_rate_limit("ollama_switch", max_per_min=5):
        return jsonify({"error": "Rate limited"}), 429

    body = request.get_json(silent=True) or {}
    model = body.get("model", "").strip()
    if not model:
        return jsonify({"error": "model is required"}), 400

    # Unload all currently loaded models
    ps_data = _ollama_request("/api/ps", timeout=10)
    if ps_data and "models" in ps_data:
        for m in ps_data["models"]:
            loaded_name = m.get("name", "")
            if loaded_name:
                _ollama_request("/api/generate", method="POST", data={
                    "model": loaded_name, "keep_alive": 0,
                }, timeout=10)

    # Load the new model with a minimal prompt to warm it up
    result = _ollama_request("/api/generate", method="POST", data={
        "model": model, "prompt": "", "keep_alive": "5m",
    }, timeout=30)

    if result is None:
        return jsonify({"error": f"Failed to load model {model}"}), 503

    log.info("Switched to model: %s", model)
    return jsonify({"ok": True, "model": model})
