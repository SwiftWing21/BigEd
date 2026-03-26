"""
Ingest Blueprint — REST API endpoints for the Ingestion Hub.

12 endpoints for source management, HF schema/rows, caching, staging,
dispatch, and file upload.

v0.900.00b
"""

from flask import Blueprint, jsonify, request
import logging

log = logging.getLogger(__name__)

ingest_bp = Blueprint("ingest", __name__)


# ═══════════════════════════════════════════════════════════════════════════
# Source management
# ═══════════════════════════════════════════════════════════════════════════


@ingest_bp.route("/api/ingest/sources")
def api_ingest_sources():
    """List all ingest sources (config + DB)."""
    try:
        import ingest_manager
        sources = ingest_manager.list_sources()
        return jsonify({"sources": sources, "count": len(sources)})
    except Exception as e:
        log.warning("GET /api/ingest/sources failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/ingest/sources", methods=["POST"])
def api_ingest_add_source():
    """Add a new ingest source."""
    try:
        import ingest_manager
        body = request.get_json(force=True)
        sid = body.get("id")
        if not sid:
            return jsonify({"error": "Missing required field: id"}), 400
        ingest_manager.add_source(
            source_id=sid,
            dataset=body.get("dataset", ""),
            skill=body.get("skill", ""),
            agent_role=body.get("agent_role", ""),
            content_column=body.get("content_column", ""),
            batch_size=body.get("batch_size", 50),
            source_type=body.get("type", "huggingface"),
        )
        return jsonify({"ok": True, "id": sid})
    except Exception as e:
        log.warning("POST /api/ingest/sources failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/ingest/sources/<id>", methods=["DELETE"])
def api_ingest_remove_source(id):
    """Remove an ingest source by ID."""
    try:
        import ingest_manager
        ingest_manager.remove_source(id)
        return jsonify({"ok": True, "id": id})
    except Exception as e:
        log.warning("DELETE /api/ingest/sources/%s failed", id, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# HuggingFace schema & rows
# ═══════════════════════════════════════════════════════════════════════════


@ingest_bp.route("/api/ingest/sources/<id>/schema")
def api_ingest_schema(id):
    """Fetch HuggingFace dataset schema for a source."""
    try:
        import ingest_manager
        sources = ingest_manager.list_sources()
        source = next((s for s in sources if s["id"] == id), None)
        if not source:
            return jsonify({"error": f"Source '{id}' not found"}), 404
        dataset_id = source.get("dataset", "")
        if not dataset_id:
            return jsonify({"error": f"Source '{id}' has no dataset configured"}), 400
        result = ingest_manager.fetch_hf_schema(dataset_id)
        if result.get("error"):
            return jsonify(result), 502
        return jsonify(result)
    except Exception as e:
        log.warning("GET /api/ingest/sources/%s/schema failed", id, exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/ingest/sources/<id>/rows")
def api_ingest_rows(id):
    """Fetch rows from HuggingFace and auto-cache the batch."""
    try:
        import ingest_manager
        sources = ingest_manager.list_sources()
        source = next((s for s in sources if s["id"] == id), None)
        if not source:
            return jsonify({"error": f"Source '{id}' not found"}), 404
        dataset_id = source.get("dataset", "")
        if not dataset_id:
            return jsonify({"error": f"Source '{id}' has no dataset configured"}), 400

        offset = request.args.get("offset", 0, type=int)
        limit = request.args.get("limit", source.get("batch_size", 50), type=int)
        split = request.args.get("split", "train")

        result = ingest_manager.fetch_hf_rows(dataset_id, offset=offset, length=limit, split=split)

        # Auto-cache the fetched batch
        if result.get("rows"):
            ingest_manager.cache_batch(id, result["rows"])

        return jsonify(result)
    except Exception as e:
        log.warning("GET /api/ingest/sources/%s/rows failed", id, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Staging
# ═══════════════════════════════════════════════════════════════════════════


@ingest_bp.route("/api/ingest/stage", methods=["POST"])
def api_ingest_stage():
    """Stage items for review before dispatch."""
    try:
        import ingest_manager
        body = request.get_json(force=True)
        items = body.get("items", [])
        ids = ingest_manager.stage_items(items)
        return jsonify({"ok": True, "ids": ids})
    except Exception as e:
        log.warning("POST /api/ingest/stage failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/ingest/staging")
def api_ingest_staging():
    """List all staged items."""
    try:
        import ingest_manager
        items = ingest_manager.get_staging()
        return jsonify(items)
    except Exception as e:
        log.warning("GET /api/ingest/staging failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/ingest/staging/<int:id>", methods=["DELETE"])
def api_ingest_remove_staged(id):
    """Remove a single staged item."""
    try:
        import ingest_manager
        ingest_manager.remove_staged(id)
        return jsonify({"ok": True, "id": id})
    except Exception as e:
        log.warning("DELETE /api/ingest/staging/%s failed", id, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════════════════


@ingest_bp.route("/api/ingest/dispatch", methods=["POST"])
def api_ingest_dispatch():
    """Dispatch staged items to tasks or RAG."""
    try:
        import ingest_manager
        body = request.get_json(force=True, silent=True) or {}
        item_ids = body.get("item_ids")
        result = ingest_manager.dispatch_staged(item_ids=item_ids)
        return jsonify(result)
    except Exception as e:
        log.warning("POST /api/ingest/dispatch failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Cache
# ═══════════════════════════════════════════════════════════════════════════


@ingest_bp.route("/api/ingest/cache/stats")
def api_ingest_cache_stats():
    """Return cache usage statistics."""
    try:
        import ingest_manager
        return jsonify(ingest_manager.cache_stats())
    except Exception as e:
        log.warning("GET /api/ingest/cache/stats failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/ingest/cache/evict", methods=["POST"])
def api_ingest_cache_evict():
    """Evict processed cache batches and return stats."""
    try:
        import ingest_manager
        evicted = ingest_manager.evict_processed()
        stats = ingest_manager.cache_stats()
        stats["evicted"] = evicted
        return jsonify(stats)
    except Exception as e:
        log.warning("POST /api/ingest/cache/evict failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# File upload
# ═══════════════════════════════════════════════════════════════════════════


@ingest_bp.route("/api/ingest/upload", methods=["POST"])
def api_ingest_upload():
    """Accept a multipart file upload, save to cache _uploads/ dir."""
    try:
        import ingest_manager

        if "file" not in request.files:
            return jsonify({"error": "No file provided (expected 'file' field)"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        upload_dir = ingest_manager._get_cache_dir() / "_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        dest = upload_dir / f.filename
        f.save(str(dest))

        # Read text for preview and token estimate
        try:
            text = dest.read_text(encoding="utf-8")
        except Exception:
            text = ""

        token_estimate = len(text) // 4
        preview = text[:500] if text else ""
        size_kb = round(dest.stat().st_size / 1024, 2)

        return jsonify({
            "filename": f.filename,
            "path": str(dest),
            "token_estimate": token_estimate,
            "preview": preview,
            "size_kb": size_kb,
        })
    except Exception as e:
        log.warning("POST /api/ingest/upload failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── API Key Management ────────────────────────────────────────────────────────

@ingest_bp.route("/api/keys/status")
def api_keys_status():
    """Return status of all registered API keys (set/missing, masked values)."""
    try:
        import os
        import tomllib
        from pathlib import Path

        # Load registry
        registry_path = Path(__file__).resolve().parent / "keys_registry.toml"
        if not registry_path.exists():
            return jsonify({"keys": [], "error": "keys_registry.toml not found"})

        with open(registry_path, "rb") as f:
            data = tomllib.load(f)

        # Read ~/.secrets for current values
        secrets = {}
        secrets_path = Path.home() / ".secrets"
        if secrets_path.exists():
            try:
                for line in secrets_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:]
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    secrets[k] = v
            except Exception:
                pass

        keys = []
        for entry in data.get("key", []):
            env_var = entry.get("env_var", "")
            # Check env first, then ~/.secrets
            value = os.environ.get(env_var, "") or secrets.get(env_var, "")
            is_set = bool(value) and value != "REPLACE_ME"

            masked = ""
            if is_set and len(value) > 12:
                masked = value[:6] + "..." + value[-4:]
            elif is_set:
                masked = "***set***"

            keys.append({
                "name": entry.get("name", env_var),
                "label": entry.get("label", ""),
                "env_var": env_var,
                "purpose": entry.get("purpose", ""),
                "skills": entry.get("skills", []),
                "tier": entry.get("tier", "unknown"),
                "required": entry.get("required", False),
                "signup_hint": entry.get("signup_hint", ""),
                "is_set": is_set,
                "masked": masked,
            })

        return jsonify({"keys": keys, "count": len(keys)})
    except Exception as e:
        log.warning("GET /api/keys/status failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ingest_bp.route("/api/keys/set", methods=["POST"])
def api_keys_set():
    """Set an API key value. Writes to ~/.secrets and loads into os.environ."""
    try:
        import os
        from pathlib import Path

        data = request.get_json(force=True)
        key_name = data.get("key_name", "").strip()
        value = data.get("value", "").strip()

        if not key_name or not value:
            return jsonify({"error": "key_name and value required"}), 400

        # Validate key_name is in registry (prevent arbitrary env writes)
        import tomllib
        registry_path = Path(__file__).resolve().parent / "keys_registry.toml"
        with open(registry_path, "rb") as f:
            registry = tomllib.load(f)
        valid_vars = {e.get("env_var") for e in registry.get("key", [])}
        if key_name not in valid_vars:
            return jsonify({"error": f"'{key_name}' not in keys registry"}), 400

        # Write to ~/.secrets
        secrets_path = Path.home() / ".secrets"
        if not secrets_path.exists():
            secrets_path.write_text("", encoding="utf-8")

        lines = secrets_path.read_text(encoding="utf-8").splitlines()
        updated = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("export "):
                stripped = stripped[7:].strip()
            if stripped.startswith(f"{key_name}="):
                new_lines.append(f"export {key_name}={value}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"export {key_name}={value}")
        secrets_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        # Also set in current process env
        os.environ[key_name] = value

        return jsonify({"status": "ok", "key": key_name, "note": "Saved to ~/.secrets and loaded into current process"})
    except Exception as e:
        log.warning("POST /api/keys/set failed", exc_info=True)
        return jsonify({"error": str(e)}), 500
