"""ML Experiment Framework — structured experiment lifecycle for fleet ML work.

Statuses: PROPOSED -> APPROVED -> RUNNING -> EVALUATING -> DEPLOYED | ROLLED_BACK | REJECTED

Skills propose experiments (embedding retrain, reranker tuning, router update, etc.).
Low-risk types auto-approve; high-risk require HITL. Thermal/GPU safety gates
prevent experiments from destabilizing fleet hardware.
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent
HW_STATE_FILE = FLEET_DIR / "hw_state.json"

VALID_STATUSES = {
    "PROPOSED", "APPROVED", "RUNNING", "EVALUATING",
    "DEPLOYED", "ROLLED_BACK", "REJECTED",
}


class ExperimentFramework:
    """Manages the full lifecycle of ML experiments with safety gates."""

    # ── Public API ────────────────────────────────────────────────────

    def propose(self, agent: str, experiment_type: str,
                hypothesis: str = None, config: dict = None) -> int:
        """Create an experiment proposal. Auto-approves if config allows.

        Returns the experiment ID.
        """
        import db
        now = time.time()
        config_json = json.dumps(config) if config else None
        exp_id = None

        auto = self._should_auto_approve(experiment_type)

        def _do():
            nonlocal exp_id
            with db.get_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO ml_experiments
                       (agent, experiment_type, hypothesis, config_json,
                        status, auto_approved, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (agent, experiment_type, hypothesis, config_json,
                     "APPROVED" if auto else "PROPOSED",
                     1 if auto else 0,
                     now),
                )
                exp_id = cur.lastrowid

        db._retry_write(_do)
        log.info("Experiment %d proposed by %s (type=%s, auto=%s)",
                 exp_id, agent, experiment_type, auto)
        return exp_id

    def run(self, exp_id: int, train_fn, eval_fn) -> dict:
        """Full experiment lifecycle: approval check -> thermal -> GPU lock ->
        eval_before -> train -> eval_after -> deploy gate.

        train_fn(config: dict) -> str|None  — returns artifact path or None
        eval_fn(config: dict) -> dict        — returns metrics dict

        Returns the final experiment record as a dict.
        """
        import db

        exp = self.get(exp_id)
        if exp is None:
            raise ValueError(f"Experiment {exp_id} not found")

        status = exp["status"]
        if status not in ("APPROVED", "PROPOSED"):
            raise ValueError(
                f"Experiment {exp_id} cannot run (status={status})")
        if status == "PROPOSED":
            raise ValueError(
                f"Experiment {exp_id} is PROPOSED — approve first or enable auto-approve")

        config = json.loads(exp["config_json"]) if exp["config_json"] else {}

        # Thermal safety gate
        if not self._check_thermal_safe():
            log.warning("Experiment %d blocked: thermal limits exceeded", exp_id)
            return self.get(exp_id)

        # GPU lock
        if not self._acquire_gpu_lock(exp_id):
            log.warning("Experiment %d blocked: could not acquire GPU lock", exp_id)
            return self.get(exp_id)

        try:
            # Transition to RUNNING
            self._update_status(exp_id, "RUNNING")

            # Pre-training evaluation
            try:
                metrics_before = eval_fn(config)
            except Exception:
                log.warning("Experiment %d: eval_before failed", exp_id, exc_info=True)
                metrics_before = {}
            self._update_metrics(exp_id, before=metrics_before)

            # Train
            try:
                artifact_path = train_fn(config)
            except Exception:
                log.warning("Experiment %d: training failed", exp_id, exc_info=True)
                self._update_status(exp_id, "ROLLED_BACK")
                return self.get(exp_id)

            if artifact_path:
                self._update_artifact(exp_id, artifact_path)

            # Post-training evaluation
            self._update_status(exp_id, "EVALUATING")
            try:
                metrics_after = eval_fn(config)
            except Exception:
                log.warning("Experiment %d: eval_after failed", exp_id, exc_info=True)
                metrics_after = {}
            self._update_metrics(exp_id, after=metrics_after)

            # Deploy gate
            self._deploy_gate(exp_id, metrics_before, metrics_after,
                              bool(exp.get("auto_approved")))

        finally:
            self._release_gpu_lock(exp_id)

        return self.get(exp_id)

    def get(self, exp_id: int) -> dict | None:
        """Fetch a single experiment by ID."""
        import db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ml_experiments WHERE id = ?", (exp_id,)
            ).fetchone()
            return dict(row) if row else None

    def history(self, agent: str = None, experiment_type: str = None,
                limit: int = 50) -> list[dict]:
        """Return experiment history with optional filters."""
        import db
        clauses = []
        params = []
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        if experiment_type:
            clauses.append("experiment_type = ?")
            params.append(experiment_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with db.get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM ml_experiments{where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def pending_approval(self) -> list[dict]:
        """Return experiments awaiting HITL approval."""
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ml_experiments WHERE status = 'PROPOSED' ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def approve(self, exp_id: int) -> bool:
        """Manually approve a PROPOSED experiment. Returns True on success."""
        exp = self.get(exp_id)
        if exp is None or exp["status"] != "PROPOSED":
            return False
        self._update_status(exp_id, "APPROVED")
        log.info("Experiment %d manually approved", exp_id)
        return True

    def reject(self, exp_id: int) -> bool:
        """Manually reject a PROPOSED experiment. Returns True on success."""
        exp = self.get(exp_id)
        if exp is None or exp["status"] != "PROPOSED":
            return False
        self._update_status(exp_id, "REJECTED")
        log.info("Experiment %d rejected", exp_id)
        return True

    def best_experiment(self, experiment_type: str,
                        metric: str) -> dict | None:
        """Return the best DEPLOYED experiment for a type, ranked by a metric.

        Searches metrics_after_json for the given metric key and returns the
        experiment with the highest value.
        """
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM ml_experiments
                   WHERE experiment_type = ? AND status = 'DEPLOYED'
                     AND metrics_after_json IS NOT NULL
                   ORDER BY created_at DESC""",
                (experiment_type,),
            ).fetchall()

        best = None
        best_val = None
        for row in rows:
            rec = dict(row)
            try:
                metrics = json.loads(rec["metrics_after_json"])
                val = metrics.get(metric)
                if val is not None and (best_val is None or val > best_val):
                    best_val = val
                    best = rec
            except Exception:
                continue
        return best

    # ── Internal helpers ──────────────────────────────────────────────

    def _should_auto_approve(self, experiment_type: str) -> bool:
        """Check fleet.toml [experiments] config for auto-approval."""
        try:
            from config import load_config
            cfg = load_config()
            exp_cfg = cfg.get("experiments", {})
            if not exp_cfg.get("auto_approve", False):
                return False
            types = exp_cfg.get("auto_approve_types", {})
            return bool(types.get(experiment_type, False))
        except Exception:
            log.warning("Failed to read experiment config", exc_info=True)
            return False

    def _in_auto_window(self, exp_cfg: dict) -> bool:
        """Check if current time falls within an auto-approve window.

        Windows are strings like "Sat 00:00-06:00" in
        [experiments.auto_windows] windows list.
        """
        import datetime as dt
        windows = exp_cfg.get("auto_windows", {}).get("windows", [])
        if not windows:
            return True  # no windows configured = always allowed
        now = dt.datetime.now()
        day_name = now.strftime("%a")  # Mon, Tue, Wed, Thu, Fri, Sat, Sun
        for w in windows:
            try:
                parts = w.split(" ", 1)
                if len(parts) != 2:
                    continue
                wday, time_range = parts
                if wday != day_name:
                    continue
                start_s, end_s = time_range.split("-")
                start_t = dt.datetime.strptime(start_s.strip(), "%H:%M").time()
                end_t = dt.datetime.strptime(end_s.strip(), "%H:%M").time()
                if start_t <= now.time() <= end_t:
                    return True
            except Exception:
                log.warning("Bad auto_window entry: %s", w, exc_info=True)
                continue
        return False

    def _check_thermal_safe(self) -> bool:
        """Read hw_state.json and block if GPU >80C or VRAM >85%."""
        try:
            if not HW_STATE_FILE.exists():
                return True  # no hw data = assume safe
            data = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
            gpu_temp = data.get("gpu_temp_c", 0)
            if gpu_temp and gpu_temp > 80:
                log.warning("GPU temp %.1fC exceeds 80C limit", gpu_temp)
                return False
            vram = data.get("vram", {})
            vram_used = vram.get("used_mb", 0)
            vram_total = vram.get("total_mb", 1)
            if vram_total > 0 and (vram_used / vram_total) > 0.85:
                log.warning("VRAM usage %.0f%% exceeds 85%% limit",
                            (vram_used / vram_total) * 100)
                return False
            return True
        except Exception:
            log.warning("Failed to read hw_state.json", exc_info=True)
            return True  # fail open — don't block experiments on read error

    def _acquire_gpu_lock(self, exp_id: int) -> bool:
        """Acquire GPU lock using the locks table with 7200s expiry."""
        import db
        lock_name = "gpu_experiment"
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        acquired = False

        def _do():
            nonlocal acquired
            with db.get_conn() as conn:
                # Check for existing non-expired lock
                row = conn.execute(
                    "SELECT holder, acquired_at FROM locks WHERE name = ?",
                    (lock_name,),
                ).fetchone()
                if row:
                    try:
                        acq_time = time.mktime(
                            time.strptime(row["acquired_at"], "%Y-%m-%d %H:%M:%S"))
                        if (time.time() - acq_time) < 7200:
                            acquired = False
                            return
                    except Exception:
                        pass  # stale format — take over
                conn.execute(
                    "INSERT OR REPLACE INTO locks (name, holder, acquired_at) "
                    "VALUES (?, ?, ?)",
                    (lock_name, f"experiment_{exp_id}", now_iso),
                )
                acquired = True

        try:
            db._retry_write(_do)
        except Exception:
            log.warning("Failed to acquire GPU lock for experiment %d",
                        exp_id, exc_info=True)
            return False
        return acquired

    def _release_gpu_lock(self, exp_id: int) -> None:
        """Release GPU lock for this experiment."""
        import db
        lock_name = "gpu_experiment"

        def _do():
            with db.get_conn() as conn:
                conn.execute(
                    "DELETE FROM locks WHERE name = ? AND holder = ?",
                    (lock_name, f"experiment_{exp_id}"),
                )

        try:
            db._retry_write(_do)
        except Exception:
            log.warning("Failed to release GPU lock for experiment %d",
                        exp_id, exc_info=True)

    def _update_status(self, exp_id: int, status: str) -> None:
        """Update experiment status. Sets completed_at for terminal states."""
        import db
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")

        def _do():
            with db.get_conn() as conn:
                if status in ("DEPLOYED", "ROLLED_BACK", "REJECTED"):
                    conn.execute(
                        "UPDATE ml_experiments SET status = ?, completed_at = ? WHERE id = ?",
                        (status, time.time(), exp_id),
                    )
                else:
                    conn.execute(
                        "UPDATE ml_experiments SET status = ? WHERE id = ?",
                        (status, exp_id),
                    )

        db._retry_write(_do)

    def _update_metrics(self, exp_id: int, before: dict = None,
                        after: dict = None) -> None:
        """Update before/after metrics on an experiment."""
        import db

        def _do():
            with db.get_conn() as conn:
                if before is not None:
                    conn.execute(
                        "UPDATE ml_experiments SET metrics_before_json = ? WHERE id = ?",
                        (json.dumps(before), exp_id),
                    )
                if after is not None:
                    conn.execute(
                        "UPDATE ml_experiments SET metrics_after_json = ? WHERE id = ?",
                        (json.dumps(after), exp_id),
                    )

        db._retry_write(_do)

    def _update_artifact(self, exp_id: int, artifact_path: str) -> None:
        """Record the artifact path produced by training."""
        import db

        # Save previous artifact path for rollback
        exp = self.get(exp_id)
        previous = exp.get("artifact_path") if exp else None

        def _do():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE ml_experiments SET artifact_path = ?, previous_artifact = ? WHERE id = ?",
                    (artifact_path, previous, exp_id),
                )

        db._retry_write(_do)

    def _deploy_gate(self, exp_id: int, metrics_before: dict,
                     metrics_after: dict, auto_approved: bool) -> None:
        """Decide whether to deploy, roll back, or send to HITL.

        - If metrics degraded: ROLLED_BACK
        - If auto_approved and improved: DEPLOYED
        - Otherwise: back to PROPOSED for HITL review
        """
        improved = self._metrics_improved(metrics_before, metrics_after)
        degraded = self._metrics_degraded(metrics_before, metrics_after)

        if degraded:
            self._update_status(exp_id, "ROLLED_BACK")
            log.info("Experiment %d rolled back — metrics degraded", exp_id)
        elif auto_approved and improved:
            self._update_status(exp_id, "DEPLOYED")
            log.info("Experiment %d auto-deployed — metrics improved", exp_id)
        elif improved:
            # Improved but not auto-approved: send to HITL for final sign-off
            self._update_status(exp_id, "PROPOSED")
            log.info("Experiment %d metrics improved — awaiting HITL approval for deploy",
                     exp_id)
        else:
            # No clear improvement or degradation: HITL decides
            self._update_status(exp_id, "PROPOSED")
            log.info("Experiment %d inconclusive — awaiting HITL review", exp_id)

    @staticmethod
    def _metrics_improved(before: dict, after: dict) -> bool:
        """Return True if any numeric metric improved and none degraded significantly."""
        if not before or not after:
            return False
        dominated = False
        any_better = False
        for key in after:
            if key not in before:
                continue
            try:
                b_val = float(before[key])
                a_val = float(after[key])
            except (TypeError, ValueError):
                continue
            if a_val > b_val:
                any_better = True
            elif a_val < b_val * 0.95:  # >5% regression on any metric
                dominated = True
        return any_better and not dominated

    @staticmethod
    def _metrics_degraded(before: dict, after: dict) -> bool:
        """Return True if any numeric metric degraded by >5%."""
        if not before or not after:
            return False
        for key in after:
            if key not in before:
                continue
            try:
                b_val = float(before[key])
                a_val = float(after[key])
            except (TypeError, ValueError):
                continue
            if b_val > 0 and a_val < b_val * 0.95:
                return True
        return False
