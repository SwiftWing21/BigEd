"""Module Snapshotter — point-in-time persistence for module system state.

Captures enabled/disabled modules, active profile, dependency graph hash,
schema versions, and the relevant fleet.toml sections. Supports diff,
restore-changeset generation, and retention-based pruning.

All DB writes use db._retry_write() for WAL busy handling.
"""
import hashlib
import json
import logging
from pathlib import Path

FLEET_DIR = Path(__file__).parent

log = logging.getLogger("module_snapshotter")


# ── Config ─────────────────────────────────────────────────────────────────────


def _load_toml() -> dict:
    """Load fleet.toml. Returns empty dict on any failure."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            log.warning("tomllib/tomli not available — TOML config unavailable")
            return {}
    try:
        with open(FLEET_DIR / "fleet.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        log.warning("Failed to read fleet.toml", exc_info=True)
        return {}


def _modules_config() -> dict:
    """Return [modules] section with defaults."""
    cfg = _load_toml()
    mcfg = cfg.get("modules", {})
    return {
        "snapshot_retention": mcfg.get("snapshot_retention", 20),
        "auto_snapshot": mcfg.get("auto_snapshot", True),
        "manifest_dir": mcfg.get("manifest_dir", "fleet/modules"),
    }


# ── Manifest scanning ───────────────────────────────────────────────────────────


def _scan_manifests() -> dict[str, dict]:
    """Scan fleet/modules/*/manifest.json. Returns {name: manifest_dict}."""
    mcfg = _modules_config()
    manifest_dir = Path(mcfg["manifest_dir"])
    if not manifest_dir.is_absolute():
        manifest_dir = FLEET_DIR.parent / manifest_dir
    result: dict[str, dict] = {}
    try:
        for manifest_path in manifest_dir.glob("*/manifest.json"):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                name = data.get("name") or manifest_path.parent.name
                result[name] = data
            except Exception:
                log.warning("Failed to parse manifest %s", manifest_path, exc_info=True)
    except Exception:
        log.warning("Failed to scan manifests in %s", manifest_dir, exc_info=True)
    return result


# ── Current state builder ───────────────────────────────────────────────────────


def _get_current_state() -> dict:
    """Build a complete snapshot of the current module system state."""
    cfg = _load_toml()
    tabs_cfg: dict = cfg.get("launcher", {}).get("tabs", {})
    modules_section: dict = cfg.get("modules", {})
    launcher_profile: str = cfg.get("launcher", {}).get("profile", "")

    manifests = _scan_manifests()

    enabled_modules: dict[str, str] = {}
    disabled_modules: dict[str, str] = {}
    schema_versions: dict[str, str] = {}

    for name, manifest in manifests.items():
        version = manifest.get("version", "0.0.0")
        schema_ver = manifest.get("schema_version", "")
        if schema_ver:
            schema_versions[name] = schema_ver

        # A module is enabled when its tab key is True in fleet.toml
        tab_enabled = tabs_cfg.get(name, False)
        if tab_enabled:
            enabled_modules[name] = version
        else:
            disabled_modules[name] = version

    # Detect active profile: the launcher profile from fleet.toml, or infer
    # from which modules are enabled vs. profile definitions in manifests.
    active_profile = launcher_profile or _detect_profile(enabled_modules, manifests)

    # Serialize the full state for hashing
    state_for_hash = {
        "enabled_modules": enabled_modules,
        "disabled_modules": disabled_modules,
        "active_profile": active_profile,
        "schema_versions": schema_versions,
    }
    dep_graph_hash = hashlib.sha256(
        json.dumps(state_for_hash, sort_keys=True).encode()
    ).hexdigest()

    return {
        "enabled_modules": enabled_modules,
        "disabled_modules": disabled_modules,
        "installed_packages": {},  # populated by marketplace later
        "active_profile": active_profile,
        "dep_graph_hash": dep_graph_hash,
        "schema_versions": schema_versions,
        "fleet_toml_modules_section": {
            "launcher_tabs": tabs_cfg,
            "modules": modules_section,
        },
    }


def _detect_profile(enabled: dict[str, str], manifests: dict[str, dict]) -> str:
    """Heuristic: find any profile definition that matches the enabled set."""
    enabled_names = set(enabled.keys())
    for _mod_name, manifest in manifests.items():
        for profile_name, profile_data in manifest.get("profiles", {}).items():
            profile_requires = set(profile_data.get("requires", []))
            if profile_requires and profile_requires.issubset(enabled_names):
                return profile_name
    return ""


# ── Public API ─────────────────────────────────────────────────────────────────


def take_snapshot(label: str, created_by: str = "system") -> int:
    """Capture current module state. Returns snapshot ID."""
    import db

    state = _get_current_state()
    state_json = json.dumps(state, sort_keys=True)
    dep_graph_hash = state["dep_graph_hash"]

    snapshot_id: list[int] = [0]

    def _do():
        with db.get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO module_snapshots (label, state_json, dep_graph_hash, created_by)
                   VALUES (?, ?, ?, ?)""",
                (label, state_json, dep_graph_hash, created_by),
            )
            snapshot_id[0] = cur.lastrowid

    try:
        db._retry_write(_do)
    except Exception:
        log.warning("take_snapshot: DB write failed", exc_info=True)
        raise

    cfg = _modules_config()
    retention = cfg.get("snapshot_retention", 20)
    try:
        prune_snapshots(keep=retention)
    except Exception:
        log.warning("take_snapshot: auto-prune failed", exc_info=True)

    log.info("Snapshot #%d taken: %s (by %s)", snapshot_id[0], label, created_by)
    return snapshot_id[0]


def list_snapshots(limit: int = 20) -> list[dict]:
    """List available snapshots, newest first."""
    import db

    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, label, dep_graph_hash, created_at, created_by
                   FROM module_snapshots
                   ORDER BY created_at DESC, id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("list_snapshots: query failed", exc_info=True)
        return []


def get_snapshot(snapshot_id: int) -> dict | None:
    """Retrieve a specific snapshot with parsed state."""
    import db

    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT id, label, state_json, dep_graph_hash, created_at, created_by
                   FROM module_snapshots
                   WHERE id = ?""",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["state"] = json.loads(result.pop("state_json"))
        except Exception:
            log.warning("get_snapshot: failed to parse state_json for id=%d", snapshot_id, exc_info=True)
            result["state"] = {}
        return result
    except Exception:
        log.warning("get_snapshot: query failed for id=%d", snapshot_id, exc_info=True)
        return None


def diff_snapshot(snapshot_id: int) -> dict:
    """Compare a snapshot against current state. Returns added/removed/changed."""
    snap = get_snapshot(snapshot_id)
    if snap is None:
        log.warning("diff_snapshot: snapshot %d not found", snapshot_id)
        return {"added": {}, "removed": {}, "changed": {}, "error": f"Snapshot {snapshot_id} not found"}

    snap_state = snap.get("state", {})
    snap_enabled: dict[str, str] = snap_state.get("enabled_modules", {})
    snap_disabled: dict[str, str] = snap_state.get("disabled_modules", {})

    current_state = _get_current_state()
    cur_enabled: dict[str, str] = current_state.get("enabled_modules", {})

    # "added" = modules now enabled that were not enabled in snapshot
    snap_enabled_names = set(snap_enabled.keys())
    cur_enabled_names = set(cur_enabled.keys())

    added: dict[str, str] = {
        name: cur_enabled[name]
        for name in cur_enabled_names - snap_enabled_names
    }

    # "removed" = modules enabled in snapshot that are no longer enabled
    removed: dict[str, str] = {
        name: snap_enabled[name]
        for name in snap_enabled_names - cur_enabled_names
    }

    # "changed" = modules enabled in both but with different versions
    changed: dict[str, dict[str, str]] = {}
    for name in snap_enabled_names & cur_enabled_names:
        old_ver = snap_enabled[name]
        new_ver = cur_enabled[name]
        if old_ver != new_ver:
            changed[name] = {"from": old_ver, "to": new_ver}

    return {
        "snapshot_id": snapshot_id,
        "snapshot_label": snap.get("label", ""),
        "snapshot_created_at": snap.get("created_at", ""),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def restore_snapshot(snapshot_id: int) -> dict:
    """Generate a changeset dict for interactive approval. Does NOT execute.

    Returns a dict with:
    - enable: list of module names to re-enable
    - disable: list of module names to disable
    - warnings: list of warning strings (rollback_safe=false, missing migrations)
    """
    snap = get_snapshot(snapshot_id)
    if snap is None:
        log.warning("restore_snapshot: snapshot %d not found", snapshot_id)
        return {
            "snapshot_id": snapshot_id,
            "enable": [],
            "disable": [],
            "warnings": [f"Snapshot {snapshot_id} not found"],
            "executable": False,
        }

    snap_state = snap.get("state", {})
    snap_enabled: dict[str, str] = snap_state.get("enabled_modules", {})

    current_state = _get_current_state()
    cur_enabled: dict[str, str] = current_state.get("enabled_modules", {})

    snap_enabled_names = set(snap_enabled.keys())
    cur_enabled_names = set(cur_enabled.keys())

    # Modules to enable: were enabled in snapshot, not currently enabled
    to_enable = sorted(snap_enabled_names - cur_enabled_names)
    # Modules to disable: currently enabled, were not in snapshot
    to_disable = sorted(cur_enabled_names - snap_enabled_names)

    warnings: list[str] = []

    # Check rollback_safe for modules being disabled (they are being rolled back)
    manifests = _scan_manifests()
    for name in to_disable:
        manifest = manifests.get(name, {})
        if manifest.get("rollback_safe") is False:
            warnings.append(
                f"Module '{name}' has rollback_safe=false — manual review required before disabling"
            )

    # Check for missing down-migration files for version changes
    for name in to_enable:
        snap_ver = snap_enabled.get(name, "")
        cur_ver = cur_enabled.get(name, "")
        if snap_ver and cur_ver and snap_ver != cur_ver:
            # Check for <cur_ver>_down.sql migration
            migration_path = FLEET_DIR.parent / "fleet" / "modules" / name / f"{cur_ver}_down.sql"
            if not migration_path.exists():
                warnings.append(
                    f"Module '{name}': no down-migration '{cur_ver}_down.sql' found "
                    f"for rollback from {cur_ver} → {snap_ver}"
                )

    return {
        "snapshot_id": snapshot_id,
        "snapshot_label": snap.get("label", ""),
        "snapshot_created_at": snap.get("created_at", ""),
        "enable": to_enable,
        "disable": to_disable,
        "warnings": warnings,
        "executable": True,
    }


def prune_snapshots(keep: int = 20) -> int:
    """Remove oldest snapshots beyond the keep limit. Returns count removed."""
    import db

    removed_count: list[int] = [0]

    def _do():
        with db.get_conn() as conn:
            # Count total snapshots
            total = conn.execute("SELECT COUNT(*) FROM module_snapshots").fetchone()[0]
            excess = total - keep
            if excess <= 0:
                return
            # Delete the oldest (lowest id / earliest created_at)
            conn.execute(
                """DELETE FROM module_snapshots
                   WHERE id IN (
                       SELECT id FROM module_snapshots
                       ORDER BY created_at ASC, id ASC
                       LIMIT ?
                   )""",
                (excess,),
            )
            removed_count[0] = excess

    try:
        db._retry_write(_do)
    except Exception:
        log.warning("prune_snapshots: DB write failed", exc_info=True)
        raise

    if removed_count[0]:
        log.info("prune_snapshots: removed %d snapshots (keeping %d)", removed_count[0], keep)
    return removed_count[0]
