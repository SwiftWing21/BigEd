#!/usr/bin/env python3
"""Scheduler — dynamic scaling, periodic triggers, and task scheduling.

Extracted from supervisor.py during restructure. Decides what work to
do and when: agent scaling, auto-triggers, training detection.
"""

import json
import logging
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent

# ── Role and scaling constants ────────────────────────────────────────
BASE_ROLES = [
    "researcher", "coder", "archivist", "analyst", "sales", "onboarding",
    "implementation", "security", "planner", "legal", "account_manager",
    "ds_rag", "ds_fleet", "ds_research",
]
CORE_AGENTS = {"coder_1", "researcher", "planner", "archivist"}
SCALE_ORDER = ["coder_2", "coder_3", "analyst", "security", "coder"]
SCALE_UP_QUEUE_DEPTH = 2
SCALE_DOWN_IDLE_SECS = 300
MAX_DYNAMIC_PER_ROLE = 4

# ── Auto-triggered pipeline intervals ────────────────────────────────
RESEARCH_INTERVAL = 86400
EVOLUTION_INTERVAL = 604800
_SCHED_CHECK_INTERVAL = 60
SCALE_CHECK_INTERVAL = 30
MODEL_RECOMMEND_INTERVAL = 6 * 3600
CONFIG_RELOAD_INTERVAL = 300
FEEDBACK_CHECK_INTERVAL = 600
COST_ANOMALY_INTERVAL = 600


def _json_log(level, event, **kwargs):
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


class _CapacityState:
    """Tracks Claude capacity bonus window state."""
    active = False


class Scheduler:
    """Dynamic scaling, periodic triggers, and task scheduling."""

    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm  # ProcessManager
        self._capacity_state = _CapacityState()
        # Interval trackers — staggered to avoid all-at-once burst on boot
        now = time.time()
        self._last_scale_check: float = 0  # intentional: scale immediately on boot
        self._last_research_trigger = now - random.uniform(0, RESEARCH_INTERVAL)
        self._last_evolution_trigger = now - random.uniform(0, EVOLUTION_INTERVAL)
        self._last_results_mtime: float = 0
        self._last_model_recommend = now - random.uniform(0, MODEL_RECOMMEND_INTERVAL)
        self._last_sched_check = now - random.uniform(0, 300)
        self._last_trigger_check = now - random.uniform(0, 60)
        self._last_config_reload = now - random.uniform(0, CONFIG_RELOAD_INTERVAL)
        self._last_cost_anomaly_check = now - random.uniform(0, 300)
        self._last_capacity_check = now - random.uniform(0, 300)
        self._last_training_check = now - random.uniform(0, 30)

    def update_config(self, config: dict) -> None:
        self.config = config

    # ── Public interface ────────────────────────────────────────────

    def build_roles(self) -> list[str]:
        """Expand BASE_ROLES, replacing 'coder' with coder_1..coder_N and filtering disabled."""
        disabled = set(self.config.get("fleet", {}).get("disabled_agents", []))
        roles = []
        for r in BASE_ROLES:
            if r in disabled:
                continue
            if r == "coder":
                n = max(1, int(self.config.get("workers", {}).get("coder_count", 1)))
                roles.extend(f"coder_{i}" for i in range(1, n + 1))
            else:
                roles.append(r)
        return roles

    def count_pending_tasks(self) -> int:
        """Count pending tasks in the queue."""
        try:
            from db import get_conn
            with get_conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    def get_core_agents(self) -> set[str]:
        return set(CORE_AGENTS)

    def get_disabled_agents(self) -> set[str]:
        return set(self.config.get("fleet", {}).get("disabled_agents", []))

    # ── tick ────────────────────────────────────────────────────────

    def tick(self, now: float) -> None:
        """Called every 5s from main loop."""
        try:
            self._check_scaling(now)
        except Exception:
            log.warning("Scaling check failed", exc_info=True)
        try:
            self._check_training(now)
        except Exception:
            log.warning("Training check failed", exc_info=True)
        try:
            self._check_auto_triggers(now)
        except Exception:
            log.warning("Auto-trigger check failed", exc_info=True)
        try:
            self._check_manual_mode(now)
        except Exception:
            log.warning("Manual mode check failed", exc_info=True)
        try:
            self._check_event_triggers(now)
        except Exception:
            log.warning("Event trigger check failed", exc_info=True)
        try:
            self._check_cost_anomaly(now)
        except Exception:
            log.warning("Cost anomaly check failed", exc_info=True)
        try:
            self._check_capacity_bonus(now)
        except Exception:
            log.warning("Capacity bonus check failed", exc_info=True)
        try:
            self._reload_config_if_stale(now)
        except Exception:
            log.warning("Config reload failed", exc_info=True)

    # ── Internal: Scaling ───────────────────────────────────────────

    def _pending_tasks_by_type(self) -> dict:
        try:
            from db import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT type, COUNT(*) as n FROM tasks WHERE status='PENDING' GROUP BY type"
                ).fetchall()
                return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    def _skill_to_role(self, skill: str, affinity_map: dict) -> str | None:
        for role, skills in affinity_map.items():
            if not isinstance(skills, list):
                continue
            if skill in skills:
                return role
        return None

    def _load_affinity_map(self) -> dict:
        try:
            from config import load_config
            return load_config().get("affinity", {})
        except Exception:
            return {}

    def _next_instance_name(self, base_role: str, running: set) -> str | None:
        for i in range(1, MAX_DYNAMIC_PER_ROLE + 1):
            name = f"{base_role}_{i}" if i > 1 or base_role == "coder" else base_role
            if base_role == "coder":
                name = f"coder_{i}"
            if name not in running:
                return name
        return None

    def _predict_queue_growth(self) -> int:
        try:
            from db import get_conn
            with get_conn() as conn:
                recent = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE created_at >= datetime('now', '-5 minutes')"
                ).fetchone()[0]
                prior = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE created_at >= datetime('now', '-10 minutes') "
                    "AND created_at < datetime('now', '-5 minutes')"
                ).fetchone()[0]
            if recent > prior * 1.5 and recent > 3:
                return recent - prior
        except Exception:
            pass
        return 0

    def _get_ram_usage_pct(self) -> float:
        try:
            import psutil
            return psutil.virtual_memory().percent
        except Exception:
            return 0.0

    def _should_scale_up(self, pending: int, running: set) -> list:
        to_start = []
        if pending < SCALE_UP_QUEUE_DEPTH:
            return to_start
        ram_ceiling = self.config.get("fleet", {}).get("ram_ceiling_pct", 95)
        if ram_ceiling > 0:
            ram_pct = self._get_ram_usage_pct()
            if ram_pct >= ram_ceiling:
                log.info(f"Scale-up blocked: RAM {ram_pct:.1f}% >= ceiling {ram_ceiling}%")
                return to_start
        max_total = self.config.get("fleet", {}).get("max_workers", 16)
        if max_total <= 0:
            try:
                from system_info import get_worker_limits
                max_total = get_worker_limits()["max_workers"]
            except Exception:
                max_total = 16
        if len(running) >= max_total:
            return to_start
        by_type = self._pending_tasks_by_type()
        affinity = self._load_affinity_map()
        role_demand = {}
        for skill, count in by_type.items():
            role = self._skill_to_role(skill, affinity)
            if role:
                role_demand[role] = role_demand.get(role, 0) + count
        for role, demand in sorted(role_demand.items(), key=lambda x: -x[1]):
            if demand < 2:
                continue
            name = self._next_instance_name(role, running | set(to_start))
            if name and name not in running and len(to_start) + len(running) < max_total:
                to_start.append(name)
                log.info(f"Type-aware scale: {name} for {demand} pending {role} tasks")
        if not to_start and pending >= SCALE_UP_QUEUE_DEPTH:
            for agent in SCALE_ORDER:
                if agent not in running and len(to_start) + len(running) < max_total:
                    to_start.append(agent)
                    if pending // SCALE_UP_QUEUE_DEPTH <= len(to_start):
                        break
        return to_start

    def _should_scale_down(self, running: set) -> list:
        now = time.time()
        to_stop = []
        for name in running:
            if name in CORE_AGENTS:
                continue
            idle_since = self.pm.last_busy.get(name, now)
            if now - idle_since > SCALE_DOWN_IDLE_SECS:
                to_stop.append(name)
        return to_stop

    def _check_scaling(self, now: float) -> None:
        if now - self._last_scale_check < SCALE_CHECK_INTERVAL:
            return
        self._last_scale_check = now
        import db

        pending = self.count_pending_tasks()
        running = self.pm.get_running_workers()
        disabled = self.get_disabled_agents()

        # Update last-busy timestamps
        try:
            with db.get_conn() as conn:
                busy_agents = conn.execute(
                    "SELECT name FROM agents WHERE status='BUSY' "
                    "AND (julianday('now') - julianday(last_heartbeat)) * 86400 < 60"
                ).fetchall()
            for row in busy_agents:
                self.pm.last_busy[row["name"]] = now
        except Exception:
            pass

        # ML predictor or heuristic
        _ml_predictor_used = False
        try:
            scaling_cfg = self.config.get("scaling", {})
            if scaling_cfg.get("ml_predictor_enabled", True):
                from predictive_scaler import (
                    predict_optimal_agents as _ml_predict,
                    record_scaling_event as _record_scaling,
                    _get_task_rate,
                )
                _rate_5m = _get_task_rate(5)
                _rate_15m = _get_task_rate(15)
                _optimal = _ml_predict(pending, len(running), _rate_5m, _rate_15m)
                if _optimal > len(running):
                    pending += (_optimal - len(running)) * SCALE_UP_QUEUE_DEPTH
                    log.info(f"ML predictor: optimal={_optimal}, inflating pending to {pending}")
                _ml_predictor_used = True
        except Exception:
            pass

        if not _ml_predictor_used:
            predicted = self._predict_queue_growth()
            if predicted > 0:
                log.info(f"Predictive scaling: {predicted} additional tasks expected")
                pending += predicted

        # Build dynamic pool
        all_roles = self.build_roles()
        dynamic_pool = [r for r in all_roles if r not in CORE_AGENTS and r not in disabled]

        # Scale up
        to_start = self._should_scale_up(pending, running)
        to_start = [r for r in to_start if r not in disabled and r in dynamic_pool]
        for role in to_start:
            log.info(f"Scaling up: starting {role} ({pending} pending tasks)")
            _json_log("INFO", "scale_up", worker=role, pending=pending)
            self.pm.start_worker(role)
            self.pm.last_busy[role] = now

        # Scale down
        to_stop = self._should_scale_down(running)
        for role in to_stop:
            idle_secs = int(now - self.pm.last_busy.get(role, now))
            log.info(f"Scaling down: stopping {role} (idle {idle_secs // 60}m{idle_secs % 60}s)")
            _json_log("INFO", "scale_down", worker=role, idle_secs=idle_secs)
            self.pm.stop_worker(role)

        # Record ML training data
        try:
            if _ml_predictor_used:
                _action = "scale_up" if to_start else ("scale_down" if to_stop else "none")
                _target = len(running) + len(to_start) - len(to_stop)
                _record_scaling(
                    queue_depth=self.count_pending_tasks(),
                    agent_count=len(running),
                    task_rate_5m=_rate_5m,
                    task_rate_15m=_rate_15m,
                    action=_action,
                    target_agents=_target,
                )
        except Exception:
            pass

        # Federation overflow routing
        self._check_federation_overflow(pending, running)

    def _check_federation_overflow(self, pending: int, running: set) -> None:
        """Federation overflow routing when queue is deep."""
        import db
        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("enabled") or pending <= 10:
            return

        overflow_threshold = federation_cfg.get("overflow_threshold", 0.85)
        max_capacity = self.config.get("fleet", {}).get("max_workers", 10) * 5
        if pending / max(max_capacity, 1) <= overflow_threshold:
            return

        # Auto-discovered + manual peers
        try:
            import discovery
            _all_peers = discovery.get_all_peers()
            fed_peers = [p["url"] for p in _all_peers if p.get("online", False)]
        except Exception:
            fed_peers = federation_cfg.get("peers", [])

        _hb_ssl = None
        try:
            from fleet_tls import is_tls_enabled, get_ssl_context
            if is_tls_enabled():
                _hb_ssl = get_ssl_context("client")
        except Exception:
            pass

        for peer_url in fed_peers:
            try:
                _of_kwargs = {"timeout": 3}
                if _hb_ssl:
                    _of_kwargs["context"] = _hb_ssl
                with urllib.request.urlopen(
                    f"{peer_url}/api/federation/peers", **_of_kwargs
                ) as r:
                    peer_data = json.loads(r.read())  # noqa: F841
                log.info(f"Federation overflow: {pending} pending, routing to {peer_url}")
                _json_log("INFO", "federation_overflow", pending=pending, peer=peer_url)
                break
            except Exception:
                pass

        # Cross-fleet task routing via federation_router
        try:
            from federation_router import should_route_remotely, find_best_peer, route_to_peer, record_local_route
            if federation_cfg.get("enabled") and pending > 0:
                _routed_count = 0
                if should_route_remotely("", priority=5):
                    try:
                        with db.get_conn() as _fc:
                            _pending_rows = _fc.execute(
                                "SELECT id, type, payload_json, priority FROM tasks "
                                "WHERE status='PENDING' ORDER BY priority DESC, id ASC LIMIT 5"
                            ).fetchall()
                        best_peer = find_best_peer("")
                        if best_peer:
                            for _pr in _pending_rows:
                                _task_priority = _pr["priority"] or 5
                                local_priority_min = int(federation_cfg.get("local_priority_min", 9))
                                if _task_priority >= local_priority_min:
                                    continue
                                task_dict = {
                                    "type": _pr["type"],
                                    "payload": json.loads(_pr["payload_json"] or "{}"),
                                    "priority": _task_priority,
                                }
                                result = route_to_peer(best_peer, task_dict)
                                if result.get("ok"):
                                    def _mark_forwarded(_tid=_pr["id"], _peer=best_peer["url"],
                                                        _remote_id=result.get("task_id")):
                                        with db.get_conn() as _mf:
                                            _mf.execute(
                                                "UPDATE tasks SET status='FORWARDED', "
                                                "result_json=? WHERE id=? AND status='PENDING'",
                                                (json.dumps({
                                                    "forwarded_to": _peer,
                                                    "remote_task_id": _remote_id,
                                                }), _tid))
                                    db._retry_write(_mark_forwarded)
                                    _routed_count += 1
                                    log.info(f"Federation: routed task {_pr['id']} ({_pr['type']}) to {best_peer['url']}")
                                    _json_log("INFO", "federation_route", task_id=_pr["id"],
                                              task_type=_pr["type"], peer=best_peer["url"])
                                else:
                                    log.debug(f"Federation: route failed for task {_pr['id']}: {result.get('error')}")
                                    break
                    except Exception as e:
                        log.debug(f"Federation routing error: {e}")
                if _routed_count == 0:
                    record_local_route()
        except ImportError:
            pass

    # ── Internal: Training ──────────────────────────────────────────

    def _check_training(self, now: float) -> None:
        training_interval = self.config.get("fleet", {}).get("training_check_interval_secs", 30)
        if now - self._last_training_check < training_interval:
            return
        self._last_training_check = now

        from marathon import is_training_running, _check_training_checkpoints, _evict_gpu_models, training_needs_eviction
        import db

        training_now, training_profile = is_training_running()
        if training_now and not self.pm.training_active:
            needs_eviction, reason = training_needs_eviction(self.config, training_profile)
            log.info(f"train.py detected (profile={training_profile or 'unknown'}) — {reason}")
            _json_log("INFO", "training_detected", profile=training_profile or "unknown", reason=reason)
            self.pm.training_active = True

            if needs_eviction:
                _evict_gpu_models(self.config)
                time.sleep(2)
                self.pm.stop_ollama()
                self.pm.start_ollama(gpu=False)
                self.pm.ollama_evicted_for_training = True
                mode_msg = "Ollama CPU-only"
            else:
                self.pm.ollama_evicted_for_training = False
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

        elif training_now and self.pm.training_active:
            # VRAM reactive eviction: if GPU memory >90%, force Ollama to CPU
            try:
                import gpu
                gpu_info = gpu.get_gpu_info()
                used_mb = gpu_info.get("memory_used_mb", 0)
                total_mb = gpu_info.get("memory_total_mb", 0)
                if total_mb > 0 and (used_mb / total_mb) > 0.90:
                    if not self.pm.ollama_evicted_for_training:
                        log.warning(f"VRAM usage {used_mb}/{total_mb}MB (>90%) — evicting Ollama to CPU")
                        self.pm.stop_ollama()
                        self.pm.start_ollama(gpu=False)
                        self.pm.ollama_evicted_for_training = True
            except Exception:
                log.debug("GPU check unavailable for VRAM reactive eviction")

        elif not training_now and self.pm.training_active:
            self.pm.training_active = False
            if self.pm.ollama_evicted_for_training:
                log.info("Training finished — restoring Ollama to GPU mode")
                self.pm.stop_ollama()
                self.pm.start_ollama(gpu=not self.config.get("fleet", {}).get("eco_mode", False))
                self.pm.ollama_evicted_for_training = False
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

    # ── Internal: Auto-triggers ─────────────────────────────────────

    def _check_auto_triggers(self, now: float) -> None:
        import db

        # Daily research — check PENDING+RUNNING to avoid overlap
        if now - self._last_research_trigger > RESEARCH_INTERVAL:
            try:
                with db.get_conn() as conn:
                    pending_research = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE type='research_loop' "
                        "AND status IN ('PENDING','RUNNING')"
                    ).fetchone()[0]
                if pending_research == 0:
                    def _insert_research():
                        with db.get_conn() as conn:
                            conn.execute(
                                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                                "VALUES ('research_loop', 'PENDING', 3, ?, datetime('now'))",
                                (json.dumps({"action": "detect_gaps"}),))
                    db._retry_write(_insert_research)
                    log.info("Auto-triggered daily research cycle")
                self._last_research_trigger = now
            except Exception as e:
                log.debug(f"Research trigger failed: {e}")

        # Weekly evolution — check PENDING+RUNNING to avoid overlap
        if now - self._last_evolution_trigger > EVOLUTION_INTERVAL:
            try:
                with db.get_conn() as conn:
                    pending_evo = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE type='evolution_coordinator' "
                        "AND status IN ('PENDING','RUNNING')"
                    ).fetchone()[0]
                if pending_evo == 0:
                    def _insert_evo():
                        with db.get_conn() as conn:
                            conn.execute(
                                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                                "VALUES ('evolution_coordinator', 'PENDING', 2, ?, datetime('now'))",
                                (json.dumps({"action": "evolve"}),))
                    db._retry_write(_insert_evo)
                    log.info("Auto-triggered weekly evolution sweep")
                self._last_evolution_trigger = now
            except Exception as e:
                log.debug(f"Evolution trigger failed: {e}")

        # ML bridge import (watch results.tsv)
        results_tsv = FLEET_DIR.parent / "autoresearch" / "results.tsv"
        if results_tsv.exists():
            mtime = results_tsv.stat().st_mtime
            if mtime > self._last_results_mtime and self._last_results_mtime > 0:
                try:
                    def _insert_ml_bridge():
                        with db.get_conn() as conn:
                            conn.execute(
                                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                                "VALUES ('ml_bridge', 'PENDING', 4, ?, datetime('now'))",
                                (json.dumps({"action": "import_results"}),))
                    db._retry_write(_insert_ml_bridge)
                    log.info("Auto-triggered ml_bridge import (new results.tsv entries)")
                except Exception:
                    pass
            self._last_results_mtime = mtime

        # Model recommendation (every 6h) — check PENDING+RUNNING to avoid overlap
        if now - self._last_model_recommend >= MODEL_RECOMMEND_INTERVAL:
            self._last_model_recommend = now
            try:
                with db.get_conn() as conn:
                    existing = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE type='model_recommend' "
                        "AND status IN ('PENDING','RUNNING')"
                    ).fetchone()[0]
                if existing == 0:
                    db.post_task("model_recommend", json.dumps({"action": "analyze"}), priority=3)
                    log.info("Dispatched model_recommend analysis task")
            except Exception as e:
                log.debug(f"Model recommend dispatch error: {e}")

    def _check_manual_mode(self, now: float) -> None:
        if now - self._last_sched_check < _SCHED_CHECK_INTERVAL:
            return
        self._last_sched_check = now
        try:
            sys.path.insert(0, str(FLEET_DIR))
            from manual_mode import ManualModeEngine
            from datetime import datetime, timezone, timedelta

            engine = ManualModeEngine()
            sched = engine.get_scheduler()
            if not sched.get("enabled"):
                return
            next_run = sched.get("next_run", "")
            if not next_run:
                return
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if now_str >= next_run:
                queue = engine.get_queue()
                if not queue:
                    log.info("[SCHED] Manual Mode scheduler fired but queue is empty — skipping")
                else:
                    log.info("[SCHED] Manual Mode scheduler firing: %d items", len(queue))
                    try:
                        engine.run_queue(queue)
                        log.info("[SCHED] Manual Mode scheduled run complete")
                    except Exception as exc:
                        log.warning("[SCHED] Manual Mode scheduled run error: %s", exc)
                if sched.get("mode") == "recurring":
                    interval = int(sched.get("interval_days", 1))
                    new_next = (
                        datetime.now(timezone.utc) + timedelta(days=interval)
                    ).strftime("%Y-%m-%d %H:%M")
                    sched["next_run"] = new_next
                    engine.set_scheduler(sched)
                    log.info("[SCHED] Next Manual Mode run scheduled for %s", new_next)
                else:
                    sched["enabled"] = False
                    engine.set_scheduler(sched)
                    log.info("[SCHED] One-time Manual Mode run complete — scheduler disabled")
        except Exception as exc:
            log.debug("[SCHED] Manual Mode schedule check error: %s", exc)

    def _check_event_triggers(self, now: float) -> None:
        if now - self._last_trigger_check < 30:
            return
        self._last_trigger_check = now
        try:
            from event_triggers import check_all_triggers
            dispatched = check_all_triggers(self.config)
            if dispatched:
                log.info(f"Triggers: dispatched {dispatched} task(s)")
        except Exception:
            pass

    def _check_cost_anomaly(self, now: float) -> None:
        if now - self._last_cost_anomaly_check < COST_ANOMALY_INTERVAL:
            return
        self._last_cost_anomaly_check = now
        try:
            from cost_tracking import detect_cost_anomaly
            import db
            anomaly = detect_cost_anomaly()
            throttle_flag = FLEET_DIR / ".cost_anomaly_throttle"
            if anomaly:
                log.warning(f"Cost anomaly: ${anomaly['today_cost']} today vs "
                            f"${anomaly['avg_cost']} avg ({anomaly['multiplier']}x)")
                _json_log("WARNING", "cost_anomaly_throttle", **anomaly)
                throttle_flag.write_text(json.dumps({
                    "ts": time.time(),
                    "today_cost": anomaly["today_cost"],
                    "avg_cost": anomaly["avg_cost"],
                    "multiplier": anomaly["multiplier"],
                }), encoding="utf-8")
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "cost_anomaly",
                        "title": f"Cost anomaly: ${anomaly['today_cost']} today "
                                 f"({anomaly['multiplier']}x avg ${anomaly['avg_cost']})",
                        "tags": ["cost", "anomaly"],
                    }))
                except Exception:
                    pass
            else:
                if throttle_flag.exists():
                    throttle_flag.unlink(missing_ok=True)
                    log.info("Cost anomaly cleared — idle evolution resumed")
                    _json_log("INFO", "cost_anomaly_cleared")
        except Exception:
            pass

    def _check_capacity_bonus(self, now: float) -> None:
        if now - self._last_capacity_check < 300:
            return
        self._last_capacity_check = now
        try:
            from skills.claude_efficiency import is_in_bonus_window
            in_bonus = is_in_bonus_window(self.config)
            if in_bonus and not self._capacity_state.active:
                self._capacity_state.active = True
                log.info("Claude capacity bonus window active")
                _json_log("INFO", "capacity_bonus_start")
            elif not in_bonus and self._capacity_state.active:
                self._capacity_state.active = False
                log.info("Claude capacity bonus window ended")
                _json_log("INFO", "capacity_bonus_end")
        except Exception:
            pass

    def _reload_config_if_stale(self, now: float) -> None:
        if now - self._last_config_reload < CONFIG_RELOAD_INTERVAL:
            return
        self._last_config_reload = now
        try:
            from config import reload_config
            reload_config()
        except Exception:
            pass
