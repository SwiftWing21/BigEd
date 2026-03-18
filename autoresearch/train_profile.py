#!/usr/bin/env python3
"""
Profile-aware training launcher for autoresearch experiments.

Usage:
    uv run python train_profile.py                    # use active profile from profiles.toml
    uv run python train_profile.py --profile stable   # explicit profile
    uv run python train_profile.py --profile flat_out
    uv run python train_profile.py --list             # show all profiles

What it does:
  1. Loads the selected profile from profiles.toml
  2. Stops Ollama / restarts it in CPU-only mode if the profile requires it
  3. Sets DEPTH, model_dim, HEAD_DIM, batch sizes, LR etc. as env vars
  4. For flat_out: enables gradient checkpointing + CPU optimizer offload
  5. Launches train.py — which must exist in this directory
  6. On OOM (flat_out only): retries with fallback dimensions
  7. Restores Ollama to its previous mode when training finishes
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        sys.exit("Need tomllib (Python 3.11+) or tomli: pip install tomli")

HERE        = Path(__file__).parent
PROFILES    = HERE / "profiles.toml"
TRAIN_PY    = HERE / "train.py"
RESULTS_TSV = HERE / "results.tsv"


# ── Ollama helpers ─────────────────────────────────────────────────────────

def _ollama_running() -> bool:
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ollama.exe"], capture_output=True, text=True)
            return "ollama.exe" in r.stdout
        else:
            r = subprocess.run(["pgrep", "-f", "ollama serve"], capture_output=True)
            return r.returncode == 0
    except FileNotFoundError:
        return False


def _get_ollama_vram_gb() -> float:
    import urllib.request
    import json
    try:
        req = urllib.request.Request("http://localhost:11434/api/ps")
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read())
            vram_bytes = sum(m.get("size_vram", 0) for m in data.get("models", []))
            return vram_bytes / (1024**3)
    except Exception:
        return 0.0


def _stop_ollama():
    print("[profile] Stopping Ollama...")
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)
    time.sleep(2)


def _start_ollama(gpu: bool):
    env = os.environ.copy()
    if not gpu:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
        print("[profile] Starting Ollama (CPU-only mode)")
    else:
        env.pop("CUDA_VISIBLE_DEVICES", None)
        print("[profile] Starting Ollama (GPU mode)")
    subprocess.Popen(
        ["ollama", "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)


def setup_ollama(mode: str):
    """Ensure Ollama is running in the requested mode (gpu|cpu)."""
    want_gpu = (mode == "gpu")
    if _ollama_running():
        _stop_ollama()
    _start_ollama(gpu=want_gpu)


# ── Profile loader ─────────────────────────────────────────────────────────

def load_profile(name: str | None = None) -> tuple[str, dict]:
    with open(PROFILES, "rb") as f:
        config = tomllib.load(f)

    active = name or config.get("active", "stable")
    
    if active == "balanced":
        return "balanced", {
            "description": "Auto-adjusts training dimensions to fit alongside running Ollama models",
            "vram_target_gb": 10.0,
            "ollama_mode": "gpu",
            "DEPTH": 4,           # Overridden dynamically in main()
            "model_dim": 256,
            "HEAD_DIM": 128,
            "ASPECT_RATIO": 64,
            "DEVICE_BATCH_SIZE": 16,
            "TOTAL_BATCH_SIZE": 65536,
            "MATRIX_LR": 0.04,
            "SCALAR_LR": 0.85,
            "WEIGHT_DECAY": 0.05,
            "WARMDOWN_RATIO": 0.5,
        }
        
    profiles = config.get("profiles", {})

    if active not in profiles:
        available = ", ".join(list(profiles.keys()) + ["balanced"])
        sys.exit(f"Unknown profile '{active}'. Available: {available}")

    return active, profiles[active]


def list_profiles():
    with open(PROFILES, "rb") as f:
        config = tomllib.load(f)
    active = config.get("active", "stable")
    print("Available profiles:")
    print(f"  {'balanced':<12} {'Auto-adjusts training to fit alongside active Ollama models':<55} VRAM ≤10.0GB" + (" ← active" if active == "balanced" else ""))
    for name, p in config["profiles"].items():
        marker = " ← active" if name == active else ""
        vram = p.get("vram_target_gb", "?")
        print(f"  {name:<12} {p['description']:<55} VRAM ≤{vram}GB{marker}")


# ── Training env builder ───────────────────────────────────────────────────

def build_env(profile: dict) -> dict:
    """Build the environment dict for train.py."""
    env = os.environ.copy()

    # Architecture
    env["DEPTH"]        = str(profile["DEPTH"])
    env["model_dim"]    = str(profile["model_dim"])
    env["HEAD_DIM"]     = str(profile["HEAD_DIM"])
    env["ASPECT_RATIO"] = str(profile["ASPECT_RATIO"])

    # Batch / LR
    env["DEVICE_BATCH_SIZE"] = str(profile["DEVICE_BATCH_SIZE"])
    env["TOTAL_BATCH_SIZE"]  = str(profile["TOTAL_BATCH_SIZE"])
    env["MATRIX_LR"]         = str(profile["MATRIX_LR"])
    env["SCALAR_LR"]         = str(profile["SCALAR_LR"])
    env["WEIGHT_DECAY"]      = str(profile["WEIGHT_DECAY"])
    env["WARMDOWN_RATIO"]    = str(profile["WARMDOWN_RATIO"])

    # Memory extensions
    if profile.get("gradient_checkpointing"):
        env["GRADIENT_CHECKPOINTING"] = "1"

    if profile.get("cpu_offload"):
        env["CPU_OFFLOAD"] = "1"
        # Help PyTorch avoid fragmentation when splitting VRAM / system RAM
        env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:512,expandable_segments:True")

    return env


# ── OOM fallback (flat_out only) ───────────────────────────────────────────

def run_with_oom_fallback(profile_name: str, profile: dict, env: dict) -> int:
    """Run train.py; if OOM and profile has a fallback, retry with smaller dims."""
    rc = subprocess.run([sys.executable, str(TRAIN_PY)], env=env).returncode

    if rc != 0 and profile_name == "flat_out":
        fallback_depth = profile.get("oom_fallback_depth")
        fallback_dim   = profile.get("oom_fallback_model_dim")
        if fallback_depth and fallback_dim:
            print(f"\n[profile] OOM detected — retrying with DEPTH={fallback_depth} model_dim={fallback_dim}")
            env["DEPTH"]     = str(fallback_depth)
            env["model_dim"] = str(fallback_dim)
            rc = subprocess.run([sys.executable, str(TRAIN_PY)], env=env).returncode

    return rc


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Profile-aware training launcher")
    parser.add_argument("--profile", "-p", help="Profile name (micro | stable | flat_out)")
    parser.add_argument("--list",    "-l", action="store_true", help="List available profiles")
    parser.add_argument("--dry-run",       action="store_true", help="Print config without running")
    args = parser.parse_args()

    if args.list:
        list_profiles()
        return

    if not TRAIN_PY.exists():
        sys.exit(
            f"train.py not found at {TRAIN_PY}\n"
            "Place your training script there and re-run."
        )

    profile_name, profile = load_profile(args.profile)

    if profile_name == "balanced":
        vram_in_use = _get_ollama_vram_gb()
        available = 10.0 - vram_in_use
        print(f"\n[profile] BALANCED MODE: Ollama is using ~{vram_in_use:.1f}GB VRAM.")
        print(f"[profile] Available for training: ~{available:.1f}GB")
        if available >= 6.5:
            profile["DEPTH"] = 6
            profile["ASPECT_RATIO"] = 64
            profile["DEVICE_BATCH_SIZE"] = 32
            print("[profile] Auto-selected: DEPTH=6, DEVICE_BATCH_SIZE=32")
        elif available >= 4.0:
            profile["DEPTH"] = 4
            profile["ASPECT_RATIO"] = 64
            profile["DEVICE_BATCH_SIZE"] = 16
            print("[profile] Auto-selected: DEPTH=4, DEVICE_BATCH_SIZE=16")
        else:
            profile["DEPTH"] = 3
            profile["ASPECT_RATIO"] = 64
            profile["DEVICE_BATCH_SIZE"] = 8
            print("[profile] Auto-selected: DEPTH=3, DEVICE_BATCH_SIZE=8 (Aggressive VRAM saving)")
        profile["model_dim"] = profile["DEPTH"] * profile["ASPECT_RATIO"]

    print(f"\n{'='*60}")
    print(f"  Autoresearch — profile: {profile_name.upper()}")
    print(f"  {profile['description']}")
    print(f"  DEPTH={profile['DEPTH']}  model_dim={profile['model_dim']}  "
          f"HEAD_DIM={profile['HEAD_DIM']}")
    print(f"  VRAM target: ≤{profile.get('vram_target_gb', '?')}GB  "
          f"Ollama: {profile['ollama_mode'].upper()}")
    if profile.get("gradient_checkpointing"):
        print("  gradient_checkpointing: ON")
    if profile.get("cpu_offload"):
        print("  cpu_offload: ON  (optimizer states → system RAM)")
    print("="*60 + "\n")

    if args.dry_run:
        print("Dry run — not launching training.")
        return

    # Snapshot whether Ollama was already running before we touch it
    ollama_was_running = _ollama_running()

    # Manage Ollama
    setup_ollama(profile["ollama_mode"])

    # Build environment and run
    env = build_env(profile)

    def _restore_on_signal(sig, frame):
        print("\n[profile] Interrupted — restoring Ollama to GPU mode...")
        setup_ollama("gpu")
        sys.exit(1)

    signal.signal(signal.SIGINT,  _restore_on_signal)
    signal.signal(signal.SIGTERM, _restore_on_signal)

    print(f"[profile] Launching train.py...")
    rc = run_with_oom_fallback(profile_name, profile, env)

    print(f"\n[profile] Training finished (exit={rc})")

    # Restore Ollama to GPU mode after training (unless we didn't touch it)
    if profile["ollama_mode"] == "cpu":
        print("[profile] Restoring Ollama to GPU mode...")
        setup_ollama("gpu")
    elif not ollama_was_running:
        # We started it, but it wasn't running before — leave it
        pass

    sys.exit(rc)


if __name__ == "__main__":
    main()
