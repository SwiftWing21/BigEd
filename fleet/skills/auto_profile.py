"""
Auto Profile — Generate optimal fleet + autoresearch profiles for ANY hardware.

Detects hardware, calculates VRAM/RAM budgets, and generates profiles
that maximize both inference speed AND training quality simultaneously.

Works for: mobile (<8GB), desktop (8-64GB), unified (Mac/DGX), server (64GB+).

Actions:
  detect       — detect hardware + show all viable configurations
  generate     — write autoresearch/profiles.toml with optimal profiles for this system
  recommend    — show recommendations without writing

Usage:
    lead_client.py task '{"type": "auto_profile"}'
    lead_client.py task '{"type": "auto_profile", "payload": {"action": "generate"}}'
"""
import json
import os
import sys
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
AUTORESEARCH_DIR = FLEET_DIR.parent / "autoresearch"
SKILL_NAME = "auto_profile"
DESCRIPTION = "Generate optimal fleet + training profiles for any hardware."
REQUIRES_NETWORK = False

# Model VRAM sizes (approximate, GB)
MODEL_SIZES = {
    "qwen3:0.6b": 0.5,
    "qwen3:1.7b": 1.4,
    "qwen3:4b": 2.5,
    "qwen3:8b": 6.9,
}

# Training architecture presets by VRAM budget
TRAINING_PRESETS = {
    "none":   {"vram": 0,   "depth": 0, "dim": 0,   "params": "0",    "desc": "No training"},
    "tiny":   {"vram": 1.0, "depth": 3, "dim": 128,  "params": "~1M",  "desc": "Minimal validation"},
    "micro":  {"vram": 1.8, "depth": 3, "dim": 256,  "params": "~3M",  "desc": "Quick iteration"},
    "small":  {"vram": 4.0, "depth": 4, "dim": 384,  "params": "~10M", "desc": "Decent quality"},
    "stable": {"vram": 8.0, "depth": 6, "dim": 384,  "params": "~26M", "desc": "Full quality"},
    "large":  {"vram": 11.0,"depth": 6, "dim": 512,  "params": "~46M", "desc": "Max single-GPU"},
    "xlarge": {"vram": 20.0,"depth": 8, "dim": 768,  "params": "~150M","desc": "Multi-GPU/unified"},
}

# Worker scaling by RAM
WORKER_TIERS = [
    (8,   2, 256),   # <8GB: 2 workers, 256MB limit
    (16,  4, 384),   # 8-16GB: 4 workers
    (32,  6, 512),   # 16-32GB: 6 workers
    (64,  10, 512),  # 32-64GB: 10 workers
    (128, 13, 512),  # 64-128GB: 13 workers
    (9999,16, 768),  # 128GB+: 16 workers
]


def run(payload: dict, config: dict, log) -> dict:
    action = payload.get("action", "detect")
    if action == "detect":
        return _detect(config, log)
    elif action == "generate":
        return _generate(config, log)
    elif action == "recommend":
        return _recommend(config, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _get_hw() -> dict:
    """Detect hardware."""
    try:
        sys.path.insert(0, str(FLEET_DIR))
        from skills.hardware_profiler import _get_hardware
        return _get_hardware()
    except ImportError:
        return {"ram_gb": 8, "vram_total_gb": 0, "gpu_count": 0,
                "memory_mode": "discrete", "gpu_name": "unknown"}


def _calculate_configs(hw: dict) -> list:
    """Calculate all viable model + training configurations for this hardware.

    Returns list of configs sorted by combined score (inference + training quality).
    """
    vram = hw.get("vram_total_gb", 0)
    ram = hw.get("ram_gb", 8)
    memory_mode = hw.get("memory_mode", "discrete")
    gpu_count = hw.get("gpu_count", 0)

    # For unified memory, VRAM = RAM (shared pool)
    if memory_mode == "unified":
        vram = ram * 0.75  # Reserve 25% for OS + apps

    # Multi-GPU: aggregate VRAM
    if gpu_count > 1:
        # Model parallelism available — can use more VRAM
        pass  # vram already reflects total across GPUs

    overhead = 1.5  # CUDA context + safety margin
    available = max(0, vram - overhead)

    configs = []

    for model_name, model_vram in MODEL_SIZES.items():
        if model_vram > available:
            continue  # Model doesn't fit

        remaining = available - model_vram
        # Find best training preset that fits remaining VRAM
        best_training = "none"
        for preset_name, preset in sorted(TRAINING_PRESETS.items(),
                                          key=lambda x: x[1]["vram"], reverse=True):
            if preset["vram"] <= remaining:
                best_training = preset_name
                break

        training = TRAINING_PRESETS[best_training]

        # Score: balance inference quality + training quality
        # Inference: larger model = better (0.6b=1, 1.7b=3, 4b=5, 8b=8)
        inference_scores = {"qwen3:0.6b": 1, "qwen3:1.7b": 3, "qwen3:4b": 5, "qwen3:8b": 8}
        # Training: larger = better
        training_scores = {"none": 0, "tiny": 1, "micro": 2, "small": 4, "stable": 7, "large": 9, "xlarge": 10}

        inf_score = inference_scores.get(model_name, 0)
        train_score = training_scores.get(best_training, 0)
        combined = inf_score + train_score
        total_vram = model_vram + training["vram"]

        configs.append({
            "model": model_name,
            "model_vram_gb": model_vram,
            "training_preset": best_training,
            "training_vram_gb": training["vram"],
            "training_params": training["params"],
            "training_desc": training["desc"],
            "total_vram_gb": round(total_vram, 1),
            "headroom_gb": round(available - total_vram, 1),
            "inference_score": inf_score,
            "training_score": train_score,
            "combined_score": combined,
            "ollama_mode": "gpu",
            "gradient_checkpointing": total_vram > available * 0.85,
        })

    # Also add training-only configs (Ollama on CPU)
    for preset_name, preset in TRAINING_PRESETS.items():
        if preset["vram"] > 0 and preset["vram"] <= available:
            configs.append({
                "model": "any (CPU)",
                "model_vram_gb": 0,
                "training_preset": preset_name,
                "training_vram_gb": preset["vram"],
                "training_params": preset["params"],
                "training_desc": preset["desc"],
                "total_vram_gb": preset["vram"],
                "headroom_gb": round(available - preset["vram"], 1),
                "inference_score": 1,  # CPU inference is slow but works
                "training_score": {"none": 0, "tiny": 1, "micro": 2, "small": 4,
                                   "stable": 7, "large": 9, "xlarge": 10}.get(preset_name, 0),
                "combined_score": 1 + {"none": 0, "tiny": 1, "micro": 2, "small": 4,
                                       "stable": 7, "large": 9, "xlarge": 10}.get(preset_name, 0),
                "ollama_mode": "cpu",
                "gradient_checkpointing": False,
            })

    # Sort by combined score (highest first)
    configs.sort(key=lambda c: (-c["combined_score"], c["total_vram_gb"]))

    # Worker count based on RAM
    workers = 2
    mem_limit = 256
    for threshold, w, ml in WORKER_TIERS:
        if ram < threshold:
            workers = w
            mem_limit = ml
            break

    return configs, workers, mem_limit


def _detect(config, log) -> dict:
    """Detect hardware and show all viable configurations."""
    hw = _get_hw()
    configs, workers, mem_limit = _calculate_configs(hw)

    log.info(f"Hardware: {hw.get('ram_gb', 0)}GB RAM, {hw.get('vram_total_gb', 0)}GB VRAM, "
             f"{hw.get('gpu_count', 0)} GPU(s), {hw.get('memory_mode', 'discrete')}")
    log.info(f"Found {len(configs)} viable configurations")

    # Top 3 recommendations
    top = configs[:3] if configs else []
    for i, c in enumerate(top):
        log.info(f"  #{i+1}: {c['model']} + {c['training_preset']} training "
                 f"({c['total_vram_gb']}GB, score={c['combined_score']})")

    return {
        "hardware": hw,
        "configurations": configs,
        "top_3": top,
        "workers": workers,
        "memory_limit_mb": mem_limit,
    }


def _recommend(config, log) -> dict:
    """Show human-readable recommendations."""
    result = _detect(config, log)
    hw = result["hardware"]
    top = result["top_3"]

    recs = []
    labels = ["BEST (balanced)", "ALTERNATIVE (inference focus)", "ALTERNATIVE (training focus)"]

    for i, cfg in enumerate(top):
        label = labels[i] if i < len(labels) else f"Option {i+1}"
        recs.append({
            "label": label,
            "inference": f"{cfg['model']} on GPU (~{'120' if '8b' in cfg['model'] else '80' if '4b' in cfg['model'] else '40'} tok/s)",
            "training": f"{cfg['training_preset']} ({cfg['training_params']} params)",
            "vram": f"{cfg['total_vram_gb']}/{hw.get('vram_total_gb', 0)}GB",
            "ollama_mode": cfg["ollama_mode"],
        })

    return {
        "hardware_summary": f"{hw.get('gpu_name', '?')} | {hw.get('ram_gb', 0)}GB RAM | {hw.get('vram_total_gb', 0)}GB VRAM",
        "recommendations": recs,
        "workers": result["workers"],
    }


def _generate(config, log) -> dict:
    """Write optimized profiles.toml for this hardware."""
    result = _detect(config, log)
    hw = result["hardware"]
    configs = result["configurations"]

    if not configs:
        return {"error": "No viable configurations for this hardware"}

    profiles_path = AUTORESEARCH_DIR / "profiles.toml"
    if not profiles_path.parent.exists():
        return {"error": f"Autoresearch directory not found: {AUTORESEARCH_DIR}"}

    # Generate profiles.toml
    vram = hw.get("vram_total_gb", 0)
    ram = hw.get("ram_gb", 0)
    gpu = hw.get("gpu_name", "unknown")

    lines = [
        f"# Auto-generated by BigEd CC hardware_profiler",
        f"# Hardware: {gpu}, {ram}GB RAM, {vram}GB VRAM",
        f"# Generated for {hw.get('gpu_count', 1)} GPU(s), {hw.get('memory_mode', 'discrete')} memory",
        f"#",
        f'active = "hybrid"',
        "",
    ]

    # Generate each profile from calculated configs
    gpu_configs = [c for c in configs if c["ollama_mode"] == "gpu" and c["training_preset"] != "none"]
    cpu_configs = [c for c in configs if c["ollama_mode"] == "cpu" and c["training_preset"] != "none"]

    # Profile 1: micro (both on GPU, smallest training)
    micro = next((c for c in gpu_configs if c["training_preset"] in ("tiny", "micro")), None)
    if micro:
        preset = TRAINING_PRESETS[micro["training_preset"]]
        lines.extend([
            f'[profiles.micro]',
            f'description = "Quick iteration -- {micro["model"]} + {micro["training_preset"]} training"',
            f'ollama_mode = "gpu"',
            f'ollama_model = "{micro["model"]}"',
            f'DEPTH = {preset["depth"]}',
            f'model_dim = {preset["dim"]}',
            f'HEAD_DIM = 64',
            f'ASPECT_RATIO = 64',
            f'DEVICE_BATCH_SIZE = 8',
            f'TOTAL_BATCH_SIZE = 65536',
            f'gradient_checkpointing = false',
            f'vram_target_gb = {preset["vram"]}',
            "",
        ])

    # Profile 2: hybrid (both on GPU, best balanced)
    hybrid = next((c for c in gpu_configs if c["combined_score"] == max(g["combined_score"] for g in gpu_configs)), None) if gpu_configs else None
    if hybrid:
        preset = TRAINING_PRESETS[hybrid["training_preset"]]
        lines.extend([
            f'[profiles.hybrid]',
            f'description = "Balanced -- {hybrid["model"]} + {hybrid["training_preset"]} training"',
            f'ollama_mode = "gpu"',
            f'ollama_model = "{hybrid["model"]}"',
            f'DEPTH = {preset["depth"]}',
            f'model_dim = {preset["dim"]}',
            f'HEAD_DIM = 128',
            f'ASPECT_RATIO = 64',
            f'DEVICE_BATCH_SIZE = 16',
            f'TOTAL_BATCH_SIZE = 65536',
            f'gradient_checkpointing = {str(hybrid["gradient_checkpointing"]).lower()}',
            f'vram_target_gb = {preset["vram"]}',
            "",
        ])

    # Profile 3: stable (Ollama on CPU, max training quality)
    stable = next((c for c in cpu_configs if c["training_score"] == max(g["training_score"] for g in cpu_configs)), None) if cpu_configs else None
    if stable:
        preset = TRAINING_PRESETS[stable["training_preset"]]
        lines.extend([
            f'[profiles.stable]',
            f'description = "Max training -- Ollama on CPU, {stable["training_preset"]} training"',
            f'ollama_mode = "cpu"',
            f'DEPTH = {preset["depth"]}',
            f'model_dim = {preset["dim"]}',
            f'HEAD_DIM = 128',
            f'ASPECT_RATIO = 64',
            f'DEVICE_BATCH_SIZE = 32',
            f'TOTAL_BATCH_SIZE = 65536',
            f'gradient_checkpointing = false',
            f'vram_target_gb = {preset["vram"]}',
            "",
        ])

    # Don't overwrite — write to a .generated file for review
    out_path = profiles_path.with_suffix(".generated.toml")
    out_path.write_text("\n".join(lines), encoding="utf-8")

    log.info(f"Generated profiles written to {out_path.name} (review before replacing profiles.toml)")

    return {
        "generated": str(out_path),
        "profiles": [p for p in ["micro", "hybrid", "stable"] if any(f"[profiles.{p}]" in l for l in lines)],
        "hardware": f"{gpu} | {ram}GB RAM | {vram}GB VRAM",
        "note": "Review .generated.toml before replacing profiles.toml",
    }
