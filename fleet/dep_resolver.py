"""Pure-logic dependency resolver for the BigEd module system.

No database access, no side effects. Only stdlib imports.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("dep_resolver")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CycleError(Exception):
    """Raised when a dependency cycle is detected in the graph."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Dependencies:
    """Holds the three dependency lists for a module."""

    requires: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    recommends: List[str] = field(default_factory=list)

    def copy(self) -> "Dependencies":
        return Dependencies(
            requires=list(self.requires),
            conflicts=list(self.conflicts),
            recommends=list(self.recommends),
        )


@dataclass
class ModuleManifest:
    """Parsed representation of a module's manifest.json."""

    name: str
    version: str
    type: str
    description: str
    author: str = ""
    dependencies: Dependencies = field(default_factory=Dependencies)
    profiles: Dict[str, dict] = field(default_factory=dict)
    schema_version: str = "1"
    rollback_safe: bool = True
    min_fleet_version: str = ""


@dataclass
class Conflict:
    """A detected mutual-exclusion conflict between two modules."""

    module_a: str
    module_b: str
    reason: str


@dataclass
class ChangeSet:
    """The complete set of actions needed to fulfil a resolve() request."""

    enable: List[str] = field(default_factory=list)
    disable: List[str] = field(default_factory=list)
    install: List[str] = field(default_factory=list)
    uninstall: List[str] = field(default_factory=list)
    upgrade: List[str] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class DependencyGraph:
    """Directed acyclic graph of module dependencies."""

    nodes: Dict[str, ModuleManifest] = field(default_factory=dict)
    # edges[A] = [B, C]  means A requires B and C
    edges: Dict[str, List[str]] = field(default_factory=dict)
    # reverse[B] = [A]   means A requires B
    reverse: Dict[str, List[str]] = field(default_factory=dict)

    def add_node(self, manifest: ModuleManifest) -> None:
        """Register a module in the graph."""
        self.nodes[manifest.name] = manifest
        self.edges.setdefault(manifest.name, [])
        self.reverse.setdefault(manifest.name, [])

    def add_edge(self, from_node: str, to_node: str) -> None:
        """Add a directed edge: from_node requires to_node."""
        self.edges.setdefault(from_node, [])
        self.reverse.setdefault(to_node, [])
        if to_node not in self.edges[from_node]:
            self.edges[from_node].append(to_node)
        if from_node not in self.reverse[to_node]:
            self.reverse[to_node].append(from_node)


# ---------------------------------------------------------------------------
# Task 4: Version constraint parsing
# ---------------------------------------------------------------------------

_CONSTRAINT_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_]*)(?:@(>=|==|\^)(.+))?$"
)


def parse_constraint(spec: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Parse a dependency spec like 'accounts@>=1.0' into (name, op, version).

    Returns (name, None, None) when no version constraint is present.
    Raises ValueError for malformed specs.
    """
    m = _CONSTRAINT_RE.match(spec.strip())
    if not m:
        raise ValueError(f"Invalid dependency spec: {spec!r}")
    name, op, ver = m.group(1), m.group(2), m.group(3)
    return (name, op, ver)


def _parse_semver(v: str) -> Tuple[int, ...]:
    """Parse a version string into a tuple of ints, padded to 3 components."""
    parts = v.split(".")
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except Exception:
            result.append(0)
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def check_version_constraint(installed: str, op: Optional[str], required: Optional[str]) -> bool:
    """Return True if *installed* satisfies the constraint (op, required).

    Supports: None/None (any), '>=' (greater-or-equal), '==' (exact),
    '^' (compatible: same major + >=).
    """
    if op is None or required is None:
        return True
    iv = _parse_semver(installed)
    rv = _parse_semver(required)
    if op == ">=":
        return iv >= rv
    if op == "==":
        return iv == rv
    if op == "^":
        # Same major, and installed >= required
        return iv[0] == rv[0] and iv >= rv
    log.warning("Unknown version operator %r — treating as satisfied", op)
    return True


# ---------------------------------------------------------------------------
# Task 5: Manifest parser + graph builder
# ---------------------------------------------------------------------------

def parse_manifest(manifest_path: str) -> ModuleManifest:
    """Read and parse a manifest.json file from disk."""
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return parse_manifest_dict(data)


def parse_manifest_dict(data: dict) -> ModuleManifest:
    """Build a ModuleManifest from an already-loaded dict."""
    raw_deps = data.get("dependencies", {})
    deps = Dependencies(
        requires=list(raw_deps.get("requires", [])),
        conflicts=list(raw_deps.get("conflicts", [])),
        recommends=list(raw_deps.get("recommends", [])),
    )
    profiles: Dict[str, dict] = data.get("profiles", {})
    return ModuleManifest(
        name=data["name"],
        version=data.get("version", "0.0.0"),
        type=data.get("type", "launcher"),
        description=data.get("description", ""),
        author=data.get("author", ""),
        dependencies=deps,
        profiles=profiles,
        schema_version=str(data.get("schema_version", "1")),
        rollback_safe=bool(data.get("rollback_safe", True)),
        min_fleet_version=data.get("min_fleet_version", ""),
    )


def _merge_profile_deps(manifest: ModuleManifest, profile: Optional[str]) -> Dependencies:
    """Return a merged Dependencies combining global + profile-specific entries."""
    merged = manifest.dependencies.copy()
    if profile and profile in manifest.profiles:
        pdata = manifest.profiles[profile]
        for req in pdata.get("requires", []):
            if req not in merged.requires:
                merged.requires.append(req)
        for con in pdata.get("conflicts", []):
            if con not in merged.conflicts:
                merged.conflicts.append(con)
        for rec in pdata.get("recommends", []):
            if rec not in merged.recommends:
                merged.recommends.append(rec)
    return merged


def build_dependency_graph(manifests: List[ModuleManifest]) -> DependencyGraph:
    """Build a DependencyGraph from a list of ModuleManifest objects.

    Edges are based on global requires only (profile edges are resolved
    at resolve-time via _merge_profile_deps).
    """
    graph = DependencyGraph()
    for m in manifests:
        graph.add_node(m)
    for m in manifests:
        for spec in m.dependencies.requires:
            dep_name, _op, _ver = parse_constraint(spec)
            # Ensure the target node exists even if not in the manifest list
            if dep_name not in graph.nodes:
                graph.edges.setdefault(dep_name, [])
                graph.reverse.setdefault(dep_name, [])
            graph.add_edge(m.name, dep_name)
    return graph


# ---------------------------------------------------------------------------
# Task 6: Topological sort + cycle detection
# ---------------------------------------------------------------------------

def topological_sort(graph: DependencyGraph) -> List[str]:
    """Return nodes in topological order (leaves/dependencies first).

    Raises CycleError if a cycle is detected.
    """
    visited: set = set()
    in_stack: set = set()
    result: List[str] = []

    def _dfs(node: str, path: List[str]) -> None:
        if node in in_stack:
            cycle_start = path.index(node)
            cycle_path = " → ".join(path[cycle_start:] + [node])
            raise CycleError(f"Dependency cycle detected: {cycle_path}")
        if node in visited:
            return
        in_stack.add(node)
        path.append(node)
        for dep in graph.edges.get(node, []):
            _dfs(dep, path)
        path.pop()
        in_stack.discard(node)
        visited.add(node)
        result.append(node)

    for node in list(graph.nodes.keys()):
        if node not in visited:
            _dfs(node, [])

    return result


# ---------------------------------------------------------------------------
# Task 7: resolve() + detect_conflicts()
# ---------------------------------------------------------------------------

def detect_conflicts(graph: DependencyGraph, proposed_enabled: List[str]) -> List[Conflict]:
    """Return deduplicated list of mutual-exclusion conflicts among proposed_enabled."""
    enabled_set = set(proposed_enabled)
    seen: set = set()
    conflicts: List[Conflict] = []

    for name in proposed_enabled:
        manifest = graph.nodes.get(name)
        if manifest is None:
            continue
        for conflicting in manifest.dependencies.conflicts:
            if conflicting in enabled_set:
                key = tuple(sorted([name, conflicting]))
                if key not in seen:
                    seen.add(key)
                    conflicts.append(Conflict(
                        module_a=name,
                        module_b=conflicting,
                        reason=f"{name} declares conflict with {conflicting}",
                    ))
    return conflicts


def _resolve_enable(
    graph: DependencyGraph,
    target: str,
    profile: Optional[str],
    current_state: set,
    visited: set,
    cs: ChangeSet,
) -> None:
    """Recursively collect modules to enable for *target* and its deps."""
    if target in visited:
        return
    visited.add(target)

    manifest = graph.nodes.get(target)
    if manifest is None:
        log.warning("resolve: unknown module %r", target)
        return

    effective_deps = _merge_profile_deps(manifest, profile)

    # Check for conflicts with already-enabled modules
    for conflicting in effective_deps.conflicts:
        if conflicting in current_state:
            cs.conflicts.append(Conflict(
                module_a=target,
                module_b=conflicting,
                reason=f"{target} conflicts with already-enabled {conflicting}",
            ))

    # Also check if target itself is flagged as a conflict by already-enabled modules
    for enabled_name in current_state:
        enabled_manifest = graph.nodes.get(enabled_name)
        if enabled_manifest and target in enabled_manifest.dependencies.conflicts:
            key = tuple(sorted([target, enabled_name]))
            already = any(
                tuple(sorted([c.module_a, c.module_b])) == key for c in cs.conflicts
            )
            if not already:
                cs.conflicts.append(Conflict(
                    module_a=enabled_name,
                    module_b=target,
                    reason=f"{enabled_name} conflicts with {target}",
                ))

    # Recurse into required deps
    for spec in effective_deps.requires:
        dep_name, op, ver = parse_constraint(spec)
        if dep_name not in current_state:
            _resolve_enable(graph, dep_name, profile, current_state, visited, cs)

    # Warn about useful but missing recommends
    for spec in effective_deps.recommends:
        rec_name, _op, _ver = parse_constraint(spec)
        if rec_name not in current_state and rec_name not in visited:
            cs.warnings.append(
                f"Recommended module {rec_name!r} is not enabled — "
                f"{target} may have reduced functionality"
            )

    if target not in current_state and target not in cs.enable:
        cs.enable.append(target)


def _resolve_disable(
    graph: DependencyGraph,
    target: str,
    current_state: set,
    visited: set,
    cs: ChangeSet,
) -> None:
    """Recursively collect enabled dependents that would break if *target* is disabled."""
    if target in visited:
        return
    visited.add(target)

    if target in current_state and target not in cs.disable:
        cs.disable.append(target)

    for dependent in graph.reverse.get(target, []):
        if dependent in current_state:
            _resolve_disable(graph, dependent, current_state, visited, cs)


def resolve(
    graph: DependencyGraph,
    action: str,
    target: str,
    active_profile: Optional[str],
    current_state: set,
) -> ChangeSet:
    """Compute the full ChangeSet needed to perform *action* on *target*.

    action: 'enable' | 'install' | 'disable' | 'uninstall'
    target: module name
    active_profile: optional profile name (e.g. 'consulting')
    current_state: set of currently-enabled module names
    """
    cs = ChangeSet()

    if action in ("enable", "install"):
        _resolve_enable(graph, target, active_profile, current_state, set(), cs)
        if action == "install":
            cs.install = list(cs.enable)
            cs.enable = []
    elif action in ("disable", "uninstall"):
        _resolve_disable(graph, target, current_state, set(), cs)
        if action == "uninstall":
            cs.uninstall = list(cs.disable)
            cs.disable = []
    else:
        log.warning("resolve: unknown action %r", action)

    return cs
