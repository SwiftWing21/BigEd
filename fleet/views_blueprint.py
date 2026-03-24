"""Hybrid ViewPort — Views REST API (Phase 2–4).

Blueprint providing /api/views/* endpoints for data source discovery,
individual source lookup, registration health, graph rendering,
view config serving, static file serving, and the drag-and-drop view builder.

Registered in dashboard.py alongside other blueprints.
"""
import logging
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).resolve().parent

views_bp = Blueprint("views", __name__)


# ── GET /api/views/sources — all registered data sources ─────────────────────

@views_bp.route("/api/views/sources")
def api_views_sources():
    """Return all registered data sources from the view registry."""
    try:
        import view_registry
        sources = view_registry.get_sources()
        return jsonify({"sources": sources, "count": len(sources)})
    except ImportError:
        log.warning("view_registry module not available")
        return jsonify({"sources": [], "count": 0, "warning": "view_registry not loaded"})
    except Exception:
        log.warning("Failed to fetch view sources", exc_info=True)
        return jsonify({"error": "Failed to retrieve view sources"}), 500


# ── GET /api/views/sources/<name> — single source by name ────────────────────

@views_bp.route("/api/views/sources/<name>")
def api_views_source_by_name(name):
    """Return a single registered source by name, or 404."""
    try:
        import view_registry
        source = view_registry.get_source(name)
        if source is None:
            return jsonify({"error": "Source not found", "name": name}), 404
        return jsonify(source)
    except ImportError:
        log.warning("view_registry module not available")
        return jsonify({"error": "view_registry not loaded"}), 503
    except Exception:
        log.warning("Failed to fetch view source %r", name, exc_info=True)
        return jsonify({"error": "Failed to retrieve view source"}), 500


# ── GET /api/views/health — registration health ──────────────────────────────

@views_bp.route("/api/views/health")
def api_views_health():
    """Return registration health: registered vs attempted, failed modules."""
    try:
        import view_registry
        health = view_registry.get_health()
        registered = health.get("registered", 0)
        attempted = health.get("attempted", 0)
        failed = health.get("failed", [])
        if not failed:
            status = "ok"
        elif registered == 0:
            status = "unavailable"
        else:
            status = "partial"
        return jsonify({
            "registered": registered,
            "attempted": attempted,
            "failed": failed,
            "status": status,
        })
    except ImportError:
        log.warning("view_registry module not available")
        return jsonify({
            "registered": 0,
            "attempted": 0,
            "failed": [],
            "status": "unavailable",
            "warning": "view_registry not loaded",
        })
    except Exception:
        log.warning("Failed to fetch view health", exc_info=True)
        return jsonify({"error": "Failed to retrieve view health"}), 500


# ── Phase 3: Graph rendering, view configs, static files ─────────────────────


# ── GET /view/graph/<name> — full-chrome graph view page ─────────────────────

@views_bp.route("/view/graph/<name>")
def view_graph(name):
    """Serve full-chrome graph view page."""
    template = FLEET_DIR / "templates" / "view_graph.html"
    if not template.exists():
        return Response("<h1>Graph template not found</h1>", status=404, mimetype="text/html")
    return Response(template.read_text(encoding="utf-8"), mimetype="text/html")


# ── GET /view/embed/<name> — minimal embed view for launcher webview ─────────

@views_bp.route("/view/embed/<name>")
def view_embed(name):
    """Serve minimal-chrome embed view for launcher webview."""
    template = FLEET_DIR / "templates" / "view_embed.html"
    if not template.exists():
        return Response("<h1>Embed template not found</h1>", status=404, mimetype="text/html")
    return Response(template.read_text(encoding="utf-8"), mimetype="text/html")


# ── GET /api/views/config/<name> — single view config JSON ──────────────────

@views_bp.route("/api/views/config/<name>")
def api_views_config(name):
    """Serve a pre-built view config JSON."""
    # Sanitize name to prevent path traversal
    safe_name = name.replace("..", "").replace("/", "").replace("\\", "")
    config_path = FLEET_DIR / "views" / f"{safe_name}.json"
    if not config_path.exists():
        return jsonify({"error": "View config not found", "name": name}), 404
    try:
        import json
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return jsonify(config)
    except Exception:
        log.warning("Failed to load view config %r", name, exc_info=True)
        return jsonify({"error": "Failed to load view config"}), 500


# ── GET /api/views/configs — list all available view configs ─────────────────

@views_bp.route("/api/views/configs")
def api_views_configs():
    """List all available view configs."""
    views_dir = FLEET_DIR / "views"
    if not views_dir.exists():
        return jsonify({"configs": [], "count": 0})
    configs = []
    for f in sorted(views_dir.glob("*.json")):
        try:
            import json
            data = json.loads(f.read_text(encoding="utf-8"))
            configs.append({
                "name": data.get("name", f.stem),
                "description": data.get("description", ""),
                "layout": data.get("layout", "cluster"),
            })
        except Exception:
            log.warning("Failed to read view config %s", f.name, exc_info=True)
    return jsonify({"configs": configs, "count": len(configs)})


# ── GET /api/views/graph/<name> — aggregated graph data ──────────────────────

@views_bp.route("/api/views/graph/<name>")
def api_views_graph(name):
    """Aggregate graph data for a named view from registered sources."""
    # Load view config
    safe_name = name.replace("..", "").replace("/", "").replace("\\", "")
    config_path = FLEET_DIR / "views" / f"{safe_name}.json"

    if config_path.exists():
        import json
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        # Dynamic view from a single source
        import view_registry
        source = view_registry.get_source(name)
        if source is None:
            return jsonify({"error": "View not found", "name": name}), 404
        config = {
            "name": name,
            "layout": source.get("layout_hint", "cluster"),
            "sources": [name],
        }

    # Aggregate data from registered sources
    import view_registry
    sources_data = []
    requested_sources = config.get("sources", [])

    for src_name in requested_sources:
        source = view_registry.get_source(src_name)
        if source is None:
            continue
        # Build placeholder graph data from source metadata
        # (Real data comes from source data_endpoints in future phases)
        nodes = [
            {"id": f"{src_name}:{nt}", "type": nt, "source": src_name, "status": "IDLE"}
            for nt in source.get("node_types", [])
        ]
        edges = [
            {
                "source": f"{src_name}:{source.get('node_types', ['unknown'])[0]}",
                "target": f"{src_name}:{nt}",
                "type": et,
                "weight": 1,
            }
            for et, nt in zip(
                source.get("edge_types", []),
                source.get("node_types", [])[1:] or ["unknown"],
            )
        ]
        sources_data.append({
            "source": src_name,
            "nodes": nodes,
            "edges": edges,
            "color": source.get("color", "#888"),
            "icon": source.get("icon", "circle"),
        })

    return jsonify({"sources": sources_data, "view": config})


# ── GET /static/<path:filename> — serve static files ─────────────────────────

@views_bp.route("/static/<path:filename>")
def serve_static(filename):
    """Serve static files (view_engine.js, tokens.css, etc)."""
    static_dir = FLEET_DIR / "static"
    return send_from_directory(str(static_dir), filename)


# ── Phase 4: View builder, config save/delete ───────────────────────────────


# ── GET /view/builder — drag-and-drop view builder page ──────────────────────

@views_bp.route("/view/builder")
def view_builder():
    """Serve the drag-and-drop view builder page."""
    template = FLEET_DIR / "templates" / "view_builder.html"
    if not template.exists():
        return Response("<h1>Builder template not found</h1>", status=404, mimetype="text/html")
    return Response(template.read_text(encoding="utf-8"), mimetype="text/html")


# ── POST /api/views/config/<name> — save view config JSON ───────────────────

@views_bp.route("/api/views/config/<name>", methods=["POST"])
def api_views_config_save(name):
    """Save a view config JSON to fleet/views/<name>.json."""
    import json
    import os

    # Path traversal protection
    safe_name = name.replace("..", "").replace("/", "").replace("\\", "")
    if not safe_name or safe_name != name:
        return jsonify({"error": "Invalid view name"}), 400

    views_dir = FLEET_DIR / "views"
    os.makedirs(views_dir, exist_ok=True)
    config_path = views_dir / f"{safe_name}.json"

    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Expected JSON object"}), 400

        # Ensure schema version
        data.setdefault("schema_version", 1)
        data["name"] = safe_name

        config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info("Saved view config: %s", safe_name)
        return jsonify({"status": "saved", "name": safe_name})
    except Exception:
        log.warning("Failed to save view config %r", name, exc_info=True)
        return jsonify({"error": "Failed to save view config"}), 500


# ── DELETE /api/views/config/<name> — delete custom view config ──────────────

@views_bp.route("/api/views/config/<name>", methods=["DELETE"])
def api_views_config_delete(name):
    """Delete a custom view config."""
    safe_name = name.replace("..", "").replace("/", "").replace("\\", "")
    if not safe_name or safe_name != name:
        return jsonify({"error": "Invalid view name"}), 400

    # Protect pre-built views
    protected = {"fleet-overview", "training-pipeline", "data-flow", "bottleneck-detector"}
    if safe_name in protected:
        return jsonify({"error": "Cannot delete pre-built view"}), 403

    config_path = FLEET_DIR / "views" / f"{safe_name}.json"
    if not config_path.exists():
        return jsonify({"error": "View not found"}), 404

    try:
        config_path.unlink()
        log.info("Deleted view config: %s", safe_name)
        return jsonify({"status": "deleted", "name": safe_name})
    except Exception:
        log.warning("Failed to delete view config %r", name, exc_info=True)
        return jsonify({"error": "Failed to delete view config"}), 500


# ── Experiment API ───────────────────────────────────────────────────────

@views_bp.route("/api/experiments")
def api_experiments_list():
    """List recent experiments."""
    try:
        from experiment import ExperimentFramework
        fw = ExperimentFramework()
        agent = request.args.get("agent")
        exp_type = request.args.get("type")
        limit = int(request.args.get("limit", 50))
        experiments = fw.history(agent=agent, experiment_type=exp_type, limit=limit)
        return jsonify({"experiments": experiments, "count": len(experiments)})
    except Exception:
        log.warning("Failed to list experiments", exc_info=True)
        return jsonify({"error": "Failed to list experiments"}), 500


@views_bp.route("/api/experiments/pending")
def api_experiments_pending():
    """List experiments awaiting HITL approval."""
    try:
        from experiment import ExperimentFramework
        fw = ExperimentFramework()
        pending = fw.pending_approval()
        return jsonify({"pending": pending, "count": len(pending)})
    except Exception:
        log.warning("Failed to list pending experiments", exc_info=True)
        return jsonify({"error": "Failed to list pending experiments"}), 500


@views_bp.route("/api/experiments/<int:exp_id>/approve", methods=["POST"])
def api_experiments_approve(exp_id):
    """Approve a pending experiment."""
    try:
        from experiment import ExperimentFramework
        fw = ExperimentFramework()
        if fw.approve(exp_id):
            return jsonify({"status": "approved", "id": exp_id})
        return jsonify({"error": "Cannot approve experiment"}), 400
    except Exception:
        log.warning("Failed to approve experiment %d", exp_id, exc_info=True)
        return jsonify({"error": "Failed to approve experiment"}), 500


@views_bp.route("/api/experiments/<int:exp_id>/reject", methods=["POST"])
def api_experiments_reject(exp_id):
    """Reject a pending experiment."""
    try:
        from experiment import ExperimentFramework
        fw = ExperimentFramework()
        if fw.reject(exp_id):
            return jsonify({"status": "rejected", "id": exp_id})
        return jsonify({"error": "Cannot reject experiment"}), 400
    except Exception:
        log.warning("Failed to reject experiment %d", exp_id, exc_info=True)
        return jsonify({"error": "Failed to reject experiment"}), 500
