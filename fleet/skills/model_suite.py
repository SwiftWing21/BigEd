"""
Model Suite — Consolidated model management, hardware profiling, and auto-configuration.

Merges model_manager, hardware_profiler, and auto_profile into a single skill with
one canonical hardware detection implementation shared by all subactions.

Deployment Classes (hardware_profiler):
  mobile      — <8GB RAM, APU/iGPU or no GPU
  desktop_low — 8-16GB RAM, 2-6GB VRAM
  desktop     — 16-32GB RAM, 8-12GB VRAM
  desktop_pro — 32-64GB RAM, 16-24GB VRAM
  unified     — Apple Silicon or DGX Spark (shared RAM/VRAM)
  server      — 64GB+ RAM, 24GB+ VRAM or multi-GPU
  extreme     — 128GB+ RAM, multi-GPU, NVLink

Actions:
  check          — compare installed vs needed models (model_manager)
  install        — pull all missing models (model_manager)
  install_one    — pull a single model (model_manager)
  profiles       — list model_profiles.toml entries (model_manager)
  apply_profile  — apply a named profile to fleet.toml (model_manager)
  update_check   — check for model updates and alternatives (model_manager)
  debug          — diagnose/evict idle models (model_manager)
  detect         — full hardware detection + deployment classification (hardware_profiler)
  recommend      — optimal fleet.toml settings for this hardware (hardware_profiler)
  apply          — write recommended settings to fleet.toml (hardware_profiler)
  auto_detect    — detect hardware + all viable inference+training configs (auto_profile)
  auto_generate  — write autoresearch/profiles.generated.toml (auto_profile)
  auto_recommend — human-readable inference+training recommendations (auto_profile)
"""
import json
import logging
import os
import platform
import sys
import urllib.request
from pathlib import Path

SKILL_NAME = "model_suite"
DESCRIPTION = "Model management: check, install, profiles, hardware detection, auto-configuration"
VERSION = "1.0.0"
REQUIRES_NETWORK = True
COMPLEXITY = "medium"
SUITE = "model"

log = logging.getLogger(SKILL_NAME)

FLEET_DIR = Path(__file__).parent.parent
AUTORESEARCH_DIR = FLEET_DIR.parent / "autoresearch"


# ── Deployment class definitions (hardware_profiler) ─────────────────────────

DEPLOYMENT_PROFILES = {
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
        "vram_range": (0, 0),
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

# ── Auto-profile constants ────────────────────────────────────────────────────

# Model VRAM sizes (approximate, GB)
MODEL_SIZES = {
    "qwen3:0.6b": 0.5,
    "qwen3:1.7b": 1.4,
    "qwen3:4b": 2.5,
    "qwen3:8b": 6.9,
}

# Training architecture presets by VRAM budget
TRAINING_PRESETS = {
    "none":   {"vram": 0,    "depth": 0, "dim": 0,   "params": "0",    "desc": "No training"},
    "tiny":   {"vram": 1.0,  "depth": 3, "dim": 128,  "params": "~1M",  "desc": "Minimal validation"},
    "micro":  {"vram": 1.8,  "depth": 3, "dim": 256,  "params": "~3M",  "desc": "Quick iteration"},
    "small":  {"vram": 4.0,  "depth": 4, "dim": 384,  "params": "~10M", "desc": "Decent quality"},
    "stable": {"vram": 8.0,  "depth": 6, "dim": 384,  "params": "~26M", "desc": "Full quality"},
    "large":  {"vram": 11.0, "depth": 6, "dim": 512,  "params": "~46M", "desc": "Max single-GPU"},
    "xlarge": {"vram": 20.0, "depth": 8, "dim": 768,  "params": "~150M","desc": "Multi-GPU/unified"},
}

# Worker scaling by RAM
WORKER_TIERS = [
    (8,    2, 256),
    (16,   4, 384),
    (32,   6, 512),
    (64,  10, 512),
    (128, 13, 512),
    (9999, 16, 768),
]


# ── Entry point ───────────────────────────────────────────────────────────────

def run(payload: dict, config: dict) -> dict:
    from skills._dispatch import dispatch_action
    return dispatch_action(payload, config, {
        "check":          _check,
        "install":        _install,
        "install_one":    _install_one,
        "profiles":       _profiles,
        "apply_profile":  _apply_profile,
        "update_check":   _update_check,
        "debug":          _debug,
        "detect":         _detect,
        "recommend":      _recommend,
        "apply":          _apply,
        "auto_detect":    _auto_detect,
        "auto_generate":  _auto_generate,
        "auto_recommend": _auto_recommend,
    }, default="check")


# ── Canonical hardware detection (ONE implementation) ─────────────────────────

def _detect_hardware() -> dict:
    """Detect full hardware profile.

    Canonical hardware detection shared by all subactions. Returns:
        platform, arch, os, os_version, cpu_name, cpu_cores,
        ram_gb, vram_gb, vram_total_gb, gpu_name, gpu_count,
        memory_mode ('discrete' | 'unified'), gpus[]
    """
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
        hw["ram_gb"] = round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except ImportError:
        pass

    # GPU (NVIDIA via pynvml)
    try:
        import pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        hw["gpu_count"] = count
        total_vram = 0.0
        for i in range(count):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            name = pynvml.nvmlDeviceGetName(h)
            vram = round(mem.total / 1024 ** 3, 1)
            total_vram += vram
            hw["gpus"].append({
                "index": i,
                "name": name if isinstance(name, str) else name.decode(),
                "vram_gb": vram,
                "vram_free_gb": round(mem.free / 1024 ** 3, 1),
            })
        hw["vram_total_gb"] = round(total_vram, 1)
        hw["vram_gb"] = round(total_vram, 1)
        if count > 0:
            hw["gpu_name"] = hw["gpus"][0]["name"]
        pynvml.nvmlShutdown()
    except Exception:
        pass

    # Apple Silicon — unified memory
    if sys.platform == "darwin":
        hw["memory_mode"] = "unified"
        hw["vram_gb"] = hw["ram_gb"]
        hw["vram_total_gb"] = hw["ram_gb"]
        try:
            import subprocess
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0:
                hw["cpu_name"] = r.stdout.strip()
                if "Apple" in hw["cpu_name"]:
                    hw["gpu_name"] = hw["cpu_name"] + " (integrated)"
                    hw["gpu_count"] = 1
        except Exception:
            pass

    # DGX Spark / unified NVIDIA memory
    if hw["gpu_count"] > 0 and hw["ram_gb"] > 100 and hw["vram_total_gb"] > 100:
        hw["memory_mode"] = "unified"

    return hw


def _classify_hardware(hw: dict) -> str:
    """Classify hardware dict into a deployment tier name."""
    ram = hw.get("ram_gb", 0)
    vram = hw.get("vram_total_gb", 0)
    memory_mode = hw.get("memory_mode", "discrete")
    gpu_count = hw.get("gpu_count", 0)

    if memory_mode == "unified":
        return "unified"
    if ram >= 256 or vram >= 80:
        return "extreme"
    if ram >= 64 or vram >= 24:
        return "server"
    if gpu_count > 1 or vram >= 16 or ram >= 32:
        return "desktop_pro"
    if ram >= 16 and vram >= 6:
        return "desktop"
    if ram >= 8:
        return "desktop_low"
    return "mobile"


# ── model_manager subactions ──────────────────────────────────────────────────

def _ollama_host(config: dict) -> str:
    return config.get("models", {}).get("ollama_host", "http://localhost:11434")


def _get_installed(host: str) -> list:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _get_loaded(host: str) -> list:
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as r:
            data = json.loads(r.read())
        return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1)}
                for m in data.get("models", [])]
    except Exception:
        return []


def _get_needed(config: dict) -> list:
    models = config.get("models", {})
    tiers = models.get("tiers", {})
    needed = set()
    needed.add(models.get("local", "qwen3:8b"))
    if models.get("conductor_model"):
        needed.add(models["conductor_model"])
    if models.get("vision_model"):
        needed.add(models["vision_model"])
    for tier_model in tiers.values():
        needed.add(tier_model)
    needed.discard("")
    return sorted(needed)


def _pull_model(model_name: str, host: str) -> dict:
    if not model_name:
        return {"status": "error", "error": "model name required"}
    try:
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(
            f"{host}/api/pull", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            lines = r.read().decode("utf-8", errors="replace").strip().split("\n")
        for line in reversed(lines):
            try:
                status = json.loads(line)
                if status.get("status") == "success":
                    return {"status": "installed", "model": model_name}
                if "error" in status:
                    return {"status": "error", "model": model_name, "error": status["error"]}
            except json.JSONDecodeError:
                continue
        return {"status": "completed", "model": model_name}
    except Exception as e:
        return {"status": "error", "model": model_name, "error": str(e)}


def _check(payload: dict, config: dict) -> dict:
    host = _ollama_host(config)
    installed = _get_installed(host)
    loaded = _get_loaded(host)
    needed = _get_needed(config)
    missing = [m for m in needed if m not in installed]
    extra = [m for m in installed if m not in needed]
    return {
        "status": "ok",
        "installed": installed,
        "loaded": loaded,
        "needed": needed,
        "missing": missing,
        "extra": extra,
        "ready": len(missing) == 0,
        "summary": f"{len(installed)} installed, {len(missing)} missing, {len(loaded)} loaded",
    }


def _install(payload: dict, config: dict) -> dict:
    host = _ollama_host(config)
    needed = _get_needed(config)
    installed = _get_installed(host)
    missing = [m for m in needed if m not in installed]
    if not missing:
        return {"status": "all_installed", "count": len(installed)}
    results = [_pull_model(m, host) for m in missing]
    success = sum(1 for r in results if r.get("status") in ("installed", "completed"))
    return {
        "status": "done",
        "pulled": success,
        "failed": len(results) - success,
        "details": results,
    }


def _install_one(payload: dict, config: dict) -> dict:
    host = _ollama_host(config)
    return _pull_model(payload.get("model", ""), host)


def _profiles(payload: dict, config: dict) -> dict:
    profiles_path = FLEET_DIR / "model_profiles.toml"
    if not profiles_path.exists():
        return {"status": "error", "error": "model_profiles.toml not found"}
    try:
        import tomllib
        with open(profiles_path, "rb") as f:
            data = tomllib.load(f)
        profiles = {}
        for key, val in data.items():
            if isinstance(val, dict) and "local" in val:
                profiles[key] = {
                    "local": val.get("local"),
                    "conductor": val.get("conductor_model"),
                    "description": val.get("description", ""),
                }
        return {"status": "ok", "profiles": profiles}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _apply_profile(payload: dict, config: dict) -> dict:
    profile_name = payload.get("profile", "")
    profiles_path = FLEET_DIR / "model_profiles.toml"
    fleet_toml = FLEET_DIR / "fleet.toml"
    if not profiles_path.exists():
        return {"status": "error", "error": "model_profiles.toml not found"}
    try:
        import tomllib
        import tomlkit
        import tempfile
        with open(profiles_path, "rb") as f:
            profiles = tomllib.load(f)
        if profile_name not in profiles:
            return {"status": "error", "error": f"Profile '{profile_name}' not found",
                    "available": list(profiles.keys())}
        profile = profiles[profile_name]
        doc = tomlkit.parse(fleet_toml.read_text(encoding="utf-8"))
        doc.setdefault("models", {})
        if "local" in profile:
            doc["models"]["local"] = profile["local"]
        if "conductor_model" in profile:
            doc["models"]["conductor_model"] = profile["conductor_model"]
        if "tiers" in profile:
            doc["models"].setdefault("tiers", {})
            for k, v in profile["tiers"].items():
                doc["models"]["tiers"][k] = v
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(FLEET_DIR), suffix=".toml")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp_path, str(fleet_toml))
        return {"status": "applied", "profile": profile_name, "config": profile}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _update_check(payload: dict, config: dict) -> dict:
    host = _ollama_host(config)
    installed = _get_installed(host)
    needed = _get_needed(config)
    updates = []
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        local_models = {m["name"]: m for m in data.get("models", [])}
    except Exception:
        return {"status": "error", "error": "Cannot reach Ollama", "updates": []}

    for model_name in needed:
        if model_name not in local_models:
            updates.append({"model": model_name, "status": "not_installed", "action": "install"})
            continue
        local_info = local_models[model_name]
        local_digest = local_info.get("digest", "")[:12]
        local_size_gb = round(local_info.get("size", 0) / 1e9, 1)
        modified = local_info.get("modified_at", "")[:10]
        try:
            body = json.dumps({"name": model_name}).encode()
            req = urllib.request.Request(
                f"{host}/api/show", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                show_data = json.loads(r.read())
            updates.append({
                "model": model_name,
                "status": "installed",
                "digest": local_digest,
                "size_gb": local_size_gb,
                "installed_date": modified,
                "family": show_data.get("details", {}).get("family", ""),
                "parameter_size": show_data.get("details", {}).get("parameter_size", ""),
                "quantization": show_data.get("details", {}).get("quantization_level", ""),
            })
        except Exception:
            updates.append({"model": model_name, "status": "installed",
                            "digest": local_digest, "size_gb": local_size_gb})

    tiers = config.get("models", {}).get("tiers", {})
    tier_alternatives = {
        "default":  ["qwen3:8b", "llama3.1:8b", "gemma2:9b", "mistral:7b"],
        "mid":      ["qwen3:4b", "phi3:mini", "gemma2:2b"],
        "low":      ["qwen3:1.7b", "phi3:mini"],
        "critical": ["qwen3:0.6b", "tinyllama:1.1b"],
    }
    suggestions = []
    for tier_key, alternatives in tier_alternatives.items():
        current = tiers.get(tier_key, "")
        for alt in alternatives:
            base = alt.split(":")[0]
            if alt != current and not any(base in m for m in installed):
                suggestions.append({
                    "tier": tier_key,
                    "current": current,
                    "alternative": alt,
                    "reason": f"Alternative for {tier_key} tier — may offer different intelligence/speed tradeoffs",
                })

    hitl_recommendation = None
    if suggestions:
        hitl_recommendation = {
            "type": "model_update_review",
            "title": f"{len(suggestions)} alternative models available for evaluation",
            "detail": "Swarm recommends reviewing these model alternatives. "
                      "Pull and benchmark to compare intelligence vs performance.",
            "suggestions": suggestions[:5],
        }

    return {
        "status": "ok",
        "models": updates,
        "suggestions": suggestions,
        "hitl_recommendation": hitl_recommendation,
        "summary": f"{len(updates)} models checked, {len(suggestions)} alternatives found",
    }


def _debug(payload: dict, config: dict) -> dict:
    host = _ollama_host(config)
    target = payload.get("target")
    clean = payload.get("clean", False)
    sys.path.insert(0, str(FLEET_DIR))
    try:
        import debug_models
    finally:
        sys.path.pop(0)
    try:
        report = debug_models.clean_idle(host, target) if clean else debug_models.diagnose(host, target)
        if isinstance(report, dict) and "status" not in report:
            report["status"] = "ok"
        return report
    except RuntimeError as e:
        return {"status": "error", "error": str(e)}


# ── hardware_profiler subactions ──────────────────────────────────────────────

def _detect(payload: dict, config: dict) -> dict:
    """Full hardware detection + deployment classification."""
    hw = _detect_hardware()
    tier = _classify_hardware(hw)
    profile = DEPLOYMENT_PROFILES.get(tier, DEPLOYMENT_PROFILES["desktop"])
    log.info(
        "Hardware: %s — %.1fGB RAM, %.1fGB VRAM, %d GPU(s), %s memory",
        tier, hw["ram_gb"], hw["vram_total_gb"], hw["gpu_count"], hw["memory_mode"],
    )
    return {
        "status": "ok",
        "hardware": hw,
        "classification": tier,
        "profile": profile,
        "description": profile["description"],
    }


def _recommend(payload: dict, config: dict) -> dict:
    """Recommend optimal fleet.toml settings for detected hardware."""
    detection = _detect(payload, config)
    hw = detection["hardware"]
    tier = detection["classification"]
    profile = detection["profile"]

    recommendations = {
        "status": "ok",
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

    if tier == "mobile":
        recommendations["notes"].append(
            "Mobile mode: minimal fleet, smallest model, short keep-alive to save battery")
        recommendations["notes"].append(
            "Consider disabling idle evolution to reduce CPU/power usage")
    elif tier == "unified":
        recommendations["notes"].append(
            f"Unified memory: {hw['ram_gb']}GB shared between CPU and GPU")
        recommendations["notes"].append("Models can be larger than discrete VRAM would allow")
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


def _apply(payload: dict, config: dict) -> dict:
    """Apply recommended settings to fleet.toml (creates backup first)."""
    import re
    import shutil
    recs = _recommend(payload, config)

    toml_path = FLEET_DIR / "fleet.toml"
    if toml_path.exists():
        backup = toml_path.with_suffix(".toml.bak")
        shutil.copy2(toml_path, backup)
        log.info("Backed up fleet.toml to %s", backup.name)

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
    log.info("Applied %s profile to fleet.toml", recs["classification"])

    return {
        "status": "ok",
        "applied": True,
        "classification": recs["classification"],
        "backup": "fleet.toml.bak",
        "notes": recs["notes"],
    }


# ── auto_profile subactions ───────────────────────────────────────────────────

def _calculate_configs(hw: dict) -> tuple:
    """Calculate all viable model + training configurations for this hardware.

    Returns (configs_list, worker_count, memory_limit_mb).
    """
    vram = hw.get("vram_total_gb", 0)
    ram = hw.get("ram_gb", 8)
    memory_mode = hw.get("memory_mode", "discrete")
    gpu_count = hw.get("gpu_count", 0)

    if memory_mode == "unified":
        vram = ram * 0.75  # Reserve 25% for OS + apps

    overhead = 1.5  # CUDA context + safety margin
    available = max(0.0, vram - overhead)

    configs = []
    inference_scores = {"qwen3:0.6b": 1, "qwen3:1.7b": 3, "qwen3:4b": 5, "qwen3:8b": 8}
    training_scores = {"none": 0, "tiny": 1, "micro": 2, "small": 4,
                       "stable": 7, "large": 9, "xlarge": 10}

    for model_name, model_vram in MODEL_SIZES.items():
        if model_vram > available:
            continue
        remaining = available - model_vram
        best_training = "none"
        for preset_name, preset in sorted(TRAINING_PRESETS.items(),
                                          key=lambda x: x[1]["vram"], reverse=True):
            if preset["vram"] <= remaining:
                best_training = preset_name
                break
        training = TRAINING_PRESETS[best_training]
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
            "inference_score": inference_scores.get(model_name, 0),
            "training_score": training_scores.get(best_training, 0),
            "combined_score": inference_scores.get(model_name, 0) + training_scores.get(best_training, 0),
            "ollama_mode": "gpu",
            "gradient_checkpointing": total_vram > available * 0.85,
        })

    # Training-only configs (Ollama on CPU)
    for preset_name, preset in TRAINING_PRESETS.items():
        if preset["vram"] > 0 and preset["vram"] <= available:
            ts = training_scores.get(preset_name, 0)
            configs.append({
                "model": "any (CPU)",
                "model_vram_gb": 0,
                "training_preset": preset_name,
                "training_vram_gb": preset["vram"],
                "training_params": preset["params"],
                "training_desc": preset["desc"],
                "total_vram_gb": preset["vram"],
                "headroom_gb": round(available - preset["vram"], 1),
                "inference_score": 1,
                "training_score": ts,
                "combined_score": 1 + ts,
                "ollama_mode": "cpu",
                "gradient_checkpointing": False,
            })

    configs.sort(key=lambda c: (-c["combined_score"], c["total_vram_gb"]))

    workers, mem_limit = 2, 256
    for threshold, w, ml in WORKER_TIERS:
        if ram < threshold:
            workers, mem_limit = w, ml
            break

    return configs, workers, mem_limit


def _auto_detect(payload: dict, config: dict) -> dict:
    """Detect hardware and show all viable inference+training configurations."""
    hw = _detect_hardware()
    configs, workers, mem_limit = _calculate_configs(hw)
    log.info(
        "Hardware: %.1fGB RAM, %.1fGB VRAM, %d GPU(s), %s",
        hw.get("ram_gb", 0), hw.get("vram_total_gb", 0),
        hw.get("gpu_count", 0), hw.get("memory_mode", "discrete"),
    )
    log.info("Found %d viable configurations", len(configs))
    top = configs[:3]
    for i, c in enumerate(top):
        log.info("  #%d: %s + %s training (%sGB, score=%s)",
                 i + 1, c["model"], c["training_preset"],
                 c["total_vram_gb"], c["combined_score"])
    return {
        "status": "ok",
        "hardware": hw,
        "configurations": configs,
        "top_3": top,
        "workers": workers,
        "memory_limit_mb": mem_limit,
    }


def _auto_recommend(payload: dict, config: dict) -> dict:
    """Show human-readable inference+training recommendations."""
    result = _auto_detect(payload, config)
    hw = result["hardware"]
    top = result["top_3"]
    labels = ["BEST (balanced)", "ALTERNATIVE (inference focus)", "ALTERNATIVE (training focus)"]
    recs = []
    for i, cfg in enumerate(top):
        label = labels[i] if i < len(labels) else f"Option {i + 1}"
        tok = "120" if "8b" in cfg["model"] else "80" if "4b" in cfg["model"] else "40"
        recs.append({
            "label": label,
            "inference": f"{cfg['model']} on GPU (~{tok} tok/s)",
            "training": f"{cfg['training_preset']} ({cfg['training_params']} params)",
            "vram": f"{cfg['total_vram_gb']}/{hw.get('vram_total_gb', 0)}GB",
            "ollama_mode": cfg["ollama_mode"],
        })
    return {
        "status": "ok",
        "hardware_summary": (
            f"{hw.get('gpu_name', '?')} | {hw.get('ram_gb', 0)}GB RAM"
            f" | {hw.get('vram_total_gb', 0)}GB VRAM"
        ),
        "recommendations": recs,
        "workers": result["workers"],
    }


def _auto_generate(payload: dict, config: dict) -> dict:
    """Write optimized profiles.generated.toml for this hardware."""
    result = _auto_detect(payload, config)
    hw = result["hardware"]
    configs = result["configurations"]

    if not configs:
        return {"status": "error", "error": "No viable configurations for this hardware"}

    profiles_path = AUTORESEARCH_DIR / "profiles.toml"
    if not profiles_path.parent.exists():
        return {"status": "error", "error": f"Autoresearch directory not found: {AUTORESEARCH_DIR}"}

    vram = hw.get("vram_total_gb", 0)
    ram = hw.get("ram_gb", 0)
    gpu = hw.get("gpu_name", "unknown")

    lines = [
        "# Auto-generated by BigEd CC model_suite",
        f"# Hardware: {gpu}, {ram}GB RAM, {vram}GB VRAM",
        f"# Generated for {hw.get('gpu_count', 1)} GPU(s), {hw.get('memory_mode', 'discrete')} memory",
        "#",
        'active = "hybrid"',
        "",
    ]

    gpu_configs = [c for c in configs if c["ollama_mode"] == "gpu" and c["training_preset"] != "none"]
    cpu_configs = [c for c in configs if c["ollama_mode"] == "cpu" and c["training_preset"] != "none"]

    micro = next((c for c in gpu_configs if c["training_preset"] in ("tiny", "micro")), None)
    if micro:
        preset = TRAINING_PRESETS[micro["training_preset"]]
        lines.extend([
            '[profiles.micro]',
            f'description = "Quick iteration -- {micro["model"]} + {micro["training_preset"]} training"',
            'ollama_mode = "gpu"',
            f'ollama_model = "{micro["model"]}"',
            f'DEPTH = {preset["depth"]}',
            f'model_dim = {preset["dim"]}',
            'HEAD_DIM = 64',
            'ASPECT_RATIO = 64',
            'DEVICE_BATCH_SIZE = 8',
            'TOTAL_BATCH_SIZE = 65536',
            'gradient_checkpointing = false',
            f'vram_target_gb = {preset["vram"]}',
            "",
        ])

    hybrid = (
        next((c for c in gpu_configs
              if c["combined_score"] == max(g["combined_score"] for g in gpu_configs)), None)
        if gpu_configs else None
    )
    if hybrid:
        preset = TRAINING_PRESETS[hybrid["training_preset"]]
        lines.extend([
            '[profiles.hybrid]',
            f'description = "Balanced -- {hybrid["model"]} + {hybrid["training_preset"]} training"',
            'ollama_mode = "gpu"',
            f'ollama_model = "{hybrid["model"]}"',
            f'DEPTH = {preset["depth"]}',
            f'model_dim = {preset["dim"]}',
            'HEAD_DIM = 128',
            'ASPECT_RATIO = 64',
            'DEVICE_BATCH_SIZE = 16',
            'TOTAL_BATCH_SIZE = 65536',
            f'gradient_checkpointing = {str(hybrid["gradient_checkpointing"]).lower()}',
            f'vram_target_gb = {preset["vram"]}',
            "",
        ])

    stable = (
        next((c for c in cpu_configs
              if c["training_score"] == max(g["training_score"] for g in cpu_configs)), None)
        if cpu_configs else None
    )
    if stable:
        preset = TRAINING_PRESETS[stable["training_preset"]]
        lines.extend([
            '[profiles.stable]',
            f'description = "Max training -- Ollama on CPU, {stable["training_preset"]} training"',
            'ollama_mode = "cpu"',
            f'DEPTH = {preset["depth"]}',
            f'model_dim = {preset["dim"]}',
            'HEAD_DIM = 128',
            'ASPECT_RATIO = 64',
            'DEVICE_BATCH_SIZE = 32',
            'TOTAL_BATCH_SIZE = 65536',
            'gradient_checkpointing = false',
            f'vram_target_gb = {preset["vram"]}',
            "",
        ])

    out_path = profiles_path.with_suffix(".generated.toml")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Generated profiles written to %s (review before replacing profiles.toml)",
             out_path.name)

    return {
        "status": "ok",
        "generated": str(out_path),
        "profiles": [p for p in ["micro", "hybrid", "stable"]
                     if any(f"[profiles.{p}]" in l for l in lines)],
        "hardware": f"{gpu} | {ram}GB RAM | {vram}GB VRAM",
        "note": "Review .generated.toml before replacing profiles.toml",
    }
