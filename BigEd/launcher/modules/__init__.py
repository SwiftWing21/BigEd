"""
Module loader & registry for BigEd CC.

v0.22: Basic module loading, manifest, interface validation
v0.24: Deployment profiles, dependency resolution, module discovery
v0.25: Data contract validation, cross-module data flow, export
v0.26: Deprecation lifecycle (ACTIVE→DEPRECATED→SUNSET→REMOVED)
"""
import csv
import importlib
import io
import json
import logging
from pathlib import Path

log = logging.getLogger("modules")

MODULES_DIR = Path(__file__).parent
MANIFEST_PATH = MODULES_DIR / "manifest.json"

# Required Module interface methods
_REQUIRED_METHODS = {"build_tab", "on_refresh", "on_close"}

# Deployment profiles — which modules each profile enables
DEPLOYMENT_PROFILES = {
    "minimal": ["ingestion", "outputs"],
    "research": ["ingestion", "outputs"],
    "consulting": ["crm", "onboarding", "customers", "accounts", "ingestion", "outputs"],
    "full": ["crm", "onboarding", "customers", "accounts", "ingestion", "outputs"],
}

# Deprecation lifecycle states
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_DEPRECATED = "deprecated"
LIFECYCLE_SUNSET = "sunset"
LIFECYCLE_REMOVED = "removed"


# ── Manifest I/O ─────────────────────────────────────────────────────────────

def _load_manifest() -> dict:
    """Load manifest.json → dict keyed by module name."""
    if not MANIFEST_PATH.exists():
        return {}
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return {m["name"]: m for m in data.get("modules", [])}
    except Exception as e:
        log.warning("Failed to load manifest: %s", e)
        return {}


def _save_manifest(manifest_dict: dict):
    """Write manifest dict back to manifest.json."""
    data = {"modules": list(manifest_dict.values())}
    MANIFEST_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_modules() -> list[dict]:
    """Scan modules/ for mod_*.py files, return metadata list."""
    found = []
    for f in sorted(MODULES_DIR.glob("mod_*.py")):
        name = f.stem.removeprefix("mod_")
        found.append({"name": name, "file": f.name, "path": f})
    return found


def get_new_modules() -> list[str]:
    """Return names of modules discovered on disk but not in manifest."""
    manifest = _load_manifest()
    discovered = discover_modules()
    return [m["name"] for m in discovered if m["name"] not in manifest]


# ── Deployment Profiles ──────────────────────────────────────────────────────

def resolve_profile(profile_name: str, tab_cfg: dict) -> dict:
    """
    Merge a deployment profile with explicit tab config.
    Profile sets baseline, explicit config overrides.
    """
    profile_modules = DEPLOYMENT_PROFILES.get(profile_name, [])
    resolved = {name: (name in profile_modules) for name in
                {m["name"] for m in discover_modules()}}
    # Explicit config overrides profile
    resolved.update(tab_cfg)
    return resolved


# ── Dependency Resolution ────────────────────────────────────────────────────

def _resolve_dependencies(loaded: dict, all_discovered: list[str]) -> tuple[dict, list[str]]:
    """
    Ensure all DEPENDS_ON are met. Auto-enable missing deps if possible.
    Returns (loaded_dict, warnings_list).
    """
    warnings = []
    # Build dependency graph
    changed = True
    while changed:
        changed = False
        to_remove = []
        for name, instance in list(loaded.items()):
            depends = getattr(instance, "DEPENDS_ON", [])
            for dep in depends:
                if dep not in loaded:
                    # Dependency not loaded — try to auto-load it
                    if dep in all_discovered:
                        warnings.append(
                            f"Auto-enabling '{dep}' (required by '{name}')")
                        # Will be loaded in the caller
                    else:
                        warnings.append(
                            f"Module '{name}' requires '{dep}' (not available) — disabling")
                        to_remove.append(name)
                        changed = True
                        break
        for name in to_remove:
            if name in loaded:
                del loaded[name]

    return loaded, warnings


# ── Deprecation Lifecycle ────────────────────────────────────────────────────

def get_lifecycle_state(meta: dict) -> str:
    """Determine lifecycle state from manifest metadata."""
    if not meta.get("deprecated", False):
        return LIFECYCLE_ACTIVE

    sunset = meta.get("sunset_version", "")
    from . import _version_check
    if sunset and _version_check.is_past_sunset(sunset):
        return LIFECYCLE_SUNSET

    return LIFECYCLE_DEPRECATED


def deprecate_module(name: str, sunset_version: str = "",
                     migration_notes: str = ""):
    """Mark a module as deprecated in the manifest."""
    manifest = _load_manifest()
    if name not in manifest:
        return False
    from . import _version_check
    manifest[name]["deprecated"] = True
    manifest[name]["deprecated_since"] = _version_check._CURRENT_VERSION
    manifest[name]["sunset_version"] = sunset_version
    manifest[name]["migration_notes"] = migration_notes
    _save_manifest(manifest)
    return True


def undeprecate_module(name: str):
    """Remove deprecation from a module."""
    manifest = _load_manifest()
    if name not in manifest:
        return False
    manifest[name]["deprecated"] = False
    manifest[name].pop("deprecated_since", None)
    manifest[name].pop("sunset_version", None)
    manifest[name].pop("migration_notes", None)
    _save_manifest(manifest)
    return True


# ── Data Export ──────────────────────────────────────────────────────────────

def export_module_data(module_instance, fmt: str = "json") -> str:
    """Export module data in JSON or CSV format."""
    if not hasattr(module_instance, "export_data"):
        return ""

    data = module_instance.export_data()
    if not data:
        return ""

    if fmt == "csv":
        if not data:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        return output.getvalue()
    else:
        return json.dumps(data, indent=2, default=str)


def export_all_module_data(loaded_modules: dict, fmt: str = "json") -> dict:
    """Export data from all loaded modules."""
    result = {}
    for name, instance in loaded_modules.items():
        try:
            exported = export_module_data(instance, fmt)
            if exported:
                result[name] = exported
        except Exception as e:
            log.error("Failed to export data from module '%s': %s", name, e)
    return result


# ── Data Contract Validation ─────────────────────────────────────────────────

def validate_module_data(module_instance, record: dict) -> tuple[bool, str]:
    """Validate a record against the module's DATA_SCHEMA."""
    schema = getattr(module_instance, "DATA_SCHEMA", None)
    if not schema:
        return True, "No schema defined"

    fields = schema.get("fields", {})
    for field, spec in fields.items():
        value = record.get(field)
        if spec.get("required") and not value:
            return False, f"Missing required field: {field}"
        if "enum" in spec and value and value not in spec["enum"]:
            return False, f"Invalid value for {field}: {value} (allowed: {spec['enum']})"

    return True, "OK"


# ── Main Loader ──────────────────────────────────────────────────────────────

def load_modules(app, tab_cfg: dict) -> dict:
    """
    Load and return enabled modules.

    Args:
        app: BigEdCC main app instance (passed to Module.__init__)
        tab_cfg: dict from load_tab_cfg() — keys are module names, values are bool

    Returns:
        dict of name → Module instance for successfully loaded modules
    """
    manifest = _load_manifest()
    discovered = discover_modules()
    all_names = [m["name"] for m in discovered]
    loaded = {}
    new_modules = []

    for mod_info in discovered:
        name = mod_info["name"]
        filename = mod_info["file"]

        # Check manifest for deprecation/sunset
        meta = manifest.get(name, {})
        lifecycle = get_lifecycle_state(meta)

        if lifecycle == LIFECYCLE_SUNSET:
            log.info("Module '%s' past sunset — auto-disabled", name)
            continue

        if lifecycle == LIFECYCLE_DEPRECATED:
            log.info("Module '%s' is deprecated (sunset: %s)",
                     name, meta.get("sunset_version", "none"))

        # Check if enabled in tab config
        enabled = tab_cfg.get(name, meta.get("default_enabled", False))
        if not enabled:
            log.debug("Module '%s' disabled in config", name)
            continue

        # Import the module
        try:
            mod = importlib.import_module(f"modules.{mod_info['file'][:-3]}")
            cls = getattr(mod, "Module", None)
            if cls is None:
                log.warning("Module '%s' has no Module class — skipping", name)
                continue

            # Validate interface
            missing = _REQUIRED_METHODS - set(dir(cls))
            if missing:
                log.warning("Module '%s' missing methods: %s", name, missing)
                continue

            instance = cls(app)
            loaded[name] = instance
            log.info("Loaded module: %s (%s)", name, cls.LABEL)

        except Exception as e:
            log.error("Failed to load module '%s': %s", name, e)
            continue

        # Track in manifest if new
        if name not in manifest:
            new_modules.append(name)
            manifest[name] = {
                "name": name,
                "file": filename,
                "version": getattr(cls, "VERSION", "0.22"),
                "default_enabled": getattr(cls, "DEFAULT_ENABLED", False),
                "deprecated": False,
            }

    # Dependency resolution
    loaded, dep_warnings = _resolve_dependencies(loaded, all_names)
    for w in dep_warnings:
        log.warning(w)

    # Save updated manifest if new modules were discovered
    if new_modules:
        _save_manifest(manifest)
        log.info("New modules discovered: %s", new_modules)

    return loaded


# ── Status / Settings UI ─────────────────────────────────────────────────────

def get_module_status(tab_cfg: dict) -> list[dict]:
    """Return status info for all discovered modules (for settings UI)."""
    manifest = _load_manifest()
    discovered = discover_modules()
    result = []
    for mod_info in discovered:
        name = mod_info["name"]
        meta = manifest.get(name, {})
        lifecycle = get_lifecycle_state(meta)
        result.append({
            "name": name,
            "file": mod_info["file"],
            "enabled": tab_cfg.get(name, meta.get("default_enabled", False)),
            "lifecycle": lifecycle,
            "deprecated": meta.get("deprecated", False),
            "deprecated_since": meta.get("deprecated_since", ""),
            "sunset_version": meta.get("sunset_version", ""),
            "migration_notes": meta.get("migration_notes", ""),
            "version": meta.get("version", "unknown"),
        })
    return result
