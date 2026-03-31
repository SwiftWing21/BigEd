"""Alert monitoring background thread.

Extracted from dashboard.py (Phase 3 of dashboard decomposition).
The _alert_monitor() thread checks for alert-worthy conditions every 30s.
Alert state (_alerts, _add_alert, etc.) lives in dashboard_utils.py.
"""
import json
import logging
import time
from datetime import datetime

from dashboard_utils import (
    _load_config, query, _add_alert,
    HW_STATE_JSON, FLEET_DIR,
)

log = logging.getLogger("dashboard.alerts")

# Grace period: don't fire stale-agent alerts until 5min after monitor start
_monitor_start_time = None


def start_alert_monitor():
    """Start the alert monitor background thread. Call once at app startup."""
    import threading
    threading.Thread(target=_alert_monitor, daemon=True).start()


def _alert_monitor():
    """Background thread checking for alert-worthy conditions."""
    global _monitor_start_time
    _monitor_start_time = time.time()

    while True:
        try:
            # Check thermal
            if HW_STATE_JSON.exists():
                hw = json.loads(HW_STATE_JSON.read_text())
                gpu_temp = hw.get("gpu_temp_c", 0)
                cfg = _load_config()
                thermal = cfg.get("thermal", {})
                sustained = thermal.get("gpu_max_sustained_c", 75)
                burst = thermal.get("gpu_max_burst_c", 78)

                if gpu_temp > burst:
                    _add_alert("critical", f"GPU temp {gpu_temp}C exceeds burst limit {burst}C", "thermal")
                elif gpu_temp > sustained:
                    _add_alert("warning", f"GPU temp {gpu_temp}C above sustained limit {sustained}C", "thermal")

            # Check for crashed workers (stale heartbeats)
            # Skip disabled/quarantined agents and allow 5min grace after startup
            cfg = _load_config()
            disabled = set(cfg.get("fleet", {}).get("disabled_agents", []))
            agents = query("""
                SELECT name, last_heartbeat, status FROM agents
                WHERE last_heartbeat < datetime('now', '-5 minutes')
                AND status NOT IN ('OFFLINE', 'QUARANTINED', 'SLEEPING')
            """)
            stale = [a for a in agents if a["name"] not in disabled]
            if stale and (time.time() - _monitor_start_time) > 300:
                names = ", ".join(a["name"] for a in stale)
                _add_alert("warning", f"{len(stale)} agent(s) stale: {names}", "fleet")

            # Check disk space
            import shutil
            total, used, free = shutil.disk_usage(str(FLEET_DIR))
            free_gb = free / (1024**3)
            if free_gb < 5:
                _add_alert("warning", f"Low disk space: {free_gb:.1f}GB free", "system")

            # Check training lock timeout
            locks = query("SELECT * FROM locks WHERE name='training'")
            if locks:
                acquired = locks[0].get("acquired_at", "")
                if acquired:
                    try:
                        acq_time = datetime.fromisoformat(acquired)
                        elapsed = (datetime.utcnow() - acq_time).total_seconds()
                        cfg = _load_config()
                        timeout = cfg.get("training", {}).get("lock_timeout_secs", 7200)
                        if elapsed > timeout * 0.9:
                            _add_alert("warning",
                                       f"Training lock held for {elapsed/3600:.1f}h (timeout: {timeout/3600:.1f}h)",
                                       "training")
                    except Exception:
                        pass

            # Check for anomalous API spend (v0.170.04b: uses detect_cost_anomaly)
            try:
                from cost_tracking import detect_cost_anomaly
                anomaly = detect_cost_anomaly()
                if anomaly:
                    throttle_active = (FLEET_DIR / ".cost_anomaly_throttle").exists()
                    throttle_label = " [idle evolution paused]" if throttle_active else ""
                    _add_alert("warning",
                        f"Cost anomaly: ${anomaly['today_cost']:.2f} today "
                        f"({anomaly['multiplier']}x avg ${anomaly['avg_cost']:.2f})"
                        f"{throttle_label}", "cost")
            except Exception:
                pass

            # Check for high-scoring skill drafts pending review
            try:
                drafts = query("""
                    SELECT t.id, t.type, t.intelligence_score, t.assigned_to
                    FROM tasks t
                    WHERE t.type IN ('skill_evolve', 'evolution_coordinator')
                    AND t.status = 'DONE'
                    AND t.intelligence_score > 0.7
                    AND t.created_at >= datetime('now', '-24 hours')
                    AND t.id NOT IN (
                        SELECT CAST(json_extract(body_json, '$.task_id') AS INTEGER)
                        FROM messages WHERE json_extract(body_json, '$.type') = 'draft_reviewed'
                    )
                    LIMIT 3
                """)
                if drafts:
                    _add_alert("info", f"{len(drafts)} high-quality skill draft(s) ready for review", "evolution")
            except Exception:
                pass

        except Exception as e:
            _alert_failure_count = getattr(_alert_monitor, '_failures', 0) + 1
            _alert_monitor._failures = _alert_failure_count
            if _alert_failure_count <= 3:
                log.warning("Alert monitor error: %s", e)

        time.sleep(30)  # Check every 30s
