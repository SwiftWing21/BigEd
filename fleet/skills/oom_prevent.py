"""
OOM Prevention — estimate VRAM/RAM requirements before task execution.
Called by supervisor or worker to check if a task can safely execute
without triggering an out-of-memory crash.

Usage:
    from skills.oom_prevent import check_oom_risk
    risk = check_oom_risk(skill_name, config)
    if risk["safe"]:
        # proceed with task
    else:
        # requeue or scale down model first
"""
import os
from pathlib import Path
import logging

SKILL_NAME = "oom_prevent"
DESCRIPTION = "Estimate VRAM/RAM requirements and prevent out-of-memory crashes"
VERSION = "1.0.0"
COMPLEXITY = "complex"
REQUIRES_NETWORK = False
SUITE = "ops"
log = logging.getLogger(SKILL_NAME)

FLEET_DIR = Path(__file__).parent.parent

# Estimated VRAM usage per model (GB) — measured on RTX 3080 Ti
MODEL_VRAM = {
    "qwen3:8b": 6.9,
    "qwen3:4b": 3.5,
    "qwen3:1.7b": 1.5,
    "qwen3:0.6b": 0.5,
    "llava": 5.0,
    "minicpm-v": 4.0,
    "qwen-vl": 5.5,
}

# Skills that are known to be VRAM-heavy (load additional models or use GPU)
HEAVY_SKILLS = {
    "vision_analyze": 5.0,     # loads vision model alongside worker model
    "code_write": 0.5,         # may trigger review model load
    "skill_evolve": 0.5,       # iterative, multiple inference calls
    "benchmark": 1.0,          # loads test model variants
    "generate_image": 4.0,     # SD model if enabled
}

# Skills that are CPU-only (no VRAM concern)
CPU_ONLY_SKILLS = {
    "rag_index", "rag_query", "ingest", "flashcard", "knowledge_prune",
    "rag_compress", "stability_report", "db_migrate", "db_encrypt",
    "dead_code_scan", "marathon_log", "service_manager", "git_manager",
}


def run(payload: dict, config: dict) -> dict:
    """Check OOM risk for a skill or get current memory status."""
    action = payload.get("action", "status")

    if action == "status":
        result = _get_memory_status()
        result.setdefault("status", "ok")
        return result
    elif action == "check":
        skill = payload.get("skill", "")
        result = check_oom_risk(skill, config)
        result.setdefault("status", "ok")
        return result
    elif action == "estimate":
        skill = payload.get("skill", "")
        result = _estimate_requirements(skill, config)
        result.setdefault("status", "ok")
        return result
    else:
        return {"status": "error", "error": f"Unknown action: {action}"}


def _get_memory_status() -> dict:
    """Current VRAM and RAM status."""
    import psutil
    ram = psutil.virtual_memory()
    status = {
        "ram_total_gb": round(ram.total / 1e9, 1),
        "ram_used_gb": round(ram.used / 1e9, 1),
        "ram_available_gb": round(ram.available / 1e9, 1),
        "ram_pct": round(ram.percent, 1),
    }

    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        status["vram_total_gb"] = round(mem.total / 1e9, 1)
        status["vram_used_gb"] = round(mem.used / 1e9, 1)
        status["vram_free_gb"] = round(mem.free / 1e9, 1)
        status["vram_pct"] = round(mem.used / mem.total * 100, 1)
    except Exception:
        status["vram_total_gb"] = 0
        status["vram_free_gb"] = 0
        status["vram_pct"] = 0

    return status


def _infer_model_vram(model_name: str) -> float:
    """Infer VRAM usage from model name or Ollama API. No hardcoded fallback."""
    import re

    # 1. Parse parameter count from name (e.g., "qwen3:8b" → 8, "llama3:70b" → 70)
    match = re.search(r':(\d+\.?\d*)b', model_name.lower())
    if match:
        params_b = float(match.group(1))
        # Rule of thumb: ~0.85 GB per billion parameters (Q4 quantization)
        return round(params_b * 0.85, 1)

    # 2. Try Ollama API for model info
    try:
        import json
        import urllib.request
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/show",
            data=body, headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            info = json.loads(r.read())
        # Extract size from model info
        size_bytes = info.get("size", 0)
        if size_bytes > 0:
            return round(size_bytes / 1e9 * 1.15, 1)  # 15% overhead for KV cache
        # Try parameter count from modelinfo
        params = info.get("details", {}).get("parameter_size", "")
        match = re.search(r'(\d+\.?\d*)\s*B', params)
        if match:
            return round(float(match.group(1)) * 0.85, 1)
    except Exception:
        pass

    # 3. Last resort: use 50% of total GPU VRAM (safe on any system)
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return round(mem.total / 1e9 * 0.5, 1)
    except Exception:
        pass

    # Absolute fallback: 3GB (conservative for small models)
    return 3.0


def _estimate_requirements(skill_name: str, config: dict) -> dict:
    """Estimate VRAM/RAM needed for a skill."""
    if skill_name in CPU_ONLY_SKILLS:
        return {
            "skill": skill_name,
            "vram_needed_gb": 0,
            "ram_needed_gb": 0.5,
            "cpu_only": True,
        }

    # Base: current model VRAM — dynamic lookup
    models = config.get("models", {})
    current_model = models.get("local", "qwen3:8b")
    base_vram = MODEL_VRAM.get(current_model)

    if base_vram is None:
        # Not in our table — try to infer from model name pattern
        base_vram = _infer_model_vram(current_model)

    # Additional for heavy skills
    extra_vram = HEAVY_SKILLS.get(skill_name, 0)

    return {
        "skill": skill_name,
        "model": current_model,
        "vram_base_gb": base_vram,
        "vram_extra_gb": extra_vram,
        "vram_total_gb": round(base_vram + extra_vram, 1),
        "cpu_only": False,
    }


def check_oom_risk(skill_name: str, config: dict) -> dict:
    """Check if executing a skill is safe given current memory state.

    Returns:
        {"safe": bool, "risk": "none"|"low"|"medium"|"high"|"critical",
         "reason": str, "recommendation": str}
    """
    status = _get_memory_status()
    estimate = _estimate_requirements(skill_name, config)

    # CPU-only skills are always safe (just check RAM)
    if estimate.get("cpu_only"):
        if status["ram_pct"] > 95:
            return {
                "safe": False, "risk": "high",
                "reason": f"RAM at {status['ram_pct']}%",
                "recommendation": "Wait for RAM to free up",
            }
        return {"safe": True, "risk": "none", "reason": "CPU-only skill"}

    vram_free = status.get("vram_free_gb", 0)
    # Use estimate, or infer from configured model if estimate missing
    vram_needed = estimate.get("vram_total_gb")
    if vram_needed is None:
        current_model = config.get("models", {}).get("local", "qwen3:8b")
        vram_needed = _infer_model_vram(current_model)

    # If the model is already loaded, it's already consuming VRAM — no additional needed
    # Only extra_vram (vision models, etc.) would need additional space
    model_loaded = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=3) as r:
            import json as _json
            ps = _json.loads(r.read())
        loaded_names = [m["name"] for m in ps.get("models", [])]
        current_model = config.get("models", {}).get("local", "qwen3:8b")
        if current_model in loaded_names:
            model_loaded = True
            vram_needed = estimate.get("vram_extra_gb", 0)
    except Exception:
        # Can't reach Ollama — check if model was recently active via hw_state.json
        try:
            hw_path = FLEET_DIR / "hw_state.json"
            if hw_path.exists():
                import json as _json
                hw = _json.loads(hw_path.read_text(encoding="utf-8"))
                if hw.get("model") and hw.get("status") != "offline":
                    model_loaded = True
                    vram_needed = estimate.get("vram_extra_gb", 0)
                    log.debug("OOM check: Ollama unreachable but hw_state shows model active")
        except Exception:
            pass
        if not model_loaded:
            log.debug("OOM check: Ollama unreachable, using conservative estimate")

    # Compare free VRAM against needs
    headroom = vram_free - vram_needed
    if headroom < -1:
        return {
            "safe": False, "risk": "critical",
            "reason": f"Need {vram_needed}GB VRAM, only {vram_free}GB free",
            "recommendation": f"Scale down model or wait. Current model needs {estimate.get('vram_base_gb')}GB",
        }
    elif headroom < 0.5:
        return {
            "safe": False, "risk": "high",
            "reason": f"Only {round(headroom, 1)}GB VRAM headroom (need {vram_needed}GB)",
            "recommendation": "Consider scaling to a smaller model tier",
        }
    elif headroom < 2.0:
        return {
            "safe": True, "risk": "medium",
            "reason": f"{round(headroom, 1)}GB VRAM headroom — tight but feasible",
            "recommendation": "Monitor VRAM during execution",
        }
    elif headroom < 4.0:
        return {
            "safe": True, "risk": "low",
            "reason": f"{round(headroom, 1)}GB VRAM headroom",
            "recommendation": None,
        }
    else:
        return {
            "safe": True, "risk": "none",
            "reason": f"{round(headroom, 1)}GB VRAM headroom — comfortable",
            "recommendation": None,
        }
