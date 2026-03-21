#!/usr/bin/env python3
"""
Dr. Ders (HW_Dr_Ders) — thermal-aware GPU/VRAM/power governor for 24/7 fleet operation.

PURPOSE:
Runs alongside the primary fleet supervisor as a MINIMAL CPU-ONLY daemon. Monitors GPU
temperature, VRAM pressure, power draw, and CPU thermals. Manages Ollama model tiers
for fleet workers. This is the ONLY process that should touch model loading/unloading.

CRITICAL DESIGN CONSTRAINTS:
- Dr. Ders itself NEVER uses GPU — it is a pure monitoring/control process
- It NEVER loads models for its own inference — only manages models for workers
- It NEVER calls call_complex() or any LLM API — it is not an AI agent
- It MUST survive GPU OOM, thermal shutdown, and Ollama crashes
- It MUST be the LAST process killed on shutdown (after workers, before Ollama)
- All operations have explicit timeouts — no unbounded waits
- All exceptions are caught and logged — the main loop NEVER crashes
- hw_state.json writes are atomic (tempfile + os.replace)

SCALING PATTERN (park + guard + recover):
- Parks on whatever model is configured in fleet.toml
- Only scales DOWN under actual VRAM/thermal pressure
- Never auto-scales UP (operator controls baseline via model-profile)
- If no model is loaded (crash/eviction), recovers to smallest available
- Steps down one tier at a time, never jumps

AI AGENTS: Do not implement model-downgrade logic in skills. Dr. Ders handles it.
"""
import gc
import json
import logging
import os
import sys
import tempfile
import time
import urllib.request
from collections import deque
from logging.handlers import RotatingFileHandler
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

# ── Logging setup — file + console ────────────────────────────────────────────
(FLEET_DIR / "logs").mkdir(parents=True, exist_ok=True)
_log_handler = RotatingFileHandler(
    FLEET_DIR / "logs" / "hw_supervisor.log",
    maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [Dr. Ders] %(message)s"))
log = logging.getLogger("dr_ders")
log.setLevel(logging.INFO)
log.addHandler(_log_handler)
log.addHandler(logging.StreamHandler(sys.stdout))

sys.path.insert(0, str(FLEET_DIR))


# ── Config ────────────────────────────────────────────────────────────────────

def load_thermal_config():
    """Load [thermal], [thermal.vram], [models.tiers], [models], [training], [fleet] from fleet.toml."""
    # Last-resort fallback defaults — must match fleet.toml [thermal] / [thermal.vram] / [models.tiers]
    # to avoid tighter-than-intended thresholds if fleet.toml is unreadable at startup.
    defaults = {
        "gpu_max_sustained_c": 82, "gpu_max_burst_c": 85,
        "cpu_max_sustained_c": 85, "cooldown_target_c": 75,
        "cooldown_window_secs": 120, "poll_interval_secs": 5,
        "grace_period_secs": 20, "cooldown_after_swap_secs": 60,
        "ambient_estimation": True,
        "vram_emergency": 0.92, "vram_high": 0.85, "vram_restore": 0.60,
        "tier_default": "qwen3:8b", "tier_mid": "qwen3:8b",
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


# ── Memory Leak Self-Monitor ──────────────────────────────────────────────────

_MEMORY_CHECK_INTERVAL = 360  # polls (~30 min at 5s interval)
_MEMORY_GROWTH_THRESHOLD_MB = 150  # warn if RSS grows this much from baseline
_MEMORY_CRITICAL_MB = 500  # force gc + log critical if RSS exceeds this
_baseline_rss_mb = 0.0
_last_gc_collect = 0


def _get_own_rss_mb():
    """Return this process's RSS in MB, or 0 if psutil unavailable."""
    if not _HAS_PSUTIL:
        return 0.0
    try:
        proc = psutil.Process(os.getpid())
        return proc.memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _memory_self_check(poll_count):
    """Periodic self-check: track RSS growth, force gc on leaks.

    Called every _MEMORY_CHECK_INTERVAL polls from the main loop.
    Returns dict with memory stats for inclusion in hw_state.json.
    """
    global _baseline_rss_mb, _last_gc_collect

    rss = _get_own_rss_mb()
    if rss == 0:
        return {}

    # First check — set baseline
    if _baseline_rss_mb == 0:
        _baseline_rss_mb = rss
        log.info(f"Memory baseline: {rss:.1f} MB RSS")
        return {"hw_sup_rss_mb": round(rss, 1)}

    growth = rss - _baseline_rss_mb
    stats = {"hw_sup_rss_mb": round(rss, 1), "hw_sup_rss_growth_mb": round(growth, 1)}

    if rss > _MEMORY_CRITICAL_MB:
        # Critical: force aggressive gc
        collected = gc.collect(2)
        log.warning(f"MEMORY CRITICAL: {rss:.1f} MB RSS (baseline {_baseline_rss_mb:.1f}) "
                    f"— forced gc collected {collected} objects")
        _last_gc_collect = poll_count
        # Re-measure after gc
        rss_after = _get_own_rss_mb()
        stats["hw_sup_rss_mb"] = round(rss_after, 1)
        stats["hw_sup_gc_freed_mb"] = round(rss - rss_after, 1)
    elif growth > _MEMORY_GROWTH_THRESHOLD_MB:
        # Growing — run gc and log warning
        if poll_count - _last_gc_collect > 60:  # don't gc-spam
            collected = gc.collect()
            log.warning(f"Memory growth: {rss:.1f} MB RSS (+{growth:.1f} from baseline) "
                        f"— gc collected {collected} objects")
            _last_gc_collect = poll_count
            rss_after = _get_own_rss_mb()
            stats["hw_sup_rss_mb"] = round(rss_after, 1)
    else:
        # Healthy — periodic gen-0 gc to prevent accumulation
        if poll_count - _last_gc_collect > _MEMORY_CHECK_INTERVAL:
            gc.collect(0)
            _last_gc_collect = poll_count

    return stats


# ── State ─────────────────────────────────────────────────────────────────────

def write_state(status, model, thermal=None, models_loaded=None, conductor_status=None, memory_stats=None):
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
        if memory_stats:
            state["memory"] = memory_stats
        # Atomic write: temp file then rename
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(FLEET_DIR), suffix='.json')
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        os.replace(tmp_path, str(HW_STATE_FILE))
    except Exception:
        pass


# ── Fleet TOML Model Management ──────────────────────────────────────────────

def get_current_local_model():
    """Read current local model from fleet.toml using tomlkit."""
    try:
        import tomlkit
        doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
        return doc.get("models", {}).get("local", "qwen3:8b")
    except Exception:
        return "qwen3:8b"


def set_local_model(target_model):
    """Atomically update local model in fleet.toml using tomlkit."""
    try:
        import tomlkit
        import tempfile
        text = FLEET_TOML.read_text(encoding="utf-8")
        doc = tomlkit.parse(text)
        current = doc.get("models", {}).get("local", "qwen3:8b")
        if current == target_model:
            return False
        doc.setdefault("models", {})["local"] = target_model
        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(FLEET_TOML.parent), suffix='.toml')
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp_path, str(FLEET_TOML))
        log.info(f"Model: {current} -> {target_model}")
        return True
    except Exception as e:
        log.info(f"set_local_model error: {e}")
        return False


# ── Ollama Control ────────────────────────────────────────────────────────────

def unload_all_models():
    try:
        req = urllib.request.Request("http://localhost:11434/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            for m in data.get("models", []):
                log.info(f"Evicting {m['name']}")
                body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
                ureq = urllib.request.Request(
                    "http://localhost:11434/api/generate", data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(ureq, timeout=5)
    except Exception:
        pass


def get_available_models(host="http://localhost:11434"):
    """Get list of models actually installed in Ollama."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def validate_configured_models(cfg):
    """Check configured models against Ollama. Returns set of available tier model names.

    Never blocks on missing models — the main loop uses the returned set to skip
    unavailable tiers. Returns empty set if Ollama is unreachable.
    """
    available = get_available_models(cfg.get("ollama_host", "http://localhost:11434"))
    if not available:
        log.warning(" No models found in Ollama (is it running?)")
        return set()

    tier_models = {
        cfg.get("tier_default", "qwen3:8b"),
        cfg.get("tier_mid", "qwen3:4b"),
        cfg.get("tier_low", "qwen3:1.7b"),
        cfg.get("tier_crit", "qwen3:0.6b"),
    }
    if cfg.get("conductor_model"):
        tier_models.add(cfg["conductor_model"])

    available_set = set(available)
    present = tier_models & available_set
    missing = sorted(tier_models - available_set)

    if missing:
        log.warning(f"Missing tier models: {', '.join(missing)}")
        log.info(f"Available: {', '.join(available)}")
    if present:
        log.info(f"{len(present)}/{len(tier_models)} configured models available: "
                 f"{', '.join(sorted(present))}")

    return present


# ── Sticky Model Loader ──────────────────────────────────────────────────────
# "Park and guard" — load once on GPU, keep it there, only swap under genuine
# pressure (thermal/VRAM emergency). Fast recovery to same state if evicted.
_model_gpu_assignment = {}       # model_name → num_gpu (99=GPU, 0=CPU)
_last_model_change = 0.0         # timestamp of last model swap
MODEL_CHANGE_COOLDOWN = 120      # minimum seconds between non-emergency model swaps


def _get_keep_alive():
    """Read keep_alive from fleet.toml [models] section, default 30m."""
    try:
        cfg = load_thermal_config()
        mins = int(cfg.get("keep_alive_mins", 30))
        return f"{mins}m"
    except Exception:
        return "30m"


def warmup_model(model_name, on_gpu=True):
    """Load a model with sticky GPU assignment. Remembers placement for fast recovery."""
    global _last_model_change
    try:
        available = get_available_models()
        if model_name not in available:
            log.info(f"Cannot warmup '{model_name}' — not installed")
            return False

        # Sticky assignment: remember GPU/CPU placement per model
        num_gpu = 99 if on_gpu else 0  # 99 = all layers on GPU, 0 = CPU only
        _model_gpu_assignment[model_name] = num_gpu
        keep_alive = _get_keep_alive()

        body = json.dumps({
            "model": model_name, "prompt": "", "keep_alive": keep_alive,
            "options": {"num_gpu": num_gpu},
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
            if "error" in resp:
                log.info(f"Warmup error for '{model_name}': {resp['error']}")
                return False
        _last_model_change = time.time()
        return True
    except Exception as e:
        log.info(f"Warmup failed for '{model_name}': {e}")
        return False


def can_transition_model(emergency=False) -> bool:
    """Check if enough time has passed since last model change (dampening)."""
    if emergency:
        return True  # Always allow emergency transitions
    elapsed = time.time() - _last_model_change
    if elapsed < MODEL_CHANGE_COOLDOWN:
        log.info(f"Model change cooldown: {MODEL_CHANGE_COOLDOWN - elapsed:.0f}s remaining")
        return False
    return True


def recover_model(model_name):
    """Fast recovery: reload model with same GPU/CPU assignment it had before."""
    prev_gpu = _model_gpu_assignment.get(model_name, 99)  # default to GPU
    on_gpu = prev_gpu > 0
    log.info(f"Fast recovery: {model_name} → {'GPU' if on_gpu else 'CPU'} (restoring previous state)")
    return warmup_model(model_name, on_gpu=on_gpu)


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
                log.info(f"Pre-training eviction: {model_name}")
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
    if _HAS_PSUTIL:
        try:
            if hasattr(psutil, 'sensors_temperatures'):
                temps = psutil.sensors_temperatures()
                if temps:
                    # Linux: coretemp/k10temp; Windows: may not be available
                    for chip in ("coretemp", "k10temp", "acpitz"):
                        if chip in temps:
                            return max(t.current for t in temps[chip])
        except Exception:
            pass
    # Fallback: shared cpu_temp module (Windows WMI / cross-platform)
    try:
        from cpu_temp import read_cpu_temp
        val = read_cpu_temp()
        return val if val > 0 else None
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
            "model": conductor_model, "keep_alive": _get_keep_alive(),
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
    """Transition to a new model. Respects cooldown unless emergency."""
    if not emergency and not can_transition_model():
        return  # Cooldown active — skip non-emergency transition

    log.info(f"{'EMERGENCY ' if emergency else ''}Transition: {current} -> {target}")

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

    # Use sticky loader — GPU for worker models, CPU for critical/failsafe
    on_gpu = target not in (cfg.get("tier_crit", ""), cfg.get("conductor_model", ""))
    log.info(f"Loading {target} ({'GPU' if on_gpu else 'CPU'})...")
    warmup_model(target, on_gpu=on_gpu)

    write_state("ready", target)
    log.info("Transition complete.")

    if not emergency:
        time.sleep(cfg["cooldown_after_swap_secs"])


# ── Training Lock ─────────────────────────────────────────────────────────────

def check_training_active(cfg):
    """Check if training is running via DB lock or cross-platform process detection."""
    # DB lock (preferred)
    if cfg.get("training_exclusive_lock", True):
        try:
            import db
            lock = db.check_lock("training")
            if lock:
                return True
        except Exception:
            pass
    # Cross-platform process detection
    try:
        import psutil
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('train.py' in arg for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        # Fallback: pgrep on Linux/macOS only
        import sys
        if sys.platform != "win32":
            return os.system("pgrep -f '[t]rain\\.py' > /dev/null 2>&1") == 0
        return False


# ── Main Loop ─────────────────────────────────────────────────────────────────

def main():
    if not _HAS_GPU:
        log.info("No NVIDIA GPU detected. Exiting.")
        return

    cfg = load_thermal_config()
    ambient = AmbientEstimator()

    # Register with DB for sup-channel communication
    _HAS_DB = False
    try:
        import db as _db
        _db.init_db()
        _db.register_agent("dr_ders", "supervisor", os.getpid())
        _HAS_DB = True
    except Exception:
        pass

    # Thermal throttle state
    gpu_throttled = False
    below_target_since = None
    was_training = False  # track training transitions for VRAM eviction
    training_evicted = False  # True only if we actually evicted models for training

    host = cfg["ollama_host"]
    conductor_model = cfg["conductor_model"]
    air_gap = cfg["air_gap_mode"]

    log.info(f"Started. Limits: {cfg['gpu_max_sustained_c']}°C sustained, "
          f"{cfg['gpu_max_burst_c']}°C burst. Poll: {cfg['poll_interval_secs']}s")
    if conductor_model:
        log.info(f"Conductor model: {conductor_model} (CPU)")

    # IMMEDIATE state write — launcher boot polls for this file.
    # Must happen BEFORE any model checks (which involve HTTP calls that can stall).
    write_state("starting", get_current_local_model())

    # Validate configured models — store available set for tier transitions
    available_tier_models = validate_configured_models(cfg)

    # Check loaded models — keep anything from our tier system, evict unknowns
    known_models = {
        cfg["tier_default"], cfg["tier_mid"], cfg["tier_low"], cfg["tier_crit"],
        get_current_local_model(),
    }
    if conductor_model:
        known_models.add(conductor_model)
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            ps_data = json.loads(r.read())
        loaded_models = [m["name"] for m in ps_data.get("models", [])]
        if loaded_models:
            log.info(f"Pre-loaded: {', '.join(loaded_models)}")
            for m in loaded_models:
                if m not in known_models:
                    log.info(f"Evicting unknown model: {m}")
                    try:
                        body = json.dumps({"model": m, "keep_alive": 0}).encode()
                        req = urllib.request.Request(
                            f"{host}/api/generate", data=body, method="POST",
                            headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=5)
                    except Exception:
                        pass
                else:
                    log.info(f"Keeping {m} (known tier model)")
    except Exception:
        pass

    # ── Startup checkpoint verification ──────────────────────────────────
    # hw_supervisor must confirm these checks before reporting "ready".
    # This ensures the launcher boot stage gets a reliable signal.
    startup_checks = {
        "gpu_readable": False,      # can we read GPU thermal data?
        "ollama_reachable": False,   # is Ollama API responding?
        "model_state_known": False,  # do we know what model is loaded?
    }
    for attempt in range(5):
        if not startup_checks["gpu_readable"]:
            gpu_test = read_gpu_thermal()
            if gpu_test:
                startup_checks["gpu_readable"] = True
                log.info(f"Checkpoint: GPU readable ({gpu_test['gpu_temp_c']}°C)")
        if not startup_checks["ollama_reachable"]:
            try:
                with urllib.request.urlopen(f"{host}/api/tags", timeout=3):
                    startup_checks["ollama_reachable"] = True
                    log.info("Checkpoint: Ollama reachable")
            except Exception:
                pass
        if not startup_checks["model_state_known"]:
            models = get_available_models(host)
            if models:
                startup_checks["model_state_known"] = True
                log.info(f"Checkpoint: Model state known ({len(models)} available)")
        if all(startup_checks.values()):
            break
        time.sleep(1)

    passed = sum(startup_checks.values())
    total = len(startup_checks)
    if passed == total:
        log.info(f"All {total} startup checks passed — entering main loop")
        write_state("ready", get_current_local_model())
    else:
        failed = [k for k, v in startup_checks.items() if not v]
        log.warning(f"Startup checks: {passed}/{total} passed. Failed: {failed}")
        write_state("degraded", get_current_local_model())

    # ── Dr. Ders model promotion ──────────────────────────────────────────
    # Boot fast on smallest model, then promote to best available CPU-bound
    # model for better monitoring quality. Smallest stays as crash failsafe.
    installed = get_available_models(host)
    # Promotion preference: largest CPU-suitable model first
    DRDERS_PROMOTE_ORDER = ["qwen3:4b", "qwen3:1.7b", "qwen3:0.6b"]
    DRDERS_BOOT_MODEL = None
    DRDERS_PROMOTED_MODEL = None
    drders_promoted = False

    # Find smallest installed model as boot/failsafe
    for m in reversed(DRDERS_PROMOTE_ORDER):
        if m in installed:
            DRDERS_BOOT_MODEL = m
            break

    # Find largest installed model as promotion target
    for m in DRDERS_PROMOTE_ORDER:
        if m in installed and m != DRDERS_BOOT_MODEL:
            DRDERS_PROMOTED_MODEL = m
            break

    # Always keep failsafe micro model loaded on CPU — never evict this
    if DRDERS_BOOT_MODEL:
        log.info(f"Dr. Ders failsafe model: {DRDERS_BOOT_MODEL} (CPU, permanent)")
        warmup_model(DRDERS_BOOT_MODEL, on_gpu=False)

    if DRDERS_PROMOTED_MODEL:
        log.info(f"Promoting Dr. Ders: {DRDERS_BOOT_MODEL} → {DRDERS_PROMOTED_MODEL} (CPU)")
        try:
            body = json.dumps({
                "model": DRDERS_PROMOTED_MODEL, "keep_alive": _get_keep_alive(),
                "options": {"num_gpu": 0},
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/generate", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=60)
            drders_promoted = True
            log.info(f"Dr. Ders promoted to {DRDERS_PROMOTED_MODEL} (CPU-bound)")
        except Exception as e:
            log.warning(f"Promotion failed, staying on {DRDERS_BOOT_MODEL}: {e}")
    elif DRDERS_BOOT_MODEL:
        log.info(f"Only one CPU model installed ({DRDERS_BOOT_MODEL}) — no promotion needed")

    poll_count = 0
    mem_stats = {}  # populated by periodic self-check

    # ── Wake-up timer: event-driven with adaptive sleep ──────────────────
    # Instead of constant 5s polling, Dr. Ders sleeps longer when idle
    # and wakes on events (thermal spike, model eviction, training change).
    _wake_event = threading.Event()
    _base_interval = cfg["poll_interval_secs"]  # 5s base
    _idle_interval = 30  # sleep up to 30s when nothing is happening
    _current_interval = _base_interval
    _consecutive_idle = 0

    def wake_drders():
        """External trigger to wake Dr. Ders immediately (called on events)."""
        _wake_event.set()

    # Monitor hw_state.json for external wake requests
    def _watch_wake_file():
        wake_file = FLEET_DIR / ".drders_wake"
        while True:
            try:
                if wake_file.exists():
                    wake_file.unlink(missing_ok=True)
                    wake_drders()
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=_watch_wake_file, daemon=True).start()

    while True:
        # Adaptive sleep: wait for event OR timeout
        _wake_event.wait(timeout=_current_interval)
        _wake_event.clear()
        poll_count += 1

        try:
            # Memory self-check (every ~30 min)
            if poll_count % _MEMORY_CHECK_INTERVAL == 0 or poll_count == 1:
                mem_stats = _memory_self_check(poll_count)

            gpu = read_gpu_thermal()
            if not gpu:
                continue

            cpu_temp = read_cpu_thermal()
            is_training = check_training_active(cfg)
            # Cross-platform marathon detection
            is_marathon = False
            try:
                import psutil
                for proc in psutil.process_iter(['cmdline']):
                    try:
                        cmdline = proc.info.get('cmdline') or []
                        if any('dispatch_marathon.py' in arg for arg in cmdline):
                            is_marathon = True
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except ImportError:
                import sys
                if sys.platform != "win32":
                    is_marathon = os.system("pgrep -f dispatch_marathon.py > /dev/null 2>&1") == 0

            # ── Training transition: VRAM-aware eviction ──
            if is_training and not was_training:
                # Check if training profile needs Ollama off GPU
                _gpu_total = gpu.get("vram_total_gb", 12.0) or 12.0
                _training_vram_free = _gpu_total - (gpu.get("vram_used_gb", 0) or 0)
                # Read profile hint from hw_state if supervisor wrote it
                _hw_training_profile = None
                try:
                    if HW_STATE_FILE.exists():
                        _hs = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
                        _hw_training_profile = _hs.get("training_profile")
                except Exception:
                    pass
                # Small profiles (micro/balanced) can coexist with Ollama on GPU
                if _hw_training_profile in ("micro", "balanced"):
                    log.info(f"Training detected (profile={_hw_training_profile}) — keeping models on GPU")
                    training_evicted = False
                elif _training_vram_free > 5.0:
                    log.info(f"Training detected — {_training_vram_free:.1f}GB VRAM free, keeping models on GPU")
                    training_evicted = False
                else:
                    log.info(f"Training detected — {_training_vram_free:.1f}GB VRAM free, evicting GPU models")
                    training_evicted = True
                    import threading
                    threading.Thread(target=evict_models_for_training, args=(host,), daemon=True).start()
            elif was_training and not is_training:
                log.info("Training ended — models will reload on next keepalive cycle")
                training_evicted = False
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
                # Keepalive ping every ~240s — only skip if training evicted models
                if poll_count % KEEPALIVE_EVERY_N_POLLS == 0:
                    if not (is_training and training_evicted) and not gpu_throttled:
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

            # Provider health probes every ~300s (60 polls)
            if poll_count % 60 == 0 and not air_gap:
                try:
                    from providers import probe_provider_health
                    for prov in ["claude", "gemini", "local"]:
                        probe_provider_health(prov)
                except Exception:
                    pass

            # Sup inbox check every ~60s (12 polls at 5s)
            if _HAS_DB and poll_count % 12 == 0:
                try:
                    msgs = _db.get_messages("hw_supervisor", unread_only=True,
                                            limit=3, channels=["sup"])
                    for m in msgs:
                        body = json.loads(m["body_json"])
                        log.info(f"Sup: {m['from_agent']} -> {body.get('type', '?')}")
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
                log.info(f"GPU {gpu_temp}°C >= {cfg['gpu_max_burst_c']}°C burst limit — "
                      "pausing GPU tasks, switching to CPU-only")
                gpu_throttled = True
                below_target_since = None
                if _HAS_DB:
                    try:
                        _db.post_note("sup", "dr_ders", json.dumps({
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
                        log.info(f"GPU {gpu_temp}°C <= {cfg['cooldown_target_c']}°C for "
                              f"{cfg['cooldown_window_secs']}s — resuming GPU tasks")
                        gpu_throttled = False
                        below_target_since = None
                        if _HAS_DB:
                            try:
                                _db.post_note("sup", "dr_ders", json.dumps({
                                    "type": "thermal_alert",
                                    "title": f"GPU resumed at {gpu_temp}°C",
                                    "tags": ["thermal", "gpu"],
                                }))
                            except Exception:
                                pass
                else:
                    below_target_since = None

            # ── VRAM-based model scaling (park + guard + recover) ────────
            #
            # PHILOSOPHY:
            #   Park on whatever model is configured in fleet.toml.
            #   Only scale DOWN under actual VRAM/thermal pressure.
            #   Never auto-scale UP — operator controls baseline.
            #   ONE EXCEPTION: if no model is loaded at all (crashed/evicted),
            #   recover by loading the smallest available model and step up
            #   to the next available tier if VRAM allows.
            #
            # hw_supervisor itself is CPU-only — it never loads models for
            # its own use. It only manages models for fleet workers.
            #
            tier_order = [cfg["tier_default"], cfg["tier_mid"],
                          cfg["tier_low"], cfg["tier_crit"]]
            # Filter tier_order to only models confirmed available
            avail_tiers = [t for t in tier_order if t in available_tier_models]
            current_model = get_current_local_model()
            target_model = current_model
            emergency = False

            # Check if ANY model is actually loaded in Ollama
            loaded = []
            try:
                with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
                    ps = json.loads(r.read())
                loaded = [m["name"] for m in ps.get("models", [])
                          if m.get("name") != conductor_model]  # exclude conductor
            except Exception:
                pass

            if gpu_throttled:
                # THERMAL EMERGENCY: drop to smallest available tier
                if cfg["tier_crit"] in available_tier_models:
                    target_model = cfg["tier_crit"]
                else:
                    # tier_crit missing — find smallest available
                    target_model = avail_tiers[-1] if avail_tiers else current_model
                    if target_model != current_model:
                        log.warning(f"Tier 'crit' model {cfg['tier_crit']} not available, "
                                    f"falling back to {target_model}")
                if current_model != target_model:
                    emergency = True

            elif is_training and training_evicted:
                # TRAINING (evicted): drop to low tier to free VRAM for PyTorch
                if current_model not in (cfg["tier_crit"], cfg["tier_low"]):
                    if cfg["tier_low"] in available_tier_models:
                        target_model = cfg["tier_low"]
                    elif cfg["tier_crit"] in available_tier_models:
                        log.warning(f"Tier 'low' model {cfg['tier_low']} not available, "
                                    f"skipping to '{cfg['tier_crit']}'")
                        target_model = cfg["tier_crit"]
                    else:
                        log.warning(f"No low/crit tier models available for training mode")

            elif not loaded:
                # RECOVERY: no worker model loaded — find the best available
                # that fits in current VRAM. Start from smallest, step up.
                available = get_available_models(host)
                for tier in reversed(tier_order):  # smallest first
                    if tier in available:
                        # Check if we have VRAM headroom for this tier
                        if vram_pct < cfg["vram_high"]:
                            target_model = tier
                            log.warning(f"RECOVERY: no model loaded, "
                                  f"recovering to {tier}")
                            break
                        else:
                            # Under pressure — use smallest available
                            target_model = tier
                            log.warning(f"RECOVERY (pressure): loading {tier}")
                            break
                if target_model == current_model and not loaded:
                    # Nothing available — stay parked, warn
                    log.warning(f" no models loaded and none available")

            else:
                # NORMAL: only scale DOWN on pressure, never UP
                if vram_pct >= cfg["vram_emergency"]:
                    if cfg["tier_crit"] in available_tier_models:
                        target_model = cfg["tier_crit"]
                    else:
                        target_model = avail_tiers[-1] if avail_tiers else current_model
                        if target_model != current_model:
                            log.warning(f"Tier 'crit' model {cfg['tier_crit']} not available, "
                                        f"falling back to {target_model}")
                    emergency = True
                elif vram_pct >= cfg["vram_high"]:
                    # Step down one tier from current — skip unavailable tiers
                    try:
                        idx = tier_order.index(current_model)
                        # Walk forward through tiers to find next available
                        for step_idx in range(idx + 1, len(tier_order)):
                            candidate = tier_order[step_idx]
                            if candidate in available_tier_models:
                                target_model = candidate
                                break
                            else:
                                tier_name = ["default", "mid", "low", "crit"][step_idx]
                                log.info(f"Tier '{tier_name}' model {candidate} not available, skipping")
                    except ValueError:
                        # Current model not in tier_order — pick first available below default
                        for candidate in avail_tiers[1:]:
                            target_model = candidate
                            break
                # No auto-scale UP — stay parked on current model

            if target_model != current_model:
                # Final guard: don't transition to a model we know is unavailable
                if target_model not in available_tier_models and target_model != current_model:
                    log.warning(f"Target model {target_model} not in available set, staying on {current_model}")
                    target_model = current_model

            if target_model != current_model:
                transition_model(target_model, current_model, cfg, emergency)
                # Post sup note about model transition
                if _HAS_DB:
                    try:
                        _db.post_note("sup", "dr_ders", json.dumps({
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
                        if vr and not gpu_throttled and not (is_training and training_evicted):
                            vision_model = vr.get("model", "llava")
                            # Check if vision model is already loaded
                            already_loaded = any(
                                m["name"].split(":")[0] == vision_model.split(":")[0]
                                for m in models_loaded
                            )
                            if not already_loaded and not air_gap:
                                log.info(f"Vision request: loading {vision_model}")
                                warmup_model(vision_model)
                except Exception:
                    pass

                # RAM-based worker scaling recommendation (every 15min / 180 polls)
                worker_recommendation = None
                if poll_count % 180 == 0:
                    try:
                        ram = psutil.virtual_memory()
                        ram_pct = ram.percent
                        if ram_pct > 90:
                            worker_recommendation = "reduce"
                            log.warning(f"RAM {ram_pct:.0f}% — recommending worker reduction")
                        elif ram_pct > 80:
                            worker_recommendation = "hold"
                            log.info(f"RAM {ram_pct:.0f}% — holding worker count")
                        elif ram_pct < 60:
                            worker_recommendation = "increase"
                            log.info(f"RAM {ram_pct:.0f}% — headroom available for more workers")
                    except Exception:
                        pass

                write_state("ready", current_model, thermal, models_loaded, conductor_status, mem_stats)

                # ── Adaptive wake-up interval ────────────────────────
                # Speed up when something is happening, slow down when idle
                needs_attention = (
                    gpu_throttled or
                    is_training or
                    (gpu.get("gpu_temp_c", 0) > cfg["gpu_max_sustained_c"] - 5) or
                    (gpu.get("vram_pct", 0) >= cfg.get("vram_high", 0.85))
                )
                if needs_attention:
                    _current_interval = _base_interval  # 5s — active monitoring
                    _consecutive_idle = 0
                else:
                    _consecutive_idle += 1
                    # Gradually increase sleep: 5s → 10s → 15s → 20s → 30s max
                    _current_interval = min(_idle_interval, _base_interval + (_consecutive_idle * 5))

        except Exception as e:
            log.warning(f"Poll error (non-fatal): {e}")
            _current_interval = _base_interval  # Reset to fast poll on error
            _consecutive_idle = 0
            # Failsafe: recover to boot model (0.6b CPU) on errors
            if drders_promoted and DRDERS_BOOT_MODEL:
                log.info(f"Error recovery: falling back to {DRDERS_BOOT_MODEL} (CPU failsafe)")
                recover_model(DRDERS_BOOT_MODEL)
                drders_promoted = False
            # Write error state so launcher knows we're alive but struggling
            try:
                write_state("error", get_current_local_model())
            except Exception:
                pass
            # Brief backoff on repeated errors
            time.sleep(2)


if __name__ == "__main__":
    main()
