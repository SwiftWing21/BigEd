#!/usr/bin/env python3
"""Fleet supervisor — starts workers, monitors health, manages GPU/training handoff."""

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
from marathon import is_training_running, _check_training_checkpoints, _evict_gpu_models


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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SUPERVISOR] %(message)s",
    handlers=[
        logging.FileHandler(FLEET_DIR / "logs" / "supervisor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("supervisor")

BASE_ROLES = ["researcher", "coder", "archivist", "analyst", "sales", "onboarding", "implementation", "security", "planner", "legal", "account_manager"]
PYTHON = sys.executable


def _build_roles(config):
    """Expand BASE_ROLES, replacing 'coder' with coder_1..coder_N instances."""
    roles = []
    for r in BASE_ROLES:
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


def start_ollama(gpu=False):
    global ollama_proc
    env = os.environ.copy()
    if not gpu:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    elif "CUDA_VISIBLE_DEVICES" in env:
        del env["CUDA_VISIBLE_DEVICES"]
    mode = "GPU" if gpu else "CPU"
    log.info(f"Starting Ollama ({mode} mode)")
    ollama_proc = subprocess.Popen(
        ["ollama", "serve"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(3)


def stop_ollama():
    global ollama_proc
    if ollama_proc and ollama_proc.poll() is None:
        log.info("Stopping Ollama")
        ollama_proc.terminate()
        try:
            ollama_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ollama_proc.kill()
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
    port = config.get("dashboard", {}).get("port", 5555)
    log.info(f"Starting dashboard on http://localhost:{port}")
    dashboard_proc = subprocess.Popen(
        [PYTHON, str(FLEET_DIR / "dashboard.py"), "--port", str(port)],
        cwd=str(FLEET_DIR),
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
    nice = config["workers"]["nice_level"]
    cpu_limit = config["workers"]["cpu_limit_percent"]
    cmd = ["nice", f"-n{nice}", PYTHON, str(FLEET_DIR / "worker.py"), "--role", role]

    # Wrap with cpulimit if available
    if subprocess.run(["which", "cpulimit"], capture_output=True).returncode == 0:
        cmd = ["cpulimit", f"--limit={cpu_limit}", "--"] + cmd
    else:
        log.warning("cpulimit not found — install with: sudo apt install cpulimit")

    log.info(f"Starting worker: {role}")
    worker_procs[role] = subprocess.Popen(cmd, cwd=str(FLEET_DIR))

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
    """Read hw_supervisor state — returns dict or None."""
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
            f"- Ollama mode: {'CPU-only (training active)' if training_active else 'eco CPU' if config['fleet']['eco_mode'] else 'GPU'}",
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


def main():
    global training_active, config

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

    ROLES = _build_roles(config)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start Ollama — skip if already running (launcher may have pre-started it)
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        log.info("Ollama already running — skipping start")
    except Exception as e:
        log.debug(f"[main] Ollama not reachable ({e}), starting fresh")
        start_ollama(gpu=not config["fleet"]["eco_mode"])

    # Initial keepalive — pre-load worker model into VRAM (hw_supervisor takes over after boot)
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

    # NOTE: Conductor model warmup + ongoing keepalive handled by hw_supervisor.
    # hw_supervisor checks conductor every ~60s and keepalive every ~240s.

    mode_label = " [AIR-GAP]" if air_gap else " [OFFLINE]" if offline else ""
    log.info(f"Fleet up — {len(ROLES)} workers, eco={config['fleet']['eco_mode']}{mode_label}")

    last_status = 0
    last_training_check = 0
    last_stale_check = 0
    last_watchdog = 0
    last_watchdog_full = 0
    last_sup_notes_ts = None  # ISO timestamp of last sup note read
    training_interval = config["fleet"]["training_check_interval_secs"]
    worker_next_start = {}

    while True:
        now = time.time()

        # Restart dead workers with cool-down backoff
        for role in list(worker_procs.keys()):
            proc = worker_procs.get(role)
            if proc and proc.poll() is not None:
                log.warning(f"Worker '{role}' died (exit={proc.returncode}) — entering 15s cool-down")
                worker_procs[role] = None
                worker_next_start[role] = now + 15
                
        for role, next_time in list(worker_next_start.items()):
            if worker_procs.get(role) is None and now >= next_time:
                log.info(f"Cool-down complete. Respawning worker '{role}'")
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

        # Model keepalive now handled by hw_supervisor (every ~240s via hw_state.json)
        # Supervisor only reads hw_state for transition awareness

        # Training detection — toggle Ollama GPU mode
        if now - last_training_check >= training_interval:
            last_training_check = now
            training_now = is_training_running()
            if training_now and not training_active:
                log.info("train.py detected — evicting GPU models, switching Ollama to CPU-only")
                _evict_gpu_models(config)  # best-effort, has internal timeouts
                time.sleep(2)  # brief pause for eviction to complete
                stop_ollama()
                start_ollama(gpu=False)
                training_active = True
                # hw_supervisor will re-establish keepalive on next poll
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "training_state",
                        "title": "Training started — Ollama CPU-only",
                        "tags": ["training"],
                    }))
                except Exception as e:
                    log.warning(f"[training] failed to post training-started note: {e}")
                # v0.43: Log marathon training start
                try:
                    checkpoint_info = _check_training_checkpoints()
                    db.post_task("marathon_log", json.dumps({
                        "session_id": "autoresearch",
                        "goal": "ML training session",
                        "completed_steps": ["Training detected", "Ollama switched to CPU"],
                        "next_step": "Monitor checkpoints",
                        "notes": f"Checkpoints: {checkpoint_info}" if checkpoint_info else "No checkpoints yet",
                    }), priority=2)
                except Exception as e:
                    log.warning(f"[training] failed to post marathon_log (start): {e}")
            elif not training_now and training_active:
                log.info("Training finished — restoring Ollama mode")
                stop_ollama()
                start_ollama(gpu=not config["fleet"]["eco_mode"])
                training_active = False
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "training_state",
                        "title": "Training finished — Ollama restored",
                        "tags": ["training"],
                    }))
                except Exception as e:
                    log.warning(f"[training] failed to post training-finished note: {e}")
                # v0.43: Log marathon training end
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

        # Log hw_supervisor transitions
        hw_state = read_hw_state()
        if hw_state and hw_state.get("status") == "transitioning":
            log.info(f"HW supervisor transitioning to {hw_state.get('model')} — workers pausing claims")

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
                else:
                    alerts = run_cycle(log.info)
                for a in alerts:
                    log.warning(f"Watchdog alert: {a['message']}")
            except Exception as e:
                log.warning(f"Watchdog error: {e}")

        # Write status snapshot
        if now - last_status >= 30:
            last_status = now
            write_status_md()

        time.sleep(5)


if __name__ == "__main__":
    main()
