"""Self-Healing Fleet + Performance Auto-Rollback (v0.200.00b).

Automated recovery for stuck agents, failed tasks, and skill regressions.
Integrates with supervisor.py (health sweep), dashboard.py (REST endpoints),
and regression_detector.py (quality baseline data).

Circuit breakers prevent cascading failures by temporarily disabling skills
that exceed failure thresholds. Performance rollback restores prior skill
versions from knowledge/code_drafts/ when success rates regress.
"""
import json
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("self_healing")

FLEET_DIR = Path(__file__).parent

# ── In-memory circuit breaker state ──────────────────────────────────────────
_breakers = {}  # skill_name -> {"failures": [(ts, error)], "tripped_at": ts|None}
_breaker_lock = threading.Lock()

# ── Recovery action log (in-memory ring buffer for dashboard) ────────────────
_recovery_log = []  # list of dicts: {ts, action, target, detail}
_recovery_lock = threading.Lock()
_MAX_RECOVERY_LOG = 200


def _cfg():
    """Load [self_healing] config from fleet.toml with safe defaults."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("self_healing", {})
    except Exception:
        return {}


def _default(key, fallback):
    """Get a self_healing config value with fallback."""
    return _cfg().get(key, fallback)


def _log_recovery(action: str, target: str, detail: str = ""):
    """Record a recovery action for dashboard visibility and audit trail."""
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "action": action,
        "target": target,
        "detail": detail,
    }
    with _recovery_lock:
        _recovery_log.append(entry)
        if len(_recovery_log) > _MAX_RECOVERY_LOG:
            _recovery_log[:] = _recovery_log[-_MAX_RECOVERY_LOG:]
    # Also persist to audit_log for SOC 2 compliance
    try:
        from audit_log import log_event
        log_event("self_healing", "self_healing", entry, severity="warning")
    except Exception:
        pass


# ── Agent Health ─────────────────────────────────────────────────────────────

def check_agent_health(agent_name: str) -> dict:
    """Check if an agent is responsive based on heartbeat and error rate.

    Returns dict with keys: healthy (bool), last_heartbeat, error_rate,
    active_task, idle_secs, issues (list of strings).
    """
    import db
    result = {
        "agent": agent_name,
        "healthy": True,
        "last_heartbeat": None,
        "error_rate": 0.0,
        "active_task": None,
        "idle_secs": 0,
        "issues": [],
    }
    try:
        with db.get_conn() as conn:
            agent = conn.execute(
                "SELECT status, last_heartbeat, current_task_id, pid "
                "FROM agents WHERE name = ?", (agent_name,)
            ).fetchone()
            if not agent:
                result["healthy"] = False
                result["issues"].append("agent_not_found")
                return result

            result["last_heartbeat"] = agent["last_heartbeat"]
            result["active_task"] = agent["current_task_id"]

            # Check heartbeat freshness
            if agent["last_heartbeat"]:
                try:
                    hb = datetime.fromisoformat(agent["last_heartbeat"])
                    delta = (datetime.utcnow() - hb).total_seconds()
                    result["idle_secs"] = int(delta)
                    stuck_timeout = _default("agent_stuck_timeout", 300)
                    if delta > stuck_timeout:
                        result["healthy"] = False
                        result["issues"].append(f"no_heartbeat_{int(delta)}s")
                except Exception:
                    pass

            # Check recent error rate (last 30 tasks)
            recent = conn.execute(
                "SELECT status FROM tasks WHERE assigned_to = ? "
                "ORDER BY id DESC LIMIT 30", (agent_name,)
            ).fetchall()
            if recent:
                failed = sum(1 for r in recent if r["status"] == "FAILED")
                result["error_rate"] = round(failed / len(recent), 3)
                if result["error_rate"] > 0.5:
                    result["healthy"] = False
                    result["issues"].append(f"high_error_rate_{result['error_rate']}")

            # Check if PID is alive (via psutil if available)
            if agent["pid"]:
                try:
                    import psutil
                    if not psutil.pid_exists(agent["pid"]):
                        result["healthy"] = False
                        result["issues"].append("pid_dead")
                except ImportError:
                    pass  # psutil optional
    except Exception as e:
        log.warning("check_agent_health failed for %s: %s", agent_name, e)
        result["healthy"] = False
        result["issues"].append(f"check_error: {e}")
    return result


def recover_agent(agent_name: str) -> dict:
    """Kill and restart an unresponsive agent by resetting its DB state.

    The supervisor main loop will detect the agent as needing respawn
    on its next iteration. This function handles the DB cleanup.
    """
    import db
    result = {"agent": agent_name, "recovered": False, "detail": ""}
    try:
        # Kill the process if PID is known
        pid = None
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT pid FROM agents WHERE name = ?", (agent_name,)
            ).fetchone()
            if row:
                pid = row["pid"]

        if pid:
            try:
                import psutil
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                result["detail"] = f"terminated pid {pid}"
            except ImportError:
                # No psutil — signal approach
                import os
                import signal
                try:
                    os.kill(pid, signal.SIGTERM)
                    result["detail"] = f"sent SIGTERM to pid {pid}"
                except OSError:
                    result["detail"] = f"pid {pid} already dead"
            except Exception as e:
                result["detail"] = f"kill failed: {e}"

        # Reset agent state so supervisor can respawn
        def _reset():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE agents SET status='IDLE', current_task_id=NULL, pid=NULL "
                    "WHERE name = ?", (agent_name,)
                )
                # Requeue any RUNNING tasks that were assigned to this agent
                conn.execute(
                    "UPDATE tasks SET status='PENDING', assigned_to=NULL "
                    "WHERE assigned_to = ? AND status = 'RUNNING'",
                    (agent_name,)
                )
        db._retry_write(_reset)
        result["recovered"] = True
        _log_recovery("recover_agent", agent_name, result["detail"])
        log.info("Recovered agent %s: %s", agent_name, result["detail"])
    except Exception as e:
        log.warning("recover_agent failed for %s: %s", agent_name, e)
        result["detail"] = f"error: {e}"
    return result


# ── Task Retry ───────────────────────────────────────────────────────────────

def retry_failed_task(task_id: int, max_retries: int = 3) -> dict:
    """Requeue a failed task with exponential backoff tracking.

    Stores retry count in payload_json._retry_count. Refuses to retry
    beyond max_retries.
    """
    import db
    result = {"task_id": task_id, "retried": False, "detail": ""}
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT status, type, payload_json, assigned_to "
                "FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                result["detail"] = "task_not_found"
                return result
            if row["status"] != "FAILED":
                result["detail"] = f"task_status_is_{row['status']}"
                return result

            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}

            retry_count = payload.get("_retry_count", 0)
            if retry_count >= max_retries:
                result["detail"] = f"max_retries_exceeded ({retry_count}/{max_retries})"
                return result

            payload["_retry_count"] = retry_count + 1
            payload["_last_retry_ts"] = datetime.utcnow().isoformat()

        def _requeue():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET status='PENDING', assigned_to=NULL, "
                    "error=NULL, result_json=NULL, payload_json=? WHERE id=?",
                    (json.dumps(payload), task_id)
                )
        db._retry_write(_requeue)
        result["retried"] = True
        result["detail"] = f"retry {retry_count + 1}/{max_retries}"
        _log_recovery("retry_task", f"task_{task_id} ({row['type']})", result["detail"])
        log.info("Retried task %d (%s): %s", task_id, row["type"], result["detail"])
    except Exception as e:
        log.warning("retry_failed_task failed for %d: %s", task_id, e)
        result["detail"] = f"error: {e}"
    return result


# ── Circuit Breaker ──────────────────────────────────────────────────────────

def circuit_breaker_record_failure(skill_name: str, error: str = ""):
    """Record a skill failure for circuit breaker evaluation."""
    now = time.time()
    with _breaker_lock:
        if skill_name not in _breakers:
            _breakers[skill_name] = {"failures": [], "tripped_at": None}
        _breakers[skill_name]["failures"].append((now, error[:200]))


def circuit_breaker_is_open(skill_name: str) -> bool:
    """Check if a skill's circuit breaker is tripped (open).

    Returns True if the skill should be temporarily disabled.
    """
    threshold = _default("circuit_breaker_threshold", 3)
    window = _default("circuit_breaker_window", 300)
    now = time.time()

    with _breaker_lock:
        state = _breakers.get(skill_name)
        if not state:
            return False

        # If already tripped, check if cooldown has elapsed
        if state["tripped_at"]:
            if now - state["tripped_at"] > window:
                # Reset — allow the skill to try again (half-open)
                state["tripped_at"] = None
                state["failures"] = []
                log.info("Circuit breaker reset for skill %s", skill_name)
                _log_recovery("circuit_breaker_reset", skill_name)
                return False
            return True

        # Count recent failures within window
        recent = [(ts, err) for ts, err in state["failures"] if now - ts <= window]
        state["failures"] = recent  # prune old entries

        if len(recent) >= threshold:
            state["tripped_at"] = now
            log.warning("Circuit breaker TRIPPED for skill %s (%d failures in %ds)",
                        skill_name, len(recent), window)
            _log_recovery("circuit_breaker_trip", skill_name,
                          f"{len(recent)} failures in {window}s")
            return True
    return False


def get_circuit_breaker_status() -> list:
    """Return current state of all circuit breakers for dashboard."""
    now = time.time()
    window = _default("circuit_breaker_window", 300)
    result = []
    with _breaker_lock:
        for skill_name, state in _breakers.items():
            recent = [f for f in state["failures"] if now - f[0] <= window]
            result.append({
                "skill": skill_name,
                "tripped": state["tripped_at"] is not None,
                "tripped_at": datetime.utcfromtimestamp(state["tripped_at"]).isoformat()
                    if state["tripped_at"] else None,
                "recent_failures": len(recent),
                "last_error": recent[-1][1] if recent else "",
                "cooldown_remaining": max(0, int(window - (now - state["tripped_at"])))
                    if state["tripped_at"] else 0,
            })
    return result


# ── Health Sweep ─────────────────────────────────────────────────────────────

def run_health_sweep() -> dict:
    """Check all agents and recover any that are stuck.

    Called periodically from supervisor main loop. Returns summary of actions.
    """
    if not _default("enabled", True):
        return {"skipped": True, "reason": "self_healing disabled"}

    import db
    max_retries = _default("max_task_retries", 3)
    summary = {"checked": 0, "recovered_agents": [], "retried_tasks": [], "errors": []}

    try:
        # 1. Check all registered agents
        with db.get_conn() as conn:
            agents = conn.execute("SELECT name FROM agents").fetchall()

        for row in agents:
            name = row["name"]
            summary["checked"] += 1
            health = check_agent_health(name)
            if not health["healthy"]:
                log.warning("Unhealthy agent %s: %s", name, health["issues"])
                result = recover_agent(name)
                if result["recovered"]:
                    summary["recovered_agents"].append(name)

        # 2. Auto-retry recently failed tasks (not already retried to max)
        with db.get_conn() as conn:
            failed = conn.execute(
                "SELECT id, type, payload_json FROM tasks "
                "WHERE status = 'FAILED' "
                "AND created_at >= datetime('now', '-1 hour') "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()

        for task in failed:
            try:
                payload = json.loads(task["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            retry_count = payload.get("_retry_count", 0)
            if retry_count < max_retries:
                result = retry_failed_task(task["id"], max_retries)
                if result["retried"]:
                    summary["retried_tasks"].append(task["id"])
    except Exception as e:
        log.warning("Health sweep error: %s", e)
        summary["errors"].append(str(e))

    if summary["recovered_agents"] or summary["retried_tasks"]:
        log.info("Health sweep: recovered %d agents, retried %d tasks",
                 len(summary["recovered_agents"]), len(summary["retried_tasks"]))
    return summary


# ── Skill Regression Detection ───────────────────────────────────────────────

def detect_skill_regression(skill_name: str, window_hours: int = 6) -> bool:
    """Compare recent success rate vs 7-day baseline.

    Returns True if the skill has regressed (success rate dropped
    by more than regression_threshold).
    """
    import db
    threshold = _default("regression_threshold", 0.20)
    try:
        with db.get_conn() as conn:
            # Recent window
            recent = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE type = ? "
                "AND created_at >= datetime('now', ?)",
                (skill_name, f"-{window_hours} hours")
            ).fetchone()

            # 7-day baseline (excluding the recent window)
            baseline = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE type = ? "
                "AND created_at >= datetime('now', '-7 days') "
                "AND created_at < datetime('now', ?)",
                (skill_name, f"-{window_hours} hours")
            ).fetchone()

            if not baseline or baseline["total"] < 5:
                return False  # not enough baseline data
            if not recent or recent["total"] < 3:
                return False  # not enough recent data

            baseline_rate = baseline["done"] / baseline["total"]
            recent_rate = recent["done"] / recent["total"]
            drop = baseline_rate - recent_rate

            if drop > threshold:
                log.warning("Skill regression: %s success rate dropped %.1f%% "
                            "(baseline: %.1f%% -> recent: %.1f%%)",
                            skill_name, drop * 100, baseline_rate * 100,
                            recent_rate * 100)
                return True
    except Exception as e:
        log.warning("detect_skill_regression error for %s: %s", skill_name, e)
    return False


def get_rollback_candidates() -> list:
    """Find skills with >regression_threshold success rate drop in last 6 hours."""
    import db
    candidates = []
    try:
        with db.get_conn() as conn:
            skills = conn.execute(
                "SELECT DISTINCT type FROM tasks "
                "WHERE created_at >= datetime('now', '-6 hours') "
                "AND type IS NOT NULL"
            ).fetchall()

        for row in skills:
            skill_name = row["type"]
            if detect_skill_regression(skill_name):
                # Check if a draft backup exists
                drafts_dir = FLEET_DIR / "knowledge" / "code_drafts"
                has_backup = False
                backup_file = None
                if drafts_dir.exists():
                    matches = sorted(drafts_dir.glob(f"{skill_name}_draft_*.py"),
                                     reverse=True)
                    if matches:
                        has_backup = True
                        backup_file = str(matches[0])
                candidates.append({
                    "skill": skill_name,
                    "has_backup": has_backup,
                    "backup_file": backup_file,
                    "detected_at": datetime.utcnow().isoformat(),
                })
    except Exception as e:
        log.warning("get_rollback_candidates error: %s", e)
    return candidates


def rollback_skill(skill_name: str) -> dict:
    """Restore a skill from its most recent code_drafts backup.

    Only operates if auto_rollback_enabled is true and a prior draft exists.
    """
    result = {"skill": skill_name, "rolled_back": False, "detail": ""}

    if not _default("auto_rollback_enabled", True):
        result["detail"] = "auto_rollback_disabled"
        return result

    skill_file = FLEET_DIR / "skills" / f"{skill_name}.py"
    drafts_dir = FLEET_DIR / "knowledge" / "code_drafts"

    if not skill_file.exists():
        result["detail"] = "skill_file_not_found"
        return result

    if not drafts_dir.exists():
        result["detail"] = "no_code_drafts_directory"
        return result

    # Find the most recent draft
    matches = sorted(drafts_dir.glob(f"{skill_name}_draft_*.py"), reverse=True)
    if not matches:
        result["detail"] = "no_draft_backup_available"
        return result

    backup_source = matches[0]
    try:
        # Save current version as a pre-rollback backup
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_rollback = drafts_dir / f"{skill_name}_pre_rollback_{ts}.py"
        shutil.copy2(str(skill_file), str(pre_rollback))

        # Restore the draft
        shutil.copy2(str(backup_source), str(skill_file))

        result["rolled_back"] = True
        result["detail"] = f"restored from {backup_source.name}, pre-rollback saved to {pre_rollback.name}"
        _log_recovery("rollback_skill", skill_name, result["detail"])
        log.info("Rolled back skill %s: %s", skill_name, result["detail"])
    except Exception as e:
        log.warning("rollback_skill failed for %s: %s", skill_name, e)
        result["detail"] = f"error: {e}"
    return result


# ── Dashboard Data ───────────────────────────────────────────────────────────

def get_agent_health_summary() -> list:
    """Per-agent health status for dashboard."""
    import db
    agents = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT name FROM agents").fetchall()
        for row in rows:
            agents.append(check_agent_health(row["name"]))
    except Exception as e:
        log.warning("get_agent_health_summary error: %s", e)
    return agents


def get_skill_health_summary() -> list:
    """Skill success rates with regression flags for dashboard."""
    import db
    skills = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT type as skill, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done, "
                "SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed, "
                "ROUND(AVG(intelligence_score), 3) as avg_iq "
                "FROM tasks "
                "WHERE created_at >= datetime('now', '-24 hours') "
                "AND type IS NOT NULL "
                "GROUP BY type ORDER BY total DESC"
            ).fetchall()

        for row in rows:
            total = row["total"] or 1
            success_rate = round((row["done"] or 0) / total, 3)
            regressed = detect_skill_regression(row["skill"])
            breaker_open = circuit_breaker_is_open(row["skill"])
            skills.append({
                "skill": row["skill"],
                "total_24h": total,
                "success_rate": success_rate,
                "failed_24h": row["failed"] or 0,
                "avg_iq": row["avg_iq"],
                "regressed": regressed,
                "circuit_breaker_open": breaker_open,
            })
    except Exception as e:
        log.warning("get_skill_health_summary error: %s", e)
    return skills


def get_recovery_log() -> list:
    """Return recent recovery actions for dashboard."""
    with _recovery_lock:
        return list(_recovery_log)
