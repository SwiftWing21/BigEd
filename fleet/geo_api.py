"""Geo-Distributed Fleets — Dashboard REST API (v0.400.00b).

Blueprint providing /api/regions/* and /api/cdn/* endpoints for geographic
fleet management, auto-scaling configuration, and CDN distribution.

Registered in dashboard.py alongside health_bp, fleet_bp, etc.
"""

import json
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

from security import (
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

geo_bp = Blueprint("geo", __name__)


def _load_config():
    """Load fleet.toml config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    try:
        with open(FLEET_DIR / "fleet.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _require_role(role):
    """Convenience wrapper for role-based access."""
    return _require_role_raw(role, _load_config)


# ── GET /api/regions — list all regions ──────────────────────────────────────

@geo_bp.route("/api/regions")
def api_list_regions():
    """List all registered fleet regions with health and capacity."""
    try:
        from geo_fleet import list_regions
        regions = list_regions()
        return jsonify({"regions": regions, "count": len(regions)})
    except Exception as exc:
        return _safe_error(exc, "Failed to list regions")


# ── POST /api/regions — register a new region ───────────────────────────────

@geo_bp.route("/api/regions", methods=["POST"])
@_require_role("operator")
def api_register_region():
    """Register a new fleet region.

    Body JSON: {"name": "us-east-1", "endpoint": "https://...:5555", "capacity": {...}}
    """
    try:
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        endpoint = data.get("endpoint", "").strip()
        capacity = data.get("capacity")

        if not name:
            return jsonify({"error": "name is required"}), 400
        if not endpoint:
            return jsonify({"error": "endpoint is required"}), 400

        from geo_fleet import register_region
        result = register_region(name, endpoint, capacity)
        return jsonify(result), 201
    except Exception as exc:
        return _safe_error(exc, "Failed to register region")


# ── GET /api/regions/<id>/health — region health ────────────────────────────

@geo_bp.route("/api/regions/<region_id>/health")
def api_region_health(region_id):
    """Health summary for a single region."""
    try:
        from geo_fleet import get_region_health
        result = get_region_health(region_id)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as exc:
        return _safe_error(exc, "Failed to get region health")


# ── POST /api/regions/<id>/scale — manual scale ─────────────────────────────

@geo_bp.route("/api/regions/<region_id>/scale", methods=["POST"])
@_require_role("operator")
def api_scale_region(region_id):
    """Manually scale a region to a target agent count.

    Body JSON: {"target_agents": 10}
    """
    try:
        data = request.get_json(silent=True) or {}
        target = data.get("target_agents")
        if target is None:
            return jsonify({"error": "target_agents is required"}), 400

        from geo_fleet import apply_auto_scale
        result = apply_auto_scale(region_id, int(target))
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as exc:
        return _safe_error(exc, "Failed to scale region")


# ── GET /api/regions/<id>/scaling-history — scaling events ───────────────────

@geo_bp.route("/api/regions/<region_id>/scaling-history")
def api_scaling_history(region_id):
    """Scaling event history for a region.

    Query params: hours (default 24)
    """
    try:
        hours = int(request.args.get("hours", 24))
        from geo_fleet import get_scaling_history
        events = get_scaling_history(region_id, hours=hours)
        return jsonify({"events": events, "count": len(events), "hours": hours})
    except Exception as exc:
        return _safe_error(exc, "Failed to get scaling history")


# ── PUT /api/regions/<id>/hpa — configure auto-scale ────────────────────────

@geo_bp.route("/api/regions/<region_id>/hpa", methods=["PUT"])
@_require_role("operator")
def api_configure_hpa(region_id):
    """Configure HPA (horizontal pod autoscaler) bounds for a region.

    Body JSON: {"min_agents": 2, "max_agents": 20, "target_utilization": 0.7}
    """
    try:
        data = request.get_json(silent=True) or {}
        min_agents = data.get("min_agents", 2)
        max_agents = data.get("max_agents", 20)
        target_util = data.get("target_utilization", 0.7)

        from geo_fleet import configure_hpa
        result = configure_hpa(region_id, min_agents, max_agents, target_util)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as exc:
        return _safe_error(exc, "Failed to configure HPA")


# ── GET /api/regions/scaling-recommendations — per-region advice ─────────────

@geo_bp.route("/api/regions/scaling-recommendations")
def api_scaling_recommendations():
    """Per-region scaling recommendations from the ML predictor."""
    try:
        from geo_fleet import get_scaling_recommendation
        recs = get_scaling_recommendation()
        return jsonify({"recommendations": recs})
    except Exception as exc:
        return _safe_error(exc, "Failed to get scaling recommendations")


# ── GET /api/regions/nearest — latency-based region selection ────────────────

@geo_bp.route("/api/regions/nearest")
def api_nearest_region():
    """Get the nearest region for the requesting client's IP.

    Query params: ip (optional, defaults to request remote_addr)
    """
    try:
        client_ip = request.args.get("ip", request.remote_addr or "127.0.0.1")
        from geo_fleet import get_nearest_region
        region = get_nearest_region(client_ip)
        return jsonify({"client_ip": client_ip, "nearest_region": region})
    except Exception as exc:
        return _safe_error(exc, "Failed to determine nearest region")


# ── POST /api/regions/<id>/heartbeat — region heartbeat ─────────────────────

@geo_bp.route("/api/regions/<region_id>/heartbeat", methods=["POST"])
def api_region_heartbeat(region_id):
    """Receive heartbeat from a remote region.

    Body JSON: {"capacity": {...}} (optional)
    """
    try:
        data = request.get_json(silent=True) or {}
        capacity = data.get("capacity")
        from geo_fleet import update_region_heartbeat
        update_region_heartbeat(region_id, capacity)
        return jsonify({"ok": True})
    except Exception as exc:
        return _safe_error(exc, "Failed to process heartbeat")


# ── GET /api/cdn/endpoints — list CDN distribution points ───────────────────

@geo_bp.route("/api/cdn/endpoints")
def api_cdn_endpoints():
    """List all CDN distribution points with region info."""
    try:
        from geo_fleet import list_cdn_endpoints
        endpoints = list_cdn_endpoints()
        return jsonify({"endpoints": endpoints, "count": len(endpoints)})
    except Exception as exc:
        return _safe_error(exc, "Failed to list CDN endpoints")


# ── POST /api/cdn/endpoints — register CDN endpoint ─────────────────────────

@geo_bp.route("/api/cdn/endpoints", methods=["POST"])
@_require_role("operator")
def api_register_cdn():
    """Register a new CDN distribution point.

    Body JSON: {"url": "https://cdn.example.com", "region": "us-east-1"}
    """
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        region = data.get("region", "").strip()

        if not url:
            return jsonify({"error": "url is required"}), 400
        if not region:
            return jsonify({"error": "region is required"}), 400

        from geo_fleet import register_cdn_endpoint
        result = register_cdn_endpoint(url, region)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result), 201
    except Exception as exc:
        return _safe_error(exc, "Failed to register CDN endpoint")


# ── POST /api/cdn/sync — trigger CDN sync ───────────────────────────────────

@geo_bp.route("/api/cdn/sync", methods=["POST"])
@_require_role("operator")
def api_cdn_sync():
    """Push local skills to a region's CDN endpoints.

    Body JSON: {"region": "us-east-1"}
    """
    try:
        data = request.get_json(silent=True) or {}
        region = data.get("region", "").strip()

        if not region:
            return jsonify({"error": "region is required"}), 400

        from geo_fleet import sync_skills_to_cdn
        result = sync_skills_to_cdn(region)
        return jsonify(result)
    except Exception as exc:
        return _safe_error(exc, "Failed to sync skills to CDN")


# ── GET /api/cdn/skill-url — nearest CDN URL for a skill ────────────────────

@geo_bp.route("/api/cdn/skill-url")
def api_skill_download_url():
    """Get the CDN download URL for a skill package.

    Query params: skill (required), region (optional, defaults to nearest)
    """
    try:
        skill_name = request.args.get("skill", "").strip()
        if not skill_name:
            return jsonify({"error": "skill query param is required"}), 400

        region = request.args.get("region", "")
        if not region:
            client_ip = request.remote_addr or "127.0.0.1"
            from geo_fleet import get_nearest_region
            region = get_nearest_region(client_ip)

        from geo_fleet import get_skill_download_url
        url = get_skill_download_url(skill_name, region)
        return jsonify({"skill": skill_name, "region": region, "url": url})
    except Exception as exc:
        return _safe_error(exc, "Failed to get skill download URL")
