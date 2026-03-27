#!/usr/bin/env python3
"""Boot sequence -- ordered startup for the supervisor.

Extracted from supervisor.py main() pre-loop setup. Runs once at
supervisor launch, returns initialized module instances for the main loop.
"""

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import boot_status

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent


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


def _register_views():
    """Register supervisor data source for Hybrid ViewPort."""
    try:
        import view_registry
        view_registry.register_source(
            name="supervisor",
            category="fleet",
            node_types=["supervisor", "agent", "worker"],
            edge_types=["manages", "dispatches", "heartbeat"],
            data_endpoint="/api/fleet/graph",
            icon="cpu",
            layout_hint="radial",
            metrics=["uptime_s", "worker_count", "task_queue_depth"],
        )
    except Exception as e:
        log.debug(f"ViewPort registration failed: {e}")


def _json_log(level, event, **kwargs):
    """Structured JSON log line for fleet processes."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


def boot(config=None):
    """Execute the full supervisor boot sequence.

    Order:
    1. PID acquire (exit if duplicate)
    2. Log rotation
    3. DB init + register supervisor agent
    4. DAG queue start
    5. Load config + secrets (skip in air-gap)
    6. Start Ollama (adopt or launch)
    7. Resolve best model + export override
    8. Initial keepalive ping
    9. Start dashboard (background thread)
    10. Start Dr. Ders
    11. Start core workers (no stagger)
    12. Start Discord + OpenClaw (if online)
    13. Deferred federation (background thread)
    14. Start backup manager
    15. Register ViewPort data sources
    16. Write STATUS.md (caller handles this)

    Returns (pm, scheduler, health_monitor, federation_manager, config, core_roles)
    or None if boot fails (duplicate supervisor).
    """
    sys.path.insert(0, str(FLEET_DIR))
    import db
    from config import load_config, is_offline, is_air_gap

    # These modules are created by other pods and merged before this runs
    from process_manager import ProcessManager
    from scheduler import Scheduler, CORE_AGENTS
    from health_monitor import HealthMonitor
    from federation_manager import FederationManager

    # Ensure directories
    (FLEET_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (FLEET_DIR / "knowledge" / "summaries").mkdir(parents=True, exist_ok=True)
    (FLEET_DIR / "knowledge" / "reports").mkdir(parents=True, exist_ok=True)

    # 1. Log rotation
    try:
        from log_manager import rotate_logs
        rotation = rotate_logs()
        if rotation.get("files_archived"):
            print(f"[SUPERVISOR] Logs rotated: {rotation['files_archived']} files archived to sessions/{rotation['session_id']}")
    except Exception as e:
        print(f"[SUPERVISOR] Log rotation skipped: {e}")

    # 2. PID acquire
    boot_status.update_stage("pid", "starting")
    try:
        from pid_manager import acquire_pid, release_pid
        if not acquire_pid("supervisor"):
            log.warning("Another supervisor is already running -- exiting")
            boot_status.update_stage("pid", "failed")
            return None
        import atexit
        atexit.register(lambda: release_pid("supervisor"))
    except Exception as e:
        log.warning("PID manager unavailable: %s", e)
    boot_status.update_stage("pid", "done")

    # 3. DB init
    boot_status.update_stage("db", "starting")
    db.init_db()
    db.register_agent("supervisor", "supervisor", os.getpid())
    boot_status.update_stage("db", "done")

    # 4. DAG queue
    try:
        from dag_queue import start as start_dag_queue
        start_dag_queue()
    except ImportError as e:
        log.debug(f"[main] DAG queue not available (optional): {e}")

    # 5. Config + secrets
    if config is None:
        config = load_config()

    air_gap = is_air_gap(config)
    offline = is_offline(config)

    if not air_gap:
        _load_secrets()
    else:
        log.info("AIR-GAP mode -- secrets loading disabled")

    if air_gap:
        log.info("AIR-GAP mode enabled -- dashboard, Discord, OpenClaw disabled")
    elif offline:
        log.info("OFFLINE mode enabled -- Discord, OpenClaw disabled")

    # Create module instances
    pm = ProcessManager(config)
    sched = Scheduler(config, pm)
    hm = HealthMonitor(config, pm)
    fm = FederationManager(config, pm)

    # Build roles
    all_roles = sched.build_roles()
    disabled = sched.get_disabled_agents()
    core_roles = [r for r in all_roles if r in CORE_AGENTS and r not in disabled]
    dynamic_pool = [r for r in all_roles if r not in CORE_AGENTS and r not in disabled]
    log.info(f"Dynamic scaling: booting {len(core_roles)} core agents, {len(dynamic_pool)} on-demand")
    log.info(f"Core: {', '.join(core_roles)} | Pool: {', '.join(dynamic_pool)}")

    # 6. Start Ollama
    boot_status.update_stage("ollama", "starting")
    pm.start_ollama(gpu=not config.get("fleet", {}).get("eco_mode", False))
    boot_status.update_stage("ollama", "done")

    # 7. Resolve model
    resolved_model = pm.get_best_available_model()
    configured_model = config.get("models", {}).get("local", "qwen3:8b")
    if resolved_model != configured_model:
        log.info(f"Model fallback: '{configured_model}' -> '{resolved_model}'")
        _json_log("INFO", "model_fallback", configured=configured_model, resolved=resolved_model)
    else:
        log.info(f"Using configured model: {resolved_model}")
    os.environ["FLEET_MODEL_OVERRIDE"] = resolved_model

    # 8. Initial keepalive
    if not air_gap:
        pm.ping_ollama_keepalive(model=resolved_model)

    # 9. Dashboard (background thread)
    boot_status.update_stage("dashboard", "starting")
    if not air_gap:
        threading.Thread(target=pm.start_dashboard, daemon=True).start()
    boot_status.update_stage("dashboard", "done")

    # 10. Dr. Ders
    boot_status.update_stage("dr_ders", "starting")
    pm.start_hw_supervisor()
    boot_status.update_stage("dr_ders", "done")

    # 11. Core workers
    boot_status.update_stage("workers", "starting")
    for role in core_roles:
        pm.start_worker(role)
        pm.last_busy[role] = time.time()
    boot_status.update_stage("workers", "done")

    # 12. Discord + OpenClaw (if online)
    if not offline:
        pm.start_discord_bot()
        pm.start_openclaw()

    # 13. Federation (deferred background thread)
    def _deferred_federation():
        fm.setup_tls()

    federation_cfg = config.get("federation", {})
    if federation_cfg.get("enabled") and not offline:
        fm.start_discovery()
        fm.announce_rejoin(core_roles)
    threading.Thread(target=_deferred_federation, daemon=True).start()

    # 14. Backup manager
    try:
        from backup_manager import BackupManager
        _backup = BackupManager(config)
        _backup.perform_backup(trigger="fleet_startup")
        _backup.start_auto_save()
        log.info(f"Auto-save enabled: every {_backup.interval}s to {_backup.location}")
    except Exception as e:
        log.warning(f"Backup manager failed to start: {e}")

    # 15. ViewPort
    _register_views()

    boot_status.update_stage("ready", "done")

    mode_label = " [AIR-GAP]" if air_gap else " [OFFLINE]" if offline else ""
    log.info(f"Fleet up -- {len(core_roles)} core workers (dynamic scaling enabled), "
             f"eco={config.get('fleet', {}).get('eco_mode', False)}{mode_label}")
    _json_log("INFO", "supervisor_startup", workers=len(core_roles),
              eco=config.get("fleet", {}).get("eco_mode", False),
              mode=mode_label.strip() or "normal",
              scaling="dynamic", core=len(core_roles), pool=len(dynamic_pool))

    boot_status.clear()
    return pm, sched, hm, fm, config, core_roles
