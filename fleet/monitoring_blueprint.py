"""Monitoring endpoints — health, thermal, alerts, logs, integrity, SLA, metrics.

Extracted from dashboard.py (Phase 5 of dashboard decomposition).
"""
import json
import logging
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, Response, request

from dashboard_utils import (
    FLEET_DIR, DB_PATH, KNOWLEDGE_DIR, HW_STATE_JSON, VALID_AGENT,
    _load_config, get_conn, query,
    _get_request_role, _require_role,
    _check_rate_limit, _is_recent, safe_error,
    _alerts, _alert_lock,
    _safe_error,
)

log = logging.getLogger("dashboard.monitoring")

monitoring_bp = Blueprint("monitoring", __name__)


# ── Alerts ──────────────────────────────────────────────────────────────────

@monitoring_bp.route("/api/alerts")
def api_alerts():
    """Return current alerts — in-memory + persistent DB alerts."""
    hours = int(request.args.get("hours", 24))
    severity = request.args.get("severity")
    # In-memory alerts (legacy SSE-based)
    with _alert_lock:
        mem_alerts = list(_alerts[-50:])
    # Persistent DB alerts (0.22.00)
    try:
        import db
        db_alerts = db.get_alerts(hours=hours, severity=severity)
    except Exception:
        db_alerts = []
    return jsonify({"memory": mem_alerts, "persistent": db_alerts})


@monitoring_bp.route("/api/alerts/ack/<int:alert_id>", methods=["POST"])
def api_ack_alert(alert_id):
    """Acknowledge an alert."""
    deny = _require_role("operator")
    if deny:
        return deny
    with _alert_lock:
        for a in _alerts:
            if a["id"] == alert_id:
                a["acknowledged"] = True
                return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


# ── Log Viewer ──────────────────────────────────────────────────────────────

@monitoring_bp.route("/api/logs/stream")
def api_logs_stream():
    """SSE endpoint streaming log lines (tail -f style).

    Query params:
        source: "supervisor" (default), "dashboard", or "worker"
    """
    import queue as queue_mod

    source = request.args.get("source", "supervisor").strip()
    allowed_sources = {
        "supervisor": FLEET_DIR / "logs" / "supervisor.log",
        "dashboard": FLEET_DIR / "logs" / "dashboard.log",
    }

    log_path = allowed_sources.get(source)
    if log_path is None:
        # Also allow worker logs: worker_<name>.log
        if source.startswith("worker_") and re.match(r'^worker_[a-zA-Z0-9_-]+$', source):
            log_path = FLEET_DIR / "logs" / f"{source}.log"
        else:
            return jsonify({"error": f"Unknown log source: {source}"}), 400

    def generate():
        try:
            yield f"data: {{\"type\": \"connected\", \"source\": \"{source}\"}}\n\n"

            if not log_path.exists():
                yield f"data: {{\"type\": \"info\", \"line\": \"Log file not found: {log_path.name}\"}}\n\n"
                return

            # Start by sending last 50 lines
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    for line in lines[-50:]:
                        line = line.rstrip()
                        if line:
                            escaped = json.dumps(line)
                            yield f"data: {{\"type\": \"log\", \"line\": {escaped}}}\n\n"
            except Exception:
                pass

            # Then tail for new lines
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)  # Seek to end
                    while True:
                        line = f.readline()
                        if line:
                            line = line.rstrip()
                            if line:
                                escaped = json.dumps(line)
                                yield f"data: {{\"type\": \"log\", \"line\": {escaped}}}\n\n"
                        else:
                            # No new data — send keepalive
                            yield ": keepalive\n\n"
                            time.sleep(1)
            except GeneratorExit:
                pass
        except GeneratorExit:
            pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@monitoring_bp.route("/api/logs/recent")
def api_logs_recent():
    """Last N log lines as JSON array.

    Query params:
        n (int):      number of lines (default 100, max 1000)
        source (str): "supervisor" (default), "dashboard", or "worker_<name>"
        filter (str): optional substring filter
    """
    try:
        n = min(1000, max(1, int(request.args.get("n", 100))))
        source = request.args.get("source", "supervisor").strip()
        line_filter = request.args.get("filter", "").strip()

        allowed_sources = {
            "supervisor": FLEET_DIR / "logs" / "supervisor.log",
            "dashboard": FLEET_DIR / "logs" / "dashboard.log",
        }

        log_path = allowed_sources.get(source)
        if log_path is None:
            if source.startswith("worker_") and re.match(r'^worker_[a-zA-Z0-9_-]+$', source):
                log_path = FLEET_DIR / "logs" / f"{source}.log"
            else:
                return jsonify({"error": f"Unknown log source: {source}"}), 400

        if not log_path.exists():
            return jsonify({"lines": [], "total": 0, "source": source})

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception as e:
            return jsonify({"error": _safe_error(e), "lines": []}), 500

        # Apply filter if provided
        if line_filter:
            all_lines = [l for l in all_lines if line_filter.lower() in l.lower()]

        # Return last N lines
        recent = [l.rstrip() for l in all_lines[-n:] if l.strip()]

        return jsonify({
            "lines": recent,
            "total": len(recent),
            "source": source,
            "log_path": str(log_path),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e), "lines": []}), 500


@monitoring_bp.route("/api/logs/sources")
def api_logs_sources():
    """List available log sources (files in fleet/logs/)."""
    try:
        logs_dir = FLEET_DIR / "logs"
        sources = []
        if logs_dir.exists():
            for f in sorted(logs_dir.glob("*.log")):
                sources.append({
                    "name": f.stem,
                    "file": f.name,
                    "size_bytes": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
        return jsonify({"sources": sources, "total": len(sources)})
    except Exception as e:
        return jsonify({"error": _safe_error(e), "sources": []}), 500


# ── Thermal ─────────────────────────────────────────────────────────────────

# Background CPU sampler cache — imported from dashboard.py's module-level cache
# We need a reference to the dashboard's _cpu_pct_cache. Since this is read-only
# and set by a background thread in dashboard.py, we access it via a getter.
_cpu_pct_cache_ref = None


def set_cpu_cache_ref(ref_dict):
    """Called by dashboard.py to share the CPU cache reference."""
    global _cpu_pct_cache_ref
    _cpu_pct_cache_ref = ref_dict


def _get_cpu_pct():
    if _cpu_pct_cache_ref is not None:
        return _cpu_pct_cache_ref.get("value", 0.0)
    return 0.0


@monitoring_bp.route("/api/thermal")
def api_thermal():
    """Live GPU/CPU temps, fan speed, power, ambient estimate."""
    result = {
        "gpu_temp_c": 0, "gpu_power_w": 0, "gpu_fan_pct": 0,
        "gpu_vram_used_gb": 0, "gpu_vram_total_gb": 0,
        "cpu_temp_c": None, "ambient_estimate_c": None,
        "thermal_state": "unknown", "model_tier": "unknown",
    }

    # Read from hw_state.json (written by hw_supervisor.write_state())
    if HW_STATE_JSON.exists():
        try:
            hw = json.loads(HW_STATE_JSON.read_text())
            th = hw.get("thermal", {})
            model = hw.get("model", "unknown")

            # Determine model tier from model name
            tier_map = {"qwen3:8b": "default", "qwen3:4b": "mid",
                        "qwen3:1.7b": "low", "qwen3:0.6b": "critical"}
            model_tier = tier_map.get(model, model or "unknown")

            cpu_t = th.get("cpu_temp_c", 0)
            ambient_t = th.get("ambient_est_c", 0)
            result.update({
                "gpu_temp_c": th.get("gpu_temp_c", 0),
                "gpu_power_w": th.get("gpu_power_w", 0),
                "gpu_fan_pct": th.get("gpu_fan_pct", 0),
                "gpu_vram_used_gb": round(th.get("vram_used_gb", 0), 2),
                "gpu_vram_total_gb": round(th.get("vram_total_gb", 0), 2),
                "cpu_temp_c": cpu_t if cpu_t and cpu_t > 0 else None,
                "ambient_estimate_c": ambient_t if ambient_t and ambient_t > 0 else None,
                "thermal_state": hw.get("status", "unknown"),
                "model_tier": model_tier,
            })
        except Exception:
            pass

    # Fallback: read GPU directly if hw_state.json has no thermal data
    if result["gpu_temp_c"] == 0:
        try:
            from gpu import detect_gpu, read_telemetry
            backend, has_gpu = detect_gpu()
            if has_gpu:
                gpu_data = read_telemetry(backend)
                if gpu_data:
                    result["gpu_temp_c"] = gpu_data.get("gpu_temp_c", 0)
                    result["gpu_power_w"] = gpu_data.get("gpu_power_w", 0)
                    result["gpu_fan_pct"] = gpu_data.get("gpu_fan_pct", 0)
                    result["gpu_vram_used_gb"] = round(gpu_data.get("vram_used_gb", 0), 2)
                    result["gpu_vram_total_gb"] = round(gpu_data.get("vram_total_gb", 0), 2)
        except Exception:
            pass

    # Fallback: read CPU temp directly if hw_state.json has no CPU data
    if not result["cpu_temp_c"]:
        try:
            from cpu_temp import read_cpu_temp
            val = read_cpu_temp()
            if val > 0:
                result["cpu_temp_c"] = val
        except Exception:
            pass

    # Annotate if GPU sensor is still unreadable after fallbacks
    if result["gpu_temp_c"] == 0:
        result["_note"] = "0\u00b0C = unable to access GPU sensor"

    # System resources (always available, even without GPU)
    try:
        import psutil
        ram = psutil.virtual_memory()
        result["system"] = {
            "ram_total_gb": round(ram.total / (1024**3), 1),
            "ram_used_gb": round(ram.used / (1024**3), 1),
            "ram_pct": ram.percent,
            "cpu_pct": _get_cpu_pct(),
            "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
        }
    except Exception:
        pass

    # Add config thresholds
    cfg = _load_config()
    thermal = cfg.get("thermal", {})
    result["thresholds"] = {
        "gpu_sustained": thermal.get("gpu_max_sustained_c", 75),
        "gpu_burst": thermal.get("gpu_max_burst_c", 78),
        "cpu_sustained": thermal.get("cpu_max_sustained_c", 80),
        "cooldown_target": thermal.get("cooldown_target_c", 72),
    }

    return jsonify(result)


@monitoring_bp.route("/api/fleet/provider-health")
def api_provider_health():
    try:
        from providers import get_provider_health
        return jsonify(get_provider_health())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Integrity ───────────────────────────────────────────────────────────────

@monitoring_bp.route("/api/integrity")
def api_integrity():
    try:
        from integrity import verify_integrity
        return jsonify(verify_integrity())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@monitoring_bp.route("/api/integrity/refresh", methods=["POST"])
def api_integrity_refresh():
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from integrity import save_manifest
        path = save_manifest()
        return jsonify({"status": "manifest_saved", "path": str(path)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Cluster Metrics ─────────────────────────────────────────────────────────

@monitoring_bp.route("/api/cluster/metrics")
def api_cluster_metrics():
    """Aggregated metrics across all federated peers."""
    try:
        from federation_data import get_cluster_metrics
        return jsonify(get_cluster_metrics())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── SLA Monitoring ──────────────────────────────────────────────────────────

@monitoring_bp.route("/api/sla")
def api_sla():
    """SLA monitoring -- task completion time guarantees."""
    try:
        conn = get_conn()
        # Average completion time by skill (last 7 days)
        metrics = conn.execute("""
            SELECT type as skill,
                   COUNT(*) as tasks,
                   AVG(CAST((julianday(
                       CASE WHEN status='DONE' THEN created_at END
                   ) - julianday(created_at)) * 86400 AS INTEGER)) as avg_secs,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate
            FROM tasks
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY type
            ORDER BY tasks DESC LIMIT 20
        """).fetchall()

        # Overall fleet SLA
        overall = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                   SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
            FROM tasks WHERE created_at >= datetime('now', '-24 hours')
        """).fetchone()

        return jsonify({
            "skills": [dict(r) for r in metrics],
            "overall_24h": {
                "total": overall["total"],
                "success_rate": round(overall["done"] / max(overall["total"], 1) * 100, 1),
                "failure_rate": round(overall["failed"] / max(overall["total"], 1) * 100, 1),
            },
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500
