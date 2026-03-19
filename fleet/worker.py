#!/usr/bin/env python3
"""Generic worker process. Usage: uv run python worker.py --role <role>"""

import argparse
import importlib
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db
from config import load_config, is_offline, is_air_gap, AIR_GAP_SKILLS

HW_STATE_FILE = FLEET_DIR / "hw_state.json"


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


SKILL_TIMEOUTS = {
    "code_write": 900,
    "code_write_review": 900,
    "fma_review": 900,
    "pen_test": 600,
    "security_audit": 600,
}
DEFAULT_SKILL_TIMEOUT = 600


def run_skill(skill_name, payload, config, log):
    # Air-gap mode: deny-by-default whitelist
    if is_air_gap(config) and skill_name not in AIR_GAP_SKILLS:
        log.warning(f"Skill '{skill_name}' blocked by air-gap mode")
        raise PermissionError(f"Skill '{skill_name}' not in air-gap whitelist")

    # Offline mode: check REQUIRES_NETWORK on the skill module
    if is_offline(config):
        try:
            mod_check = importlib.import_module(f"skills.{skill_name}")
            if getattr(mod_check, "REQUIRES_NETWORK", False):
                log.warning(f"Skill '{skill_name}' requires network — rejected (offline_mode)")
                return {"error": "offline_mode enabled", "skill": skill_name}
        except ImportError:
            pass

    timeout = SKILL_TIMEOUTS.get(skill_name, DEFAULT_SKILL_TIMEOUT)
    result = [None]
    exc = [None]

    def _target():
        try:
            module = importlib.import_module(f"skills.{skill_name}")
            result[0] = module.run(payload, config)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log.error(f"Skill '{skill_name}' timed out after {timeout}s")
        raise TimeoutError(f"Skill '{skill_name}' exceeded {timeout}s timeout")
    if exc[0]:
        log.error(f"Skill '{skill_name}' error: {exc[0]}")
        raise exc[0]
    return result[0]


def _should_review(skill_name, config, payload):
    """Check if this skill output should go through adversarial review."""
    review_cfg = config.get("review", {})
    if not review_cfg.get("enabled", False):
        return False
    # Don't review internal/review skills
    if skill_name.startswith("_"):
        return False
    # Check if we've already exceeded max review rounds
    max_rounds = review_cfg.get("max_rounds", 2)
    if payload.get("_review_round", 0) >= max_rounds:
        return False
    # Check if skill is high-stakes
    try:
        from skills._review import HIGH_STAKES_SKILLS
        return skill_name in HIGH_STAKES_SKILLS
    except ImportError:
        return False


def _run_review(skill_name, task_payload, result, config, log):
    """Run the adversarial review on a skill output."""
    try:
        from skills._review import run as review_run
        review_payload = {
            "skill_name": skill_name,
            "task_payload": task_payload,
            "result": result,
        }
        verdict = review_run(review_payload, config)
        log.info(f"Review verdict for '{skill_name}': {verdict.get('verdict')} "
                 f"(confidence={verdict.get('confidence', '?')})")
        return verdict
    except Exception as e:
        log.warning(f"Review failed for '{skill_name}': {e} — auto-passing")
        return {"verdict": "PASS", "critique": f"Review error: {e}", "confidence": 0.0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    args = parser.parse_args()

    role = args.role
    log = setup_logging(role)
    config = load_config()

    db.init_db()
    db.register_agent(role, role, os.getpid())

    # Load role-based skill affinity from config
    base_role = role.split("_")[0]
    affinity_skills = config.get("affinity", {}).get(base_role, None)
    if affinity_skills:
        log.info(f"Skill affinity: {', '.join(affinity_skills)}")

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

        # Pause during hw_supervisor model transitions
        try:
            if HW_STATE_FILE.exists():
                hw = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
                if hw.get("status") == "transitioning":
                    log.info("HW transition in progress — pausing task claims")
                    time.sleep(5)
                    continue
        except Exception:
            pass

        # Check inbox for broadcast/direct messages — act on known types
        paused = getattr(main, '_paused', False)
        try:
            msgs = db.get_messages(role, unread_only=True, limit=5)
            for m in msgs:
                log.info(f"Message from {m['from_agent']}: {m['body_json']}")
                try:
                    body = json.loads(m['body_json']) if isinstance(m['body_json'], str) else m['body_json']
                except Exception:
                    body = {}
                msg_type = body.get("type", "")
                if msg_type == "pause":
                    log.info("Received PAUSE command — suspending task claims")
                    paused = True
                    main._paused = True
                elif msg_type == "resume":
                    log.info("Received RESUME command — resuming task claims")
                    paused = False
                    main._paused = False
                elif msg_type == "ping":
                    log.info(f"PING from {m['from_agent']} — responding")
                    db.post_message(role, m['from_agent'],
                                    json.dumps({"type": "pong", "status": "alive", "role": role}))
                elif msg_type == "human_response":
                    tid = body.get("task_id")
                    log.info(f"Human response received for task {tid}")
                elif msg_type == "config_reload":
                    log.info("Reloading config")
                    config = load_config()
        except Exception:
            pass

        if paused:
            db.heartbeat(role, status='PAUSED')
            time.sleep(5)
            continue

        # Check if quarantined by watchdog
        try:
            from db import get_conn
            with get_conn() as _conn:
                _row = _conn.execute(
                    "SELECT status FROM agents WHERE name=?", (role,)).fetchone()
                if _row and _row["status"] == "QUARANTINED":
                    log.warning("Agent quarantined by watchdog — pausing claims")
                    time.sleep(10)
                    continue
        except Exception:
            pass

        task = db.claim_task(role, affinity_skills=affinity_skills)
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
                # Evaluator-Optimizer: route high-stakes skills through review
                if _should_review(task['type'], config, payload):
                    verdict = _run_review(task['type'], payload, result, config, log)
                    if verdict.get("verdict") == "FAIL":
                        rounds = db.reject_task(task['id'], verdict.get("critique", ""))
                        log.info(f"Task {task['id']} REVIEW FAIL (round {rounds}): {verdict.get('critique', '')[:100]}")
                    else:
                        db.complete_task(task['id'], json.dumps(result))
                        log.info(f"Task {task['id']} REVIEW PASS → done")
                else:
                    db.complete_task(task['id'], json.dumps(result))
                    log.info(f"Task {task['id']} done")
            except Exception as e:
                err_str = str(e).lower()
                # Overload / Network Drop detection
                if any(k in err_str for k in ("timeout", "connection", "rate limit", "503", "502")):
                    log.warning(f"Task {task['id']} failed due to overload/timeout. Re-queuing...")
                    db.requeue_task(task['id'])
                    time.sleep(10)  # Back off to let the system recover
                else:
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
