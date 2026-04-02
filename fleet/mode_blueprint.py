"""Mode control endpoints — mode status, switch, and helpers.

Extracted from dashboard.py (Phase 4 of dashboard decomposition).
All /api/mode/* routes plus mode detection/switching helpers live here.
"""
import importlib.util
import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    FLEET_DIR, _load_config, _broadcast_sse,
    _safe_error,
)

log = logging.getLogger("dashboard.mode")

mode_bp = Blueprint("mode", __name__)

# ── Mode Control state ─────────────────────────────────────────────────────

# Track desired mode so the UI stays consistent while a mode is starting up.
# Auto-expires after 30s if the target never becomes active.
_desired_mode = None        # {"mode": str, "since": float} or None
_DESIRED_MODE_TIMEOUT = 30  # seconds

# Serialise concurrent switch requests
_mode_switch_lock = threading.Lock()

# Queue pause flag file — shared with workers
_QUEUE_PAUSE_FILE = FLEET_DIR / ".queue_paused"


# ── Mode helpers ───────────────────────────────────────────────────────────


def _set_desired_mode(mode_id: str | None):
    global _desired_mode
    if mode_id and mode_id != "normal":
        _desired_mode = {"mode": mode_id, "since": time.time()}
    else:
        _desired_mode = None


def _persist_mode(mode_id: str):
    """Save last_mode to fleet.toml so it survives restarts."""
    try:
        import tomlkit
        toml_path = FLEET_DIR / "fleet.toml"
        doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
        doc.setdefault("fleet", {})["last_mode"] = mode_id
        toml_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to persist last_mode: %s", e)


def _get_effective_mode() -> tuple:
    """Return (active_mode, state) considering desired-mode intent.

    If a mode switch was requested recently and the probe says 'normal',
    report the desired mode as 'starting' until it either comes up or times out.
    """
    global _desired_mode
    probed = _get_active_mode()
    probed_state = _get_mode_state(probed)

    if _desired_mode:
        target = _desired_mode["mode"]
        elapsed = time.time() - _desired_mode["since"]
        if probed == target:
            # Mode is up -- clear desire
            _desired_mode = None
            return probed, probed_state
        if elapsed < _DESIRED_MODE_TIMEOUT:
            # Still waiting for mode to start
            return target, "starting"
        # Timed out -- mode never started, revert
        log.warning("Mode %s failed to start within %ds -- reverting to normal", target, _DESIRED_MODE_TIMEOUT)
        _desired_mode = None

    return probed, probed_state


def _detect_available_modes() -> list:
    """Return list of mode dicts available based on config + module probing."""
    modes = [
        {
            "id": "normal",
            "name": "Normal Fleet",
            "icon": "lightning",
            "description": "Standard worker pool with idle evolution",
        }
    ]
    try:
        cfg = _load_config()

        # Fleet Training -- only if [training] section exists in fleet.toml
        if cfg.get("training"):
            modes.append({
                "id": "training",
                "name": "Fleet Training",
                "icon": "brain",
                "description": "Exclusive skill optimization with GPU lock",
            })

        # Research Marathon -- autoresearch/ dir exists AND experiment module importable
        autoresearch_dir = Path(__file__).parent.parent / "autoresearch"
        if autoresearch_dir.is_dir():
            if importlib.util.find_spec("experiment") is not None:
                modes.append({
                    "id": "research_marathon",
                    "name": "Research Marathon",
                    "icon": "microscope",
                    "description": "Autonomous experiment loop",
                })
    except Exception:
        log.warning("_detect_available_modes failed", exc_info=True)

    return modes


def _get_active_mode() -> str:
    """Probe running state to determine the currently active mode id."""

    # Training -- check exclusive lock in DB
    try:
        from db import query as _db_query
        lock = _db_query("SELECT id FROM training_locks WHERE released_at IS NULL LIMIT 1")
        if lock:
            return "training"
    except Exception:
        pass

    # Research Marathon -- check running experiment
    try:
        from experiment import get_running_experiment
        if get_running_experiment():
            return "research_marathon"
    except Exception:
        pass

    return "normal"


def _get_mode_state(active_mode: str) -> str:
    """Return 'running', 'starting', or 'stopped' for the given mode."""
    if active_mode == "normal":
        return "running"
    if active_mode == "training":
        try:
            from db import query as _db_query
            lock = _db_query("SELECT id FROM training_locks WHERE released_at IS NULL LIMIT 1")
            return "running" if lock else "stopped"
        except Exception:
            pass
        return "stopped"
    if active_mode == "research_marathon":
        try:
            from experiment import get_running_experiment
            return "running" if get_running_experiment() else "stopped"
        except Exception:
            pass
        return "stopped"
    return "stopped"


def _get_mode_detail(active_mode: str) -> dict:
    """Return mode-specific detail dict (schema varies by mode)."""
    if active_mode == "training":
        try:
            from db import query as _db_query
            lock = _db_query(
                "SELECT lock_holder, progress_pct, profile FROM training_locks "
                "WHERE released_at IS NULL LIMIT 1"
            )
            if lock:
                row = lock[0]
                return {
                    "progress_pct": float(row.get("progress_pct") or 0),
                    "profile": row.get("profile") or "default",
                    "lock_holder": row.get("lock_holder") or "unknown",
                }
        except Exception:
            pass
        return {"progress_pct": 0.0, "profile": "default", "lock_holder": "unknown"}

    if active_mode == "research_marathon":
        try:
            from experiment import get_running_experiment
            exp = get_running_experiment()
            if exp:
                return {
                    "experiments_done": int(exp.get("experiments_done", 0)),
                    "current_experiment": exp.get("name") or exp.get("id") or "",
                    "running_since": exp.get("started_at") or "",
                }
        except Exception:
            pass
        return {"experiments_done": 0, "current_experiment": "", "running_since": ""}

    # normal
    return {}


def _get_modifier_states() -> dict:
    """Return dict of cross-cutting modifier states."""
    result: dict = {
        "queue_paused": False,
        "api_gate": {"enabled": False, "budget": 0.0, "spent": 0.0},
        "eco_mode": False,
        "offline_mode": False,
        "hitl_evolution": False,
    }

    # Queue pause -- flag file
    try:
        result["queue_paused"] = _QUEUE_PAUSE_FILE.exists()
    except Exception:
        pass

    # API gate -- lazy import to avoid circular issues
    try:
        import api_gate
        gate = api_gate.status()
        result["api_gate"] = {
            "enabled": bool(gate.get("enabled")),
            "budget": float(gate.get("session_budget_usd", 0.0)),
            "spent": float(gate.get("session_spend_usd", 0.0)),
        }
    except Exception:
        pass

    # Fleet config modifiers -- eco_mode, offline_mode, hitl_evolution
    try:
        cfg = _load_config()
        fleet_cfg = cfg.get("fleet", {})
        result["eco_mode"] = bool(fleet_cfg.get("eco_mode", False))
        result["offline_mode"] = bool(fleet_cfg.get("offline_mode", False))
        result["hitl_evolution"] = bool(fleet_cfg.get("hitl_evolution", False))
    except Exception:
        pass

    return result


def restore_mode(app):
    """On dashboard startup, restore last_mode and trigger switch if needed."""
    try:
        cfg = _load_config()
        saved = cfg.get("fleet", {}).get("last_mode", "normal")
        if saved and saved != "normal":
            available_ids = {m["id"] for m in _detect_available_modes()}
            if saved in available_ids:
                log.info("Restoring saved mode: %s", saved)
                _set_desired_mode(saved)
                # Trigger startup in background
                def _bg_restore():
                    try:
                        with app.test_request_context(json={"mode": saved, "force": False}):
                            api_mode_switch()
                    except Exception as e:
                        log.warning("Mode restore failed for %s: %s", saved, e)
                threading.Thread(target=_bg_restore, daemon=True, name="mode-restore").start()
    except Exception as e:
        log.warning("restore_mode failed: %s", e)


# ── Routes ─────────────────────────────────────────────────────────────────


@mode_bp.route("/api/mode/status")
def api_mode_status():
    """Return active mode, available modes, and modifier states."""
    try:
        active, state = _get_effective_mode()
        available = _detect_available_modes()
        detail = _get_mode_detail(active)
        modifiers = _get_modifier_states()

        # Annotate each available mode with its current state
        active_ids = {active}
        for m in available:
            m["state"] = state if m["id"] in active_ids else "available"

        return jsonify({
            "active": active,
            "state": state,
            "available_modes": available,
            "detail": detail,
            "modifiers": modifiers,
        })
    except Exception as e:
        log.warning("api_mode_status failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@mode_bp.route("/api/mode/switch", methods=["POST"])
def api_mode_switch():
    """Coordinator: stop current mode and start a new one.

    Body JSON:
        mode (str): target mode id
        force (bool): if True, tear down current mode first
        expected_current (str): compare-and-swap guard (required when force=True)
    """
    try:
        data = request.get_json(force=True) or {}
        target = data.get("mode", "normal")
        force = bool(data.get("force", False))
        expected_current = data.get("expected_current")

        available_ids = {m["id"] for m in _detect_available_modes()}
        if target not in available_ids:
            return jsonify({"error": f"Unknown or unavailable mode: {target}"}), 400

        with _mode_switch_lock:
            current = _get_active_mode()
            current_state = _get_mode_state(current)

            # Conflict probe (force=False) -- tell frontend to confirm
            if not force and current != "normal" and current != target and current_state == "running":
                mode_names = {
                    "training": "Fleet Training",
                    "research_marathon": "Research Marathon",
                    "normal": "Normal Fleet",
                }
                current_name = mode_names.get(current, current)
                target_name = mode_names.get(target, target)
                return jsonify({
                    "conflict": True,
                    "current": current,
                    "current_name": current_name,
                    "message": f"{current_name} is running. Stop it and switch to {target_name}?",
                })

            # Compare-and-swap guard (force=True)
            if force and expected_current is not None and expected_current != current:
                return jsonify({"error": "stale", "current": current}), 409

            # Tear down current mode (unless it's already normal or same as target)
            if current != "normal" and current != target:
                try:
                    if current == "training":
                        try:
                            import db as _db
                            def _release():
                                with _db.get_conn() as conn:
                                    conn.execute(
                                        "UPDATE training_locks SET released_at=datetime('now') "
                                        "WHERE released_at IS NULL"
                                    )
                            _db._retry_write(_release)
                        except Exception:
                            log.warning("Training lock release failed", exc_info=True)
                    elif current == "research_marathon":
                        try:
                            from experiment import stop_running_experiment
                            stop_running_experiment()
                        except Exception:
                            log.warning("Research marathon stop failed", exc_info=True)
                except Exception as teardown_err:
                    log.warning("Mode teardown failed for %s: %s", current, teardown_err)

            # Start new mode
            if target == "normal":
                # Normal is the baseline -- teardown above is sufficient
                _set_desired_mode(None)
                _persist_mode("normal")
                return jsonify({"success": True, "mode": "normal", "state": "running"})

            try:
                if target == "training":
                    # Acquire training lock in DB
                    import db as _db
                    def _acquire():
                        with _db.get_conn() as conn:
                            conn.execute(
                                "INSERT INTO training_locks (lock_holder, acquired_at) VALUES (?, datetime('now'))",
                                ("dashboard",),
                            )
                    _db._retry_write(_acquire)
                elif target == "research_marathon":
                    from experiment import start_experiment
                    start_experiment()
            except Exception as start_err:
                log.warning("Mode start failed for %s: %s", target, start_err)
                # Fall back to normal
                _broadcast_sse({"type": "mode_switch", "data": {
                    "mode": "normal", "fallback": True, "error": str(start_err),
                }})
                return jsonify({
                    "success": False,
                    "mode": "normal",
                    "error": f"{target} failed to start: {start_err}",
                    "fallback": True,
                })

            _set_desired_mode(target)
            _persist_mode(target)
            _broadcast_sse({"type": "mode_switch", "data": {"mode": target, "state": "starting"}})
            return jsonify({"success": True, "mode": target, "state": "starting"})

    except Exception as e:
        log.warning("api_mode_switch failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500
