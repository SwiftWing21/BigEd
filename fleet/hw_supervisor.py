#!/usr/bin/env python3
"""
Hardware Supervisor — thermal-aware GPU/VRAM/power governor for 24/7 fleet operation.

PURPOSE:
Runs alongside the primary fleet supervisor (CPU-bound daemon). Monitors GPU temperature,
VRAM pressure, power draw, and CPU thermals. Dynamically scales Ollama model tiers and
worker concurrency to maintain sustained temps below 75°C (burst ceiling 78°C).

CAPABILITIES:
- VRAM monitoring + 4-tier model scaling (8b → 4b → 1.7b → 0.6b)
- GPU junction temperature tracking with sustained/burst limits
- GPU power draw monitoring (watts vs TDP headroom)
- CPU temperature monitoring via psutil
- Ambient temperature estimation from cooldown curves
- Training lock awareness (DB-based, respects exclusive_lock)
- All thresholds config-driven from fleet.toml [thermal] section
- Writes expanded hw_state.json for supervisor/worker/dashboard coordination

AI AGENTS: Do not implement model-downgrade logic in skills. This supervisor handles it.
"""
import json
import os
import re
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path

from gpu import detect_gpu, read_telemetry as _gpu_read_telemetry

_gpu_backend, _HAS_GPU = detect_gpu()

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

FLEET_DIR = Path(__file__).parent
FLEET_TOML = FLEET_DIR / "fleet.toml"
HW_STATE_FILE = FLEET_DIR / "hw_state.json"

sys.path.insert(0, str(FLEET_DIR))


# ── Config ────────────────────────────────────────────────────────────────────

def load_thermal_config():
    """Load [thermal], [thermal.vram], [models.tiers], [models], [training], [fleet] from fleet.toml."""
    defaults = {
        "gpu_max_sustained_c": 75, "gpu_max_burst_c": 78,
        "cpu_max_sustained_c": 80, "cooldown_target_c": 72,
        "cooldown_window_secs": 60, "poll_interval_secs": 5,
        "grace_period_secs": 15, "cooldown_after_swap_secs": 30,
        "ambient_estimation": True,
        "vram_emergency": 0.90, "vram_high": 0.75, "vram_restore": 0.60,
        "tier_default": "qwen3:8b", "tier_mid": "qwen3:4b",
        "tier_low": "qwen3:1.7b", "tier_crit": "qwen3:0.6b",
        "training_exclusive_lock": True,
        "ollama_host": "http://localhost:11434",
        "conductor_model": "",
        "air_gap_mode": False,
    }
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        t = data.get("thermal", {})
        v = t.get("vram", {})
        mt = data.get("models", {}).get("tiers", {})
        m = data.get("models", {})
        tr = data.get("training", {})
        fl = data.get("fleet", {})
        return {
            **defaults,
            **{k: t[k] for k in t if k != "vram" and k in defaults},
            "vram_emergency": v.get("emergency", defaults["vram_emergency"]),
            "vram_high": v.get("high", defaults["vram_high"]),
            "vram_restore": v.get("restore", defaults["vram_restore"]),
            "tier_default": mt.get("default", defaults["tier_default"]),
            "tier_mid": mt.get("mid", defaults["tier_mid"]),
            "tier_low": mt.get("low", defaults["tier_low"]),
            "tier_crit": mt.get("critical", defaults["tier_crit"]),
            "training_exclusive_lock": tr.get("exclusive_lock", True),
            "ollama_host": m.get("ollama_host", defaults["ollama_host"]),
            "conductor_model": m.get("conductor_model", ""),
            "air_gap_mode": fl.get("air_gap_mode", False),
        }
    except Exception:
        return defaults


# ── State ─────────────────────────────────────────────────────────────────────

def write_state(status, model, thermal=None, models_loaded=None, conductor_status=None):
    """Write expanded hw_state.json for supervisor/worker/dashboard/launcher."""
    try:
        state = {
            "status": status,
            "model": model,
            "updated_at": time.time(),
        }
        if thermal:
            state["thermal"] = thermal
        if models_loaded is not None:
            state["models_loaded"] = models_loaded  # list of {"name", "size_gb", "device"}
        if conductor_status is not None:
            state["conductor"] = conductor_status  # "loaded" | "unloaded" | "warming"
        HW_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


# ── Fleet TOML Model Management ──────────────────────────────────────────────

def get_current_local_model():
    try:
        text = FLEET_TOML.read_text(encoding="utf-8")
        m = re.search(r'^local\s*=\s*["\']([^"\']+)["\']', text, re.M)
        return m.group(1) if m else "qwen3:8b"
    except Exception:
        return "qwen3:8b"


def set_local_model(target_model):
    try:
        text = FLEET_TOML.read_text(encoding="utf-8")
        current = get_current_local_model()
        if current == target_model:
            return False
        text = re.sub(
            r'^(local\s*=\s*)["\'][^"\']*["\']',
            f'\\g<1>"{target_model}"', text, flags=re.M)
        FLEET_TOML.write_text(text, encoding="utf-8")
        print(f"[HW_SUP] Model: {current} -> {target_model}")
        return True
    except Exception as e:
        print(f"[HW_SUP] Config write error: {e}")
        return False


# ── Ollama Control ────────────────────────────────────────────────────────────

def unload_all_models():
    try:
        req = urllib.request.Request("http://localhost:11434/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            for m in data.get("models", []):
                print(f"[HW_SUP] Evicting {m['name']}")
                body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
                ureq = urllib.request.Request(
                    "http://localhost:11434/api/generate", data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(ureq, timeout=5)
    except Exception:
        pass


def warmup_model(model_name):
    try:
        body = json.dumps({"model": model_name, "prompt": "", "keep_alive": "5m"}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=45)
    except Exception:
        pass


def evict_models_for_training(host=None):
    """Pre-flight VRAM eviction: unload GPU models before training starts.
    Sends keep_alive=0 to Ollama to free VRAM for PyTorch."""
    try:
        if host is None:
            host = "http://localhost:11434"
        # Get currently loaded models
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        for model in data.get("models", []):
            model_name = model.get("name", "")
            if not model_name:
                continue
            # Send keep_alive=0 to evict from VRAM
            body = json.dumps({"model": model_name, "keep_alive": 0}).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()  # consume response
                print(f"[HW_SUP] Pre-training eviction: {model_name}")
            except Exception:
                pass  # best-effort eviction
    except Exception:
        pass  # eviction is best-effort, never block training


# ── Thermal Readings ──────────────────────────────────────────────────────────

def read_gpu_thermal():
    """Read GPU temp (°C), power (W), fan (%), VRAM usage (fraction)."""
    if not _HAS_GPU:
        return None
    return _gpu_read_telemetry(_gpu_backend)


def read_cpu_thermal():
    """Read CPU package temp via psutil (Windows: WMI fallback)."""
    if not _HAS_PSUTIL:
        return None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # Linux: coretemp/k10temp; Windows: may not be available
            for chip in ("coretemp", "k10temp", "acpitz"):
                if chip in temps:
                    return max(t.current for t in temps[chip])
        return None
    except Exception:
        return None


# ── Ambient Estimation ────────────────────────────────────────────────────────

class AmbientEstimator:
    """Estimate ambient temperature from GPU cooldown curves.

    When GPU transitions from load to idle, track the cooling rate.
    Ambient ~= asymptotic temperature the GPU approaches at idle.
    """

    def __init__(self):
        self._history = deque(maxlen=120)  # 10 min at 5s intervals
        self._ambient_estimate = None
        self._last_load_temp = None
        self._cooldown_start = None

    def update(self, gpu_temp, is_under_load):
        now = time.time()
        self._history.append((now, gpu_temp, is_under_load))

        if is_under_load:
            self._last_load_temp = gpu_temp
            self._cooldown_start = None
            return

        # Track cooldown
        if self._last_load_temp and not self._cooldown_start:
            self._cooldown_start = now

        if self._cooldown_start and (now - self._cooldown_start) > 120:
            # After 2 min of idle, current temp approximates ambient + idle dissipation
            # GPU idle offset is typically 8-15°C above ambient
            idle_offset = 12  # reasonable estimate for RTX 3080 Ti in a case
            self._ambient_estimate = max(15, gpu_temp - idle_offset)

    @property
    def ambient(self):
        return self._ambient_estimate

    def cooldown_rate(self):
        """Degrees per second of cooling (positive = cooling down)."""
        if len(self._history) < 6:
            return 0.0
        recent = list(self._history)[-6:]
        if recent[-1][2]:  # under load
            return 0.0
        dt = recent[-1][0] - recent[0][0]
        if dt < 1:
            return 0.0
        return (recent[0][1] - recent[-1][1]) / dt


# ── Model Heartbeat (keepalive + conductor + inventory) ──────────────────

KEEPALIVE_EVERY_N_POLLS = 48  # ~240s at 5s poll interval


def get_loaded_models(host):
    """Query Ollama /api/ps for currently loaded models. Returns list of dicts."""
    try:
        req = urllib.request.Request(f"{host}/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            result = []
            for m in data.get("models", []):
                size_gb = m.get("size_vram", m.get("size", 0)) / (1024**3)
                result.append({
                    "name": m["name"],
                    "size_gb": round(size_gb, 2),
                })
            return result
    except Exception:
        return []


def ping_keepalive(host, model, keep_alive="24h"):
    """Send keepalive ping to keep model loaded in VRAM."""
    try:
        body = json.dumps({"model": model, "keep_alive": keep_alive}).encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def ensure_conductor(host, conductor_model):
    """Verify conductor model is loaded on CPU (num_gpu=0). Load if missing."""
    if not conductor_model:
        return "none"
    loaded = get_loaded_models(host)
    loaded_names = [m["name"] for m in loaded]
    # Check if conductor is already loaded (match with or without tag)
    for name in loaded_names:
        if name.split(":")[0] == conductor_model.split(":")[0]:
            return "loaded"
    # Not loaded — warm it up on CPU
    try:
        body = json.dumps({
            "model": conductor_model, "keep_alive": "24h",
            "options": {"num_gpu": 0},
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
        return "loaded"
    except Exception:
        return "unloaded"


# ── Model Transition ──────────────────────────────────────────────────────────

def transition_model(target, current, cfg, emergency=False):
    print(f"[HW_SUP] {'EMERGENCY ' if emergency else ''}Transition: {current} -> {target}")

    if not emergency:
        write_state("transitioning", target)
        time.sleep(cfg["grace_period_secs"])
    else:
        unload_all_models()
        write_state("transitioning", target)

    set_local_model(target)

    if not emergency:
        unload_all_models()
        time.sleep(2)

    print(f"[HW_SUP] Warming up {target}...")
    warmup_model(target)

    write_state("ready", target)
    print("[HW_SUP] Transition complete.")

    if not emergency:
        time.sleep(cfg["cooldown_after_swap_secs"])


# ── Training Lock ─────────────────────────────────────────────────────────────

def check_training_active(cfg):
    """Check if training is running via DB lock or process detection."""
    # DB lock (preferred)
    if cfg["training_exclusive_lock"]:
        try:
            import db
            lock = db.check_lock("training")
            if lock:
                return True
        except Exception:
            pass
    # Fallback: process detection (bracket trick avoids self-match)
    try:
        return os.system("pgrep -f '[t]rain\\.py' > /dev/null 2>&1") == 0
    except Exception:
        return False


# ── Main Loop ─────────────────────────────────────────────────────────────────

def main():
    if not _HAS_GPU:
        print("[HW_SUP] No NVIDIA GPU detected. Exiting.")
        return

    cfg = load_thermal_config()
    ambient = AmbientEstimator()

    # Register with DB for sup-channel communication
    _HAS_DB = False
    try:
        import db as _db
        _db.init_db()
        _db.register_agent("hw_supervisor", "supervisor", os.getpid())
        _HAS_DB = True
    except Exception:
        pass

    # Thermal throttle state
    gpu_throttled = False
    below_target_since = None
    was_training = False  # track training transitions for VRAM eviction

    host = cfg["ollama_host"]
    conductor_model = cfg["conductor_model"]
    air_gap = cfg["air_gap_mode"]

    print(f"[HW_SUP] Started. Limits: {cfg['gpu_max_sustained_c']}°C sustained, "
          f"{cfg['gpu_max_burst_c']}°C burst. Poll: {cfg['poll_interval_secs']}s")
    if conductor_model:
        print(f"[HW_SUP] Conductor model: {conductor_model} (CPU)")

    write_state("ready", get_current_local_model())

    poll_count = 0

    while True:
        time.sleep(cfg["poll_interval_secs"])
        poll_count += 1
        try:
            gpu = read_gpu_thermal()
            if not gpu:
                continue

            cpu_temp = read_cpu_thermal()
            is_training = check_training_active(cfg)
            is_marathon = os.system("pgrep -f dispatch_marathon.py > /dev/null 2>&1") == 0

            # ── Training transition: evict GPU models on start (non-blocking) ──
            if is_training and not was_training:
                print("[HW_SUP] Training detected — evicting GPU models for VRAM headroom")
                import threading
                threading.Thread(target=evict_models_for_training, args=(host,), daemon=True).start()
            elif was_training and not is_training:
                print("[HW_SUP] Training ended — models will reload on next keepalive cycle")
            was_training = is_training

            gpu_temp = gpu["gpu_temp_c"]
            vram_pct = gpu["vram_pct"]

            # Update ambient estimator
            is_loaded = vram_pct > 0.3 or gpu["gpu_power_w"] > 50
            if cfg["ambient_estimation"]:
                ambient.update(gpu_temp, is_loaded)

            # ── Model heartbeat (keepalive + conductor) ───────────────
            models_loaded = []
            conductor_status = "none"
            if not air_gap:
                models_loaded = get_loaded_models(host)
                # Keepalive ping every ~240s
                if poll_count % KEEPALIVE_EVERY_N_POLLS == 0:
                    current_local = get_current_local_model()
                    ping_keepalive(host, current_local)
                # Conductor check every ~60s (12 polls)
                if conductor_model and poll_count % 12 == 0:
                    conductor_status = ensure_conductor(host, conductor_model)
                elif conductor_model:
                    # Between checks, report based on loaded list
                    conductor_status = "loaded" if any(
                        m["name"].split(":")[0] == conductor_model.split(":")[0]
                        for m in models_loaded
                    ) else "unloaded"

            # Sup inbox check every ~60s (12 polls at 5s)
            if _HAS_DB and poll_count % 12 == 0:
                try:
                    msgs = _db.get_messages("hw_supervisor", unread_only=True,
                                            limit=3, channels=["sup"])
                    for m in msgs:
                        body = json.loads(m["body_json"])
                        print(f"[HW_SUP] Sup: {m['from_agent']} -> {body.get('type', '?')}")
                except Exception:
                    pass

            # Build thermal snapshot
            thermal = {
                **gpu,
                "cpu_temp_c": cpu_temp,
                "ambient_est_c": ambient.ambient,
                "cooldown_rate_cs": round(ambient.cooldown_rate(), 3),
                "is_training": is_training,
                "gpu_throttled": gpu_throttled,
            }

            # ── Thermal throttling ────────────────────────────────────
            if gpu_temp >= cfg["gpu_max_burst_c"] and not gpu_throttled:
                print(f"[HW_SUP] GPU {gpu_temp}°C >= {cfg['gpu_max_burst_c']}°C burst limit — "
                      "pausing GPU tasks, switching to CPU-only")
                gpu_throttled = True
                below_target_since = None
                if _HAS_DB:
                    try:
                        _db.post_note("sup", "hw_supervisor", json.dumps({
                            "type": "thermal_alert",
                            "title": f"GPU throttled at {gpu_temp}°C",
                            "tags": ["thermal", "gpu"],
                        }))
                    except Exception:
                        pass

            elif gpu_throttled:
                if gpu_temp <= cfg["cooldown_target_c"]:
                    if below_target_since is None:
                        below_target_since = time.time()
                    elif time.time() - below_target_since >= cfg["cooldown_window_secs"]:
                        print(f"[HW_SUP] GPU {gpu_temp}°C <= {cfg['cooldown_target_c']}°C for "
                              f"{cfg['cooldown_window_secs']}s — resuming GPU tasks")
                        gpu_throttled = False
                        below_target_since = None
                        if _HAS_DB:
                            try:
                                _db.post_note("sup", "hw_supervisor", json.dumps({
                                    "type": "thermal_alert",
                                    "title": f"GPU resumed at {gpu_temp}°C",
                                    "tags": ["thermal", "gpu"],
                                }))
                            except Exception:
                                pass
                else:
                    below_target_since = None

            # ── VRAM-based model scaling ──────────────────────────────
            baseline = cfg["tier_mid"] if is_marathon else cfg["tier_default"]
            current_model = get_current_local_model()
            target_model = current_model
            emergency = False

            if gpu_throttled:
                # Thermal override: use critical tier (CPU-only)
                target_model = cfg["tier_crit"]
                if current_model != cfg["tier_crit"]:
                    emergency = True
            elif is_training:
                if vram_pct > cfg["vram_emergency"]:
                    target_model = cfg["tier_crit"]
                    emergency = True
                elif current_model not in (cfg["tier_crit"], cfg["tier_low"]):
                    target_model = cfg["tier_low"]
            else:
                if vram_pct > cfg["vram_emergency"]:
                    target_model = cfg["tier_low"]
                    emergency = True
                elif vram_pct > cfg["vram_high"]:
                    target_model = cfg["tier_mid"]
                elif vram_pct < cfg["vram_restore"]:
                    target_model = baseline

            if target_model != current_model:
                transition_model(target_model, current_model, cfg, emergency)
                # Post sup note about model transition
                if _HAS_DB:
                    try:
                        _db.post_note("sup", "hw_supervisor", json.dumps({
                            "type": "model_transition",
                            "title": f"Model: {current_model} -> {target_model}",
                            "content": f"{'Emergency ' if emergency else ''}VRAM {vram_pct:.0%}",
                            "tags": ["model", "vram"],
                        }))
                    except Exception:
                        pass
            else:
                # ── Vision model rotation ──────────────────────────────
                # Check if a vision task has requested model loading
                try:
                    if HW_STATE_FILE.exists():
                        _hw = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
                        vr = _hw.get("vision_request")
                        if vr and not gpu_throttled and not is_training:
                            vision_model = vr.get("model", "llava")
                            # Check if vision model is already loaded
                            already_loaded = any(
                                m["name"].split(":")[0] == vision_model.split(":")[0]
                                for m in models_loaded
                            )
                            if not already_loaded and not air_gap:
                                print(f"[HW_SUP] Vision request: loading {vision_model}")
                                warmup_model(vision_model)
                except Exception:
                    pass

                write_state("ready", current_model, thermal, models_loaded, conductor_status)

        except Exception as e:
            print(f"[HW_SUP] Error: {e}")


if __name__ == "__main__":
    main()
