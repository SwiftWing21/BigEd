"""
BigEd CC — Module Management REST API.
Wraps BigEd/launcher/modules/hub.py for dashboard access.
"""
import json
import logging
import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

log = logging.getLogger("modules_api")
modules_bp = Blueprint("modules", __name__)

_HUB_DIR = Path(__file__).parent.parent / "BigEd" / "launcher" / "modules"


def _get_hub():
    """Lazy-load ModuleHub to avoid import at module level."""
    sys.path.insert(0, str(_HUB_DIR))
    try:
        from hub import ModuleHub
    finally:
        if str(_HUB_DIR) in sys.path:
            sys.path.remove(str(_HUB_DIR))
    try:
        from config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    return ModuleHub(cfg)


@modules_bp.route("/api/modules")
def api_modules_installed():
    try:
        hub = _get_hub()
        installed = hub.list_installed()
        return jsonify({"modules": installed})
    except Exception as e:
        log.warning("modules installed failed: %s", e)
        return jsonify({"modules": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/available")
def api_modules_available():
    try:
        hub = _get_hub()
        available = hub.list_available()
        installed_names = {m["name"] for m in hub.list_installed()}
        for m in available:
            m["installed"] = m["name"] in installed_names
        return jsonify({"modules": available})
    except Exception as e:
        log.warning("modules available failed: %s", e)
        return jsonify({"modules": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/updates")
def api_modules_updates():
    try:
        hub = _get_hub()
        updates = hub.get_update_available()
        return jsonify({"updates": updates})
    except Exception as e:
        log.warning("modules updates failed: %s", e)
        return jsonify({"updates": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/install", methods=["POST"])
def api_modules_install():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "Missing 'name' field"}), 400
    try:
        hub = _get_hub()
        result = hub.install_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module install failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/<name>/enable", methods=["POST"])
def api_modules_enable(name):
    try:
        hub = _get_hub()
        result = hub.enable_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module enable failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/<name>/disable", methods=["POST"])
def api_modules_disable(name):
    try:
        hub = _get_hub()
        result = hub.disable_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module disable failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/<name>/uninstall", methods=["DELETE"])
def api_modules_uninstall(name):
    try:
        hub = _get_hub()
        result = hub.uninstall_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module uninstall failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/suggestions")
def api_modules_suggestions():
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, module_name, reason, relevance_score, created_at "
                "FROM module_suggestions WHERE dismissed = 0 "
                "ORDER BY relevance_score DESC LIMIT 5"
            ).fetchall()
        return jsonify({"suggestions": [dict(r) for r in rows]})
    except Exception as e:
        log.warning("module suggestions failed: %s", e)
        return jsonify({"suggestions": []}), 500


@modules_bp.route("/api/modules/suggestions/<int:sid>/dismiss", methods=["POST"])
def api_modules_dismiss_suggestion(sid):
    try:
        import db
        def _do():
            with db.get_conn() as conn:
                conn.execute("UPDATE module_suggestions SET dismissed = 1 WHERE id = ?", (sid,))
        db._retry_write(_do)
        return jsonify({"ok": True})
    except Exception as e:
        log.warning("dismiss suggestion failed: %s", e)
        return jsonify({"error": str(e)}), 500
