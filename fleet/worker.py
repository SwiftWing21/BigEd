#!/usr/bin/env python3
"""Generic worker process. Usage: uv run python worker.py --role <role>"""

import argparse
import importlib
import json
import logging
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db
from config import load_config


def setup_logging(role):
    log_dir = FLEET_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger(role)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    fh = logging.FileHandler(log_dir / f"{role}.log")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def load_idle_curriculum(role):
    # Try exact role first (e.g. coder_1.toml), fall back to base role (coder.toml)
    candidates = [role]
    base = role.split("_")[0]
    if base != role:
        candidates.append(base)
    for name in candidates:
        path = FLEET_DIR / "idle_curricula" / f"{name}.toml"
        if path.exists():
            try:
                import tomllib
                with open(path, "rb") as f:
                    return tomllib.load(f).get("tasks", [])
            except Exception:
                pass
    return []


def wait_for_ollama(host: str, timeout: int, log) -> bool:
    """
    Poll the Ollama API until it responds or timeout is reached.
    Returns True if ready, False if timed out.
    """
    deadline = time.time() + timeout
    interval = 3
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        remaining = int(deadline - time.time())
        log.warning(f"Ollama not reachable at {host} — retrying ({remaining}s left)")
        time.sleep(interval)
    return False


def run_skill(skill_name, payload, config, log):
    try:
        module = importlib.import_module(f"skills.{skill_name}")
        return module.run(payload, config)
    except Exception as e:
        log.error(f"Skill '{skill_name}' error: {e}")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    args = parser.parse_args()

    role = args.role
    log = setup_logging(role)
    config = load_config()

    db.init_db()
    db.register_agent(role, role, os.getpid())
    log.info(f"Started (pid={os.getpid()}, eco={config['fleet']['eco_mode']})")

    # Verify Ollama is reachable before joining the fleet
    ollama_host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    if not wait_for_ollama(ollama_host, timeout=30, log=log):
        log.error(f"Ollama not available at {ollama_host} after 30s — exiting")
        db.heartbeat(role, status="OFFLINE")
        sys.exit(1)

    curriculum = load_idle_curriculum(role)
    curriculum_idx = 0
    last_task_time = time.time()
    idle_timeout = config['fleet']['idle_timeout_secs']

    running = True

    def shutdown(sig, frame):
        nonlocal running
        log.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while running:
        try:
            db.heartbeat(role)
        except Exception as e:
            log.warning(f"Heartbeat failed: {e}")

        task = db.claim_task(role)
        if task:
            last_task_time = time.time()
            log.info(f"Task {task['id']} claimed: {task['type']}")
            try:
                db.heartbeat(role, status='BUSY', current_task_id=task['id'])
            except Exception:
                pass
            try:
                payload = json.loads(task['payload_json']) if task['payload_json'] else {}
                result = run_skill(task['type'], payload, config, log)
                db.complete_task(task['id'], json.dumps(result))
                log.info(f"Task {task['id']} done")
            except Exception as e:
                db.fail_task(task['id'], str(e))
                log.error(f"Task {task['id']} failed: {e}")
            try:
                db.heartbeat(role, status='IDLE')
            except Exception:
                pass
            time.sleep(1)
            continue

        # Idle curriculum — planner always runs it; others require idle_enabled=true
        is_planner = role.split("_")[0] == "planner"
        idle_allowed = is_planner or config['fleet'].get('idle_enabled', False)
        if curriculum and idle_allowed and (time.time() - last_task_time) >= idle_timeout:
            item = curriculum[curriculum_idx % len(curriculum)]
            curriculum_idx += 1
            log.info(f"Idle: {item.get('type', '?')}")
            try:
                run_skill(item['type'], item.get('payload', {}), config, log)
                log.info(f"Idle task done: {item.get('type')}")
            except Exception as e:
                log.warning(f"Idle task failed: {e}")
            last_task_time = time.time()

        time.sleep(2)

    log.info("Stopped")


if __name__ == "__main__":
    main()
