#!/usr/bin/env python3
"""Unified health monitoring, recovery, and protection.

Absorbs self_healing.py and diagnostics.py. Provides:
- HealthMonitor class (tick-based, called from supervisor main loop)
- Module-level standalone functions (backward-compatible with self_healing/diagnostics imports)
"""

import collections
import gc
import json
import logging
import os
import random
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("self_healing")

FLEET_DIR = Path(__file__).parent

# ── Memory watchdog constants ─────────────────────────────────────────
_WORKER_RSS_WARN_MB = 300
_WORKER_RSS_CRITICAL_MB = 600
_HW_SUP_RSS_CRITICAL_MB = 400
_SUP_SELF_RSS_WARN_MB = 200
_MEMORY_WATCHDOG_INTERVAL = 300

# ── Supervisor health intervals ───────────────────────────────────────
STALE_TASK_RECOVERY_INTERVAL = 300
STALE_TASK_TIMEOUT = 900
WATCHDOG_INTERVAL = 60
WATCHDOG_FULL_INTERVAL = 600

# ── In-memory circuit breaker state ──────────────────────────────────
_MAX_BREAKER_FAILURES = 1000
_BREAKER_TRIM_TARGET = 500
_breakers = {}
_breaker_lock = threading.Lock()

# ── Recovery action log ──────────────────────────────────────────────
_recovery_log = collections.deque(maxlen=200)
_recovery_lock = threading.Lock()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Config helpers (from self_healing.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cfg():
    """Load [self_healing] config from fleet.toml with safe defaults."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("self_healing", {})
    except Exception:
        return {}


def _default(key, fallback):
    return _cfg().get(key, fallback)


def _log_recovery(action: str, target: str, detail: str = ""):
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "action": action,
        "target": target,
        "detail": detail,
    }
    with _recovery_lock:
        _recovery_log.append(entry)  # deque handles eviction automatically
    try:
        from audit_log import log_event
        log_event("self_healing", "self_healing", entry, severity="warning")
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Standalone functions — from self_healing.py (unchanged signatures)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_agent_health(agent_name: str) -> dict:
    """Check if an agent is responsive based on heartbeat and error rate."""
    import db
    result = {
        "agent": agent_name, "healthy": True, "last_heartbeat": None,
        "error_rate": 0.0, "active_task": None, "idle_secs": 0, "issues": [],
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
            recent = conn.execute(
                "SELECT status FROM tasks WHERE assigned_to = ? "
                "AND classification != 'synthetic_prefix' "
                "ORDER BY id DESC LIMIT 30", (agent_name,)
            ).fetchall()
            if recent:
                failed = sum(1 for r in recent if r["status"] == "FAILED")
                result["error_rate"] = round(failed / len(recent), 3)
                if result["error_rate"] > 0.5:
                    result["healthy"] = False
                    result["issues"].append(f"high_error_rate_{result['error_rate']}")
            if agent["pid"]:
                try:
                    import psutil
                    if not psutil.pid_exists(agent["pid"]):
                        result["healthy"] = False
                        result["issues"].append("pid_dead")
                except ImportError:
                    pass
    except Exception as e:
        log.warning("check_agent_health failed for %s: %s", agent_name, e)
        result["healthy"] = False
        result["issues"].append(f"check_error: {e}")
    return result


def recover_agent(agent_name: str) -> dict:
    """Kill and restart an unresponsive agent by resetting its DB state."""
    import db
    result = {"agent": agent_name, "recovered": False, "detail": ""}
    try:
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
                import signal as _sig
                try:
                    os.kill(pid, _sig.SIGTERM)
                    result["detail"] = f"sent SIGTERM to pid {pid}"
                except OSError:
                    result["detail"] = f"pid {pid} already dead"
            except Exception as e:
                result["detail"] = f"kill failed: {e}"

        def _reset():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE agents SET status='IDLE', current_task_id=NULL, pid=NULL "
                    "WHERE name = ?", (agent_name,))
                conn.execute(
                    "UPDATE tasks SET status='PENDING', assigned_to=NULL "
                    "WHERE assigned_to = ? AND status = 'RUNNING'", (agent_name,))
        db._retry_write(_reset)
        result["recovered"] = True
        _log_recovery("recover_agent", agent_name, result["detail"])
        log.info("Recovered agent %s: %s", agent_name, result["detail"])
    except Exception as e:
        log.warning("recover_agent failed for %s: %s", agent_name, e)
        result["detail"] = f"error: {e}"
    return result


def retry_failed_task(task_id: int, max_retries: int = 3) -> dict:
    """Requeue a failed task with exponential backoff tracking."""
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
                    (json.dumps(payload), task_id))
        db._retry_write(_requeue)
        result["retried"] = True
        result["detail"] = f"retry {retry_count + 1}/{max_retries}"
        _log_recovery("retry_task", f"task_{task_id} ({row['type']})", result["detail"])
        log.info("Retried task %d (%s): %s", task_id, row["type"], result["detail"])
    except Exception as e:
        log.warning("retry_failed_task failed for %d: %s", task_id, e)
        result["detail"] = f"error: {e}"
    return result


# ── Circuit Breaker ──────────────────────────────────────────────────

def circuit_breaker_record_failure(skill_name: str, error: str = ""):
    """Record a skill failure for circuit breaker evaluation."""
    now = time.time()
    with _breaker_lock:
        if skill_name not in _breakers:
            _breakers[skill_name] = {"failures": [], "tripped_at": None}
        _breakers[skill_name]["failures"].append((now, error[:200]))
        # Cap: trim to most recent _BREAKER_TRIM_TARGET when exceeding max
        if len(_breakers[skill_name]["failures"]) > _MAX_BREAKER_FAILURES:
            _breakers[skill_name]["failures"] = _breakers[skill_name]["failures"][-_BREAKER_TRIM_TARGET:]


def circuit_breaker_is_open(skill_name: str) -> bool:
    """Check if a skill's circuit breaker is tripped (open)."""
    threshold = _default("circuit_breaker_threshold", 3)
    window = _default("circuit_breaker_window", 300)
    now = time.time()
    with _breaker_lock:
        state = _breakers.get(skill_name)
        if not state:
            return False
        if state["tripped_at"]:
            if now - state["tripped_at"] > window:
                state["tripped_at"] = None
                state["failures"] = []
                log.info("Circuit breaker reset for skill %s", skill_name)
                _log_recovery("circuit_breaker_reset", skill_name)
                return False
            return True
        recent = [(ts, err) for ts, err in state["failures"] if now - ts <= window]
        state["failures"] = recent
        if not recent and not state["tripped_at"]:
            # No recent failures and not tripped — clean up entirely
            del _breakers[skill_name]
            return False
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


# ── Health Sweep ─────────────────────────────────────────────────────

def run_health_sweep() -> dict:
    """Check all agents and recover any that are stuck."""
    if not _default("enabled", True):
        return {"skipped": True, "reason": "self_healing disabled"}
    import db
    max_retries = _default("max_task_retries", 3)
    summary = {"checked": 0, "recovered_agents": [], "retried_tasks": [], "errors": []}
    try:
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
        _NO_RETRY_TYPES = {"skill_draft", "skill_test", "skill_evolve", "skill_promote",
                           "deploy_skill", "skill_lifecycle_suite", "evolution_coordinator"}
        with db.get_conn() as conn:
            failed = conn.execute(
                "SELECT id, type, payload_json FROM tasks "
                "WHERE status = 'FAILED' "
                "AND created_at >= datetime('now', '-1 hour') "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
        for task in failed:
            if task["type"] in _NO_RETRY_TYPES:
                continue
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


# ── Skill Regression Detection ───────────────────────────────────────

def detect_skill_regression(skill_name: str, window_hours: int = 6) -> bool:
    """Compare recent success rate vs 7-day baseline."""
    import db
    threshold = _default("regression_threshold", 0.20)
    try:
        with db.get_conn() as conn:
            recent = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE type = ? "
                "AND created_at >= datetime('now', ?)",
                (skill_name, f"-{window_hours} hours")
            ).fetchone()
            baseline = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE type = ? "
                "AND created_at >= datetime('now', '-7 days') "
                "AND created_at < datetime('now', ?)",
                (skill_name, f"-{window_hours} hours")
            ).fetchone()
            if not baseline or baseline["total"] < 5:
                return False
            if not recent or recent["total"] < 3:
                return False
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
    """Restore a skill from its most recent code_drafts backup."""
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
    matches = sorted(drafts_dir.glob(f"{skill_name}_draft_*.py"), reverse=True)
    if not matches:
        result["detail"] = "no_draft_backup_available"
        return result
    backup_source = matches[0]
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_rollback = drafts_dir / f"{skill_name}_pre_rollback_{ts}.py"
        shutil.copy2(str(skill_file), str(pre_rollback))
        shutil.copy2(str(backup_source), str(skill_file))
        result["rolled_back"] = True
        result["detail"] = f"restored from {backup_source.name}, pre-rollback saved to {pre_rollback.name}"
        _log_recovery("rollback_skill", skill_name, result["detail"])
        log.info("Rolled back skill %s: %s", skill_name, result["detail"])
    except Exception as e:
        log.warning("rollback_skill failed for %s: %s", skill_name, e)
        result["detail"] = f"error: {e}"
    return result


# ── Dashboard Data ───────────────────────────────────────────────────

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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Standalone functions — from diagnostics.py (unchanged signatures)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def quarantine_agent(name: str, reason: str) -> None:
    """Set agent status to QUARANTINED with reason stored in messages."""
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE agents SET status='QUARANTINED' WHERE name=?", (name,))
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json, channel)
                VALUES ('watchdog', ?, ?, 'fleet')
            """, (name, json.dumps({"type": "quarantine", "reason": reason})))
    db._retry_write(_do)


def clear_quarantine(name: str) -> None:
    """Remove quarantine status — agent returns to IDLE."""
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE agents SET status='IDLE' WHERE name=? AND status='QUARANTINED'",
                (name,))
    db._retry_write(_do)


def get_failure_streaks(threshold: int = 3) -> list:
    """Find agents with N+ consecutive recent task failures."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT assigned_to as agent,
                   COUNT(*) as fail_count,
                   MAX(error) as last_error
            FROM (
                SELECT assigned_to, error, status,
                       ROW_NUMBER() OVER (PARTITION BY assigned_to ORDER BY id DESC) as rn
                FROM tasks
                WHERE assigned_to IS NOT NULL AND status IN ('FAILED', 'DONE')
                  AND classification != 'synthetic_prefix'
            )
            WHERE rn <= ? AND status = 'FAILED'
            GROUP BY assigned_to
            HAVING fail_count >= ?
        """, (threshold + 2, threshold)).fetchall()
        if not rows:
            rows = conn.execute("""
                SELECT assigned_to as agent, COUNT(*) as fail_count,
                       MAX(error) as last_error
                FROM (
                    SELECT * FROM tasks
                    WHERE assigned_to IS NOT NULL AND status = 'FAILED'
                      AND classification != 'synthetic_prefix'
                    ORDER BY id DESC LIMIT ?
                )
                GROUP BY assigned_to
                HAVING fail_count >= ?
            """, (threshold * 20, threshold)).fetchall()
        return [dict(r) for r in rows]


def get_stuck_reviews(timeout_minutes: int = 30) -> list:
    """Find tasks stuck in REVIEW status for too long."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT id, type, assigned_to
            FROM tasks
            WHERE status = 'REVIEW'
              AND (julianday('now') - julianday(created_at)) * 1440 > ?
        """, (timeout_minutes,)).fetchall()
        return [dict(r) for r in rows]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HealthMonitor class — tick-based supervisor integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _json_log(level, event, **kwargs):
    """Structured JSON log line."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


class HealthMonitor:
    """Unified health monitoring, recovery, and protection."""

    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm  # ProcessManager instance
        now = time.time()
        self._last_health_sweep = now - random.uniform(0, 60)
        self._last_memory_watchdog = now - random.uniform(0, _MEMORY_WATCHDOG_INTERVAL)
        self._last_stale_check = now - random.uniform(0, STALE_TASK_RECOVERY_INTERVAL)
        self._last_watchdog = now - random.uniform(0, WATCHDOG_INTERVAL)
        self._last_watchdog_full = now - random.uniform(0, WATCHDOG_FULL_INTERVAL)
        self._last_context_cleanup = now - random.uniform(0, 600)
        self._last_feedback_check = now - random.uniform(0, 600)
        self._last_cache_cleanup = now - random.uniform(0, 3600)
        self._last_rag_cleanup = now - random.uniform(0, 3600)

    def update_config(self, config: dict) -> None:
        self.config = config

    def tick(self, now: float) -> None:
        """Called every 5s from main loop. Runs all health checks."""
        try:
            self._run_health_sweep(now)
        except Exception:
            log.warning("Health sweep failed", exc_info=True)
        try:
            self._run_memory_watchdog(now)
        except Exception:
            log.warning("Memory watchdog failed", exc_info=True)
        try:
            self._recover_stale_tasks(now)
        except Exception:
            log.warning("Stale task recovery failed", exc_info=True)
        try:
            self._run_watchdog(now)
        except Exception:
            log.warning("Watchdog failed", exc_info=True)
        try:
            self._cleanup_contexts(now)
        except Exception:
            log.warning("Context cleanup failed", exc_info=True)
        try:
            self._check_feedback(now)
        except Exception:
            log.warning("Feedback check failed", exc_info=True)
        try:
            self._cleanup_caches(now)
        except Exception:
            log.warning("Cache cleanup failed", exc_info=True)
        try:
            self._cleanup_rag(now)
        except Exception:
            log.warning("RAG cleanup failed", exc_info=True)

    def _run_health_sweep(self, now: float) -> None:
        heal_cfg = self.config.get("self_healing", {})
        heal_interval = heal_cfg.get("health_sweep_interval", 60)
        if not heal_cfg.get("enabled", True):
            return
        if now - self._last_health_sweep < heal_interval:
            return
        self._last_health_sweep = now
        sweep = run_health_sweep()
        if sweep.get("recovered_agents") or sweep.get("retried_tasks"):
            log.info("Health sweep: recovered %d agents, retried %d tasks",
                     len(sweep.get("recovered_agents", [])),
                     len(sweep.get("retried_tasks", [])))
            _json_log("INFO", "health_sweep",
                      recovered=len(sweep.get("recovered_agents", [])),
                      retried=len(sweep.get("retried_tasks", [])))

    def _run_memory_watchdog(self, now: float) -> None:
        if now - self._last_memory_watchdog < _MEMORY_WATCHDOG_INTERVAL:
            return
        self._last_memory_watchdog = now
        try:
            import psutil
        except ImportError:
            return
        import db
        actions = []
        # 1. Self-check
        try:
            own = psutil.Process(os.getpid())
            own_rss = own.memory_info().rss / (1024 * 1024)
            if own_rss > _SUP_SELF_RSS_WARN_MB:
                collected = gc.collect()
                log.warning(f"Supervisor self RSS: {own_rss:.0f} MB — gc collected {collected}")
                actions.append(f"sup_gc:{collected}")
            else:
                gc.collect(0)
        except Exception:
            pass
        # 2. Worker RSS
        for role, proc in list(self.pm.worker_procs.items()):
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
                    self.pm.worker_procs[role] = None
                    actions.append(f"restart:{role}:{rss:.0f}MB")
                elif rss > _WORKER_RSS_WARN_MB:
                    log.info(f"Worker '{role}' RSS: {rss:.0f} MB (elevated)")
                    actions.append(f"warn:{role}:{rss:.0f}MB")
            except Exception:
                pass
        # 3. Dr. Ders cross-check
        try:
            hw_state = self.pm.read_hw_state()
            if hw_state:
                hw_rss = hw_state.get("memory", {}).get("hw_sup_rss_mb", 0)
                if hw_rss > _HW_SUP_RSS_CRITICAL_MB:
                    log.warning(f"Dr. Ders RSS {hw_rss:.0f} MB > {_HW_SUP_RSS_CRITICAL_MB} MB — flagging for restart")
                    actions.append(f"dr_ders_leak:{hw_rss:.0f}MB")
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

    def _recover_stale_tasks(self, now: float) -> None:
        if now - self._last_stale_check < STALE_TASK_RECOVERY_INTERVAL:
            return
        self._last_stale_check = now
        import db
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

    def _run_watchdog(self, now: float) -> None:
        if now - self._last_watchdog < WATCHDOG_INTERVAL:
            return
        self._last_watchdog = now
        try:
            from skills._watchdog import run_cycle, run_full_cycle
            if now - self._last_watchdog_full >= WATCHDOG_FULL_INTERVAL:
                self._last_watchdog_full = now
                alerts = run_full_cycle(log.info)
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

    def _cleanup_contexts(self, now: float) -> None:
        if now - self._last_context_cleanup < 1800:
            return
        self._last_context_cleanup = now
        try:
            from context_manager import clear_stale_contexts
            cleared = clear_stale_contexts()
            if cleared:
                log.info(f"Cleared {cleared} stale agent contexts")
        except Exception:
            pass

    def _check_feedback(self, now: float) -> None:
        if now - self._last_feedback_check < 600:
            return
        self._last_feedback_check = now
        try:
            from reinforcement import age_out_unreviewed
            aged = age_out_unreviewed()
            if aged:
                log.debug(f"Feedback: aged out {aged} unreviewed outputs")
        except Exception:
            pass
        try:
            from ml_router import retrain_if_stale
            retrain_result = retrain_if_stale()
            if retrain_result and not retrain_result.get("error"):
                log.info("ML router retrained: accuracy=%.3f, samples=%d",
                         retrain_result.get("accuracy", 0),
                         retrain_result.get("sample_count", 0))
        except Exception:
            pass

    def _cleanup_caches(self, now: float) -> None:
        if now - self._last_cache_cleanup < 300:
            return
        self._last_cache_cleanup = now
        try:
            from cache_manager import invalidate_stale
            stale = invalidate_stale()
            if stale:
                log.debug(f"Cache: invalidated {stale} stale caches")
        except Exception:
            pass

    def _cleanup_rag(self, now: float) -> None:
        if now - self._last_rag_cleanup < 1800:
            return
        self._last_rag_cleanup = now
        try:
            from rag import RAGIndex
            idx = RAGIndex()
            result = idx.cleanup_stale()
            removed = result.get("stale_removed", 0)
            if removed:
                log.info(f"RAG: cleaned {removed} stale index entries")
        except Exception:
            pass
