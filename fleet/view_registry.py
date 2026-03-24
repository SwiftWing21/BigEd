"""Hybrid ViewPort — Data Source Registry.

Central registry where fleet modules declare graph-renderable data sources.
Pure Python, no Flask dependency. The views blueprint imports from here.
"""

import importlib
import logging
import threading
from copy import deepcopy

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category defaults — icon, color, layout per category
# ---------------------------------------------------------------------------

CATEGORY_DEFAULTS: dict[str, dict] = {
    "fleet":    {"icon": "cpu",      "color": "#4caf50", "layout_hint": "radial"},
    "training": {"icon": "flask",    "color": "#ff9800", "layout_hint": "swimlane"},
    "storage":  {"icon": "database", "color": "#4fc3f7", "layout_hint": "cluster"},
    "external": {"icon": "globe",    "color": "#9c7cfc", "layout_hint": "tree"},
    "security": {"icon": "shield",   "color": "#f44336", "layout_hint": "cluster"},
    "knowledge": {"icon": "book",   "color": "#66bb6a", "layout_hint": "tree"},
}

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_sources: dict[str, dict] = {}
_discovery_result: dict | None = None

# Modules to probe during discover_and_register()
_DISCOVERABLE_MODULES: list[str] = [
    "supervisor",
    "hw_supervisor",
    "rag",
    "reinforcement",
    "federation_router",
    "ml_router",
    "self_healing",
    "billing",
    "marketplace",
    "geo_fleet",
    "compliance",
    "control_plane",
    "tenant_admin",
    "sso",
    "backup_manager",
    "filesystem_guard",
    "marathon",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_source(
    name: str,
    category: str,
    node_types: list[str],
    edge_types: list[str],
    data_endpoint: str,
    **kwargs,
) -> None:
    """Register a graph data source.

    Required: name, category, node_types, edge_types, data_endpoint.
    Optional kwargs: icon, color, layout_hint, animation_rules, metrics.
    Category defaults fill any unspecified optional fields.
    """
    defaults = CATEGORY_DEFAULTS.get(category, {})

    source = {
        "name": name,
        "category": category,
        "node_types": list(node_types),
        "edge_types": list(edge_types),
        "data_endpoint": data_endpoint,
        "icon": kwargs.get("icon", defaults.get("icon", "circle")),
        "color": kwargs.get("color", defaults.get("color", "#888888")),
        "layout_hint": kwargs.get("layout_hint", defaults.get("layout_hint", "cluster")),
    }

    if "animation_rules" in kwargs:
        source["animation_rules"] = kwargs["animation_rules"]
    if "metrics" in kwargs:
        source["metrics"] = list(kwargs["metrics"])

    # Forward-compatible: pass through unknown kwargs
    for key, val in kwargs.items():
        if key not in source and key not in ("animation_rules", "metrics"):
            source[key] = val

    with _lock:
        if name in _sources:
            log.warning("view_registry: overwriting existing source %r", name)
        _sources[name] = source

    log.info("view_registry: registered source %r (category=%s)", name, category)


def get_sources() -> list[dict]:
    """Return all registered sources (deep copies)."""
    with _lock:
        return [deepcopy(s) for s in _sources.values()]


def get_source(name: str) -> dict | None:
    """Return a single source by name, or None."""
    with _lock:
        src = _sources.get(name)
        return deepcopy(src) if src is not None else None


def get_health() -> dict:
    """Return discovery health summary."""
    with _lock:
        if _discovery_result is None:
            return {"registered": len(_sources), "attempted": 0, "failed": []}
        return {
            "registered": len(_discovery_result.get("registered", [])),
            "attempted": len(_DISCOVERABLE_MODULES),
            "failed": list(_discovery_result.get("failed", [])),
        }


def clear() -> None:
    """Remove all registered sources. For testing only."""
    global _discovery_result
    with _lock:
        _sources.clear()
        _discovery_result = None


def discover_and_register() -> dict:
    """Auto-discover modules and call their _register_views().

    Returns summary: {"registered": [...], "failed": [...], "skipped": [...]}.
    """
    global _discovery_result
    registered: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    for mod_name in _DISCOVERABLE_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            log.warning("view_registry: could not import %r — skipping", mod_name, exc_info=True)
            skipped.append(mod_name)
            continue

        register_fn = getattr(mod, "_register_views", None)
        if register_fn is None:
            log.debug("view_registry: %r has no _register_views — skipping", mod_name)
            skipped.append(mod_name)
            continue

        try:
            register_fn()
            registered.append(mod_name)
        except Exception:
            log.warning("view_registry: _register_views() failed for %r", mod_name, exc_info=True)
            failed.append(mod_name)

    result = {"registered": registered, "failed": failed, "skipped": skipped}
    with _lock:
        _discovery_result = result

    log.info(
        "view_registry: discovery complete — %d/%d registered, %d failed, %d skipped",
        len(registered), len(_DISCOVERABLE_MODULES), len(failed), len(skipped),
    )
    return result
