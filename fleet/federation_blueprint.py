"""Federation endpoints — peer discovery, heartbeat, routing, mTLS, HITL.

Extracted from dashboard.py (Phase 4 of dashboard decomposition).
All /api/federation/* routes live here.
"""
import logging
import time

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    _require_role, _get_request_role, _broadcast_sse, _safe_error,
)
from security import validate_peer_url

log = logging.getLogger("dashboard.federation")

federation_bp = Blueprint("federation", __name__)

# Federation state -- peer heartbeats tracked in memory
_federation_peers = {}


# ── Heartbeat & Discovery ──────────────────────────────────────────────────


@federation_bp.route("/api/federation/heartbeat", methods=["POST"])
def api_federation_heartbeat():
    """Receive heartbeat from peer fleet."""
    deny = _require_role("operator")
    if deny:
        return deny
    data = request.get_json(silent=True) or {}
    fleet_id = data.get("fleet_id") or "unknown"
    _federation_peers[fleet_id] = {
        "agents": data.get("agents", 0),
        "pending": data.get("pending", 0),
        "last_seen": time.time(),
    }
    # Prune stale peers (>2 hours since last heartbeat)
    now = time.time()
    stale_peers = [k for k, v in _federation_peers.items() if now - v["last_seen"] > 7200]
    for k in stale_peers:
        del _federation_peers[k]
    return jsonify({"ok": True})


@federation_bp.route("/api/federation/peers")
def api_federation_peers():
    """List known federation peers and their online status."""
    now = time.time()
    peers = {k: {**v, "online": now - v["last_seen"] < 120}
             for k, v in _federation_peers.items()}
    return jsonify(peers)


@federation_bp.route("/api/federation/discovered")
def api_federation_discovered():
    """List auto-discovered peers (separate from manually configured).

    Returns peers found via UDP broadcast and/or mDNS, with online status.
    """
    try:
        import discovery
        discovered = discovery.get_discovered_peers()
        all_peers = discovery.get_all_peers()
        return jsonify({
            "discovered": discovered,
            "all_peers": all_peers,
            "discovery_running": discovery._running,
            "fleet_id": discovery._fleet_id,
        })
    except ImportError:
        return jsonify({"discovered": [], "all_peers": [], "discovery_running": False,
                        "fleet_id": "", "error": "discovery module not available"})
    except Exception as e:
        return jsonify({"discovered": [], "all_peers": [], "discovery_running": False,
                        "fleet_id": "", "error": _safe_error(e)})


# ── Routing ────────────────────────────────────────────────────────────────


@federation_bp.route("/api/federation/capacity")
def api_federation_capacity():
    """Aggregated cluster capacity -- local + all reachable peers."""
    try:
        from federation_router import get_aggregated_capacity
        return jsonify(get_aggregated_capacity())
    except ImportError:
        return jsonify({"error": "federation_router not available"}), 501


@federation_bp.route("/api/federation/routing-stats")
def api_federation_routing_stats():
    """Routing statistics -- how many tasks routed locally vs remotely."""
    try:
        from federation_router import get_routing_stats
        return jsonify(get_routing_stats())
    except ImportError:
        return jsonify({"error": "federation_router not available"}), 501
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@federation_bp.route("/api/federation/route", methods=["POST"])
def api_federation_route():
    """Manually route a task to a specific peer fleet.

    Body JSON: {"peer_url": "http://...", "type": "skill_name",
                "payload": {...}, "priority": 5}
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        peer_url = data.get("peer_url")
        task_type = data.get("type")
        if not peer_url or not task_type:
            return jsonify({"error": "peer_url and type are required"}), 400

        ok, reason = validate_peer_url(peer_url)
        if not ok:
            return jsonify({"error": f"Invalid peer_url: {reason}"}), 400

        from federation_router import route_to_peer

        peer = {"url": peer_url}
        task_dict = {
            "type": task_type,
            "payload": data.get("payload", {}),
            "priority": data.get("priority", 5),
        }
        result = route_to_peer(peer, task_dict)

        if result.get("ok"):
            _broadcast_sse({"type": "federation_route", "data": result})
            return jsonify(result)
        else:
            return jsonify(result), 502
    except ImportError:
        return jsonify({"error": "federation_router not available"}), 501


# ── HITL ───────────────────────────────────────────────────────────────────


@federation_bp.route("/api/federation/hitl")
def api_federation_hitl():
    """Aggregated HITL tasks from local fleet and all federation peers."""
    try:
        from federation_hitl import get_all_hitl_tasks
        tasks = get_all_hitl_tasks()
        return jsonify(tasks)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@federation_bp.route("/api/federation/hitl/respond", methods=["POST"])
def api_federation_hitl_respond():
    """Respond to a HITL task on a remote peer fleet.

    Body: {"peer_url": "http://...", "task_id": 123, "response": "approved"}
    If peer_url is "local" or omitted, routes to the local fleet.
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        peer_url = data.get("peer_url", "local")
        task_id = data.get("task_id")
        response_text = data.get("response", "").strip()

        if not task_id:
            return jsonify({"error": "task_id is required"}), 400
        if not response_text:
            return jsonify({"error": "response is required"}), 400

        if peer_url == "local" or not peer_url:
            # Local response
            import db
            db.respond_to_agent(int(task_id), response_text)
            _broadcast_sse({
                "type": "hitl_response",
                "data": {"task_id": task_id, "responded": True, "source": "local"},
            })
            return jsonify({"ok": True, "task_id": task_id, "source": "local"})
        else:
            # Remote response -- forward to peer
            from federation_hitl import respond_to_remote_hitl
            result = respond_to_remote_hitl(peer_url, int(task_id), response_text)
            if "error" in result:
                return jsonify(result), 502
            _broadcast_sse({
                "type": "hitl_response",
                "data": {"task_id": task_id, "responded": True, "source": f"peer:{peer_url}"},
            })
            return jsonify({"ok": True, "task_id": task_id, "source": f"peer:{peer_url}"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@federation_bp.route("/api/federation/hitl/notify", methods=["POST"])
def api_federation_hitl_notify():
    """Receive notification from a peer about a new HITL task.

    Broadcasts an SSE event so connected dashboards update live.
    Body: task_info dict with _source_fleet field.
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        source_fleet = data.get("_source_fleet", "unknown")
        _broadcast_sse({
            "type": "remote_hitl_waiting",
            "data": {
                "task_id": data.get("task_id") or data.get("id"),
                "type": data.get("type", ""),
                "question": data.get("question", ""),
                "agent": data.get("agent", ""),
                "source_fleet": source_fleet,
            },
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── mTLS ───────────────────────────────────────────────────────────────────


@federation_bp.route("/api/federation/cert-status")
def api_federation_cert_status():
    """Certificate health info for the dashboard."""
    try:
        from fleet_tls import get_cert_info
        return jsonify(get_cert_info())
    except ImportError:
        return jsonify({"tls_enabled": False, "warning": "fleet_tls module not available"})


@federation_bp.route("/api/federation/exchange-cert", methods=["POST"])
def api_federation_exchange_cert():
    """Peer sends its cert, receives local cert.

    Request body: {"peer_id": "...", "cert_pem": "-----BEGIN CERTIFICATE..."}
    Response: {"ok": true, "cert_pem": "-----BEGIN CERTIFICATE..."}
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        from fleet_tls import store_trusted_cert, get_local_cert_pem, is_tls_enabled
        if not is_tls_enabled():
            return jsonify({"error": "Federation TLS not enabled"}), 400
        data = request.get_json(silent=True) or {}
        peer_id = data.get("peer_id")
        cert_pem = data.get("cert_pem")
        if not peer_id or not cert_pem:
            return jsonify({"error": "peer_id and cert_pem required"}), 400
        # Store the incoming peer cert
        store_trusted_cert(peer_id, cert_pem)
        # Return our cert for mutual trust
        local_cert = get_local_cert_pem()
        return jsonify({"ok": True, "cert_pem": local_cert})
    except FileNotFoundError as e:
        return jsonify({"error": _safe_error(e)}), 404
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500
