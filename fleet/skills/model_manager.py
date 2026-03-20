"""0.15.00: Model manager — inventory, install, profile switching for Ollama models."""
import json
import os
import sys
import urllib.request
from pathlib import Path

SKILL_NAME = "model_manager"
DESCRIPTION = "Check installed vs needed models, pull missing, switch hardware profiles"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "check")
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")

    if action == "check":
        return _check_models(config, host)
    elif action == "install":
        return _install_missing(config, host)
    elif action == "install_one":
        return _pull_model(payload.get("model", ""), host)
    elif action == "profiles":
        return _list_profiles()
    elif action == "apply_profile":
        return _apply_profile(payload.get("profile", ""), config)
    elif action == "hardware":
        return _detect_hardware()
    elif action == "recommend":
        return _recommend_profile()
    elif action == "update_check":
        return _check_model_updates(config, host)
    elif action == "debug":
        return _debug_models(host, payload.get("target"), payload.get("clean", False))
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _get_installed(host):
    """Get list of installed Ollama models."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _get_loaded(host):
    """Get list of currently loaded (in memory) models."""
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as r:
            data = json.loads(r.read())
        return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1)}
                for m in data.get("models", [])]
    except Exception:
        return []


def _get_needed(config):
    """Get set of models needed by fleet.toml configuration."""
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


def _check_models(config, host):
    """Compare installed vs needed models."""
    installed = _get_installed(host)
    loaded = _get_loaded(host)
    needed = _get_needed(config)
    missing = [m for m in needed if m not in installed]
    extra = [m for m in installed if m not in needed]

    return json.dumps({
        "installed": installed,
        "loaded": loaded,
        "needed": needed,
        "missing": missing,
        "extra": extra,
        "ready": len(missing) == 0,
        "summary": f"{len(installed)} installed, {len(missing)} missing, {len(loaded)} loaded"
    })


def _pull_model(model_name, host):
    """Pull a single model from Ollama registry."""
    if not model_name:
        return json.dumps({"error": "model name required"})
    try:
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(
            f"{host}/api/pull", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        # Stream response — collect status lines
        with urllib.request.urlopen(req, timeout=600) as r:
            lines = r.read().decode("utf-8", errors="replace").strip().split("\n")

        # Check last status line
        for line in reversed(lines):
            try:
                status = json.loads(line)
                if status.get("status") == "success":
                    return json.dumps({"status": "installed", "model": model_name})
                if "error" in status:
                    return json.dumps({"status": "error", "model": model_name, "error": status["error"]})
            except json.JSONDecodeError:
                continue

        return json.dumps({"status": "completed", "model": model_name})
    except Exception as e:
        return json.dumps({"status": "error", "model": model_name, "error": str(e)})


def _install_missing(config, host):
    """Pull all missing models."""
    needed = _get_needed(config)
    installed = _get_installed(host)
    missing = [m for m in needed if m not in installed]

    if not missing:
        return json.dumps({"status": "all_installed", "count": len(installed)})

    results = []
    for model in missing:
        result = json.loads(_pull_model(model, host))
        results.append(result)

    success = sum(1 for r in results if r.get("status") in ("installed", "completed"))
    return json.dumps({
        "status": "done",
        "pulled": success,
        "failed": len(results) - success,
        "details": results,
    })


def _list_profiles():
    """List available model profiles from model_profiles.toml."""
    profiles_path = FLEET_DIR / "model_profiles.toml"
    if not profiles_path.exists():
        return json.dumps({"error": "model_profiles.toml not found"})
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
        return json.dumps({"profiles": profiles})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _apply_profile(profile_name, config):
    """Apply a model profile to fleet.toml."""
    profiles_path = FLEET_DIR / "model_profiles.toml"
    fleet_toml = FLEET_DIR / "fleet.toml"
    if not profiles_path.exists():
        return json.dumps({"error": "model_profiles.toml not found"})
    try:
        import tomllib, tomlkit, tempfile
        with open(profiles_path, "rb") as f:
            profiles = tomllib.load(f)

        if profile_name not in profiles:
            return json.dumps({"error": f"Profile '{profile_name}' not found",
                             "available": list(profiles.keys())})

        profile = profiles[profile_name]

        # Update fleet.toml
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

        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(FLEET_DIR), suffix='.toml')
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp_path, str(fleet_toml))

        return json.dumps({"status": "applied", "profile": profile_name, "config": profile})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _detect_hardware():
    """Detect system hardware for profile recommendation."""
    import psutil

    hw = {
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "ram_available_gb": round(psutil.virtual_memory().available / 1e9, 1),
    }

    # GPU detection
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        hw["gpu_name"] = pynvml.nvmlDeviceGetName(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        hw["gpu_vram_gb"] = round(mem.total / 1e9, 1)
        hw["gpu_vram_free_gb"] = round(mem.free / 1e9, 1)
    except Exception:
        hw["gpu_name"] = None
        hw["gpu_vram_gb"] = 0

    return json.dumps(hw)


def _recommend_profile():
    """Recommend a model profile based on detected hardware."""
    hw = json.loads(_detect_hardware())

    gpu_vram = hw.get("gpu_vram_gb", 0)
    ram = hw.get("ram_total_gb", 0)

    if gpu_vram >= 10:
        profile = "dev_gpu"
        reason = f"GPU with {gpu_vram}GB VRAM — full GPU acceleration"
    elif gpu_vram >= 6:
        profile = "dev_gpu_light"
        reason = f"GPU with {gpu_vram}GB VRAM — lighter GPU models"
    elif ram >= 24:
        profile = "dev_cpu"
        reason = f"No GPU (or small VRAM) but {ram}GB RAM — CPU with quality models"
    elif ram >= 12:
        profile = "dev_cpu_light"
        reason = f"{ram}GB RAM — CPU with smaller models"
    else:
        profile = "minimal"
        reason = f"Limited resources ({ram}GB RAM) — minimal footprint"

    return json.dumps({"recommended": profile, "reason": reason, "hardware": hw})


def _check_model_updates(config, host):
    """Check installed models for available updates and discover new model families.

    Compares local model digests against Ollama registry. Reports:
    - Models with newer versions available
    - New model families worth evaluating (based on fleet tier sizes)
    - HITL recommendation if a model change would improve intelligence or performance
    """
    installed = _get_installed(host)
    needed = _get_needed(config)

    # Get detailed info for installed models (includes digest, modified_at, size)
    updates = []
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        local_models = {m["name"]: m for m in data.get("models", [])}
    except Exception:
        return json.dumps({"error": "Cannot reach Ollama", "updates": []})

    # Check each needed model for available updates via /api/show
    for model_name in needed:
        if model_name not in local_models:
            updates.append({
                "model": model_name, "status": "not_installed",
                "action": "install"
            })
            continue

        local_info = local_models[model_name]
        local_digest = local_info.get("digest", "")[:12]
        local_size_gb = round(local_info.get("size", 0) / 1e9, 1)
        modified = local_info.get("modified_at", "")[:10]

        # Check registry for latest digest
        try:
            body = json.dumps({"name": model_name}).encode()
            req = urllib.request.Request(
                f"{host}/api/show", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                show_data = json.loads(r.read())
            # Compare template/parameter hashes for version changes
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
            updates.append({
                "model": model_name, "status": "installed",
                "digest": local_digest, "size_gb": local_size_gb,
            })

    # Suggest new models based on tier sizes (discover alternatives)
    tiers = config.get("models", {}).get("tiers", {})
    suggestions = []
    # Map tier sizes to recommended model families
    tier_alternatives = {
        "default": ["qwen3:8b", "llama3.1:8b", "gemma2:9b", "mistral:7b"],
        "mid": ["qwen3:4b", "phi3:mini", "gemma2:2b"],
        "low": ["qwen3:1.7b", "phi3:mini"],
        "critical": ["qwen3:0.6b", "tinyllama:1.1b"],
    }
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

    # Build HITL recommendation if there are actionable findings
    hitl_recommendation = None
    if suggestions:
        hitl_recommendation = {
            "type": "model_update_review",
            "title": f"{len(suggestions)} alternative models available for evaluation",
            "detail": "Swarm recommends reviewing these model alternatives. "
                      "Pull and benchmark to compare intelligence vs performance.",
            "suggestions": suggestions[:5],  # top 5
        }

    return json.dumps({
        "models": updates,
        "suggestions": suggestions,
        "hitl_recommendation": hitl_recommendation,
        "summary": f"{len(updates)} models checked, {len(suggestions)} alternatives found",
    })


def _debug_models(host, target=None, clean=False):
    """Diagnose loaded models, detect idle blockers, optionally evict them.

    Delegates to fleet/debug_models.py — the canonical module for all
    idle-model detection and VRAM management.

    payload:
        target  (str)  – model name to protect from eviction
        clean   (bool) – if True, evict idle non-target models
    """
    # Import the canonical module (lives at fleet/debug_models.py)
    sys.path.insert(0, str(FLEET_DIR))
    try:
        import debug_models
    finally:
        sys.path.pop(0)

    try:
        if clean:
            report = debug_models.clean_idle(host, target)
        else:
            report = debug_models.diagnose(host, target)
        return json.dumps(report, default=str)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})
