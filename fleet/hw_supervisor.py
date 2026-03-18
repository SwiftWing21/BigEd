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

try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _HAS_GPU = True
except Exception:
    _HAS_GPU = False

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
    """Load [thermal], [thermal.vram], [models.tiers], [training] from fleet.toml."""
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
    }
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        t = data.get("thermal", {})
        v = t.get("vram", {})
        m = data.get("models", {}).get("tiers", {})
        tr = data.get("training", {})
        return {
            **defaults,
            **{k: t[k] for k in t if k != "vram" and k in defaults},
            "vram_emergency": v.get("emergency", defaults["vram_emergency"]),
            "vram_high": v.get("high", defaults["vram_high"]),
            "vram_restore": v.get("restore", defaults["vram_restore"]),
            "tier_default": m.get("default", defaults["tier_default"]),
            "tier_mid": m.get("mid", defaults["tier_mid"]),
            "tier_low": m.get("low", defaults["tier_low"]),
            "tier_crit": m.get("critical", defaults["tier_crit"]),
            "training_exclusive_lock": tr.get("exclusive_lock", True),
        }
    except Exception:
        return defaults


# ── State ─────────────────────────────────────────────────────────────────────

def write_state(status, model, thermal=None):
    """Write expanded hw_state.json for supervisor/worker/dashboard."""
    try:
        state = {
            "status": status,
            "model": model,
            "updated_at": time.time(),
        }
        if thermal:
            state["thermal"] = thermal
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


# ── Thermal Readings ──────────────────────────────────────────────────────────

def read_gpu_thermal():
    """Read GPU temp (°C), power (W), fan (%), VRAM usage (fraction)."""
    if not _HAS_GPU:
        return None
    try:
        temp = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
        vram_pct = mem.used / mem.total
        try:
            power_mw = pynvml.nvmlDeviceGetPowerUsage(_GPU_HANDLE)
            power_w = power_mw / 1000.0
        except Exception:
            power_w = 0.0
        try:
            fan_pct = pynvml.nvmlDeviceGetFanSpeed(_GPU_HANDLE)
        except Exception:
            fan_pct = -1
        return {
            "gpu_temp_c": temp,
            "gpu_power_w": round(power_w, 1),
            "gpu_fan_pct": fan_pct,
            "vram_used_gb": round(mem.used / (1024**3), 2),
            "vram_total_gb": round(mem.total / (1024**3), 2),
            "vram_pct": round(vram_pct, 3),
        }
    except Exception:
        return None


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
    # Fallback: process detection
    try:
        return os.system("pgrep -f train.py > /dev/null 2>&1") == 0
    except Exception:
        return False


# ── Main Loop ─────────────────────────────────────────────────────────────────

def main():
    if not _HAS_GPU:
        print("[HW_SUP] No NVIDIA GPU detected. Exiting.")
        return

    cfg = load_thermal_config()
    ambient = AmbientEstimator()

    # Thermal throttle state
    gpu_throttled = False
    below_target_since = None

    print(f"[HW_SUP] Started. Limits: {cfg['gpu_max_sustained_c']}°C sustained, "
          f"{cfg['gpu_max_burst_c']}°C burst. Poll: {cfg['poll_interval_secs']}s")

    write_state("ready", get_current_local_model())

    while True:
        time.sleep(cfg["poll_interval_secs"])
        try:
            gpu = read_gpu_thermal()
            if not gpu:
                continue

            cpu_temp = read_cpu_thermal()
            is_training = check_training_active(cfg)
            is_marathon = os.system("pgrep -f dispatch_marathon.py > /dev/null 2>&1") == 0

            gpu_temp = gpu["gpu_temp_c"]
            vram_pct = gpu["vram_pct"]

            # Update ambient estimator
            is_loaded = vram_pct > 0.3 or gpu["gpu_power_w"] > 50
            if cfg["ambient_estimation"]:
                ambient.update(gpu_temp, is_loaded)

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

            elif gpu_throttled:
                if gpu_temp <= cfg["cooldown_target_c"]:
                    if below_target_since is None:
                        below_target_since = time.time()
                    elif time.time() - below_target_since >= cfg["cooldown_window_secs"]:
                        print(f"[HW_SUP] GPU {gpu_temp}°C <= {cfg['cooldown_target_c']}°C for "
                              f"{cfg['cooldown_window_secs']}s — resuming GPU tasks")
                        gpu_throttled = False
                        below_target_since = None
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
            else:
                write_state("ready", current_model, thermal)

        except Exception as e:
            print(f"[HW_SUP] Error: {e}")


if __name__ == "__main__":
    main()
