"""
Unified system info — RAM, CPU, GPU, and platform detection in one call.

Combines psutil (RAM/CPU) with gpu.py (VRAM/temp) into a single snapshot.
Used by dashboard /api/health, supervisor scaling, launcher walkthrough,
and CLAUDE.USER.md auto-generation.

Usage:
    from system_info import detect_system, get_memory, get_worker_limits
    info = detect_system()       # full snapshot
    mem = get_memory()           # just RAM
    limits = get_worker_limits() # recommended max_workers based on RAM
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Optional


def get_memory() -> dict:
    """RAM snapshot: total, used, available, percent."""
    try:
        import psutil
        ram = psutil.virtual_memory()
        return {
            "ram_total_gb": round(ram.total / (1024**3), 1),
            "ram_used_gb": round(ram.used / (1024**3), 1),
            "ram_available_gb": round(ram.available / (1024**3), 1),
            "ram_pct": ram.percent,
        }
    except Exception:
        return {"ram_total_gb": 0, "ram_used_gb": 0, "ram_available_gb": 0, "ram_pct": 0}


def get_cpu() -> dict:
    """CPU snapshot: cores (physical + logical), current percent."""
    try:
        import psutil
        return {
            "cpu_physical": psutil.cpu_count(logical=False) or 0,
            "cpu_logical": psutil.cpu_count(logical=True) or 0,
            "cpu_pct": psutil.cpu_percent(interval=0),
        }
    except Exception:
        return {"cpu_physical": 0, "cpu_logical": 0, "cpu_pct": 0}


def get_gpu() -> dict:
    """GPU snapshot: name, VRAM total/used, temperature. Uses gpu.py backend."""
    try:
        from gpu import detect_gpu, read_telemetry
        backend, has_gpu = detect_gpu()
        if not has_gpu:
            return {"gpu_name": "none", "has_gpu": False}
        telem = read_telemetry(backend)
        if not telem:
            return {"gpu_name": backend.get_name(), "has_gpu": True}
        return {
            "gpu_name": backend.get_name(),
            "has_gpu": True,
            "gpu_temp_c": telem.get("gpu_temp_c"),
            "vram_total_gb": round(telem.get("vram_total_bytes", 0) / (1024**3), 1),
            "vram_used_gb": round(telem.get("vram_used_bytes", 0) / (1024**3), 1),
        }
    except Exception:
        return {"gpu_name": "unknown", "has_gpu": False}


def get_platform() -> dict:
    """Platform info: OS, arch, Python version."""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "shell": _detect_shell(),
    }


def detect_system() -> dict:
    """Full system snapshot — RAM, CPU, GPU, platform, local models in one call."""
    return {
        "memory": get_memory(),
        "cpu": get_cpu(),
        "gpu": get_gpu(),
        "platform": get_platform(),
        "local_models": detect_local_models(),
    }


def get_worker_limits(ram_total_gb: float = 0) -> dict:
    """Recommend max_workers and memory_limit_mb based on available RAM.

    Heuristic:
      <8GB  → 3 workers, 256MB limit
      8-16  → 6 workers, 384MB limit
      16-32 → 10 workers, 512MB limit
      32-64 → 13 workers, 512MB limit
      64+   → 16 workers, 768MB limit
    """
    if ram_total_gb <= 0:
        mem = get_memory()
        ram_total_gb = mem["ram_total_gb"]

    if ram_total_gb < 8:
        return {"max_workers": 3, "memory_limit_mb": 256, "tier": "minimal"}
    elif ram_total_gb < 16:
        return {"max_workers": 6, "memory_limit_mb": 384, "tier": "basic"}
    elif ram_total_gb < 32:
        return {"max_workers": 10, "memory_limit_mb": 512, "tier": "standard"}
    elif ram_total_gb < 64:
        return {"max_workers": 13, "memory_limit_mb": 512, "tier": "high"}
    else:
        return {"max_workers": 16, "memory_limit_mb": 768, "tier": "server"}


def generate_user_md() -> str:
    """Generate a CLAUDE.USER.md from detected system info."""
    info = detect_system()
    mem = info["memory"]
    cpu = info["cpu"]
    gpu = info["gpu"]
    plat = info["platform"]
    limits = get_worker_limits(mem["ram_total_gb"])

    gpu_line = f'{gpu["gpu_name"]}, {gpu.get("vram_total_gb", "?")}GB VRAM' if gpu["has_gpu"] else "None (CPU-only)"
    ollama_line = _detect_ollama()

    return f"""# User & Environment — {platform.node()}

## Hardware
- **GPU:** {gpu_line}
- **RAM:** {mem["ram_total_gb"]}GB — max_workers: {limits["max_workers"]} ({limits["tier"]})
- **CPU:** {cpu["cpu_physical"]} cores ({cpu["cpu_logical"]} logical)
- **Platform:** {plat["os"]} {plat["os_version"]} — shell: {plat["shell"]}

## Environment
- Python: {plat["python"]}
- Ollama: {ollama_line}
- Keys: HF_TOKEN, ANTHROPIC_API_KEY, VRAM_LIMIT_GB=10

## MCP Servers
| Server | Transport | URL/Command | Status |
|--------|-----------|-------------|--------|
| playwright | http | http://localhost:8931 | check with probe |

## Model Routing
- **Local default:** qwen3:8b (~6.9GB, ~45 tok/s)
- **CPU conductor:** qwen3:4b (~89 tok/s)
- **API fallback:** Claude → Gemini → Local

## Worker Limits (auto-detected)
- RAM tier: {limits["tier"]} ({mem["ram_total_gb"]}GB)
- Recommended max_workers: {limits["max_workers"]}
- Memory limit per worker: {limits["memory_limit_mb"]}MB
"""


def detect_local_models() -> dict:
    """Discover all local model backends and their loaded/available models.

    Probes Ollama, llama.cpp, and llamafile servers. Returns what's running,
    what models are loaded in VRAM, and what's available on disk.
    """
    try:
        from config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}

    ollama_host = cfg.get("models", {}).get("ollama_host", "http://localhost:11434")
    backends_cfg = cfg.get("models", {}).get("backends", {})
    llama_cpp_url = backends_cfg.get("llama_cpp_url", "http://localhost:8080")

    result = {
        "backends": {},
        "total_loaded": 0,
        "total_available": 0,
    }

    # ── Ollama ────────────────────────────────────────────────────────────
    ollama = {"name": "ollama", "running": False, "url": ollama_host,
              "loaded": [], "available": []}
    try:
        import urllib.request
        import json as _json
        with urllib.request.urlopen(f"{ollama_host}/api/tags", timeout=3) as r:
            data = _json.loads(r.read())
            ollama["running"] = True
            ollama["available"] = [m["name"] for m in data.get("models", [])]
        # Check what's actually loaded in VRAM
        with urllib.request.urlopen(f"{ollama_host}/api/ps", timeout=3) as r:
            ps = _json.loads(r.read())
            ollama["loaded"] = [m["name"] for m in ps.get("models", [])]
    except Exception:
        pass
    result["backends"]["ollama"] = ollama

    # ── llama.cpp ─────────────────────────────────────────────────────────
    llama_cpp = {"name": "llama_cpp", "running": False, "url": llama_cpp_url,
                 "loaded": [], "available": []}
    try:
        import urllib.request
        import json as _json
        with urllib.request.urlopen(f"{llama_cpp_url}/v1/models", timeout=2) as r:
            data = _json.loads(r.read())
            llama_cpp["running"] = True
            models = [m["id"] for m in data.get("data", [])]
            llama_cpp["available"] = models
            llama_cpp["loaded"] = models  # llama.cpp loads one model at a time
    except Exception:
        pass
    result["backends"]["llama_cpp"] = llama_cpp

    # ── llamafile ─────────────────────────────────────────────────────────
    # llamafile uses same API as llama.cpp but might run on a different port
    llamafile_url = backends_cfg.get("llamafile_url", "http://localhost:8081")
    llamafile = {"name": "llamafile", "running": False, "url": llamafile_url,
                 "loaded": [], "available": []}
    try:
        import urllib.request
        import json as _json
        with urllib.request.urlopen(f"{llamafile_url}/v1/models", timeout=2) as r:
            data = _json.loads(r.read())
            llamafile["running"] = True
            models = [m["id"] for m in data.get("data", [])]
            llamafile["available"] = models
            llamafile["loaded"] = models
    except Exception:
        pass
    result["backends"]["llamafile"] = llamafile

    # ── Summary ───────────────────────────────────────────────────────────
    for b in result["backends"].values():
        result["total_loaded"] += len(b["loaded"])
        result["total_available"] += len(b["available"])

    return result


def _detect_shell() -> str:
    """Best-effort shell detection."""
    import os
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name
    if sys.platform == "win32":
        if os.environ.get("BASH_VERSION"):
            return "bash (Git Bash)"
        return "powershell"
    return "unknown"


def _detect_ollama() -> str:
    """Check if Ollama is reachable. Reads host from fleet.toml."""
    import urllib.request
    host = "http://localhost:11434"
    try:
        from config import load_config
        cfg = load_config()
        host = cfg.get("models", {}).get("ollama_host", host)
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as resp:
            if resp.status == 200:
                return f"{host} (running)"
    except Exception:
        pass
    return f"{host} (not detected)"
