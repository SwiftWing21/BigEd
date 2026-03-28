"""
BigEd CC — Module Management REST API.
Wraps BigEd/launcher/modules/hub.py for dashboard access.

Includes dependency resolution, snapshot management, and registry endpoints.
"""
import json
import logging
import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

log = logging.getLogger("modules_api")
modules_bp = Blueprint("modules", __name__)

_HUB_DIR = Path(__file__).parent.parent / "BigEd" / "launcher" / "modules"
_FLEET_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_all_manifests(cfg: dict) -> list:
    """Load all manifests from disk (launcher modules dir) + marketplace from DB."""
    from dep_resolver import parse_manifest_dict

    manifest_dir_str = cfg.get("modules", {}).get("manifest_dir", "fleet/modules")
    manifest_dir = Path(manifest_dir_str)
    if not manifest_dir.is_absolute():
        manifest_dir = _FLEET_DIR.parent / manifest_dir

    manifests = []
    if manifest_dir.exists():
        for manifest_path in manifest_dir.glob("*/manifest.json"):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                manifests.append(parse_manifest_dict(data))
            except Exception:
                log.warning("Failed to load manifest %s", manifest_path, exc_info=True)

    # Also load marketplace modules from DB (new schema: individual columns)
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT name, version, module_type, description,
                          requires, conflicts, recommends,
                          rollback_safe, min_fleet_version
                   FROM module_registry WHERE source = 'marketplace'"""
            ).fetchall()
        for row in rows:
            try:
                data = {
                    "name": row["name"],
                    "version": row["version"],
                    "type": row["module_type"],
                    "description": row["description"] or "",
                    "dependencies": {
                        "requires": json.loads(row["requires"] or "[]"),
                        "conflicts": json.loads(row["conflicts"] or "[]"),
                        "recommends": json.loads(row["recommends"] or "[]"),
                    },
                    "rollback_safe": bool(row["rollback_safe"]),
                    "min_fleet_version": row["min_fleet_version"] or "0.0.0",
                }
                manifests.append(parse_manifest_dict(data))
            except Exception:
                log.warning("Failed to parse marketplace manifest from DB", exc_info=True)
    except Exception:
        log.warning("_load_all_manifests: marketplace query failed", exc_info=True)

    return manifests


def _get_module_state(cfg: dict) -> set:
    """Get current enabled module names from fleet.toml tabs."""
    try:
        tabs = cfg.get("launcher", {}).get("tabs", {})
        return {name for name, enabled in tabs.items() if enabled}
    except Exception:
        log.warning("_get_module_state: failed", exc_info=True)
        return set()


def _apply_tab_changes(enable_list: list, disable_list: list) -> None:
    """Update fleet.toml [launcher.tabs] via string replacement.

    Replaces 'name = false' with 'name = true' and vice versa.
    This is a known trade-off — a TOML-aware writer would be safer,
    but this works for the current single-value-per-line format.
    """
    toml_path = _FLEET_DIR / "fleet.toml"
    try:
        with open(toml_path, "r", encoding="utf-8") as f:
            content = f.read()

        for name in enable_list:
            content = content.replace(f"{name} = false", f"{name} = true")
        for name in disable_list:
            content = content.replace(f"{name} = true", f"{name} = false")

        with open(toml_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        log.warning("_apply_tab_changes: failed to update fleet.toml", exc_info=True)
        raise


def _rebuild_registry(cfg: dict) -> int:
    """Delete and rebuild module_registry table from all manifests.

    Returns count of entries written. Uses db._retry_write().
    """
    import db

    manifests = _load_all_manifests(cfg)
    records = []
    for m in manifests:
        records.append({
            "name": m.name,
            "version": m.version,
            "module_type": m.type,
            "description": m.description,
            "requires": json.dumps(m.dependencies.requires),
            "conflicts": json.dumps(m.dependencies.conflicts),
            "recommends": json.dumps(m.dependencies.recommends),
            "rollback_safe": int(m.rollback_safe),
            "min_fleet_version": m.min_fleet_version,
            "source": "disk",
        })

    def _do():
        with db.get_conn() as conn:
            conn.execute("DELETE FROM module_registry")
            for rec in records:
                conn.execute(
                    """INSERT OR REPLACE INTO module_registry
                       (name, version, module_type, description, requires,
                        conflicts, recommends, rollback_safe, min_fleet_version, source)
                       VALUES (:name, :version, :module_type, :description, :requires,
                               :conflicts, :recommends, :rollback_safe, :min_fleet_version, :source)""",
                    rec,
                )

    db._retry_write(_do)
    log.info("_rebuild_registry: wrote %d entries", len(records))
    return len(records)


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
        # Enrich each module with its runtime enabled state from fleet.toml
        # [launcher.tabs].  manifest.json only stores default_enabled (the
        # install-time default); the operator's actual on/off choice lives in
        # fleet.toml, so we overlay it here so the UI can show the correct dot
        # colour and button label.
        try:
            from config import load_config
            cfg = load_config()
            tabs = cfg.get("launcher", {}).get("tabs", {})
            for m in installed:
                name = m.get("name", "")
                if name in tabs:
                    # Explicit runtime state takes priority over default_enabled
                    m["enabled"] = bool(tabs[name])
                else:
                    # Not yet in fleet.toml — fall back to default_enabled
                    m["enabled"] = m.get("default_enabled", False)
        except Exception:
            log.warning("api_modules_installed: failed to overlay tab state", exc_info=True)
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


# ---------------------------------------------------------------------------
# Dependency resolution endpoints
# ---------------------------------------------------------------------------


@modules_bp.route("/api/modules/preview-change", methods=["POST"])
def api_modules_preview_change():
    """Preview a proposed module change — returns changeset without executing."""
    body = request.get_json(silent=True) or {}
    action = body.get("action", "").strip()
    target = body.get("target", "").strip()
    profile = body.get("profile") or None

    if not action or not target:
        return jsonify({"error": "Missing 'action' or 'target'"}), 400
    if action not in ("enable", "disable", "install", "uninstall"):
        return jsonify({"error": f"Invalid action: {action!r}. Must be enable/disable/install/uninstall"}), 400

    try:
        from config import load_config
        from dep_resolver import build_dependency_graph, resolve

        cfg = load_config()
        manifests = _load_all_manifests(cfg)
        graph = build_dependency_graph(manifests)
        current_state = _get_module_state(cfg)
        cs = resolve(graph, action, target, profile, current_state)

        import datetime
        snapshot_label = f"pre-{action}-{target}-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        requires_approval = bool(cs.conflicts) or len(cs.disable) > 0 or len(cs.uninstall) > 0

        return jsonify({
            "action": action,
            "target": target,
            "profile": profile,
            "changeset": {
                "enable": cs.enable,
                "disable": cs.disable,
                "install": cs.install,
                "uninstall": cs.uninstall,
                "upgrade": cs.upgrade,
                "conflicts": [
                    {"module_a": c.module_a, "module_b": c.module_b, "reason": c.reason}
                    for c in cs.conflicts
                ],
                "warnings": cs.warnings,
            },
            "snapshot_label": snapshot_label,
            "requires_approval": requires_approval,
        })
    except Exception as e:
        log.warning("preview-change failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/apply-change", methods=["POST"])
def api_modules_apply_change():
    """Execute an approved changeset. Re-validates before applying."""
    body = request.get_json(silent=True) or {}
    changeset = body.get("changeset")
    approved = body.get("approved", False)
    snapshot_label = body.get("snapshot_label", "pre-apply")
    profile = body.get("profile") or None

    if not changeset:
        return jsonify({"error": "Missing 'changeset'"}), 400
    if not approved:
        return jsonify({"error": "Change not approved. Set 'approved': true to execute."}), 400

    try:
        from config import load_config
        from dep_resolver import build_dependency_graph, resolve
        import module_snapshotter

        cfg = load_config()
        manifests = _load_all_manifests(cfg)
        graph = build_dependency_graph(manifests)
        current_state = _get_module_state(cfg)

        # Re-validate: determine what the target action is from the changeset
        enable_list = changeset.get("enable", [])
        disable_list = changeset.get("disable", [])
        install_list = changeset.get("install", [])
        uninstall_list = changeset.get("uninstall", [])

        # Re-validate each target against current state
        for target in enable_list:
            fresh_cs = resolve(graph, "enable", target, profile, current_state)
            if any(c.module_a == target or c.module_b == target for c in fresh_cs.conflicts):
                return jsonify({
                    "error": "Stale changeset — conflicts detected on re-validation",
                    "target": target,
                    "conflicts": [
                        {"module_a": c.module_a, "module_b": c.module_b, "reason": c.reason}
                        for c in fresh_cs.conflicts
                    ],
                }), 409

        # Take pre-change snapshot
        snap_id = module_snapshotter.take_snapshot(snapshot_label, created_by="apply-change")

        # Apply tab changes
        _apply_tab_changes(enable_list + install_list, disable_list + uninstall_list)

        # Rebuild registry
        cfg_fresh = load_config()
        count = _rebuild_registry(cfg_fresh)

        return jsonify({
            "ok": True,
            "snapshot_id": snap_id,
            "applied": {
                "enabled": enable_list,
                "disabled": disable_list,
                "installed": install_list,
                "uninstalled": uninstall_list,
            },
            "registry_entries": count,
        })
    except Exception as e:
        log.warning("apply-change failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@modules_bp.route("/api/modules/snapshots")
def api_modules_snapshots():
    """List available rollback points."""
    try:
        import module_snapshotter
        limit = int(request.args.get("limit", 20))
        snaps = module_snapshotter.list_snapshots(limit=limit)
        return jsonify({"snapshots": snaps, "count": len(snaps)})
    except Exception as e:
        log.warning("snapshots list failed: %s", e, exc_info=True)
        return jsonify({"snapshots": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/snapshots/<int:snapshot_id>")
def api_modules_snapshot_detail(snapshot_id):
    """Get snapshot details including full state."""
    try:
        import module_snapshotter
        snap = module_snapshotter.get_snapshot(snapshot_id)
        if snap is None:
            return jsonify({"error": f"Snapshot {snapshot_id} not found"}), 404
        return jsonify(snap)
    except Exception as e:
        log.warning("snapshot detail failed for id=%s: %s", snapshot_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/snapshots/<int:snapshot_id>/diff")
def api_modules_snapshot_diff(snapshot_id):
    """Diff a snapshot against the current state."""
    try:
        import module_snapshotter
        diff = module_snapshotter.diff_snapshot(snapshot_id)
        if "error" in diff and not diff.get("added") and not diff.get("removed"):
            return jsonify(diff), 404
        return jsonify(diff)
    except Exception as e:
        log.warning("snapshot diff failed for id=%s: %s", snapshot_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Rollback endpoints
# ---------------------------------------------------------------------------


@modules_bp.route("/api/modules/rollback/<int:snapshot_id>", methods=["POST"])
def api_modules_rollback_preview(snapshot_id):
    """Preview restore changeset for approval. Does NOT execute."""
    try:
        import module_snapshotter
        changeset = module_snapshotter.restore_snapshot(snapshot_id)
        if not changeset.get("executable"):
            return jsonify(changeset), 404
        return jsonify(changeset)
    except Exception as e:
        log.warning("rollback preview failed for id=%s: %s", snapshot_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/rollback/<int:snapshot_id>/apply", methods=["POST"])
def api_modules_rollback_apply(snapshot_id):
    """Execute an approved rollback. Takes a pre-rollback snapshot first."""
    body = request.get_json(silent=True) or {}
    approved = body.get("approved", False)

    if not approved:
        return jsonify({"error": "Rollback not approved. Set 'approved': true to execute."}), 400

    try:
        import module_snapshotter
        from config import load_config

        # Generate the restore changeset
        changeset = module_snapshotter.restore_snapshot(snapshot_id)
        if not changeset.get("executable"):
            return jsonify({"error": f"Snapshot {snapshot_id} not found or not restorable"}), 404

        # Take a pre-rollback snapshot for safety
        import datetime
        pre_label = f"pre-rollback-to-{snapshot_id}-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        pre_snap_id = module_snapshotter.take_snapshot(pre_label, created_by="rollback")

        # Apply the tab changes
        enable_list = changeset.get("enable", [])
        disable_list = changeset.get("disable", [])
        _apply_tab_changes(enable_list, disable_list)

        # Rebuild registry
        cfg = load_config()
        count = _rebuild_registry(cfg)

        return jsonify({
            "ok": True,
            "restored_from_snapshot": snapshot_id,
            "pre_rollback_snapshot_id": pre_snap_id,
            "applied": {
                "enabled": enable_list,
                "disabled": disable_list,
            },
            "warnings": changeset.get("warnings", []),
            "registry_entries": count,
        })
    except Exception as e:
        log.warning("rollback apply failed for id=%s: %s", snapshot_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Registry endpoints
# ---------------------------------------------------------------------------


@modules_bp.route("/api/modules/registry")
def api_modules_registry():
    """Return cached dependency graph from DB."""
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT name, version, module_type, description,
                          requires, conflicts, recommends,
                          rollback_safe, min_fleet_version, source
                   FROM module_registry
                   ORDER BY name"""
            ).fetchall()
        entries = []
        for row in rows:
            entry = dict(row)
            for field in ("requires", "conflicts", "recommends"):
                try:
                    entry[field] = json.loads(entry[field]) if entry[field] else []
                except Exception:
                    entry[field] = []
            entry["rollback_safe"] = bool(entry.get("rollback_safe", 1))
            entries.append(entry)
        return jsonify({"registry": entries, "count": len(entries)})
    except Exception as e:
        log.warning("registry get failed: %s", e, exc_info=True)
        return jsonify({"registry": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/registry/rebuild", methods=["POST"])
def api_modules_registry_rebuild():
    """Force rebuild the registry cache from manifests."""
    try:
        from config import load_config
        cfg = load_config()
        count = _rebuild_registry(cfg)
        return jsonify({"ok": True, "entries_written": count})
    except Exception as e:
        log.warning("registry rebuild failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Manifest endpoints
# ---------------------------------------------------------------------------


@modules_bp.route("/api/modules/manifests/<name>")
def api_modules_manifest(name):
    """Get a specific module's manifest by name."""
    try:
        from config import load_config
        cfg = load_config()
        manifest_dir_str = cfg.get("modules", {}).get("manifest_dir", "fleet/modules")
        manifest_dir = Path(manifest_dir_str)
        if not manifest_dir.is_absolute():
            manifest_dir = _FLEET_DIR.parent / manifest_dir

        manifest_path = manifest_dir / name / "manifest.json"
        if not manifest_path.exists():
            # Fall back to DB registry
            import db
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT manifest_json FROM module_registry WHERE name = ?", (name,)
                ).fetchone()
            if row and row["manifest_json"]:
                return jsonify(json.loads(row["manifest_json"]))
            return jsonify({"error": f"Manifest for '{name}' not found"}), 404

        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        log.warning("manifest get failed for %s: %s", name, e, exc_info=True)
        return jsonify({"error": str(e)}), 500
