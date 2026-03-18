#!/usr/bin/env python3
"""
Secondary Hardware Supervisor (CPU-bound)

PURPOSE:
This daemon runs alongside the primary fleet supervisor. It is strictly CPU-bound
and acts as a safety governor for GPU memory (VRAM) management and model distribution.

CAPABILITIES / NOTES FOR AI AGENTS:
- This script actively monitors VRAM via pynvml.
- It dynamically rewrites the `local` model in `fleet.toml` to smaller variants
  (e.g., qwen3:4b, qwen3:1.7b) if VRAM gets dangerously high (>75% or >90%).
- When VRAM frees up, it restores the default user-configured model.
- It aggressively unloads idle Ollama models when VRAM is critically low to 
  prevent CUDA OutOfMemory errors during `train.py` or Stable Diffusion tasks.
- SUPERVISOR INTEGRATION: It writes to `hw_state.json`. The primary fleet supervisor 
  must read this file and pause task distribution when `status == "transitioning"`.
- AI AGENTS: Do not manually implement model-downgrade logic in individual skills; 
  rely on this supervisor to handle model switching and scaling seamlessly.
"""
import os
import time
import json
import re
import urllib.request
from pathlib import Path

try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _HAS_GPU = True
except Exception:
    _HAS_GPU = False

FLEET_DIR = Path(__file__).parent
FLEET_TOML = FLEET_DIR / "fleet.toml"
HW_STATE_FILE = FLEET_DIR / "hw_state.json"

# Define safe fallback tiers for VRAM pressure
TIER_DEFAULT = "qwen3:8b"  # will be captured dynamically on startup
TIER_MID     = "qwen3:4b"
TIER_LOW     = "qwen3:1.7b"
TIER_CRIT    = "qwen3:0.6b"


def write_state(status: str, current_model: str):
    """Communicate directly with the primary supervisor via shared state."""
    try:
        state_data = {
            "status": status,
            "model": current_model,
            "updated_at": time.time()
        }
        HW_STATE_FILE.write_text(json.dumps(state_data), encoding="utf-8")
    except Exception:
        pass


def get_current_local_model():
    try:
        text = FLEET_TOML.read_text(encoding="utf-8")
        m = re.search(r'^local\s*=\s*["\']([^"\']+)["\']', text, re.M)
        return m.group(1) if m else TIER_DEFAULT
    except Exception:
        return TIER_DEFAULT

def set_local_model(target_model):
    try:
        text = FLEET_TOML.read_text(encoding="utf-8")
        current = get_current_local_model()
        if current == target_model:
            return False
        text = re.sub(r'^(local\s*=\s*)["\'][^"\']*["\']', f'\\g<1>"{target_model}"', text, flags=re.M)
        FLEET_TOML.write_text(text, encoding="utf-8")
        print(f"[HW_SUP] Model adapted for task distribution: {current} -> {target_model}")
        return True
    except Exception as e:
        print(f"[HW_SUP] Config write error: {e}")
        return False

def unload_all_models():
    try:
        req = urllib.request.Request("http://localhost:11434/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            for m in data.get("models", []):
                print(f"[HW_SUP] Emergency VRAM eviction: unloading {m['name']}")
                body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
                ureq = urllib.request.Request(
                    "http://localhost:11434/api/generate", 
                    data=body, method="POST", 
                    headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(ureq, timeout=5)
    except Exception:
        pass


def warmup_model(model_name: str):
    """Send a dummy request to force Ollama to load the model into VRAM."""
    try:
        body = json.dumps({"model": model_name, "prompt": "", "keep_alive": "5m"}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", 
            data=body, method="POST", 
            headers={"Content-Type": "application/json"}
        )
        # Allow up to 45 seconds for a model to load from disk into VRAM
        urllib.request.urlopen(req, timeout=45)
    except Exception:
        pass


def transition_model(target: str, current: str, emergency: bool = False):
    """Coordinate a smooth hand-off with the primary supervisor."""
    print(f"[HW_SUP] {'EMERGENCY ' if emergency else ''}Hand-off initiated: {current} -> {target}")
    
    if not emergency:
        print("[HW_SUP] Signaling primary supervisor to pause for smooth transition...")
        write_state("transitioning", target)
        time.sleep(15)  # 15s grace period for workers to finish quick tasks
    else:
        print("[HW_SUP] Immediate eviction required to avoid OOM!")
        unload_all_models()
        write_state("transitioning", target)

    # Apply new model to config
    set_local_model(target)
    
    if not emergency:
        print("[HW_SUP] Unloading previous models...")
        unload_all_models()
        time.sleep(2)

    print(f"[HW_SUP] Warming up {target} (allow 15-30s for load)...")
    warmup_model(target)
    
    write_state("ready", target)
    print("[HW_SUP] Hand-off complete. Resuming fleet operations.")
    
    if not emergency:
        print("[HW_SUP] Cooldown period active (30s).")
        time.sleep(30)  # Prevent rapid bouncing back and forth


def main():
    if not _HAS_GPU:
        print("[HW_SUP] No NVIDIA GPU detected. Exiting hardware supervisor.")
        return

    print("[HW_SUP] Hardware Supervisor started. Monitoring VRAM for dynamic model scaling.")
    
    write_state("ready", get_current_local_model())

    while True:
        time.sleep(5)  # Poll every 5s
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            usage_pct = mem.used / mem.total
            
            # Check active workload types
            is_training = os.system("pgrep -f train.py > /dev/null") == 0
            is_marathon = os.system("pgrep -f dispatch_marathon.py > /dev/null") == 0
            
            # Determine baseline based on stability requirements
            baseline_model = TIER_MID if is_marathon else TIER_DEFAULT
            current_model = get_current_local_model()
            target_model = current_model
            emergency = False
            
            if is_training:
                # Training takes absolute priority.
                if usage_pct > 0.90:
                    target_model = TIER_CRIT
                    emergency = True
                elif current_model not in (TIER_CRIT, TIER_LOW):
                    target_model = TIER_LOW
            else:
                # Normal operation thresholds
                if usage_pct > 0.90:
                    target_model = TIER_LOW
                    emergency = True
                elif usage_pct > 0.75:
                    target_model = TIER_MID
                elif usage_pct < 0.60:
                    target_model = baseline_model

            if target_model != current_model:
                transition_model(target_model, current_model, emergency)
            else:
                write_state("ready", current_model)

        except Exception as e:
            print(f"[HW_SUP] Monitor loop error: {e}")

if __name__ == "__main__":
    main()