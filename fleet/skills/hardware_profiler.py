"""
Hardware Profiler — Identify deployment class and optimize fleet configuration.

Classifies hardware into deployment tiers, recommends model/worker/memory settings,
and helps Dr. Ders with adaptive configuration for any device.

Deployment Classes:
  mobile      — <8GB RAM, APU/iGPU or no GPU (handheld, tablet, phone)
  desktop_low — 8-16GB RAM, 2-6GB VRAM (basic laptop/desktop)
  desktop     — 16-32GB RAM, 8-12GB VRAM (standard workstation)
  desktop_pro — 32-64GB RAM, 16-24GB VRAM (pro workstation, multi-GPU)
  unified     — Apple Silicon or DGX Spark (shared RAM/VRAM pool)
  server      — 64GB+ RAM, 24GB+ VRAM or multi-GPU (datacenter/cloud)
  extreme     — 128GB+ RAM, multi-GPU, NVLink (HPC/training rigs)

Actions:
  detect      — full hardware detection + classification
  recommend   — optimal fleet.toml settings for this hardware
  apply       — write recommended settings to fleet.toml (with backup)

Usage:
    lead_client.py task '{"type": "hardware_profiler"}'
    lead_client.py task '{"type": "hardware_profiler", "payload": {"action": "recommend"}}'
"""
import json
import os
import platform
import sys
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "hardware_profiler"
DESCRIPTION = "Identify deployment class and optimize fleet configuration for any hardware."
REQUIRES_NETWORK = False


# ── Deployment class definitions ─────────────────────────────────────────────

PROFILES = {
    "mobile": {
        "ram_range": (0, 8),
        "vram_range": (0, 2),
        "description": "Mobile/handheld — ultra-light fleet",
        "max_workers": 2,
        "default_model": "qwen3:0.6b",
        "conductor_model": "",
        "keep_alive_mins": 5,
        "idle_enabled": False,
        "eco_mode": True,
    },
    "desktop_low": {
        "ram_range": (8, 16),
        "vram_range": (2, 6),
        "description": "Basic desktop/laptop — light fleet",
        "max_workers": 4,
        "default_model": "qwen3:1.7b",
        "conductor_model": "qwen3:0.6b",
        "keep_alive_mins": 15,
        "idle_enabled": True,
        "eco_mode": True,
    },
    "desktop": {
        "ram_range": (16, 32),
        "vram_range": (6, 12),
        "description": "Standard workstation",
        "max_workers": 6,
        "default_model": "qwen3:8b",
        "conductor_model": "qwen3:4b",
        "keep_alive_mins": 30,
        "idle_enabled": True,
        "eco_mode": False,
    },
    "desktop_pro": {
        "ram_range": (32, 64),
        "vram_range": (12, 24),
        "description": "Pro workstation / multi-GPU",
        "max_workers": 10,
        "default_model": "qwen3:8b",
        "conductor_model": "qwen3:4b",
        "keep_alive_mins": 30,
        "idle_enabled": True,
        "eco_mode": False,
    },
    "unified": {
        "ram_range": (16, 512),
        "vram_range": (0, 0),  # Unified — VRAM = RAM
        "description": "Unified memory (Apple Silicon / DGX Spark)",
        "max_workers": 8,
        "default_model": "qwen3:8b",
        "conductor_model": "qwen3:4b",
        "keep_alive_mins": 30,
        "idle_enabled": True,
        "eco_mode": False,
    },
    "server": {
        "ram_range": (64, 256),
        "vram_range": (24, 80),
        "description": "Server / cloud instance",
        "max_workers": 16,
        "default_model": "qwen3:8b",
        "conductor_model": "qwen3:4b",
        "keep_alive_mins": 60,
        "idle_enabled": True,
        "eco_mode": False,
    },
    "extreme": {
        "ram_range": (256, 99999),
        "vram_range": (80, 99999),
        "description": "HPC / training rig / multi-node",
        "max_workers": 16,
        "default_model": "qwen3:8b",
        "conductor_model": "qwen3:4b",
        "keep_alive_mins": 120,
        "idle_enabled": True,
        "eco_mode": False,
    },
}


def run(payload: dict, config: dict, log) -> dict:
    action = payload.get("action", "detect")

    if action == "detect":
        return _detect(config, log)
    elif action == "recommend":
        return _recommend(config, log)
    elif action == "apply":
        return _apply(config, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _get_hardware() -> dict:
    """Detect full hardware profile."""
    hw = {
        "platform": sys.platform,
        "arch": platform.machine(),
        "os": platform.system(),
        "os_version": platform.version(),
        "cpu_name": platform.processor() or "unknown",
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": 0,
        "vram_gb": 0,
        "vram_total_gb": 0,
        "gpu_name": "none",
        "gpu_count": 0,
        "memory_mode": "discrete",
        "gpus": [],
    }

    # RAM
    try:
        import psutil
        hw["ram_gb"] = round(psutil.virtual_memory().total / 1024**3, 1)
    except ImportError:
        pass

    # GPU (NVIDIA)
    try:
        import pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        hw["gpu_count"] = count
        total_vram = 0
        for i in range(count):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            name = pynvml.nvmlDeviceGetName(h)
            vram = round(mem.total / 1024**3, 1)
            total_vram += vram
            hw["gpus"].append({
                "index": i,
                "name": name if isinstance(name, str) else name.decode(),
                "vram_gb": vram,
            })
        hw["vram_total_gb"] = round(total_vram, 1)
        hw["vram_gb"] = round(total_vram, 1)
        if count > 0:
            hw["gpu_name"] = hw["gpus"][0]["name"]
        pynvml.nvmlShutdown()
    except Exception:
        pass

    # Apple Silicon unified memory
    if sys.platform == "darwin":
        hw["memory_mode"] = "unified"
        hw["vram_gb"] = hw["ram_gb"]  # Unified — VRAM = RAM
        hw["vram_total_gb"] = hw["ram_gb"]
        try:
            import subprocess
            r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                hw["cpu_name"] = r.stdout.strip()
                if "Apple" in hw["cpu_name"]:
                    hw["gpu_name"] = hw["cpu_name"] + " (integrated)"
                    hw["gpu_count"] = 1
        except Exception:
            pass

    # Check for DGX Spark / unified NVIDIA memory
    if hw["gpu_count"] > 0 and hw["ram_gb"] > 100 and hw["vram_total_gb"] > 100:
        # Likely DGX or unified memory system
        hw["memory_mode"] = "unified"

    return hw


def _classify(hw: dict) -> str:
    """Classify hardware into deployment tier."""
    ram = hw.get("ram_gb", 0)
    vram = hw.get("vram_total_gb", 0)
    memory_mode = hw.get("memory_mode", "discrete")
    gpu_count = hw.get("gpu_count", 0)

    # Unified memory systems
    if memory_mode == "unified":
        return "unified"

    # Extreme: massive multi-GPU
    if ram >= 256 or vram >= 80:
        return "extreme"

    # Server
    if ram >= 64 or vram >= 24:
        return "server"

    # Desktop Pro: multi-GPU or high VRAM
    if gpu_count > 1 or vram >= 16 or ram >= 32:
        return "desktop_pro"

    # Desktop standard
    if ram >= 16 and vram >= 6:
        return "desktop"

    # Desktop low
    if ram >= 8:
        return "desktop_low"

    # Mobile
    return "mobile"


def _detect(config, log) -> dict:
    """Full hardware detection + classification."""
    hw = _get_hardware()
    tier = _classify(hw)
    profile = PROFILES.get(tier, PROFILES["desktop"])

    log.info(f"Hardware: {tier} — {hw['ram_gb']}GB RAM, {hw['vram_total_gb']}GB VRAM, "
             f"{hw['gpu_count']} GPU(s), {hw['memory_mode']} memory")

    return {
        "hardware": hw,
        "classification": tier,
        "profile": profile,
        "description": profile["description"],
    }


def _recommend(config, log) -> dict:
    """Recommend optimal fleet.toml settings for detected hardware."""
    detection = _detect(config, log)
    hw = detection["hardware"]
    tier = detection["classification"]
    profile = detection["profile"]

    recommendations = {
        "classification": tier,
        "description": profile["description"],
        "settings": {
            "[fleet]": {
                "max_workers": profile["max_workers"],
                "eco_mode": profile["eco_mode"],
                "idle_enabled": profile["idle_enabled"],
            },
            "[models]": {
                "local": profile["default_model"],
                "conductor_model": profile["conductor_model"],
                "keep_alive_mins": profile["keep_alive_mins"],
            },
        },
        "notes": [],
    }

    # Tier-specific notes
    if tier == "mobile":
        recommendations["notes"].append(
            "Mobile mode: minimal fleet, smallest model, short keep-alive to save battery")
        recommendations["notes"].append(
            "Consider disabling idle evolution to reduce CPU/power usage")
    elif tier == "unified":
        recommendations["notes"].append(
            f"Unified memory: {hw['ram_gb']}GB shared between CPU and GPU")
        recommendations["notes"].append(
            "Models can be larger than discrete VRAM would allow")
        if hw["ram_gb"] >= 64:
            recommendations["settings"]["[models]"]["local"] = "qwen3:8b"
            recommendations["notes"].append("64GB+ unified: can run larger models comfortably")
    elif tier in ("server", "extreme"):
        recommendations["notes"].append(
            f"Server-class: {hw['gpu_count']} GPU(s), {hw['vram_total_gb']}GB total VRAM")
        if hw["gpu_count"] > 1:
            recommendations["notes"].append(
                "Multi-GPU: enable model parallelism in fleet.toml [gpu]")
            recommendations["settings"]["[gpu]"] = {
                "multi_gpu": True,
                "gpu_count": hw["gpu_count"],
                "vram_aggregation": True,
            }
    elif tier == "desktop_pro" and hw["gpu_count"] > 1:
        recommendations["notes"].append(
            f"Multi-GPU desktop: {hw['gpu_count']} GPUs detected")
        recommendations["settings"]["[gpu]"] = {
            "multi_gpu": True,
            "gpu_count": hw["gpu_count"],
        }

    return recommendations


def _apply(config, log) -> dict:
    """Apply recommended settings to fleet.toml (creates backup first)."""
    recs = _recommend(config, log)

    # Backup current fleet.toml
    toml_path = FLEET_DIR / "fleet.toml"
    if toml_path.exists():
        import shutil
        backup = toml_path.with_suffix(".toml.bak")
        shutil.copy2(toml_path, backup)
        log.info(f"Backed up fleet.toml to {backup.name}")

    # Apply settings via regex replacement
    import re
    text = toml_path.read_text(encoding="utf-8")

    for section, values in recs["settings"].items():
        for key, value in values.items():
            pattern = rf'^(\s*{re.escape(key)}\s*=\s*).*$'
            if isinstance(value, bool):
                replacement = rf'\g<1>{"true" if value else "false"}'
            elif isinstance(value, int):
                replacement = rf'\g<1>{value}'
            elif isinstance(value, str):
                replacement = rf'\g<1>"{value}"'
            else:
                continue
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

    toml_path.write_text(text, encoding="utf-8")
    log.info(f"Applied {recs['classification']} profile to fleet.toml")

    return {
        "applied": True,
        "classification": recs["classification"],
        "backup": "fleet.toml.bak",
        "notes": recs["notes"],
    }
