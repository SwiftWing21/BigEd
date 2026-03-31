"""Remote deployment endpoints — prepare, push, receive, approve/reject.

Extracted from dashboard.py (Phase 4 of dashboard decomposition).
All /api/deploy/* routes live here.
"""
import logging

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    FLEET_DIR, _load_config, _require_role, _safe_error,
)

log = logging.getLogger("dashboard.deploy")

deploy_bp = Blueprint("deploy", __name__)


@deploy_bp.route("/api/deploy/prepare", methods=["POST"])
def api_deploy_prepare():
    """Create a deployment package for pushing to peers."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from remote_deploy import prepare_deployment
        data = request.get_json(silent=True) or {}
        pkg_path = prepare_deployment(
            include_skills=data.get("include_skills", True),
            include_config=data.get("include_config", True),
            include_models=data.get("include_models", False),
        )
        size_mb = pkg_path.stat().st_size / (1024 * 1024)
        return jsonify({"ok": True, "package": str(pkg_path), "size_mb": round(size_mb, 2)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/push", methods=["POST"])
def api_deploy_push():
    """Push a deployment package to a peer fleet."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from remote_deploy import push_to_peer
        data = request.get_json(silent=True) or {}
        peer_url = data.get("peer_url")
        package_path = data.get("package")
        if not peer_url or not package_path:
            return jsonify({"ok": False, "error": "peer_url and package required"}), 400
        result = push_to_peer(peer_url, package_path, timeout=data.get("timeout", 60))
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/status/<deploy_id>")
def api_deploy_status(deploy_id):
    """Check deployment status (local or remote via peer_url query param)."""
    try:
        peer_url = request.args.get("peer_url")
        if peer_url:
            from remote_deploy import deploy_status
            return jsonify(deploy_status(peer_url, deploy_id))
        else:
            from remote_deploy import get_local_deploy_status
            return jsonify(get_local_deploy_status(deploy_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/rollback/<deploy_id>", methods=["POST"])
def api_deploy_rollback(deploy_id):
    """Rollback a deployment (local or remote via peer_url in body)."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        peer_url = data.get("peer_url")
        if peer_url:
            from remote_deploy import rollback_peer
            return jsonify(rollback_peer(peer_url, deploy_id))
        else:
            # Local rollback -- restore from pre-deploy backup
            from backup_manager import BackupManager
            cfg = _load_config()
            bm = BackupManager(cfg)
            backups = bm.list_backups()
            pre_deploy = [b for b in backups if b.get("trigger") == "pre-deploy"]
            if not pre_deploy:
                return jsonify({"ok": False, "error": "No pre-deploy backup found"}), 404
            return jsonify({"ok": True, "backup_id": pre_deploy[0]["id"],
                           "note": "Use backup --restore to apply"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/history")
def api_deploy_history():
    """List recent deployments."""
    try:
        from remote_deploy import get_deployment_history
        limit = request.args.get("limit", 20, type=int)
        return jsonify({"deployments": get_deployment_history(limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Receiving side (peer receives a deployment push) ──────────────────────


@deploy_bp.route("/api/deploy/receive", methods=["POST"])
def api_deploy_receive():
    """Receive a deployment package from a peer fleet."""
    try:
        from remote_deploy import receive_deployment
        pkg_data = request.get_data()
        if not pkg_data:
            return jsonify({"ok": False, "error": "Empty package"}), 400
        result = receive_deployment(pkg_data)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/pending")
def api_deploy_pending():
    """List pending deployments awaiting operator approval."""
    try:
        from remote_deploy import get_pending_deployments
        return jsonify({"pending": get_pending_deployments()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/approve/<deploy_id>", methods=["POST"])
def api_deploy_approve(deploy_id):
    """Approve and apply a pending deployment (HITL gate)."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from remote_deploy import approve_deployment
        result = approve_deployment(deploy_id)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/reject/<deploy_id>", methods=["POST"])
def api_deploy_reject(deploy_id):
    """Reject a pending deployment."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from remote_deploy import reject_deployment
        result = reject_deployment(deploy_id)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
