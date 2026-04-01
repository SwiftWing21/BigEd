"""SSE streaming endpoint and adaptive broadcaster thread.

Extracted from dashboard.py (Phase 3 of dashboard decomposition).
The /api/stream route and _sse_broadcaster background thread live here.
SSE shared state (_sse_clients, _broadcast_sse, etc.) lives in dashboard_utils.py.
"""
import json
import logging
import queue
import time

from flask import Blueprint, Response

from dashboard_utils import (
    _sse_clients, _sse_lock, _broadcast_sse,
    _load_config, query, HW_STATE_JSON,
)

log = logging.getLogger("dashboard.sse")

sse_bp = Blueprint("sse", __name__)


# ── SSE endpoint ────────────────────────────────────────────────────────────

_SSE_MAX_CLIENTS = 50
_SSE_CLIENT_TIMEOUT = 120  # seconds before a silent client is pruned


@sse_bp.route("/api/stream")
def api_stream():
    """SSE endpoint for live updates (replaces 30s polling)."""
    with _sse_lock:
        if len(_sse_clients) >= _SSE_MAX_CLIENTS:
            return Response("data: {\"error\": \"too many clients\"}\n\n",
                            status=503, mimetype="text/event-stream")
        q = queue.Queue()
        _sse_clients.append({"queue": q, "last_active": time.time()})

    def generate():
        last_ping = time.time()
        try:
            # Send initial heartbeat
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    # Update last_active on successful receive
                    with _sse_lock:
                        for c in _sse_clients:
                            if c["queue"] is q:
                                c["last_active"] = time.time()
                                break
                    yield msg
                except queue.Empty:
                    # Send SSE ping every 15s to keep connection alive
                    now = time.time()
                    if now - last_ping >= 15:
                        yield ":ping\n\n"
                        last_ping = now
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                _sse_clients[:] = [c for c in _sse_clients if c["queue"] is not q]

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ── SSE broadcast thread ────────────────────────────────────────────────────

def _get_service_status() -> dict:
    """Return service health for Cosmo Bot + Dr. Ders without Flask request context."""
    import time
    import psutil

    def _find(script_name):
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                if any(script_name in str(arg) for arg in cmdline):
                    return proc
            except Exception:
                pass
        return None

    services = {}
    for key, script in (("cosmo_bot", "supervisor.py"), ("dr_ders", "hw_supervisor.py")):
        proc = _find(script)
        if proc:
            try:
                uptime_s = int(time.time() - proc.create_time())
                services[key] = {"status": "running", "pid": proc.pid, "uptime_s": uptime_s}
            except Exception:
                services[key] = {"status": "running", "pid": proc.pid, "uptime_s": 0}
        else:
            services[key] = {"status": "offline", "pid": None, "uptime_s": 0}
    return services


def _sse_broadcaster():
    """Adaptive-rate SSE push: fast (2s) when data changes, slows to 30s when stable."""
    _SSE_MIN_INTERVAL = 2    # floor: busy fleet
    _SSE_MAX_INTERVAL = 30   # ceiling: idle fleet
    _SSE_STEP_UP = 1.5       # multiplier each stable cycle
    interval = _SSE_MIN_INTERVAL
    prev_snapshot = None
    prev_service_status = None  # HP2: track service health for change detection

    # Import cpu cache from dashboard at runtime to avoid circular import
    def _get_cpu_pct():
        try:
            import dashboard
            return dashboard._cpu_pct_cache
        except Exception:
            return 0.0

    while True:
        # Prune dead clients that haven't been active for > 120s
        now_prune = time.time()
        with _sse_lock:
            _sse_clients[:] = [
                c for c in _sse_clients
                if now_prune - c.get("last_active", now_prune) < _SSE_CLIENT_TIMEOUT
            ]

        if _sse_clients:
            try:
                agents = query(
                    "SELECT name, role, status, last_heartbeat FROM agents "
                    "WHERE last_heartbeat > datetime('now', '-5 minutes') "
                    "AND status != 'DISABLED' ORDER BY name"
                )
                counts = {}
                for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
                    row = query("SELECT COUNT(*) as n FROM tasks WHERE status=? AND classification != 'synthetic_prefix'", (s,))
                    counts[s] = row[0]["n"] if row else 0

                # Thermal + system load (read hw_state.json + psutil -- cheap)
                thermal = {}
                if HW_STATE_JSON.exists():
                    try:
                        hw = json.loads(HW_STATE_JSON.read_text())
                        th = hw.get("thermal", {})
                        thermal = {
                            "gpu_temp_c": th.get("gpu_temp_c", 0),
                            "cpu_temp_c": th.get("cpu_temp_c", 0),
                            "gpu_vram_used_gb": round(th.get("vram_used_gb", 0), 2),
                            "gpu_vram_total_gb": round(th.get("vram_total_gb", 0), 2),
                        }
                    except Exception:
                        pass
                system = {}
                try:
                    import psutil
                    ram = psutil.virtual_memory()
                    system = {
                        "ram_pct": round(ram.percent, 1),
                        "cpu_pct": round(_get_cpu_pct(), 1),
                        "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
                    }
                except Exception:
                    pass

                # Live activity: running + recently completed tasks (for real-time feed)
                live_items = []
                try:
                    live_rows = query("""
                        SELECT t.id, t.type, t.status, t.assigned_to, t.classification,
                               t.created_at, substr(t.error, 1, 80) as error,
                               t.intelligence_score, t.priority, t.trace_id,
                               u.model, u.eval_duration_ms, u.input_tokens, u.output_tokens,
                               u.tokens_per_sec, u.cost_usd, u.provider
                        FROM tasks t
                        LEFT JOIN usage u ON u.task_id = t.id
                        WHERE t.classification != 'synthetic_prefix'
                          AND (t.status = 'RUNNING'
                               OR (t.status IN ('DONE', 'FAILED') AND t.created_at >= datetime('now', '-2 minutes')))
                        ORDER BY CASE t.status WHEN 'RUNNING' THEN 0 WHEN 'DONE' THEN 1 ELSE 2 END, t.id DESC
                        LIMIT 20
                    """)
                    live_items = [{
                        "id": r["id"], "type": r["type"], "status": r["status"],
                        "agent": r["assigned_to"] or "unassigned",
                        "classification": r["classification"],
                        "created_at": r["created_at"],
                        "error": r["error"] or "",
                        "model": r["model"] or "",
                        "duration_s": round(r["eval_duration_ms"] / 1000, 1) if r["eval_duration_ms"] else None,
                        "in_tokens": r["input_tokens"] or 0,
                        "out_tokens": r["output_tokens"] or 0,
                        "tok_per_sec": round(r["tokens_per_sec"], 1) if r["tokens_per_sec"] else None,
                        "cost_usd": round(r["cost_usd"], 4) if r["cost_usd"] else 0,
                        "provider": r["provider"] or "",
                        "iq_score": round(r["intelligence_score"], 2) if r["intelligence_score"] is not None else None,
                        "priority": r["priority"] or 3,
                        "trace_id": r["trace_id"] or "",
                    } for r in live_rows]
                except Exception:
                    pass

                # Legacy: recent tasks for neural pulse animation
                recent_tasks = [
                    {"agent": r["agent"], "skill": r["type"], "status": r["status"]}
                    for r in live_items if r["agent"] != "unassigned"
                ]

                # Factorio bridge status (lightweight probe)
                factorio_sse = None
                try:
                    import urllib.request as _ur2
                    from config import load_config as _lc2
                    _fp2 = _lc2().get("factorio", {}).get("bridge_port", 27016)
                    _fr2 = _ur2.urlopen(f"http://127.0.0.1:{_fp2}/api/status", timeout=1)
                    _fd2 = json.loads(_fr2.read())
                    _components = _fd2.get("components", {})
                    factorio_sse = {
                        "running": _fd2.get("running", False),
                        "tick": _fd2.get("tick", 0),
                        "paused": _fd2.get("paused", False),
                        "cadence": _fd2.get("cadence", "unknown"),
                        "stale": _fd2.get("stale", False),
                        "components": {
                            "bridge": _components.get("bridge", False),
                            "rcon": _components.get("rcon", False),
                            "headless": _components.get("headless", False),
                        },
                    }
                    if _fd2.get("running"):
                        # Inject a factorio pulse so the neural canvas shows activity
                        if not _fd2.get("paused"):
                            recent_tasks.append({
                                "agent": "Factorio", "skill": "bridge",
                                "status": "RUNNING",
                            })
                except Exception:
                    pass

                # Mode control -- active mode + modifier states for strip
                mode_sse = None
                try:
                    from mode_blueprint import _get_effective_mode, _get_modifier_states
                    _active_mode, _mode_state = _get_effective_mode()
                    mode_sse = {
                        "active": _active_mode,
                        "state": _mode_state,
                        "modifiers": _get_modifier_states(),
                    }
                except Exception:
                    pass

                payload = {
                    "agents": agents,
                    "tasks": counts,
                    "thermal": thermal,
                    "system": system,
                    "recent": recent_tasks,
                    "live": live_items,
                    "factorio": factorio_sse,
                    "mode": mode_sse,
                }

                # Adaptive rate: compare to previous snapshot
                snapshot = (
                    tuple((a["name"], a["status"]) for a in agents),
                    tuple(sorted(counts.items())),
                    thermal.get("gpu_temp_c", 0),
                    thermal.get("cpu_temp_c", 0),
                    system.get("ram_pct", 0),
                )
                if snapshot == prev_snapshot:
                    # Stable -- slow down (up to max)
                    interval = min(interval * _SSE_STEP_UP, _SSE_MAX_INTERVAL)
                else:
                    # Changed -- snap back to fast
                    interval = _SSE_MIN_INTERVAL
                prev_snapshot = snapshot

                payload["_interval"] = round(interval, 1)
                _broadcast_sse({"type": "status", "data": payload})

                # HP2: detect service health changes and push service_status event
                try:
                    svc = _get_service_status()
                    svc_snap = tuple(
                        (k, v.get("status")) for k, v in sorted(svc.items())
                    )
                    if prev_service_status is not None and svc_snap != prev_service_status:
                        _broadcast_sse({"type": "service_status", "data": svc})
                    prev_service_status = svc_snap
                except Exception:
                    pass
            except Exception:
                log.debug("SSE broadcast error", exc_info=True)
        else:
            # No clients -- idle at max rate
            interval = _SSE_MAX_INTERVAL
        time.sleep(interval)
