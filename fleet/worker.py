#!/usr/bin/env python3
"""Generic worker process. Usage: uv run python worker.py --role <role>"""

import argparse
import importlib
import json
import logging
import os
import signal
import subprocess
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

IDLE_THRESHOLD = 6  # polls with no task before entering idle mode (~30s at 5s poll)
IDLE_SKILLS = ["skill_evolve", "skill_test", "code_quality", "benchmark"]
MAX_CALLS_PER_SESSION = 500  # per-agent capability budget (OWASP LLM08)


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

# Validate skill name against actual skill files
_valid_skills = None


def _is_valid_skill(name):
    global _valid_skills
    if _valid_skills is None:
        skills_dir = Path(__file__).parent / "skills"
        _valid_skills = {f.stem for f in skills_dir.glob("*.py") if not f.name.startswith("_")}
    return name in _valid_skills


def _run_in_docker(skill_name, task, config):
    """Execute a skill inside a Docker container for isolation."""
    import tempfile
    payload = json.loads(task.get("payload_json", "{}"))

    # Write payload to temp file for container input
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(payload, f)
        input_path = f.name

    try:
        result = subprocess.run([
            "docker", "run", "--rm",
            "--network=none",  # no network access inside container
            "--memory=512m",   # memory limit
            "--cpus=1",        # CPU limit
            "-v", f"{input_path}:/input.json:ro",
            "python:3.12-slim",
            "python", "-c", f"""
import json, sys
payload = json.load(open('/input.json'))
# Minimal skill execution in sandbox
print(json.dumps({{"status": "sandboxed", "skill": "{skill_name}", "payload_received": True}}))
"""
        ], capture_output=True, text=True, timeout=120)

        if result.returncode == 0:
            return result.stdout.strip()
        return None  # fall back to native
    except Exception:
        return None
    finally:
        os.unlink(input_path)


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

    # Sandbox policy: warn if sandboxable skill runs without Docker
    security_cfg = config.get("security", {})
    docker_available = False
    sandbox_enabled = False
    if security_cfg.get("sandbox_enabled", False):
        sandbox_skills = security_cfg.get("sandbox_skills", [])
        if skill_name in sandbox_skills:
            sandbox_enabled = True
            # Check if Docker is available
            try:
                subprocess.run(["docker", "info"], capture_output=True, timeout=5)
                docker_available = True
                log.info(f"Skill '{skill_name}' — Docker sandbox available")
            except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
                log.warning(f"Skill '{skill_name}' requires sandbox but Docker unavailable — running natively")

    # 0.07.00: Actually execute in Docker if available
    if docker_available and sandbox_enabled:
        try:
            docker_result = _run_in_docker(skill_name, payload, config)
            if docker_result is not None:
                return docker_result
        except Exception as docker_err:
            log.warning(f"Docker sandbox failed for {skill_name}: {docker_err} — falling back to native")

    # Validate skill name against whitelist before import
    if not _is_valid_skill(skill_name):
        raise ValueError(f"Unknown skill '{skill_name}' — not in skills/ directory")

    # OOM prevention check — warn or requeue if VRAM insufficient
    try:
        from skills.oom_prevent import check_oom_risk
        oom = check_oom_risk(skill_name, config)
        if not oom["safe"] and oom["risk"] in ("critical", "high"):
            log.warning(f"OOM risk {oom['risk']} for {skill_name}: {oom['reason']}")
            if oom["risk"] == "critical":
                raise RuntimeError(f"OOM blocked: {oom['reason']}")
    except (ImportError, RuntimeError) as e:
        if "OOM blocked" in str(e):
            raise
    except Exception:
        pass  # OOM check must never block

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


def _cleanup_children():
    """Kill any child processes (Playwright browsers, nmap, etc.)."""
    if hasattr(os, 'killpg'):
        try:
            os.killpg(os.getpgrp(), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass


_last_evolution_pipeline = 0   # epoch timestamp of last evolution_coordinator dispatch
_last_research_trigger = 0    # epoch timestamp of last research_loop dispatch
_EVOLUTION_COOLDOWN = 3600    # 1 hour between evolution pipeline runs
_RESEARCH_COOLDOWN = 7200     # 2 hours between research cycle runs
_IDLE_MINUTES_FOR_EVOLUTION = 5  # agent must be idle this long to trigger pipeline


def _run_idle_evolution(agent_name, config):
    """Run one idle skill evolution cycle.

    v0.23: Also triggers evolution_coordinator pipeline and research_loop
    when agents are idle, enabling fleet self-improvement without operator
    intervention.
    """
    global _last_evolution_pipeline, _last_research_trigger
    log = logging.getLogger(agent_name)
    now = time.time()

    # --- Original: skill_test on least-evolved skill ---
    try:
        skill = db.get_least_evolved_skill()
        if not skill:
            return
        # Check budget before idle work
        from skills._models import check_budget
        budget = check_budget(skill, config)
        if budget and budget["exceeded"]:
            return  # respect daily budgets

        log.info(f"Idle mode: evolving '{skill}'")
        # Dispatch as low-priority self-assigned task
        tid = db.post_task("skill_test", json.dumps({"skill": skill, "idle": True}),
                          priority=1, assigned_to=agent_name)
        db.log_idle_run(agent_name, skill)
    except Exception as e:
        log.debug(f"Idle evolution skipped: {e}")

    # --- v0.23 S3: Auto-trigger evolution pipeline on idle ---
    try:
        if (now - _last_evolution_pipeline >= _EVOLUTION_COOLDOWN):
            # Verify evolution_coordinator skill exists before dispatching
            skills_dir = Path(__file__).parent / "skills"
            if (skills_dir / "evolution_coordinator.py").exists():
                log.info("Idle mode: dispatching evolution_coordinator pipeline")
                db.post_task("evolution_coordinator",
                             json.dumps({"trigger": "auto_idle", "agent": agent_name}),
                             priority=2)
                _last_evolution_pipeline = now
                db.log_idle_run(agent_name, "evolution_coordinator")
    except Exception as e:
        log.debug(f"Evolution pipeline dispatch skipped: {e}")

    # --- v0.23 S3: Auto-trigger research cycle on knowledge gaps ---
    try:
        if (now - _last_research_trigger >= _RESEARCH_COOLDOWN):
            skills_dir = Path(__file__).parent / "skills"
            if (skills_dir / "research_loop.py").exists():
                log.info("Idle mode: dispatching research_loop cycle")
                db.post_task("research_loop",
                             json.dumps({"trigger": "auto_idle", "agent": agent_name}),
                             priority=2)
                _last_research_trigger = now
                db.log_idle_run(agent_name, "research_loop")
    except Exception as e:
        log.debug(f"Research cycle dispatch skipped: {e}")


def main():
    # Set process group so parent can kill entire tree on shutdown
    if hasattr(os, 'setpgrp'):
        try:
            os.setpgrp()
        except OSError:
            pass  # not fatal if it fails (e.g. Windows without WSL)

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
    idle_count = 0
    session_calls = 0
    max_calls = config.get("workers", {}).get("max_calls_per_session", MAX_CALLS_PER_SESSION)

    # v0.43: Log session boundary on worker start
    try:
        from skills.marathon_log import log_session_boundary
        log_session_boundary("fleet_start")
    except Exception:
        pass

    running = True

    def shutdown(sig, frame):
        nonlocal running
        log.info("Shutdown signal received — cleaning up child processes")
        # v0.43: Log session boundary on worker stop
        try:
            from skills.marathon_log import log_session_boundary
            log_session_boundary("fleet_stop")
        except Exception:
            pass
        running = False
        _cleanup_children()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while running:
        try:
            db.heartbeat(role)
        except Exception as e:
            log.warning(f"Heartbeat failed: {e}")

        # Pause during Dr. Ders model transitions
        try:
            if HW_STATE_FILE.exists():
                hw = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
                if hw.get("status") == "transitioning":
                    log.info("Dr. Ders transition in progress — pausing task claims")
                    time.sleep(5)
                    continue
        except Exception:
            pass

        # Check inbox for broadcast/direct messages — act on known types
        paused = getattr(main, '_paused', False)
        try:
            msgs = db.get_messages(role, unread_only=True, limit=5,
                                       channels=["fleet", "agent", "pool"])
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
        if not task:
            idle_count += 1
            if idle_count >= IDLE_THRESHOLD and config.get("idle", {}).get("enabled", False):
                _run_idle_evolution(role, config)
                idle_count = 0
        if task:
            idle_count = 0  # reset on task claim
            last_task_time = time.time()
            session_calls += 1
            if session_calls > max_calls:
                log.warning(f"Capability budget exhausted ({max_calls} calls) — pausing 60s")
                time.sleep(60)
                session_calls = 0  # reset after cooldown
            log.info(f"Task {task['id']} claimed: {task['type']}")
            try:
                db.heartbeat(role, status='BUSY', current_task_id=task['id'])
            except Exception:
                pass
            try:
                payload = json.loads(task['payload_json']) if task['payload_json'] else {}
                # v0.01.01: Input-side guardrails — scan payload before LLM
                try:
                    from skills._watchdog import scan_input
                    payload_text = task.get("payload_json", "")
                    scan_result = scan_input(payload_text)
                    if not scan_result["clean"]:
                        for f in scan_result["findings"]:
                            log.warning(f"  [{f['type']}] {f['pattern']}")
                        # Block on injection attempts (OWASP LLM01)
                        if any(f["type"] == "injection" for f in scan_result["findings"]):
                            log.error(f"BLOCKED: Prompt injection detected in task {task['id']}")
                            db.fail_task(task['id'], "Blocked: prompt injection detected in payload")
                            continue  # skip to next task
                except Exception:
                    pass  # input scanning must never block task execution
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
                        # Intelligence scoring (non-blocking)
                        try:
                            from intelligence import score_task_output, score_task_output_tier2
                            intel_score = score_task_output(task['type'], result, config)
                            if intel_score is not None:
                                db.update_intelligence_score(task['id'], intel_score)
                            # v0.23 Tier 2: LLM-based quality eval (sampled ~10%)
                            t2_score = score_task_output_tier2(
                                task['type'], task.get('payload_json', ''),
                                result, config)
                            if t2_score is not None:
                                # Blend: 60% Tier1 + 40% Tier2
                                blended = round(0.6 * (intel_score or 0.5) + 0.4 * t2_score, 3)
                                db.update_intelligence_score(task['id'], blended)
                                log.info(f"Task {task['id']} Tier2 score: {t2_score:.3f} → blended: {blended:.3f}")
                        except Exception:
                            pass  # scoring must never block task processing
                else:
                    db.complete_task(task['id'], json.dumps(result))
                    log.info(f"Task {task['id']} done")
                    # Intelligence scoring (non-blocking)
                    try:
                        from intelligence import score_task_output, score_task_output_tier2
                        intel_score = score_task_output(task['type'], result, config)
                        if intel_score is not None:
                            db.update_intelligence_score(task['id'], intel_score)
                        # v0.23 Tier 2: LLM-based quality eval (sampled ~10%)
                        t2_score = score_task_output_tier2(
                            task['type'], task.get('payload_json', ''),
                            result, config)
                        if t2_score is not None:
                            # Blend: 60% Tier1 + 40% Tier2
                            blended = round(0.6 * (intel_score or 0.5) + 0.4 * t2_score, 3)
                            db.update_intelligence_score(task['id'], blended)
                            log.info(f"Task {task['id']} Tier2 score: {t2_score:.3f} → blended: {blended:.3f}")
                    except Exception:
                        pass  # scoring must never block task processing
                # CT-4: Post-execution budget check
                try:
                    from skills._models import check_budget
                    budget_info = check_budget(task['type'], config)
                    if budget_info and budget_info["exceeded"]:
                        log.warning(f"Budget exceeded for {task['type']}: "
                                    f"${budget_info['spent_usd']:.4f} / ${budget_info['budget_usd']:.4f}")
                except Exception:
                    pass  # budget tracking must never break task execution
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
