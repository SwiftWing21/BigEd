"""Marathon ML (v0.43) — training detection, checkpoint monitoring, VRAM-aware eviction."""
import json
import logging
import os
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
log = logging.getLogger("supervisor")

# VRAM budgets per training profile (approximate, GB)
_PROFILE_VRAM = {
    "micro": 2.0,
    "balanced": 4.0,   # dynamic, but typically 2-5 GB
    "stable": 8.4,
    "flat_out": 11.4,
}
_GPU_TOTAL_GB = 12.0  # RTX 3080 Ti


def is_training_running():
    """Cross-platform training detection. Returns (running: bool, profile: str|None)."""
    try:
        import psutil
        for proc in psutil.process_iter(['cmdline', 'environ']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('train.py' in arg for arg in cmdline):
                    # Try to detect profile from train_profile.py args or env
                    profile = None
                    for i, arg in enumerate(cmdline):
                        if arg in ('--profile', '-p') and i + 1 < len(cmdline):
                            profile = cmdline[i + 1]
                            break
                    # Check VRAM_LIMIT_GB env var as signal of constrained training
                    try:
                        env = proc.environ()
                        if env.get("VRAM_LIMIT_GB"):
                            vram_limit = float(env["VRAM_LIMIT_GB"])
                            if vram_limit <= 4.0:
                                profile = profile or "micro"
                    except (psutil.AccessDenied, ValueError):
                        pass
                    return True, profile
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False, None
    except ImportError:
        import sys
        if sys.platform != "win32":
            import subprocess
            r = subprocess.run(["pgrep", "-f", "[t]rain\\.py"], capture_output=True, text=True)
            return r.returncode == 0, None
        return False, None


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


def get_ollama_vram_usage(config):
    """Return total VRAM in use by Ollama models (GB), and list of loaded models."""
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        models = data.get("models", [])
        vram_bytes = sum(m.get("size_vram", 0) for m in models)
        return vram_bytes / (1024**3), models
    except Exception:
        return 0.0, []


def training_needs_eviction(config, profile=None):
    """Determine if training needs Ollama evicted from GPU.

    Returns (needs_eviction: bool, reason: str).

    Logic:
    - micro/balanced profiles: keep Ollama on GPU if enough VRAM remains
    - stable/flat_out: evict — they need most of the 12GB
    - Unknown profile: check VRAM headroom conservatively
    """
    vram_needed = _PROFILE_VRAM.get(profile, 0)

    if profile in ("stable", "flat_out"):
        return True, f"{profile} needs {vram_needed:.1f}GB — evicting Ollama"

    ollama_vram, models = get_ollama_vram_usage(config)
    available = _GPU_TOTAL_GB - ollama_vram

    if profile == "micro":
        # Micro only needs ~2GB — almost always fits alongside Ollama
        if available >= 3.0:  # 1GB headroom
            return False, f"micro needs ~2GB, {available:.1f}GB available — keeping Ollama on GPU"
        return True, f"micro needs ~2GB but only {available:.1f}GB free — evicting"

    if profile == "balanced":
        # Balanced auto-sizes to fit — always keep Ollama on GPU
        return False, f"balanced auto-sizes to available VRAM ({available:.1f}GB free) — keeping Ollama on GPU"

    # Unknown profile — conservative: evict only if less than 4GB free
    if available >= 4.0:
        return False, f"Unknown profile, {available:.1f}GB available — keeping Ollama on GPU"
    return True, f"Unknown profile, only {available:.1f}GB available — evicting"


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
