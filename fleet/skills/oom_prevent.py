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
import json
import os
from pathlib import Path

SKILL_NAME = "oom_prevent"
DESCRIPTION = "Estimate VRAM/RAM requirements and prevent out-of-memory crashes"
REQUIRES_NETWORK = False

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


def run(payload: dict, config: dict) -> str:
    """Check OOM risk for a skill or get current memory status."""
    action = payload.get("action", "status")

    if action == "status":
        return json.dumps(_get_memory_status())
    elif action == "check":
        skill = payload.get("skill", "")
        return json.dumps(check_oom_risk(skill, config))
    elif action == "estimate":
        skill = payload.get("skill", "")
        return json.dumps(_estimate_requirements(skill, config))
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


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


def _estimate_requirements(skill_name: str, config: dict) -> dict:
    """Estimate VRAM/RAM needed for a skill."""
    if skill_name in CPU_ONLY_SKILLS:
        return {
            "skill": skill_name,
            "vram_needed_gb": 0,
            "ram_needed_gb": 0.5,
            "cpu_only": True,
        }

    # Base: current model VRAM
    models = config.get("models", {})
    current_model = models.get("local", "qwen3:8b")
    base_vram = MODEL_VRAM.get(current_model, 7.0)

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
    vram_needed = estimate.get("vram_total_gb", 7.0)

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
