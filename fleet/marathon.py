"""Marathon ML (v0.43) — training detection, checkpoint monitoring, VRAM eviction."""
import json
import logging
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
log = logging.getLogger("supervisor")


def is_training_running():
    """Cross-platform training detection."""
    try:
        import psutil
        for proc in psutil.process_iter(['cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('train.py' in arg for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        import sys
        if sys.platform != "win32":
            import subprocess
            r = subprocess.run(["pgrep", "-f", "[t]rain\\.py"], capture_output=True, text=True)
            return r.returncode == 0
        return False


def _check_training_checkpoints():
    """Monitor autoresearch checkpoints directory for training progress."""
    checkpoint_dir = FLEET_DIR.parent / "autoresearch" / "checkpoints"
    if not checkpoint_dir.exists():
        return None
    checkpoints = sorted(checkpoint_dir.glob("*.pt"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not checkpoints:
        return None
    latest = checkpoints[0]
    return {
        "latest": latest.name,
        "count": len(checkpoints),
        "mtime": latest.stat().st_mtime,
        "size_mb": round(latest.stat().st_size / 1e6, 1),
    }


def _evict_gpu_models(config):
    """Pre-flight VRAM eviction: unload all GPU models before training starts.
    Sends keep_alive=0 to each loaded model so PyTorch gets clean VRAM."""
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        for model in data.get("models", []):
            name = model.get("name", "")
            if not name:
                continue
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()
                log.info(f"Evicted model '{name}' from VRAM")
            except Exception:
                pass
    except Exception as e:
        log.warning(f"VRAM eviction best-effort failed: {e}")
