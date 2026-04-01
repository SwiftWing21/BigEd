"""
Update Blueprint — REST API endpoints for the self-update system.

7 endpoints: status, check, apply, cancel, progress (SSE), history, rollback.

v0.400.00b
"""

from flask import Blueprint, Response, jsonify
import logging
import threading
import time

from dashboard_utils import _require_role, _safe_error

log = logging.getLogger(__name__)

update_bp = Blueprint("update", __name__)

# ── Module state ────────────────────────────────────────────────────────────

_progress_events: list[dict] = []
_progress_lock = threading.Lock()
_apply_thread: threading.Thread | None = None
_apply_start_time: float = 0.0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _push_progress(step: str, percent: float) -> None:
    """Progress callback for apply_update — stores event and broadcasts SSE."""
    elapsed = time.time() - _apply_start_time
    event = {
        "step": step,
        "status": "in_progress",
        "percent": round(percent * 100, 1),
        "detail": step,
        "elapsed": round(elapsed, 1),
    }
    with _progress_lock:
        _progress_events.append(event)

    # Broadcast via SSE
    try:
        from dashboard_utils import _broadcast_sse
        _broadcast_sse({"type": "update_progress", "data": event})
    except Exception:
        log.warning("Failed to broadcast update_progress SSE", exc_info=True)


def _apply_worker() -> None:
    """Background thread that runs the update and broadcasts completion."""
    global _apply_thread
    try:
        import update_manager
        result = update_manager.apply_update(progress_cb=_push_progress)
    except Exception as exc:
        log.warning("Update apply thread failed", exc_info=True)
        result = {"success": False, "restart_required": False, "error": str(exc)}

    # Broadcast completion
    try:
        from dashboard_utils import _broadcast_sse
        _broadcast_sse({"type": "update_complete", "data": result})
    except Exception:
        log.warning("Failed to broadcast update_complete SSE", exc_info=True)

    # Store terminal event for the SSE progress stream
    with _progress_lock:
        _progress_events.append({
            "step": "done",
            "status": "complete",
            "percent": 100.0,
            "detail": result.get("error") or "Update complete",
            "elapsed": round(time.time() - _apply_start_time, 1),
            "_result": result,
        })


# ═══════════════════════════════════════════════════════════════════════════
# 1. GET /api/update/status
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/status")
def api_update_status():
    """Return cached update status + version + mode."""
    deny = _require_role("viewer")
    if deny:
        return deny
    try:
        import update_manager
        return jsonify(update_manager.get_status())
    except Exception as e:
        log.warning("GET /api/update/status failed", exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 2. POST /api/update/check
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/check", methods=["POST"])
def api_update_check():
    """Check for available updates (git fetch or GitHub Releases)."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        import update_manager
        result = update_manager.check_for_update()
        return jsonify(result)
    except Exception as e:
        log.warning("POST /api/update/check failed", exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 3. POST /api/update/apply
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """Start update in a background thread. Returns immediately."""
    global _apply_thread, _apply_start_time
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        if _apply_thread is not None and _apply_thread.is_alive():
            return jsonify({"error": "Update already in progress"}), 409

        # Reset progress state
        with _progress_lock:
            _progress_events.clear()
        _apply_start_time = time.time()

        _apply_thread = threading.Thread(
            target=_apply_worker, daemon=True, name="update-apply"
        )
        _apply_thread.start()
        return jsonify({"ok": True, "message": "Update started"})
    except Exception as e:
        log.warning("POST /api/update/apply failed", exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 4. POST /api/update/cancel
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/cancel", methods=["POST"])
def api_update_cancel():
    """Cancel an in-progress update."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        import update_manager
        update_manager.cancel_update()
        return jsonify({"ok": True, "message": "Cancel requested"})
    except Exception as e:
        log.warning("POST /api/update/cancel failed", exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 5. GET /api/update/progress — SSE stream
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/progress")
def api_update_progress():
    """SSE stream of update progress events."""
    deny = _require_role("viewer")
    if deny:
        return deny

    def _generate():
        cursor = 0
        while True:
            with _progress_lock:
                new_events = _progress_events[cursor:]
                cursor = len(_progress_events)

            for evt in new_events:
                import json
                yield f"data: {json.dumps(evt)}\n\n"
                # Stop streaming once we see the terminal event
                if evt.get("status") == "complete":
                    return

            # If thread is done and no more events, stop
            if _apply_thread is not None and not _apply_thread.is_alive():
                # Drain any remaining
                with _progress_lock:
                    remaining = _progress_events[cursor:]
                for evt in remaining:
                    import json
                    yield f"data: {json.dumps(evt)}\n\n"
                return

            time.sleep(0.5)

    return Response(_generate(), mimetype="text/event-stream")


# ═══════════════════════════════════════════════════════════════════════════
# 6. GET /api/update/history
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/history")
def api_update_history():
    """Return update history from manifest."""
    deny = _require_role("viewer")
    if deny:
        return deny
    try:
        import update_manager
        history = update_manager.get_history()
        return jsonify({"history": history, "count": len(history)})
    except Exception as e:
        log.warning("GET /api/update/history failed", exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 7. POST /api/update/rollback — placeholder
# ═══════════════════════════════════════════════════════════════════════════


@update_bp.route("/api/update/rollback", methods=["POST"])
def api_update_rollback():
    """Rollback to previous version (not yet implemented)."""
    deny = _require_role("operator")
    if deny:
        return deny
    return jsonify({"error": "Rollback not yet implemented"}), 501
