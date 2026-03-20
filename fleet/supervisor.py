#!/usr/bin/env python3
"""Fleet supervisor — starts workers, monitors health, manages GPU/training handoff."""

import gc
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db
from config import load_config, is_offline, is_air_gap
from marathon import is_training_running, _check_training_checkpoints, _evict_gpu_models, training_needs_eviction


def _load_secrets():
    """Source ~/.secrets into env so workers inherit API keys."""
    secrets = Path.home() / ".secrets"
    if not secrets.exists():
        return
    for line in secrets.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

(FLEET_DIR / "logs").mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
_sup_handler = RotatingFileHandler(
    FLEET_DIR / "logs" / "supervisor.log",
    maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_sup_handler.setFormatter(logging.Formatter("%(asctime)s [SUPERVISOR] %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    handlers=[_sup_handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("supervisor")

import json as _json

def _json_log(level, event, **kwargs):
    """Structured JSON log line for fleet processes (0.22.00 observability)."""
    import time as _t
    entry = {"ts": _t.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(_json.dumps(entry), flush=True)

BASE_ROLES = ["researcher", "coder", "archivist", "analyst", "sales", "onboarding", "implementation", "security", "planner", "legal", "account_manager"]
PYTHON = sys.executable


def _build_roles(config):
    """Expand BASE_ROLES, replacing 'coder' with coder_1..coder_N and filtering disabled agents."""
    disabled = set(config.get("fleet", {}).get("disabled_agents", []))
    roles = []
    for r in BASE_ROLES:
        if r in disabled:
            continue
        if r == "coder":
            n = max(1, int(config.get("workers", {}).get("coder_count", 1)))
            roles.extend(f"coder_{i}" for i in range(1, n + 1))
        else:
            roles.append(r)
    return roles

ollama_proc = None
discord_proc = None
openclaw_proc = None
dashboard_proc = None
worker_procs = {}
training_active = False
ollama_evicted_for_training = False


def _find_ollama() -> str:
    """Find the ollama executable — PATH, Windows default, or WSL."""
    import shutil
    path = shutil.which("ollama")
    if path:
        return path
    # Windows: check default install location
    if sys.platform == "win32":
        win_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if win_path.exists():
            return str(win_path)
    return "ollama"  # fallback — let subprocess try PATH


def _find_running_ollama() -> bool:
    """Check if Ollama is already running (any process, not just ours)."""
    try:
        config = load_config()
        host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
        import urllib.request
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2):
            return True
    except Exception:
        return False


def _discover_loaded_models() -> list[str]:
    """Query Ollama for currently loaded models (from any session)."""
    try:
        config = load_config()
        host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
        import urllib.request
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        models = [m["name"] for m in data.get("models", [])]
        if models:
            log.info(f"Discovered loaded models: {', '.join(models)}")
        return models
    except Exception:
        return []


def start_ollama(gpu=False):
    global ollama_proc
    # If already running (from previous session, system tray, etc.) — adopt it
    if _find_running_ollama():
        loaded = _discover_loaded_models()
        log.info(f"Ollama already running — adopting ({len(loaded)} models loaded)")
        _json_log("INFO", "ollama_adopt", models_loaded=len(loaded))
        return

    ollama_exe = _find_ollama()
    env = os.environ.copy()
    if not gpu:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    elif "CUDA_VISIBLE_DEVICES" in env:
        del env["CUDA_VISIBLE_DEVICES"]
    mode = "GPU" if gpu else "CPU"
    log.info(f"Starting Ollama ({mode} mode) — {ollama_exe}")
    _json_log("INFO", "ollama_start", mode=mode, exe=ollama_exe)
    try:
        ollama_proc = subprocess.Popen(
            [ollama_exe, "serve"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        time.sleep(3)
    except FileNotFoundError:
        log.error(f"Ollama not found at '{ollama_exe}' — install from https://ollama.com")
        _json_log("ERROR", "ollama_not_found", exe=ollama_exe)
        ollama_proc = None


def stop_ollama():
    global ollama_proc
    if ollama_proc and ollama_proc.poll() is None:
        log.info("Stopping Ollama (fleet-started)")
        _json_log("INFO", "ollama_stop")
        ollama_proc.terminate()
        try:
            ollama_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ollama_proc.kill()
    elif not ollama_proc:
        # We adopted an external Ollama — don't kill it, but unload our models
        log.info("External Ollama — unloading fleet models only (not stopping process)")
        try:
            from hw_supervisor import unload_all_models
            unload_all_models()
        except Exception:
            pass
    ollama_proc = None


def start_discord_bot(config):
    global discord_proc
    if not config["fleet"].get("discord_bot_enabled", True):
        log.info("Discord bot disabled in fleet.toml")
        return
    if not os.environ.get("DISCORD_BOT_TOKEN"):
        log.info("DISCORD_BOT_TOKEN not set — Discord bot disabled")
        return
    log.info("Starting Discord bot")
    discord_proc = subprocess.Popen(
        [PYTHON, str(FLEET_DIR / "discord_bot.py")],
        cwd=str(FLEET_DIR),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def stop_discord_bot():
    global discord_proc
    if discord_proc and discord_proc.poll() is None:
        log.info("Stopping Discord bot")
        discord_proc.terminate()
        try:
            discord_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            discord_proc.kill()
    discord_proc = None


def start_openclaw(config):
    global openclaw_proc
    if not config["fleet"].get("openclaw_enabled", False):
        log.info("OpenClaw disabled in fleet.toml (set openclaw_enabled=true to enable)")
        return
    port = config.get("openclaw", {}).get("port", 18789)
    log.info(f"Starting OpenClaw gateway on port {port}")
    openclaw_proc = subprocess.Popen(
        ["openclaw", "gateway", "--port", str(port)],
        cwd=str(FLEET_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def stop_openclaw():
    global openclaw_proc
    if openclaw_proc and openclaw_proc.poll() is None:
        log.info("Stopping OpenClaw gateway")
        openclaw_proc.terminate()
        try:
            openclaw_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            openclaw_proc.kill()
    openclaw_proc = None


def start_dashboard(config):
    global dashboard_proc
    if not config.get("dashboard", {}).get("enabled", False):
        log.info("Dashboard disabled in fleet.toml")
        return
    dash_cfg = config.get("dashboard", {})
    port = dash_cfg.get("port", 5555)
    host = dash_cfg.get("bind_address", "127.0.0.1")
    log.info(f"Starting dashboard on http://{host}:{port}")
    dashboard_proc = subprocess.Popen(
        [PYTHON, str(FLEET_DIR / "dashboard.py"), "--port", str(port), "--host", host],
        cwd=str(FLEET_DIR),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def stop_dashboard():
    global dashboard_proc
    if dashboard_proc and dashboard_proc.poll() is None:
        log.info("Stopping dashboard")
        dashboard_proc.terminate()
        try:
            dashboard_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dashboard_proc.kill()
    dashboard_proc = None


def start_worker(role, config):
    cmd = [PYTHON, str(FLEET_DIR / "worker.py"), "--role", role]

    # Unix-only: nice + cpulimit for resource control
    if sys.platform != "win32":
        import shutil
        nice = config["workers"].get("nice_level", 10)
        cpu_limit = config["workers"].get("cpu_limit_percent", 80)
        cmd = ["nice", f"-n{nice}"] + cmd
        if shutil.which("cpulimit"):
            cmd = ["cpulimit", f"--limit={cpu_limit}", "--"] + cmd

    log.info(f"Starting worker: {role}")
    worker_procs[role] = subprocess.Popen(
        cmd, cwd=str(FLEET_DIR),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )

    # 0.07.00: Apply resource limits
    memory_limit = config.get("workers", {}).get("memory_limit_mb", 0)
    if memory_limit > 0:
        _apply_resource_limits(worker_procs[role], memory_limit)


def _apply_resource_limits(proc, memory_limit_mb):
    """Apply OS-level resource limits to a worker process."""
    import sys
    try:
        if sys.platform == "linux":
            # Linux: use cgroups v2 or resource module
            import resource
            # Set soft + hard memory limit (bytes)
            limit_bytes = memory_limit_mb * 1024 * 1024
            # Note: resource.setrlimit only works on current process
            # For child processes, we'd need cgroups. Log the intent.
            log.info(f"Worker {proc.pid}: memory limit {memory_limit_mb}MB (advisory — cgroups recommended)")
        elif sys.platform == "win32":
            # Windows: Job Objects
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.windll.kernel32
                job = kernel32.CreateJobObjectW(None, None)
                if job:
                    # Set memory limit via JOBOBJECT_EXTENDED_LIMIT_INFORMATION
                    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                        _fields_ = [
                            ("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD),
                        ]

                    class IO_COUNTERS(ctypes.Structure):
                        _fields_ = [("ReadOperationCount", ctypes.c_uint64)] * 6

                    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                        _fields_ = [
                            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t),
                        ]

                    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                    info.BasicLimitInformation.LimitFlags = 0x00000100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
                    info.ProcessMemoryLimit = memory_limit_mb * 1024 * 1024

                    kernel32.SetInformationJobObject(
                        job, 9,  # JobObjectExtendedLimitInformation
                        ctypes.byref(info), ctypes.sizeof(info)
                    )

                    # Assign process to job
                    handle = kernel32.OpenProcess(0x0001, False, proc.pid)  # PROCESS_TERMINATE
                    if handle:
                        kernel32.AssignProcessToJobObject(job, handle)
                        kernel32.CloseHandle(handle)
                        log.info(f"Worker {proc.pid}: Windows Job Object memory limit {memory_limit_mb}MB")
            except Exception as e:
                log.debug(f"Windows Job Object limit failed: {e}")
        else:
            log.info(f"Worker {proc.pid}: memory limit {memory_limit_mb}MB (platform: advisory only)")
    except Exception as e:
        log.debug(f"Resource limit failed for {proc.pid}: {e}")


HW_STATE_FILE = FLEET_DIR / "hw_state.json"
STALE_TASK_RECOVERY_INTERVAL = 300  # check every 5 min
STALE_TASK_TIMEOUT = 900  # 15 min with no heartbeat = stale
WATCHDOG_INTERVAL = 60  # semantic watchdog every 60s
WATCHDOG_FULL_INTERVAL = 600  # full scan (knowledge files) every 10min


def read_hw_state():
    """Read Dr. Ders state — returns dict or None."""
    try:
        if HW_STATE_FILE.exists():
            return json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug(f"[read_hw_state] failed to read hw_state.json: {e}")
    return None



def _ping_ollama_keepalive(config, keep_alive="24h"):
    """Load model into VRAM and keep it there. 24h effectively means never unload."""
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    model = config.get("models", {}).get("local", "qwen3:8b")
    body = json.dumps({"model": model, "keep_alive": keep_alive}).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as _:
            pass
        log.debug(f"Ollama keep-alive ping sent (keep_alive={keep_alive})")
    except Exception as e:
        log.warning(f"Ollama keep-alive ping failed: {e}")


def _warmup_conductor(config):
    """Pre-load the conductor model on CPU (num_gpu=0) for user chat."""
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    model = config.get("models", {}).get("conductor_model")
    if not model:
        return
    body = json.dumps({"model": model, "keep_alive": "24h", "options": {"num_gpu": 0}}).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as _:
            pass
        log.info(f"Conductor model '{model}' warmed up on CPU")
    except Exception as e:
        log.warning(f"Conductor warmup failed: {e}")


def write_status_md():
    try:
        status = db.get_fleet_status()
        # Build task type lookup from current assignments
        task_lookup = {}
        try:
            with db.get_conn() as conn:
                for a in status["agents"]:
                    tid = a.get("current_task_id")
                    if tid:
                        row = conn.execute("SELECT type FROM tasks WHERE id=?", (tid,)).fetchone()
                        if row:
                            task_lookup[a["name"]] = row["type"]
        except Exception as e:
            log.debug(f"[write_status_md] task type lookup failed: {e}")

        lines = [
            f"# Fleet Status — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Agents",
            "| Name | Role | Status | Task | Last Heartbeat |",
            "|------|------|--------|------|----------------|",
        ]
        for a in status["agents"]:
            hb = db.utc_to_local(a.get("last_heartbeat"))
            task_type = task_lookup.get(a["name"], "—")
            lines.append(f"| {a['name']} | {a['role']} | {a['status']} | {task_type} | {hb} |")
        t = status["tasks"]
        lines += [
            "",
            "## Tasks",
            f"- Pending: {t['PENDING']}  Running: {t['RUNNING']}  Done: {t['DONE']}  Failed: {t['FAILED']}",
            "",
            "## GPU",
            f"- Training detected: {training_active}",
            f"- Ollama mode: {'CPU-only (training evicted models)' if ollama_evicted_for_training else 'GPU + training coexist' if training_active else 'eco CPU' if config['fleet']['eco_mode'] else 'GPU'}",
        ]
        # Marathon training status
        checkpoint_info = _check_training_checkpoints()
        if checkpoint_info:
            lines += [
                "",
                "## Marathon",
                f"- Latest checkpoint: {checkpoint_info['latest']} ({checkpoint_info['size_mb']} MB)",
                f"- Total checkpoints: {checkpoint_info['count']}",
            ]

        (FLEET_DIR / "STATUS.md").write_text("\n".join(lines))
    except Exception as e:
        log.warning(f"STATUS.md write failed: {e}")


def shutdown(sig, frame):
    log.info("Shutting down fleet...")
    _json_log("INFO", "supervisor_shutdown")
    stop_dashboard()
    stop_openclaw()
    stop_discord_bot()
    for role, proc in worker_procs.items():
        proc.terminate()
    for role, proc in worker_procs.items():
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    stop_ollama()
    log.info("Fleet stopped.")
    sys.exit(0)


# ── Memory Watchdog — cross-monitors Dr. Ders + workers + self ────────────────

_WORKER_RSS_WARN_MB = 300       # per-worker RSS warning threshold
_WORKER_RSS_CRITICAL_MB = 600   # per-worker RSS → restart worker
_HW_SUP_RSS_CRITICAL_MB = 400   # Dr. Ders RSS → restart it
_SUP_SELF_RSS_WARN_MB = 200     # supervisor self-check warning
_MEMORY_WATCHDOG_INTERVAL = 300  # seconds between full memory sweeps


def _memory_watchdog(worker_procs_dict, config):
    """Cross-monitor memory usage of all fleet processes.

    Returns dict of actions taken for logging/state.
    """
    try:
        import psutil
    except ImportError:
        return {}

    actions = []

    # 1. Self-check — supervisor's own RSS
    try:
        own = psutil.Process(os.getpid())
        own_rss = own.memory_info().rss / (1024 * 1024)
        if own_rss > _SUP_SELF_RSS_WARN_MB:
            collected = gc.collect()
            log.warning(f"Supervisor self RSS: {own_rss:.0f} MB — gc collected {collected}")
            actions.append(f"sup_gc:{collected}")
        else:
            # Light gen-0 gc
            gc.collect(0)
    except Exception:
        pass

    # 2. Worker RSS checks — restart leaking workers
    for role, proc in list(worker_procs_dict.items()):
        if proc is None or proc.poll() is not None:
            continue
        try:
            p = psutil.Process(proc.pid)
            rss = p.memory_info().rss / (1024 * 1024)
            if rss > _WORKER_RSS_CRITICAL_MB:
                log.warning(f"Worker '{role}' RSS {rss:.0f} MB > {_WORKER_RSS_CRITICAL_MB} MB — restarting")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                worker_procs_dict[role] = None  # will be respawned by dead-worker loop
                actions.append(f"restart:{role}:{rss:.0f}MB")
            elif rss > _WORKER_RSS_WARN_MB:
                log.info(f"Worker '{role}' RSS: {rss:.0f} MB (elevated)")
                actions.append(f"warn:{role}:{rss:.0f}MB")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # 3. Dr. Ders cross-check — read RSS from hw_state.json
    try:
        hw_state_file = FLEET_DIR / "hw_state.json"
        if hw_state_file.exists():
            hw = json.loads(hw_state_file.read_text(encoding="utf-8"))
            hw_rss = hw.get("memory", {}).get("hw_sup_rss_mb", 0)
            if hw_rss > _HW_SUP_RSS_CRITICAL_MB:
                log.warning(f"Dr. Ders RSS {hw_rss:.0f} MB > {_HW_SUP_RSS_CRITICAL_MB} MB "
                            f"— flagging for restart")
                actions.append(f"dr_ders_leak:{hw_rss:.0f}MB")
                # Post note so dashboard can show the alert
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "memory_alert",
                        "title": f"Dr. Ders memory leak: {hw_rss:.0f} MB",
                        "content": "RSS exceeds threshold. Consider restarting Dr. Ders.",
                        "tags": ["memory", "dr_ders"],
                    }))
                except Exception:
                    pass
    except Exception:
        pass

    if actions:
        log.info(f"Memory watchdog: {', '.join(actions)}")
    return {"actions": actions}


def main():
    global training_active, config, ollama_evicted_for_training

    (FLEET_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (FLEET_DIR / "knowledge" / "summaries").mkdir(parents=True, exist_ok=True)
    (FLEET_DIR / "knowledge" / "reports").mkdir(parents=True, exist_ok=True)

    db.init_db()
    db.register_agent("supervisor", "supervisor", os.getpid())

    # Start async DAG processor (0.08.00)
    try:
        from dag_queue import start as start_dag_queue
        start_dag_queue()
    except ImportError as e:
        log.debug(f"[main] DAG queue not available (optional): {e}")

    config = load_config()

    # Air-gap mode: skip secrets loading entirely (no API keys in memory)
    if not is_air_gap(config):
        _load_secrets()
    else:
        log.info("AIR-GAP mode — secrets loading disabled")

    offline = is_offline(config)
    air_gap = is_air_gap(config)
    if air_gap:
        log.info("AIR-GAP mode enabled — dashboard, Discord, OpenClaw disabled")
    elif offline:
        log.info("OFFLINE mode enabled — Discord, OpenClaw disabled")

    ALL_ROLES = _build_roles(config)
    max_workers = config.get("fleet", {}).get("max_workers", 10)
    # Boot with capped worker count — Dr. Ders can scale up later
    ROLES = ALL_ROLES[:max_workers]
    if len(ALL_ROLES) > max_workers:
        log.info(f"Worker cap: starting {len(ROLES)}/{len(ALL_ROLES)} workers (max_workers={max_workers})")
        log.info(f"Deferred: {', '.join(ALL_ROLES[max_workers:])}")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start Ollama — adopts existing instance or starts fresh
    start_ollama(gpu=not config["fleet"]["eco_mode"])

    # Initial keepalive — pre-load worker model into VRAM (Dr. Ders takes over after boot)
    if not air_gap:
        _ping_ollama_keepalive(config)

    # Start workers with stagger
    for role in ROLES:
        start_worker(role, config)
        time.sleep(1)

    # Start services — skip network services when offline/air-gapped
    if not offline:
        start_discord_bot(config)
        start_openclaw(config)
    if not air_gap:
        start_dashboard(config)

    # NOTE: Conductor model warmup + ongoing keepalive handled by Dr. Ders.
    # Dr. Ders checks conductor every ~60s and keepalive every ~240s.

    mode_label = " [AIR-GAP]" if air_gap else " [OFFLINE]" if offline else ""
    log.info(f"Fleet up — {len(ROLES)} workers, eco={config['fleet']['eco_mode']}{mode_label}")
    _json_log("INFO", "supervisor_startup", workers=len(ROLES),
              eco=config["fleet"]["eco_mode"], mode=mode_label.strip() or "normal")

    last_status = 0
    last_training_check = 0
    last_stale_check = 0
    last_watchdog = 0
    last_watchdog_full = 0
    last_memory_watchdog = 0
    last_sup_notes_ts = None  # ISO timestamp of last sup note read
    training_interval = config["fleet"]["training_check_interval_secs"]
    worker_next_start = {}
    # Dynamic worker scaling — deferred roles that weren't started at boot
    deferred_roles = ALL_ROLES[max_workers:] if len(ALL_ROLES) > max_workers else []
    last_scale_check = 0
    scale_interval = config.get("fleet", {}).get("worker_scale_interval_secs", 900)
    last_model_recommend = 0
    MODEL_RECOMMEND_INTERVAL = 6 * 3600  # every 6 hours
    # v0.23 S3: Auto-Intelligence — periodic evolution + research dispatch
    last_auto_evolution = 0
    AUTO_EVOLUTION_INTERVAL = 3600  # dispatch evolution_coordinator every 1 hour
    last_auto_research = 0
    AUTO_RESEARCH_INTERVAL = 7200  # dispatch research_loop every 2 hours

    while True:
        now = time.time()

        # Dynamic worker scaling — start deferred workers if RAM allows (every 15min)
        if deferred_roles and now - last_scale_check >= scale_interval:
            last_scale_check = now
            try:
                import psutil as _ps
                ram_pct = _ps.virtual_memory().percent
                if ram_pct < 75:  # enough headroom
                    role = deferred_roles.pop(0)
                    log.info(f"Scaling up: starting deferred worker '{role}' (RAM {ram_pct:.0f}%)")
                    start_worker(role, config)
                elif ram_pct > 85:
                    log.info(f"Scaling hold: RAM {ram_pct:.0f}% — {len(deferred_roles)} workers deferred")
            except Exception as e:
                log.debug(f"Scale check error: {e}")

        # Restart dead workers with cool-down backoff
        disabled = set(config.get("fleet", {}).get("disabled_agents", []))
        for role in list(worker_procs.keys()):
            proc = worker_procs.get(role)
            if proc and proc.poll() is not None:
                # If role was disabled while running, don't respawn
                if role in disabled:
                    log.info(f"Worker '{role}' exited and is disabled — removing from worker_procs")
                    del worker_procs[role]
                    continue
                log.warning(f"Worker '{role}' died (exit={proc.returncode}) — entering 15s cool-down")
                _json_log("WARNING", "worker_crash", worker=role, exit_code=proc.returncode)
                worker_procs[role] = None
                worker_next_start[role] = now + 15

        for role, next_time in list(worker_next_start.items()):
            if role in disabled:
                worker_next_start.pop(role, None)
                continue
            if worker_procs.get(role) is None and now >= next_time:
                log.info(f"Cool-down complete. Respawning worker '{role}'")
                _json_log("INFO", "worker_respawn", worker=role)
                start_worker(role, config)
                worker_next_start.pop(role, None)

        # Restart messaging bridges if they died (skip when offline/air-gapped)
        if not offline:
            if discord_proc and discord_proc.poll() is not None:
                log.warning(f"Discord bot died (exit={discord_proc.returncode}) — restarting")
                start_discord_bot(config)
            if openclaw_proc and openclaw_proc.poll() is not None:
                log.warning(f"OpenClaw died (exit={openclaw_proc.returncode}) — restarting")
                start_openclaw(config)
        if not air_gap:
            if dashboard_proc and dashboard_proc.poll() is not None:
                log.warning(f"Dashboard died (exit={dashboard_proc.returncode}) — restarting")
                start_dashboard(config)

        # Model keepalive now handled by Dr. Ders (every ~240s via hw_state.json)
        # Supervisor only reads hw_state for transition awareness

        # Memory watchdog — cross-monitor all fleet processes (every 5 min)
        if now - last_memory_watchdog >= _MEMORY_WATCHDOG_INTERVAL:
            last_memory_watchdog = now
            try:
                _memory_watchdog(worker_procs, config)
            except Exception as e:
                log.debug(f"Memory watchdog error: {e}")

        # Training detection — VRAM-aware: only evict Ollama if training profile needs it
        if now - last_training_check >= training_interval:
            last_training_check = now
            training_now, training_profile = is_training_running()
            if training_now and not training_active:
                needs_eviction, reason = training_needs_eviction(config, training_profile)
                log.info(f"train.py detected (profile={training_profile or 'unknown'}) — {reason}")
                _json_log("INFO", "training_detected", profile=training_profile or "unknown", reason=reason)
                training_active = True

                if needs_eviction:
                    _evict_gpu_models(config)
                    time.sleep(2)
                    stop_ollama()
                    start_ollama(gpu=False)
                    ollama_evicted_for_training = True
                    mode_msg = "Ollama CPU-only"
                else:
                    ollama_evicted_for_training = False
                    mode_msg = "Ollama stays on GPU (training fits in remaining VRAM)"

                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "training_state",
                        "title": f"Training started — {mode_msg}",
                        "tags": ["training"],
                    }))
                except Exception as e:
                    log.warning(f"[training] failed to post training-started note: {e}")
                try:
                    checkpoint_info = _check_training_checkpoints()
                    db.post_task("marathon_log", json.dumps({
                        "session_id": "autoresearch",
                        "goal": "ML training session",
                        "completed_steps": ["Training detected", mode_msg],
                        "next_step": "Monitor checkpoints",
                        "notes": f"Profile: {training_profile or 'unknown'}. Checkpoints: {checkpoint_info}" if checkpoint_info else f"Profile: {training_profile or 'unknown'}. No checkpoints yet",
                    }), priority=2)
                except Exception as e:
                    log.warning(f"[training] failed to post marathon_log (start): {e}")
            elif not training_now and training_active:
                training_active = False
                if ollama_evicted_for_training:
                    log.info("Training finished — restoring Ollama to GPU mode")
                    stop_ollama()
                    start_ollama(gpu=not config["fleet"]["eco_mode"])
                    ollama_evicted_for_training = False
                else:
                    log.info("Training finished — Ollama was already on GPU, no restart needed")
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "training_state",
                        "title": "Training finished — Ollama restored",
                        "tags": ["training"],
                    }))
                except Exception as e:
                    log.warning(f"[training] failed to post training-finished note: {e}")
                try:
                    checkpoint_info = _check_training_checkpoints()
                    db.post_task("marathon_log", json.dumps({
                        "session_id": "autoresearch",
                        "goal": "ML training session",
                        "completed_steps": ["Training completed", "Ollama restored to GPU",
                                           f"Final checkpoints: {checkpoint_info['count']}" if checkpoint_info else "No checkpoints"],
                        "next_step": "Evaluate training results",
                    }), priority=2)
                except Exception as e:
                    log.warning(f"[training] failed to post marathon_log (end): {e}")

        # Log Dr. Ders transitions
        hw_state = read_hw_state()
        if hw_state and hw_state.get("status") == "transitioning":
            log.info(f"Dr. Ders transitioning to {hw_state.get('model')} — workers pausing claims")

        # Sup-channel: read inbox + notes every 30s (aligned with status write)
        if now - last_status >= 30:
            try:
                sup_msgs = db.get_messages("supervisor", unread_only=True,
                                           limit=5, channels=["sup"])
                for m in sup_msgs:
                    try:
                        body = json.loads(m["body_json"])
                        log.info(f"Sup msg from {m['from_agent']}: {body.get('type', '?')}")
                    except Exception as e:
                        log.debug(f"[sup-channel] failed to parse sup message: {e}")
                sup_notes = db.get_notes("sup", since=last_sup_notes_ts, limit=10)
                for n in sup_notes:
                    try:
                        body = json.loads(n["body_json"])
                        log.info(f"Sup note [{n['from_agent']}]: {body.get('title', '?')}")
                    except Exception as e:
                        log.debug(f"[sup-channel] failed to parse sup note: {e}")
                    last_sup_notes_ts = n.get("created_at", last_sup_notes_ts)
            except Exception as e:
                log.debug(f"Sup channel read error: {e}")

        # Recover stale RUNNING tasks (crashed workers)
        if now - last_stale_check >= STALE_TASK_RECOVERY_INTERVAL:
            last_stale_check = now
            recovered = db.recover_stale_tasks(STALE_TASK_TIMEOUT)
            for t in recovered:
                log.warning(f"Recovered stale task {t['id']} ({t['type']}) from {t['assigned_to']}")
                _json_log("WARNING", "stale_task_recovered", task_id=t["id"],
                          task_type=t["type"], agent=t["assigned_to"])
            if recovered:
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "stale_recovery",
                        "title": f"Recovered {len(recovered)} stale tasks",
                        "tasks": [{"id": t["id"], "type": t["type"]} for t in recovered[:5]],
                        "tags": ["recovery"],
                    }))
                except Exception as e:
                    log.warning(f"[stale-recovery] failed to post recovery note: {e}")

        # Semantic watchdog — failure detection, stuck reviews, DLP
        if now - last_watchdog >= WATCHDOG_INTERVAL:
            last_watchdog = now
            try:
                from skills._watchdog import run_cycle, run_full_cycle
                if now - last_watchdog_full >= WATCHDOG_FULL_INTERVAL:
                    last_watchdog_full = now
                    alerts = run_full_cycle(log.info)
                    # 0.14: Knowledge integrity check on full cycle
                    try:
                        from integrity import verify_integrity, save_manifest
                        result = verify_integrity()
                        if result.get("status") == "tampered":
                            log.warning(f"INTEGRITY: {len(result.get('modified',[]))} modified, "
                                       f"{len(result.get('missing',[]))} missing files")
                            try:
                                from audit_log import log_event
                                log_event("integrity_alert", "supervisor",
                                         {"modified": result.get("modified", [])[:5],
                                          "missing": result.get("missing", [])[:5]},
                                         severity="warning")
                            except Exception:
                                pass
                        elif result.get("status") == "no_manifest":
                            save_manifest()
                            log.info("INTEGRITY: Initial manifest created")
                    except ImportError:
                        pass
                    except Exception as e:
                        log.debug(f"Integrity check error: {e}")
                else:
                    alerts = run_cycle(log.info)
                for a in alerts:
                    log.warning(f"Watchdog alert: {a['message']}")
            except Exception as e:
                log.warning(f"Watchdog error: {e}")

        # v0.23 S3: Auto-Intelligence — periodic evolution pipeline dispatch
        if now - last_auto_evolution >= AUTO_EVOLUTION_INTERVAL:
            last_auto_evolution = now
            try:
                evo_skill_path = FLEET_DIR / "skills" / "evolution_coordinator.py"
                if evo_skill_path.exists():
                    # Check if any agent is idle before dispatching
                    with db.get_conn() as _conn:
                        idle_agents = _conn.execute(
                            "SELECT COUNT(*) as cnt FROM agents WHERE status='IDLE' "
                            "AND (julianday('now') - julianday(last_heartbeat)) * 86400 < 60"
                        ).fetchone()
                    if idle_agents and idle_agents["cnt"] > 0:
                        db.post_task("evolution_coordinator",
                                     json.dumps({"trigger": "supervisor_periodic"}),
                                     priority=2)
                        log.info("Auto-intelligence: dispatched evolution_coordinator pipeline")
            except Exception as e:
                log.debug(f"Auto-evolution dispatch error: {e}")

        # v0.23 S3: Auto-Intelligence — periodic research cycle dispatch
        if now - last_auto_research >= AUTO_RESEARCH_INTERVAL:
            last_auto_research = now
            try:
                research_skill_path = FLEET_DIR / "skills" / "research_loop.py"
                if research_skill_path.exists():
                    db.post_task("research_loop",
                                 json.dumps({"trigger": "supervisor_periodic"}),
                                 priority=2)
                    log.info("Auto-intelligence: dispatched research_loop cycle")
            except Exception as e:
                log.debug(f"Auto-research dispatch error: {e}")

        # Periodic model recommendation — HITL upgrade suggestions (every 6h)
        if now - last_model_recommend >= MODEL_RECOMMEND_INTERVAL:
            last_model_recommend = now
            try:
                db.post_task("model_recommend", json.dumps({"action": "analyze"}), priority=3)
                log.info("Dispatched model_recommend analysis task")
            except Exception as e:
                log.debug(f"Model recommend dispatch error: {e}")

        # Write status snapshot
        if now - last_status >= 30:
            last_status = now
            write_status_md()

        time.sleep(5)


if __name__ == "__main__":
    main()
