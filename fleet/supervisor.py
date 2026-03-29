#!/usr/bin/env python3
"""Fleet supervisor -- thin orchestrator over 5 focused modules.

Modules:
  process_manager.py  -- subprocess lifecycle (Ollama, workers, dashboard, etc.)
  scheduler.py        -- dynamic scaling, auto-triggers, training detection
  health_monitor.py   -- health sweeps, memory watchdog, circuit breakers
  federation_manager.py -- peer heartbeat, discovery, mTLS
  boot_sequence.py    -- ordered startup sequence
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db
from config import load_config
from marathon import _check_training_checkpoints

# -- Logging -----------------------------------------------------------------
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


def _json_log(level, event, **kwargs):
    """Structured JSON log line for fleet processes."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


# -- Module-level state (set during boot) ------------------------------------
_pm = None     # ProcessManager
_sched = None  # Scheduler
_hm = None     # HealthMonitor
_fm = None     # FederationManager
_config = None

# -- Factorio focus mode ------------------------------------------------------
_focus_file = os.path.join(os.path.dirname(__file__), ".factorio_focus.json")
_last_analyze_gen = 0


def _maybe_generate_factorio_tasks(now):
    """Auto-generate factorio_analyze tasks every 60s when Focus mode is ON."""
    global _last_analyze_gen
    try:
        with open(_focus_file) as f:
            focus = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if not focus.get("on"):
        return
    # Generate factorio_analyze every 60s
    if now - _last_analyze_gen < 60:
        return
    _last_analyze_gen = now
    # Check if there's already a pending factorio_analyze task
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE type='factorio_analyze' AND status='PENDING' LIMIT 1"
            ).fetchone()
        if row:
            return
        def _do():
            db.post_task("factorio_analyze",
                         json.dumps({"auto_generated": True, "priority": 75}),
                         priority=75)
        db._retry_write(_do)
        log.info("Focus mode: auto-generated factorio_analyze task")
    except Exception:
        log.warning("Failed to generate factorio_analyze task", exc_info=True)


def write_status_md():
    """Write fleet status snapshot to STATUS.md."""
    try:
        status = db.get_fleet_status()
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

        training_active = _pm.training_active if _pm else False
        ollama_evicted = _pm.ollama_evicted_for_training if _pm else False
        eco_mode = _config.get("fleet", {}).get("eco_mode", False) if _config else False

        lines = [
            f"# Fleet Status -- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Agents",
            "| Name | Role | Status | Task | Last Heartbeat |",
            "|------|------|--------|------|----------------|",
        ]
        for a in status["agents"]:
            hb = db.utc_to_local(a.get("last_heartbeat"))
            task_type = task_lookup.get(a["name"], "---")
            lines.append(f"| {a['name']} | {a['role']} | {a['status']} | {task_type} | {hb} |")
        t = status["tasks"]
        lines += [
            "",
            "## Tasks",
            f"- Pending: {t['PENDING']}  Running: {t['RUNNING']}  Done: {t['DONE']}  Failed: {t['FAILED']}",
            "",
            "## GPU",
            f"- Training detected: {training_active}",
            f"- Ollama mode: {'CPU-only (training evicted models)' if ollama_evicted else 'GPU + training coexist' if training_active else 'eco CPU' if eco_mode else 'GPU'}",
        ]
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
    """Signal handler -- clean fleet shutdown."""
    log.info("Shutting down fleet...")
    _json_log("INFO", "supervisor_shutdown")
    if _pm:
        _pm.shutdown_all()
    log.info("Fleet stopped.")
    sys.exit(0)


def main():
    global _pm, _sched, _hm, _fm, _config

    from boot_sequence import boot

    result = boot()
    if result is None:
        return  # Duplicate supervisor

    _pm, _sched, _hm, _fm, _config, roles = result

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Write STATUS.md immediately so boot.py doesn't wait
    write_status_md()

    last_status = time.time()
    last_sup_notes_ts = None

    while True:
      try:
        now = time.time()

        # Module ticks (each handles its own intervals internally)
        _sched.tick(now)
        _hm.tick(now)
        _fm.tick(now)
        _pm.check_alive()

        # Factorio focus mode: auto-generate analysis tasks
        _maybe_generate_factorio_tasks(now)

        # Log Dr. Ders transitions
        hw_state = _pm.read_hw_state()
        if hw_state and hw_state.get("status") == "transitioning":
            log.info(f"Dr. Ders transitioning to {hw_state.get('model')} -- workers pausing claims")

        # 30s status interval: sup-channel inbox, heartbeat, STATUS.md
        if now - last_status >= 30:
            last_status = now

            # Sup-channel inbox
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

            write_status_md()

            # Heartbeat file for Dr. Ders
            try:
                hb_file = FLEET_DIR / ".supervisor_heartbeat"
                hb_file.write_text(json.dumps({
                    "pid": os.getpid(), "ts": time.time(),
                    "workers": len(_pm.get_running_workers()),
                    "model": _config.get("models", {}).get("local", ""),
                }), encoding="utf-8")
            except Exception:
                pass

        # Check for restart request from dashboard
        restart_flag = FLEET_DIR / ".restart_requested"
        if restart_flag.exists():
            restart_flag.unlink(missing_ok=True)
            log.info("Restart requested via dashboard — restarting supervisor")
            _pm.shutdown_all()
            os.execv(sys.executable, [sys.executable] + sys.argv)

        time.sleep(5)
      except Exception:
        log.warning("Main loop iteration failed", exc_info=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
