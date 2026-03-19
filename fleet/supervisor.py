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


def is_training_running():
    try:
        out = subprocess.run(["ps", "aux"], capture_output=True, text=True).stdout
        return "train.py" in out
    except Exception:
        return False


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
    except Exception:
        pass
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
        except Exception:
            pass

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
    except Exception:
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
                log.info("train.py detected — switching Ollama to CPU-only")
                stop_ollama()
                start_ollama(gpu=False)
                training_active = True
                # hw_supervisor will re-establish keepalive on next poll
            elif not training_now and training_active:
                log.info("Training finished — restoring Ollama mode")
                stop_ollama()
                start_ollama(gpu=not config["fleet"]["eco_mode"])
                training_active = False

        # Log hw_supervisor transitions
        hw_state = read_hw_state()
        if hw_state and hw_state.get("status") == "transitioning":
            log.info(f"HW supervisor transitioning to {hw_state.get('model')} — workers pausing claims")

        # Recover stale RUNNING tasks (crashed workers)
        if now - last_stale_check >= STALE_TASK_RECOVERY_INTERVAL:
            last_stale_check = now
            recovered = db.recover_stale_tasks(STALE_TASK_TIMEOUT)
            for t in recovered:
                log.warning(f"Recovered stale task {t['id']} ({t['type']}) from {t['assigned_to']}")

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
