"""Hybrid ViewPort — Views REST API (Phase 2–4).

Blueprint providing /api/views/* endpoints for data source discovery,
individual source lookup, registration health, graph rendering,
view config serving, static file serving, and the drag-and-drop view builder.

Registered in dashboard.py alongside other blueprints.
"""
import logging
import re
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

    # Aggregate LIVE data from fleet DB
    import view_registry
    sources_data = []
    requested_sources = config.get("sources", [])

    for src_name in requested_sources:
        source = view_registry.get_source(src_name)
        if source is None:
            continue
        live = _fetch_live_source_data(src_name, source)
        sources_data.append(live)

    # Deduplicate nodes across sources (same ID from multiple sources = one node)
    seen_node_ids = set()
    for src in sources_data:
        unique_nodes = []
        for n in src.get("nodes", []):
            if n["id"] not in seen_node_ids:
                seen_node_ids.add(n["id"])
                unique_nodes.append(n)
        src["nodes"] = unique_nodes

    return jsonify({"sources": sources_data, "view": config})


def _fetch_live_source_data(src_name: str, source: dict) -> dict:
    """Fetch real-time graph data for a registered source from the fleet DB."""
    import db

    nodes = []
    edges = []
    color = source.get("color", "#888")
    icon = source.get("icon", "circle")

    try:
        if src_name == "supervisor":
            nodes, edges = _graph_supervisor(db)
        elif src_name == "rag":
            nodes, edges = _graph_rag(db)
        elif src_name == "reinforcement":
            nodes, edges = _graph_reinforcement(db)
        elif src_name == "federation":
            nodes, edges = _graph_federation(db)
        elif src_name == "autoresearch":
            nodes, edges = _graph_autoresearch(db)
        elif src_name == "knowledge":
            nodes, edges = _graph_knowledge(db)
        elif src_name == "universe":
            nodes, edges = _graph_universe(db)
        else:
            # Fallback: metadata-only nodes
            nodes = [
                {"id": f"{src_name}:{nt}", "type": nt, "source": src_name, "status": "IDLE"}
                for nt in source.get("node_types", [])
            ]
    except Exception:
        log.warning("Failed to fetch live data for source %s", src_name, exc_info=True)

    return {"source": src_name, "nodes": nodes, "edges": edges, "color": color, "icon": icon}


def _graph_supervisor(db) -> tuple:
    """Live fleet graph: supervisor hub → agents → their current tasks."""
    nodes = []
    edges = []

    # Hub node
    nodes.append({
        "id": "supervisor:hub",
        "type": "supervisor",
        "source": "supervisor",
        "label": "FLEET",
        "status": "ACTIVE",
    })

    # Live agents from DB
    with db.get_conn() as conn:
        agents = conn.execute("""
            SELECT a.name, a.role, a.status, a.current_task_id,
                   t.type as task_type, t.status as task_status
            FROM agents a
            LEFT JOIN tasks t ON a.current_task_id = t.id
            WHERE a.last_heartbeat IS NOT NULL
            ORDER BY a.name
        """).fetchall()

    for a in agents:
        agent_id = f"supervisor:{a['name']}"
        status = a["status"] or "IDLE"
        label = a["name"]
        if a["task_type"]:
            label = f"{a['name']} ({a['task_type']})"

        nodes.append({
            "id": agent_id,
            "type": "agent",
            "source": "supervisor",
            "label": label,
            "status": status,
            "metrics": {
                "role": a["role"] or "",
                "task": a["task_type"] or "",
                "task_status": a["task_status"] or "",
            },
        })
        edges.append({
            "source": "supervisor:hub",
            "target": agent_id,
            "type": "manages",
            "weight": 3 if status == "BUSY" else 1,
        })

    # Task counts as metrics on the hub
    with db.get_conn() as conn:
        counts = {}
        for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
            row = conn.execute("SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,)).fetchone()
            counts[s] = row["n"] if row else 0

    nodes[0]["metrics"] = {
        "agents": len(agents),
        "pending": counts.get("PENDING", 0),
        "running": counts.get("RUNNING", 0),
        "done": counts.get("DONE", 0),
        "failed": counts.get("FAILED", 0),
    }

    return nodes, edges


def _graph_rag(db) -> tuple:
    """RAG graph: index stats as nodes."""
    nodes = []
    edges = []

    try:
        from pathlib import Path as _P
        rag_db = _P(__file__).resolve().parent / "rag.db"
        if rag_db.exists():
            import sqlite3
            conn = sqlite3.connect(str(rag_db), timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT COUNT(*) as n FROM chunks_meta").fetchone()
            chunk_count = row["n"] if row else 0
            sources = conn.execute(
                "SELECT source, COUNT(*) as n FROM chunks_meta GROUP BY source ORDER BY n DESC LIMIT 10"
            ).fetchall()
            conn.close()

            nodes.append({
                "id": "rag:index", "type": "index", "source": "rag",
                "label": f"RAG Index ({chunk_count} chunks)", "status": "ACTIVE",
                "metrics": {"chunk_count": chunk_count},
            })
            for s in sources:
                src_id = f"rag:src:{s['source'][:20]}"
                nodes.append({
                    "id": src_id, "type": "chunk", "source": "rag",
                    "label": f"{s['source'][:25]} ({s['n']})", "status": "IDLE",
                    "metrics": {"chunks": s["n"]},
                })
                edges.append({"source": src_id, "target": "rag:index", "type": "indexes", "weight": s["n"]})
        else:
            nodes.append({"id": "rag:index", "type": "index", "source": "rag", "label": "RAG (no DB)", "status": "OFFLINE"})
    except Exception:
        log.warning("rag graph data failed", exc_info=True)
        nodes.append({"id": "rag:index", "type": "index", "source": "rag", "label": "RAG", "status": "ERROR"})

    return nodes, edges


def _graph_reinforcement(db) -> tuple:
    """Reinforcement graph: feedback counts."""
    nodes = []
    edges = []

    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM output_feedback WHERE verdict='approved'"
            ).fetchone()
            approved = row["n"] if row else 0
            row = conn.execute(
                "SELECT COUNT(*) as n FROM output_feedback WHERE verdict='rejected'"
            ).fetchone()
            rejected = row["n"] if row else 0

        nodes.append({
            "id": "reinforcement:scorer", "type": "scorer", "source": "reinforcement",
            "label": f"Feedback ({approved} approved, {rejected} rejected)",
            "status": "ACTIVE" if (approved + rejected) > 0 else "IDLE",
            "metrics": {"approved": approved, "rejected": rejected},
        })
    except Exception:
        log.warning("reinforcement graph data failed", exc_info=True)
        nodes.append({"id": "reinforcement:scorer", "type": "scorer", "source": "reinforcement", "label": "Feedback", "status": "IDLE"})

    return nodes, edges


def _graph_federation(db) -> tuple:
    """Federation graph: peer status."""
    nodes = [{"id": "federation:local", "type": "fleet", "source": "federation", "label": "Local Fleet", "status": "ACTIVE"}]
    edges = []
    # Federation peers are in-memory in dashboard.py — minimal placeholder for now
    return nodes, edges


def _graph_autoresearch(db) -> tuple:
    """Autoresearch graph: training status + best result."""
    nodes = []
    edges = []

    try:
        from pathlib import Path as _P
        results_path = _P(__file__).resolve().parent.parent / "autoresearch" / "results.tsv"
        if results_path.exists():
            import csv
            with open(results_path, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            scored = [r for r in rows if r.get("val_bpb") and float(r.get("val_bpb", 999)) > 0]
            best_bpb = min(float(r["val_bpb"]) for r in scored) if scored else None

            nodes.append({
                "id": "autoresearch:trainer", "type": "trainer", "source": "autoresearch",
                "label": f"Autoresearch ({len(rows)} runs)",
                "status": "ACTIVE",
                "metrics": {"total_runs": len(rows), "best_bpb": best_bpb},
            })
            if best_bpb:
                nodes.append({
                    "id": "autoresearch:best", "type": "evaluator", "source": "autoresearch",
                    "label": f"Best: {best_bpb:.4f} bpb",
                    "status": "IDLE",
                })
                edges.append({"source": "autoresearch:trainer", "target": "autoresearch:best", "type": "produces", "weight": 2})
        else:
            nodes.append({"id": "autoresearch:trainer", "type": "trainer", "source": "autoresearch", "label": "Autoresearch (no data)", "status": "OFFLINE"})
    except Exception:
        log.warning("autoresearch graph data failed", exc_info=True)
        nodes.append({"id": "autoresearch:trainer", "type": "trainer", "source": "autoresearch", "label": "Autoresearch", "status": "ERROR"})

    return nodes, edges


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


# ── Docs API — structured markdown parsing ───────────────────────────────

_DOC_FILES = [
    ("roadmap", "ROADMAP.md", "Active plan, version history"),
    ("blueprint", "FRAMEWORK_BLUEPRINT.md", "Architecture spec, data schema, endpoints"),
    ("audit", "AUDIT_TRACKER.md", "Grading rubric, scoreboard"),
    ("operations", "OPERATIONS.md", "Runbook, CLI reference, troubleshooting"),
    ("cross-platform", "CROSS_PLATFORM.md", "Platform matrix, FleetBridge"),
    ("contributing", "CONTRIBUTING.md", "Contributor guide, code standards"),
    ("setup", "SETUP.md", "First-time install walkthrough"),
]

_SLUG_MAP = {slug: filename for slug, filename, _ in _DOC_FILES}


@views_bp.route("/api/docs")
def api_docs_list():
    """List available documentation files."""
    project_root = FLEET_DIR.parent
    docs = []
    for slug, filename, desc in _DOC_FILES:
        path = project_root / filename
        if path.exists():
            size = path.stat().st_size
            docs.append({"slug": slug, "filename": filename, "description": desc,
                         "size_bytes": size, "exists": True})
        else:
            docs.append({"slug": slug, "filename": filename, "description": desc,
                         "size_bytes": 0, "exists": False})
    return jsonify({"docs": docs, "count": len([d for d in docs if d["exists"]])})


@views_bp.route("/api/docs/<slug>")
def api_docs_get(slug):
    """Parse a markdown doc into structured, filterable sections."""
    project_root = FLEET_DIR.parent
    filename = _SLUG_MAP.get(slug)
    if not filename:
        return jsonify({"error": "Unknown doc", "slug": slug}), 404

    path = project_root / filename
    if not path.exists():
        return jsonify({"error": "File not found", "filename": filename}), 404

    try:
        text = path.read_text(encoding="utf-8")
        sections = _parse_markdown_sections(text)

        # Extract metadata for filtering
        statuses = set()
        versions = set()
        for s in sections:
            if s.get("status"):
                statuses.add(s["status"])
            if s.get("version"):
                versions.add(s["version"])
            for sub in s.get("children", []):
                if sub.get("status"):
                    statuses.add(sub["status"])
                if sub.get("version"):
                    versions.add(sub["version"])

        return jsonify({
            "slug": slug,
            "filename": filename,
            "title": sections[0]["title"] if sections else filename,
            "sections": sections,
            "filters": {
                "statuses": sorted(statuses),
                "versions": sorted(versions),
            },
            "total_sections": sum(1 + len(s.get("children", [])) for s in sections),
        })
    except Exception:
        log.warning("Failed to parse doc %s", slug, exc_info=True)
        return jsonify({"error": "Failed to parse document"}), 500


@views_bp.route("/api/docs/<slug>/search")
def api_docs_search(slug):
    """Search within a doc's sections."""
    query = request.args.get("q", "").lower().strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    filename = _SLUG_MAP.get(slug)
    if not filename:
        return jsonify({"error": "Unknown doc"}), 404
    path = FLEET_DIR.parent / filename
    if not path.exists():
        return jsonify({"error": "File not found"}), 404

    try:
        text = path.read_text(encoding="utf-8")
        sections = _parse_markdown_sections(text)

        matches = []
        for s in sections:
            if query in s["title"].lower() or query in s.get("body", "").lower():
                matches.append({"title": s["title"], "level": s["level"],
                               "status": s.get("status"), "snippet": s.get("body", "")[:200]})
            for child in s.get("children", []):
                if query in child["title"].lower() or query in child.get("body", "").lower():
                    matches.append({"title": child["title"], "level": child["level"],
                                   "parent": s["title"], "status": child.get("status"),
                                   "snippet": child.get("body", "")[:200]})

        return jsonify({"query": query, "matches": matches, "count": len(matches)})
    except Exception:
        log.warning("Failed to search doc %s", slug, exc_info=True)
        return jsonify({"error": "Search failed"}), 500


def _parse_markdown_sections(text: str) -> list[dict]:
    """Parse markdown into a tree of sections with metadata extraction."""
    lines = text.split("\n")
    sections = []
    current_h2 = None
    current_h3 = None
    buffer = []

    def _flush_buffer():
        return "\n".join(buffer).strip()

    def _extract_metadata(title: str, body: str) -> dict:
        """Extract status, version, and tags from section content."""
        meta = {}

        # Status detection
        title_lower = title.lower()
        if "[done]" in title_lower or "\u2713" in title or "DONE" in title:
            meta["status"] = "done"
        elif "[x]" in body.lower()[:200] and "[ ]" not in body[:200]:
            meta["status"] = "done"
        elif "in progress" in title_lower or "WIP" in title:
            meta["status"] = "in_progress"
        elif "[ ]" in body[:500]:
            meta["status"] = "not_started"
        elif "completed" in title_lower:
            meta["status"] = "done"

        # Version detection (v0.XX, 0.XXX.YYb, etc)
        ver_match = re.search(r'v?(\d+\.\d+(?:\.\d+)?(?:b)?)', title)
        if ver_match:
            meta["version"] = ver_match.group(1)

        return meta

    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            # Flush previous h3
            if current_h3 is not None:
                current_h3["body"] = _flush_buffer()
                buffer = []
            # Flush previous h2
            if current_h2 is not None:
                if current_h3 is None:
                    current_h2["body"] = _flush_buffer()
                    buffer = []
                sections.append(current_h2)

            title = line[3:].strip()
            current_h2 = {
                "title": title,
                "level": 2,
                "body": "",
                "children": [],
                **_extract_metadata(title, ""),
            }
            current_h3 = None
            buffer = []

        elif line.startswith("### "):
            # Flush previous h3 or h2 body
            if current_h3 is not None:
                current_h3["body"] = _flush_buffer()
                buffer = []
            elif current_h2 is not None and not current_h2.get("children"):
                current_h2["body"] = _flush_buffer()
                buffer = []

            title = line[4:].strip()
            current_h3 = {
                "title": title,
                "level": 3,
                "body": "",
                **_extract_metadata(title, ""),
            }
            if current_h2 is not None:
                current_h2["children"].append(current_h3)
            buffer = []

        else:
            buffer.append(line)

    # Flush remaining
    if current_h3 is not None:
        current_h3["body"] = _flush_buffer()
    elif current_h2 is not None:
        current_h2["body"] = _flush_buffer()
    if current_h2 is not None:
        # Update metadata with body content
        body_text = current_h2["body"]
        for child in current_h2.get("children", []):
            body_text += " " + child.get("body", "")
            child.update(_extract_metadata(child["title"], child.get("body", "")))
        current_h2.update(_extract_metadata(current_h2["title"], body_text))
        sections.append(current_h2)

    return sections


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


# ── Knowledge Graph Data ─────────────────────────────────────────────────────

# Skill → output folder mapping (derived from codebase analysis)
_SKILL_IO_MAP = {
    # skill_name: {"output": "knowledge/subdir or None", "agent": "role", "input": [...]}
    # --- analyst ---
    "analyze_results":        {"output": "reports",             "agent": "analyst", "input": ["autoresearch"]},
    "clinical_review":        {"output": "ditl",                "agent": "analyst"},
    "evaluate":               {"output": "evaluations",         "agent": "analyst"},
    "marathon_log":           {"output": "marathon",            "agent": "analyst"},
    "quality_flywheel":       {"output": "flywheel",            "agent": "analyst", "input": ["knowledge/*"]},
    "outcome_tracker":        {"output": "outcomes",            "agent": "analyst", "input": ["tasks"]},
    "prompt_optimize":        {"output": "prompt_optimization", "agent": "analyst", "input": ["tasks"]},
    "regression_detector":    {"output": "reports",             "agent": "analyst"},
    "stability_report":       {"output": "stability",           "agent": "analyst"},
    # --- analyst (P3) ---
    "doc_freshness":          {"output": "freshness",            "agent": "analyst", "input": ["*.md"]},
    "model_eval_framework":   {"output": "evaluations",          "agent": "analyst", "input": ["ollama"]},
    "agent_personality_tune":  {"output": "personalities",       "agent": "analyst", "input": ["tasks"]},
    "cross_fleet_knowledge_sync": {"output": "sync",             "agent": "analyst", "input": ["knowledge/*"]},
    # --- archivist ---
    "flashcard":              {"output": "flashcards.jsonl",    "agent": "archivist"},
    "knowledge_prune":        {"output": None,                  "agent": "archivist", "input": ["knowledge/*"]},
    # --- coder ---
    "benchmark":              {"output": "reports",             "agent": "coder"},
    "branch_manager":         {"output": "code_writes/workspace", "agent": "coder"},
    "claude_code":            {"output": "claude_code",         "agent": "coder"},
    "code_discuss":           {"output": "code_discussion",     "agent": "coder"},
    "code_index":             {"output": "code_index.jsonl",    "agent": "coder"},
    "code_quality":           {"output": "quality/reviews",     "agent": "coder"},
    "code_refactor":          {"output": "code_writes/refactors", "agent": "coder"},
    "code_review":            {"output": "code_reviews",        "agent": "coder"},
    "code_write":             {"output": "code_writes",         "agent": "coder"},
    "code_write_review":      {"output": "code_writes/reviews", "agent": "coder"},
    "db_migrate":             {"output": None,                  "agent": "coder"},
    "deploy_skill":           {"output": "code_drafts",         "agent": "coder", "input": ["code_drafts"]},
    "evolution_coordinator":  {"output": "evolution",           "agent": "coder"},
    "fma_review":             {"output": "fma_reviews",         "agent": "coder"},
    "generate_asset":         {"output": "design",              "agent": "coder"},
    "git_manager":            {"output": None,                  "agent": "coder"},
    "github_interact":        {"output": None,                  "agent": "coder"},
    "github_sync":            {"output": None,                  "agent": "coder"},
    "hitl_respond":           {"output": None,                  "agent": "coder"},
    "html_accessibility":     {"output": "quality",             "agent": "coder"},
    "home_assistant":         {"output": "home_assistant",      "agent": "coder"},
    "js_lint":                {"output": "quality",             "agent": "coder"},
    "mqtt_inspect":           {"output": "mqtt",                "agent": "coder"},
    "product_release":        {"output": "releases",            "agent": "coder"},
    "refactor_verify":        {"output": None,                  "agent": "coder"},
    "screenshot":             {"output": "screenshots",         "agent": "coder"},
    "service_manager":        {"output": None,                  "agent": "coder"},
    "sql_review":             {"output": "quality",             "agent": "coder"},
    "skill_chain":            {"output": "chains",              "agent": "coder"},
    "skill_draft":            {"output": "code_drafts",         "agent": "coder"},
    "skill_evolve":           {"output": "evolution",           "agent": "coder"},
    "skill_learn":            {"output": "reports",             "agent": "coder"},
    "skill_promote":          {"output": None,                  "agent": "coder", "input": ["code_drafts"]},
    "skill_test":             {"output": "code_drafts",         "agent": "coder", "input": ["code_drafts"]},
    "skill_train":            {"output": "skill_training",      "agent": "coder"},
    "speech_to_text":         {"output": None,                  "agent": "coder"},
    "unifi_manage":           {"output": "network",             "agent": "coder"},
    # --- ds_fleet ---
    "billing_ocr":            {"output": None,                  "agent": "ds_fleet"},
    "claude_efficiency":      {"output": "reports",             "agent": "ds_fleet"},
    "cost_analyze":           {"output": None,                  "agent": "ds_fleet"},
    "hardware_profiler":      {"output": None,                  "agent": "ds_fleet"},
    "memory_optimizer":       {"output": None,                  "agent": "ds_fleet"},
    "model_manager":          {"output": None,                  "agent": "ds_fleet"},
    "model_recommend":        {"output": None,                  "agent": "ds_fleet"},
    "oom_prevent":            {"output": None,                  "agent": "ds_fleet"},
    "packet_optimizer":       {"output": "reports",             "agent": "ds_fleet"},
    "router_analyze":         {"output": None,                  "agent": "ds_fleet"},
    "router_retrain":         {"output": "data",                "agent": "ds_fleet"},
    "scaler_train":           {"output": "data",                "agent": "ds_fleet"},
    "token_optimizer":        {"output": None,                  "agent": "ds_fleet"},
    # --- ds_rag ---
    "embedding_train":        {"output": "models/embeddings",   "agent": "ds_rag"},
    "rag_benchmark":          {"output": "models",              "agent": "ds_rag", "input": ["rag.db"]},
    "rag_compress":           {"output": "reports",             "agent": "ds_rag", "input": ["knowledge/*"]},
    "rag_eval":               {"output": "models",              "agent": "ds_rag"},
    "rag_index":              {"output": None,                  "agent": "ds_rag", "input": ["rag.db"]},
    "reranker_train":         {"output": "models/reranker",     "agent": "ds_rag"},
    # --- ds_research ---
    "auto_profile":           {"output": None,                  "agent": "ds_research"},
    "autoresearch_analyze":   {"output": None,                  "agent": "ds_research", "input": ["autoresearch"]},
    "autoresearch_trial":     {"output": "autoresearch",        "agent": "ds_research"},
    "checkpoint_eval":        {"output": None,                  "agent": "ds_research", "input": ["autoresearch"]},
    "dataset_synthesize":     {"output": "datasets",            "agent": "ds_research"},
    "ml_bridge":              {"output": "ml_results",          "agent": "ds_research", "input": ["autoresearch"]},
    "review_discards":        {"output": "reports",             "agent": "ds_research", "input": ["autoresearch"]},
    # --- legal ---
    "legal_draft":            {"output": "legal",               "agent": "legal"},
    # --- planner ---
    "config_validate":        {"output": "quality",             "agent": "planner"},
    "plan_workload":          {"output": "reports",             "agent": "planner"},
    "swarm_consensus":        {"output": "consensus",           "agent": "planner"},
    "swarm_intelligence":     {"output": "swarm",               "agent": "planner"},
    # --- researcher ---
    "arxiv_fetch":            {"output": "summaries",           "agent": "researcher"},
    "browser_crawl":          {"output": "browser",             "agent": "researcher"},
    "curriculum_update":      {"output": "reports",             "agent": "researcher"},
    "diffusion":              {"output": "diffusion",           "agent": "researcher"},
    "discuss":                {"output": "discussion",          "agent": "researcher"},
    "generate_image":         {"output": "diffusion",           "agent": "researcher"},
    "generate_video":         {"output": "marketing/videos",    "agent": "researcher"},
    "ingest":                 {"output": "ingests",             "agent": "researcher"},
    "rag_query":              {"output": None,                  "agent": "researcher", "input": ["rag.db"]},
    "research_loop":          {"output": None,                  "agent": "researcher", "input": ["knowledge/*"]},
    "screenshot_diff":        {"output": "screenshots",         "agent": "researcher"},
    "summarize":              {"output": "summaries",           "agent": "researcher"},
    "synthesize":             {"output": "reports",             "agent": "researcher"},
    "vision_analyze":         {"output": "screenshots",         "agent": "researcher"},
    "web_crawl":              {"output": "summaries",           "agent": "researcher"},
    "web_search":             {"output": "summaries",           "agent": "researcher"},
    # --- sales ---
    "account_review":         {"output": "reports",             "agent": "account_manager"},
    "lead_research":          {"output": "leads",               "agent": "sales"},
    "marketing":              {"output": "reports",             "agent": "sales"},
    # --- security ---
    "db_encrypt":             {"output": None,                  "agent": "security"},
    "key_manager":            {"output": "reports",             "agent": "security"},
    "oss_review":             {"output": "oss_reviews",         "agent": "security"},
    "oss_review_swarm":       {"output": "oss_reviews",         "agent": "security"},
    "pen_test":               {"output": "security",            "agent": "security"},
    "secret_rotate":          {"output": None,                  "agent": "security"},
    "security_apply":         {"output": "security/applied",    "agent": "security"},
    "security_audit":         {"output": "security",            "agent": "security"},
    "security_review":        {"output": "security/reviews",    "agent": "security"},
    "mcp_probe":              {"output": "mcp",                 "agent": "security", "input": [".mcp.json"]},
}


def _graph_knowledge(db) -> tuple:
    """Knowledge graph: agents → skills → folders, with live file counts."""
    import os

    nodes = []
    edges = []
    seen_folders = set()
    seen_agents = set()
    knowledge_dir = FLEET_DIR / "knowledge"

    # Scan actual knowledge subdirectories for file counts
    folder_stats = {}
    if knowledge_dir.exists():
        for entry in knowledge_dir.iterdir():
            if entry.is_dir():
                try:
                    count = sum(1 for _ in entry.rglob("*") if _.is_file())
                    size_bytes = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                except Exception:
                    count, size_bytes = 0, 0
                folder_stats[entry.name] = {"files": count, "size_mb": round(size_bytes / 1048576, 1)}
            elif entry.is_file():
                try:
                    folder_stats[entry.name] = {"files": 1, "size_mb": round(entry.stat().st_size / 1048576, 1)}
                except Exception:
                    folder_stats[entry.name] = {"files": 1, "size_mb": 0}

    # Build graph from skill I/O map
    for skill_name, io in _SKILL_IO_MAP.items():
        skill_id = f"knowledge:skill:{skill_name}"
        agent_role = io.get("agent", "unknown")
        output_folder = io.get("output")
        input_sources = io.get("input", [])

        # Skill node
        nodes.append({
            "id": skill_id,
            "type": "skill",
            "source": "knowledge",
            "label": skill_name,
            "status": "ACTIVE",
            "metrics": {"agent": agent_role},
        })

        # Agent node (deduplicated)
        agent_id = f"knowledge:agent:{agent_role}"
        if agent_role not in seen_agents:
            seen_agents.add(agent_role)
            nodes.append({
                "id": agent_id,
                "type": "agent",
                "source": "knowledge",
                "label": agent_role,
                "status": "ACTIVE",
            })
        edges.append({
            "source": agent_id,
            "target": skill_id,
            "type": "runs",
            "weight": 1,
        })

        # Output folder node + edge
        if output_folder:
            folder_key = output_folder.split("/")[0]  # top-level folder
            folder_id = f"knowledge:folder:{output_folder}"
            if output_folder not in seen_folders:
                seen_folders.add(output_folder)
                stats = folder_stats.get(folder_key, {"files": 0, "size_mb": 0})
                nodes.append({
                    "id": folder_id,
                    "type": "folder",
                    "source": "knowledge",
                    "label": f"knowledge/{output_folder}",
                    "status": "ACTIVE" if stats["files"] > 0 else "IDLE",
                    "metrics": {"file_count": stats["files"], "size_mb": stats["size_mb"]},
                })
            edges.append({
                "source": skill_id,
                "target": folder_id,
                "type": "writes",
                "weight": 2,
            })

        # Input sources
        for inp in input_sources:
            inp_id = f"knowledge:folder:{inp}"
            if inp not in seen_folders:
                seen_folders.add(inp)
                stats = folder_stats.get(inp.split("/")[0], {"files": 0, "size_mb": 0})
                nodes.append({
                    "id": inp_id,
                    "type": "folder",
                    "source": "knowledge",
                    "label": f"knowledge/{inp}" if not inp.endswith(".db") else inp,
                    "status": "ACTIVE" if stats["files"] > 0 else "IDLE",
                    "metrics": {"file_count": stats["files"], "size_mb": stats["size_mb"]},
                })
            edges.append({
                "source": inp_id,
                "target": skill_id,
                "type": "reads",
                "weight": 1,
            })

    return nodes, edges


def _graph_universe(db) -> tuple:
    """Full-program graph: agents, skills, tasks, folders, models, messages, config."""
    nodes = []
    edges = []

    # Track IDs to avoid duplicates
    seen = set()

    def _add_node(node_id, **kwargs):
        if node_id not in seen:
            seen.add(node_id)
            nodes.append({"id": node_id, **kwargs})

    def _add_edge(src, tgt, etype, weight=1):
        edges.append({"source": src, "target": tgt, "type": etype, "weight": weight})

    knowledge_dir = FLEET_DIR / "knowledge"
    agents = []

    try:
        # ── 1. AGENTS (live from DB) ────────────────────────────────────
        with db.get_conn() as conn:
            agents = conn.execute("""
                SELECT name, role, status, current_task_id
                FROM agents
                WHERE last_heartbeat IS NOT NULL
                ORDER BY name
            """).fetchall()

        # ── 1b. ACTIVITY SCORES (tasks per agent, last hour) ───────
        activity_counts = {}
        try:
            with db.get_conn() as conn:
                act_rows = conn.execute("""
                    SELECT assigned_to, COUNT(*) as cnt
                    FROM tasks
                    WHERE created_at >= datetime('now', '-1 hour')
                      AND assigned_to IS NOT NULL
                    GROUP BY assigned_to
                """).fetchall()
            for row in act_rows:
                activity_counts[row["assigned_to"]] = row["cnt"]
        except Exception:
            pass
        max_activity = max(activity_counts.values()) if activity_counts else 1

        for a in agents:
            aid = f"agent:{a['name']}"
            score = activity_counts.get(a["name"], 0) / max_activity if max_activity else 0
            _add_node(aid, type="agent", source="universe",
                      label=a["name"], status=a["status"] or "IDLE",
                      activity_score=round(score, 3),
                      metrics={"role": a["role"] or ""})

        # ── 2. SKILLS (all registered) ──────────────────────────────────
        skills_dir = FLEET_DIR / "skills"
        if skills_dir.exists():
            import importlib
            import sys
            sys.path.insert(0, str(FLEET_DIR))
            for f in sorted(skills_dir.glob("*.py")):
                if f.name.startswith("_"):
                    continue
                mod_name = f.stem
                sid = f"skill:{mod_name}"
                try:
                    mod = importlib.import_module(f"skills.{mod_name}")
                    skill_name = getattr(mod, "SKILL_NAME", mod_name)
                    net = getattr(mod, "REQUIRES_NETWORK", False)
                    _add_node(sid, type="skill", source="universe",
                              label=skill_name, status="ACTIVE",
                              metrics={"network": net})
                except Exception:
                    _add_node(sid, type="skill", source="universe",
                              label=mod_name, status="ERROR")

        # ── 3. RECENT TASKS (last 200) ─────────────────────────────────
        with db.get_conn() as conn:
            tasks = conn.execute("""
                SELECT id, type, status, assigned_to, created_at
                FROM tasks
                WHERE created_at >= datetime('now', '-24 hours')
                ORDER BY id DESC LIMIT 200
            """).fetchall()

        for t in tasks:
            tid = f"task:{t['id']}"
            _add_node(tid, type="task", source="universe",
                      label=f"#{t['id']} {t['type'] or '?'}",
                      status=t["status"] or "PENDING")
            # Task → agent (assigned)
            if t["assigned_to"]:
                agent_id = f"agent:{t['assigned_to']}"
                _add_edge(agent_id, tid, "assigned", 2)
            # Task → skill
            if t["type"]:
                skill_id = f"skill:{t['type']}"
                _add_edge(tid, skill_id, "runs", 1)

        # ── 4. KNOWLEDGE FOLDERS (with file counts) ────────────────────
        if knowledge_dir.exists():
            for entry in sorted(knowledge_dir.iterdir()):
                if entry.is_dir():
                    try:
                        count = sum(1 for _ in entry.rglob("*") if _.is_file())
                    except Exception:
                        count = 0
                    fid = f"folder:{entry.name}"
                    _add_node(fid, type="folder", source="universe",
                              label=f"knowledge/{entry.name}",
                              status="ACTIVE" if count > 0 else "IDLE",
                              metrics={"file_count": count})

        # ── 5. MODELS (from Ollama / hw_state.json) ────────────────────
        try:
            import json as _json
            hw_path = FLEET_DIR / "hw_state.json"
            if hw_path.exists():
                hw = _json.loads(hw_path.read_text(encoding="utf-8"))
                loaded = hw.get("loaded_models", [])
                if isinstance(loaded, list):
                    for m in loaded:
                        name = m if isinstance(m, str) else m.get("name", "unknown")
                        mid = f"model:{name}"
                        _add_node(mid, type="model", source="universe",
                                  label=name, status="ACTIVE")
                # Also get configured models from fleet.toml
                from config import load_config
                cfg = load_config()
                for tier_name, model_name in cfg.get("models", {}).get("tiers", {}).items():
                    mid = f"model:{model_name}"
                    _add_node(mid, type="model", source="universe",
                              label=f"{model_name} ({tier_name})", status="IDLE")
        except Exception:
            log.warning("universe: model data failed", exc_info=True)

        # ── 6. RECENT MESSAGES (agent-to-agent, last 100) ──────────────
        with db.get_conn() as conn:
            msgs = conn.execute("""
                SELECT from_agent, channel, COUNT(*) as n
                FROM messages
                WHERE created_at >= datetime('now', '-24 hours')
                GROUP BY from_agent, channel
                ORDER BY n DESC LIMIT 50
            """).fetchall()

        for m in msgs:
            if m["from_agent"]:
                agent_id = f"agent:{m['from_agent']}"
                chan_id = f"channel:{m['channel'] or 'fleet'}"
                _add_node(chan_id, type="message", source="universe",
                          label=f"#{m['channel'] or 'fleet'}", status="ACTIVE")
                _add_edge(agent_id, chan_id, "communicates", m["n"])

        # ── 7. USAGE/COST (skill → model, last 24h) ────────────────────
        with db.get_conn() as conn:
            usage = conn.execute("""
                SELECT skill, model, SUM(cost_usd) as cost, COUNT(*) as calls
                FROM usage
                WHERE created_at >= datetime('now', '-24 hours')
                GROUP BY skill, model
                ORDER BY cost DESC LIMIT 100
            """).fetchall()

        for u in usage:
            if u["skill"] and u["model"]:
                skill_id = f"skill:{u['skill']}"
                model_id = f"model:{u['model']}"
                _add_node(model_id, type="model", source="universe",
                          label=u["model"], status="ACTIVE")
                _add_edge(skill_id, model_id, "uses_model",
                          max(1, int((u["cost"] or 0) * 1000)))

        # ── 8. CROSS-SOURCE EDGES (the glue) ───────────────────────────
        # Import the skill I/O map from the knowledge graph
        for skill_name, io in _SKILL_IO_MAP.items():
            skill_id = f"skill:{skill_name}"
            # Skill → output folder
            output = io.get("output")
            if output:
                folder_key = output.split("/")[0]
                folder_id = f"folder:{folder_key}"
                try:
                    existing_dirs = [e.name for e in knowledge_dir.iterdir()] if knowledge_dir.exists() else []
                except Exception:
                    existing_dirs = []
                if folder_key in existing_dirs:
                    _add_edge(skill_id, folder_id, "writes", 2)
            # Input folder → skill
            for inp in io.get("input", []):
                inp_key = inp.split("/")[0]
                inp_id = f"folder:{inp_key}"
                _add_edge(inp_id, skill_id, "reads", 1)
            # Agent role → skill
            agent_role = io.get("agent")
            if agent_role:
                # Find actual agents with this role
                for a in agents:
                    if a["role"] == agent_role:
                        _add_edge(f"agent:{a['name']}", skill_id, "runs", 1)

        # ── 9. CONFIG SECTIONS (from fleet.toml) ───────────────────────
        try:
            from config import load_config
            cfg = load_config()
            for section in list(cfg.keys())[:20]:  # Top 20 config sections
                if isinstance(cfg[section], dict):
                    cid = f"config:{section}"
                    _add_node(cid, type="config", source="universe",
                              label=f"[{section}]", status="IDLE",
                              metrics={"keys": len(cfg[section])})
        except Exception:
            pass

        # ── 9b. WIRE DISCONNECTED NODES ──────────────────────────────
        # Config sections → supervisor hub
        sup_id = "agent:supervisor" if "agent:supervisor" in seen else None
        if sup_id:
            for nid in list(seen):
                if nid.startswith("config:"):
                    _add_edge(sup_id, nid, "configures", 1)

        # Disconnected agents → their configured model
        cfg_model = "model:" + (cfg.get("models", {}).get("local", "qwen3:8b") if 'cfg' in dir() else "qwen3:8b")
        for a in agents:
            aid = f"agent:{a['name']}"
            # Check if agent has any edges
            has_edge = any(e["source"] == aid or e["target"] == aid for e in edges)
            if not has_edge and cfg_model in seen:
                _add_edge(aid, cfg_model, "uses_model", 1)

        # Disconnected skills → nearest agent by role heuristic
        _ROLE_SKILL_HINTS = {
            "coder": ["code_review", "code_quality", "skill_draft", "code_refactor", "code_index"],
            "researcher": ["web_search", "summarize", "research_loop", "arxiv_search"],
            "archivist": ["flashcard", "rag_index", "evaluate", "discuss"],
            "planner": ["plan_workload", "curriculum_update", "stability_report"],
            "analyst": ["data_analysis", "autoresearch_trial"],
            "security": ["security_audit", "security_review"],
        }
        _SKILL_TO_ROLE = {}
        for role, skills in _ROLE_SKILL_HINTS.items():
            for sk in skills:
                _SKILL_TO_ROLE[sk] = role
        for nid in list(seen):
            if not nid.startswith("skill:"):
                continue
            has_edge = any(e["source"] == nid or e["target"] == nid for e in edges)
            if has_edge:
                continue
            skill_name = nid.split(":", 1)[1]
            role_hint = _SKILL_TO_ROLE.get(skill_name)
            if role_hint:
                for a in agents:
                    if a["role"] == role_hint or a["name"].startswith(role_hint):
                        _add_edge(f"agent:{a['name']}", nid, "can_run", 1)
                        break
            else:
                # Fallback: connect to supervisor as "registered" relationship
                if sup_id:
                    _add_edge(sup_id, nid, "registers", 1)

        # Disconnected folders → supervisor
        for nid in list(seen):
            if not nid.startswith("folder:"):
                continue
            has_edge = any(e["source"] == nid or e["target"] == nid for e in edges)
            if not has_edge and sup_id:
                _add_edge(sup_id, nid, "manages", 1)

        # Disconnected models → supervisor
        for nid in list(seen):
            if not nid.startswith("model:"):
                continue
            has_edge = any(e["source"] == nid or e["target"] == nid for e in edges)
            if not has_edge and sup_id:
                _add_edge(sup_id, nid, "configures", 1)

        # -- 9c. API CALL NODES (from gate ring buffer) ---------------------------
        try:
            import api_gate
            for call in api_gate.get_ring(50):
                call_id = f"api_call:{call['provider']}:{int(call['timestamp'] * 1000)}"
                _add_node(call_id, type="api_call", source="universe",
                          label=f"{call['provider']} ({call['skill']})",
                          status="ACTIVE",
                          metrics={"tokens": call['input_tokens'] + call['output_tokens'],
                                   "cost": call['cost_usd'],
                                   "purpose": call['purpose']})
                skill_id = f"skill:{call['skill']}"
                _add_edge(skill_id, call_id, "api_call", 1)
                model_id = f"model:{call['provider']}"
                _add_node(model_id, type="model", source="universe",
                          label=call['provider'], status="ACTIVE")
                _add_edge(call_id, model_id, "routes_to", 1)
        except Exception:
            log.warning("universe: api_call ring failed", exc_info=True)

    except Exception:
        log.warning("universe graph failed", exc_info=True)

    return nodes, edges
