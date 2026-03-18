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

# Define safe fallback tiers for VRAM pressure
TIER_DEFAULT = "qwen3:8b"  # will be captured dynamically on startup
TIER_MID     = "qwen3:4b"
TIER_LOW     = "qwen3:1.7b"
TIER_CRIT    = "qwen3:0.6b"

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

def main():
    if not _HAS_GPU:
        print("[HW_SUP] No NVIDIA GPU detected. Exiting hardware supervisor.")
        return

    print("[HW_SUP] Hardware Supervisor started. Monitoring VRAM for dynamic model scaling.")
    
    # Capture baseline so we can restore to it when VRAM frees up
    baseline_model = get_current_local_model()
    if baseline_model in (TIER_MID, TIER_LOW, TIER_CRIT):
        baseline_model = "qwen3:8b" 
        
    print(f"[HW_SUP] Baseline task distribution model: {baseline_model}")

    while True:
        time.sleep(5)  # Poll every 5s
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            usage_pct = mem.used / mem.total
            
            # Check if training is currently dominating the GPU
            is_training = os.system("pgrep -f train.py > /dev/null") == 0
            
            if is_training:
                # Training takes absolute priority. Strip down to minimum immediately.
                if usage_pct > 0.90:
                    if set_local_model(TIER_CRIT): unload_all_models()
                else:
                    set_local_model(TIER_LOW)
                continue

            # Normal operation thresholds
            if usage_pct > 0.90:
                if set_local_model(TIER_LOW):
                    unload_all_models() # Evict large models immediately to save the fleet
            elif usage_pct > 0.75:
                set_local_model(TIER_MID)
            elif usage_pct < 0.60:
                # Safe to restore baseline if we were downgraded
                current = get_current_local_model()
                if current != baseline_model and current in (TIER_MID, TIER_LOW, TIER_CRIT):
                    set_local_model(baseline_model)

        except Exception as e:
            print(f"[HW_SUP] Monitor loop error: {e}")

if __name__ == "__main__":
    main()