# Module Dependency Resolution & Versioning/Rollback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add profile-aware dependency resolution and full snapshot rollback to BigEd's unified module system (launcher + marketplace).

**Architecture:** Three-layer design — `dep_resolver.py` (pure logic DAG resolver), `module_snapshotter.py` (snapshot persistence), and extended `modules_blueprint.py` (coordinator REST API). Manifests are the source of truth; a DB registry caches the resolved graph.

**Tech Stack:** Python 3.11+, Flask Blueprint, SQLite WAL, JSON manifests, dataclasses

**Spec:** `docs/superpowers/specs/2026-03-28-module-deps-versioning-design.md`

---

### Task 1: DB Schema — module_registry + module_snapshots tables

**Files:**
- Modify: `fleet/db.py` (add tables inside `init_db()`)

- [ ] **Step 1: Write the failing test**

Create test file:

```python
# fleet/tests/test_dep_resolver.py
"""Tests for module dependency resolution and snapshotter."""
import json
import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_module_registry_table_exists():
    """module_registry table is created by init_db."""
    import db
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='module_registry'"
        ).fetchall()
    assert len(rows) == 1, "module_registry table not found"


def test_module_snapshots_table_exists():
    """module_snapshots table is created by init_db."""
    import db
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='module_snapshots'"
        ).fetchall()
    assert len(rows) == 1, "module_snapshots table not found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py::test_module_registry_table_exists tests/test_dep_resolver.py::test_module_snapshots_table_exists -v`
Expected: FAIL — tables don't exist yet

- [ ] **Step 3: Add tables to init_db()**

In `fleet/db.py`, inside `init_db()`, after the existing `CREATE INDEX` statements (around line 365), add:

```python
        # Module dependency registry (cache — rebuilt from manifest.json files)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS module_registry (
                name TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('launcher', 'marketplace')),
                manifest_json TEXT NOT NULL,
                resolved_requires TEXT DEFAULT '[]',
                resolved_conflicts TEXT DEFAULT '[]',
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Module state snapshots for rollback
        conn.execute("""
            CREATE TABLE IF NOT EXISTS module_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                state_json TEXT NOT NULL,
                dep_graph_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                created_by TEXT DEFAULT 'system'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_created ON module_snapshots(created_at DESC)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/db.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): add module_registry and module_snapshots tables"
```

---

### Task 2: Launcher Module Manifests

**Files:**
- Create: `fleet/modules/command_center/manifest.json`
- Create: `fleet/modules/agents/manifest.json`
- Create: `fleet/modules/crm/manifest.json`
- Create: `fleet/modules/ingestion/manifest.json`
- Create: `fleet/modules/outputs/manifest.json`
- Create: `fleet/modules/intelligence/manifest.json`
- Create: `fleet/modules/manual_mode/manifest.json`
- Create: `fleet/modules/onboarding/manifest.json`
- Create: `fleet/modules/customers/manifest.json`
- Create: `fleet/modules/accounts/manifest.json`
- Create: `fleet/modules/owner_core/manifest.json`

- [ ] **Step 1: Create manifest directories and files**

Create `fleet/modules/` directory, then create each manifest. Example for `command_center`:

```json
{
  "name": "command_center",
  "version": "1.0.0",
  "type": "launcher",
  "description": "Central command dashboard with fleet overview and task dispatch",
  "author": "BigEd",
  "dependencies": {
    "requires": [],
    "conflicts": [],
    "recommends": ["agents"]
  },
  "profiles": {},
  "schema_version": "1",
  "rollback_safe": true,
  "min_fleet_version": "0.300.00b"
}
```

Each module gets the same skeleton with `version: "1.0.0"`, empty `requires`/`conflicts`, and appropriate `recommends`. Fill in known relationships:

| Module | requires | recommends | conflicts |
|--------|----------|------------|-----------|
| `command_center` | [] | ["agents"] | [] |
| `agents` | [] | ["intelligence"] | [] |
| `crm` | ["accounts"] | ["intelligence", "customers"] | [] |
| `ingestion` | [] | ["outputs"] | [] |
| `outputs` | [] | [] | [] |
| `intelligence` | [] | ["outputs"] | [] |
| `manual_mode` | [] | [] | [] |
| `onboarding` | [] | ["crm"] | [] |
| `customers` | ["accounts"] | ["crm"] | [] |
| `accounts` | [] | [] | [] |
| `owner_core` | [] | [] | [] |

Profile-specific: `crm` in `consulting` profile requires `["customers"]`.

- [ ] **Step 2: Write validation test**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def test_all_launcher_manifests_exist():
    """Every launcher tab module has a valid manifest.json."""
    modules_dir = os.path.join(os.path.dirname(__file__), "..", "modules")
    expected = [
        "command_center", "agents", "crm", "ingestion", "outputs",
        "intelligence", "manual_mode", "onboarding", "customers",
        "accounts", "owner_core",
    ]
    for name in expected:
        path = os.path.join(modules_dir, name, "manifest.json")
        assert os.path.exists(path), f"Missing manifest: {path}"
        with open(path) as f:
            data = json.load(f)
        assert data["name"] == name, f"Name mismatch in {path}"
        assert data["type"] == "launcher", f"Type must be 'launcher' in {path}"
        assert "version" in data, f"Missing version in {path}"
        assert "dependencies" in data, f"Missing dependencies in {path}"
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py::test_all_launcher_manifests_exist -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/modules/
git commit -m "feat(modules): add manifest.json for all 11 launcher modules"
```

---

### Task 3: fleet.toml — Add snapshot/manifest config

**Files:**
- Modify: `fleet/fleet.toml` (add 3 lines to `[modules]` section)

- [ ] **Step 1: Add config keys**

In `fleet/fleet.toml`, after line 338 (`verify_checksums = true`), add:

```toml
snapshot_retention = 20       # Max snapshots to keep
auto_snapshot = true          # Snapshot before every mutating operation
manifest_dir = "fleet/modules"  # Where launcher module manifests live
```

- [ ] **Step 2: Verify config loads**

Run: `cd fleet && python -c "from config import load_config; cfg = load_config(); m = cfg['modules']; print(m.get('snapshot_retention'), m.get('auto_snapshot'), m.get('manifest_dir'))"`
Expected: `20 True fleet/modules`

- [ ] **Step 3: Commit**

```bash
git add fleet/fleet.toml
git commit -m "config(modules): add snapshot_retention, auto_snapshot, manifest_dir"
```

---

### Task 4: Dependency Resolver — Data Structures + Version Constraint Parser

**Files:**
- Create: `fleet/dep_resolver.py`
- Test: `fleet/tests/test_dep_resolver.py`

- [ ] **Step 1: Write failing tests for version constraint parsing**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def test_parse_version_constraint_any():
    from dep_resolver import parse_constraint
    name, op, ver = parse_constraint("outputs")
    assert name == "outputs"
    assert op is None
    assert ver is None


def test_parse_version_constraint_gte():
    from dep_resolver import parse_constraint
    name, op, ver = parse_constraint("outputs@>=1.0")
    assert name == "outputs"
    assert op == ">="
    assert ver == "1.0"


def test_parse_version_constraint_exact():
    from dep_resolver import parse_constraint
    name, op, ver = parse_constraint("analytics@==1.5.0")
    assert name == "analytics"
    assert op == "=="
    assert ver == "1.5.0"


def test_parse_version_constraint_compatible():
    from dep_resolver import parse_constraint
    name, op, ver = parse_constraint("crm@^2.0")
    assert name == "crm"
    assert op == "^"
    assert ver == "2.0"


def test_check_version_any():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("3.2.1", None, None) is True


def test_check_version_gte_pass():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("1.5.0", ">=", "1.0") is True


def test_check_version_gte_fail():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("0.9.0", ">=", "1.0") is False


def test_check_version_exact_pass():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("1.5.0", "==", "1.5.0") is True


def test_check_version_exact_fail():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("1.5.1", "==", "1.5.0") is False


def test_check_version_compatible_pass():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("2.3.1", "^", "2.0") is True


def test_check_version_compatible_fail_major():
    from dep_resolver import check_version_constraint
    assert check_version_constraint("3.0.0", "^", "2.0") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "version" -v`
Expected: FAIL — `dep_resolver` module doesn't exist

- [ ] **Step 3: Implement data structures and version parser**

Create `fleet/dep_resolver.py`:

```python
"""Dependency resolver — pure logic, no DB access, no side effects.

Parses module manifests, builds a dependency DAG, resolves changesets
for enable/disable/install/uninstall actions with profile-aware rules.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("dep_resolver")


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Dependencies:
    requires: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    recommends: list[str] = field(default_factory=list)

    def copy(self) -> "Dependencies":
        return Dependencies(
            requires=list(self.requires),
            conflicts=list(self.conflicts),
            recommends=list(self.recommends),
        )


@dataclass
class ModuleManifest:
    name: str
    version: str
    type: str  # "launcher" or "marketplace"
    description: str = ""
    author: str = ""
    dependencies: Dependencies = field(default_factory=Dependencies)
    profiles: dict[str, dict] = field(default_factory=dict)
    schema_version: str = "1"
    rollback_safe: bool = True
    min_fleet_version: str = ""


@dataclass
class Conflict:
    module_a: str
    module_b: str
    reason: str


@dataclass
class ChangeSet:
    enable: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)
    uninstall: list[str] = field(default_factory=list)
    upgrade: list[tuple[str, str, str]] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DependencyGraph:
    """Directed graph: edges point from module → its requirements."""

    def __init__(self):
        self.nodes: dict[str, ModuleManifest] = {}
        self.edges: dict[str, list[str]] = {}       # module → [requires]
        self.reverse: dict[str, list[str]] = {}      # module → [dependents]

    def add_node(self, manifest: ModuleManifest):
        self.nodes[manifest.name] = manifest
        if manifest.name not in self.edges:
            self.edges[manifest.name] = []
        if manifest.name not in self.reverse:
            self.reverse[manifest.name] = []

    def add_edge(self, from_mod: str, to_mod: str):
        """from_mod requires to_mod."""
        if to_mod not in self.edges.get(from_mod, []):
            self.edges.setdefault(from_mod, []).append(to_mod)
        if from_mod not in self.reverse.get(to_mod, []):
            self.reverse.setdefault(to_mod, []).append(from_mod)


# ── Version Constraint Parsing ───────────────────────────────────────────────

_CONSTRAINT_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)(?:@(>=|==|\^)(.+))?$")


def parse_constraint(spec: str) -> tuple[str, Optional[str], Optional[str]]:
    """Parse 'name', 'name@>=1.0', 'name@==1.5.0', or 'name@^2.0'.

    Returns (name, operator_or_None, version_or_None).
    """
    m = _CONSTRAINT_RE.match(spec.strip())
    if not m:
        raise ValueError(f"Invalid constraint: {spec!r}")
    return m.group(1), m.group(2), m.group(3)


def _parse_semver(v: str) -> tuple[int, ...]:
    """Parse version string into tuple of ints. Pads to 3 components."""
    parts = v.split(".")
    result = []
    for p in parts:
        digits = re.match(r"(\d+)", p)
        result.append(int(digits.group(1)) if digits else 0)
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def check_version_constraint(installed: str, op: Optional[str], required: Optional[str]) -> bool:
    """Check if installed version satisfies a constraint."""
    if op is None or required is None:
        return True  # any version
    iv = _parse_semver(installed)
    rv = _parse_semver(required)
    if op == ">=":
        return iv >= rv
    elif op == "==":
        return iv == rv
    elif op == "^":
        # Compatible: same major version, >= required
        return iv[0] == rv[0] and iv >= rv
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "version" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/dep_resolver.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): dep_resolver data structures + version constraint parser"
```

---

### Task 5: Dependency Resolver — Manifest Parser + Graph Builder

**Files:**
- Modify: `fleet/dep_resolver.py`
- Test: `fleet/tests/test_dep_resolver.py`

- [ ] **Step 1: Write failing tests for manifest parsing and graph building**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def _make_manifest(name, requires=None, conflicts=None, recommends=None, profiles=None):
    """Helper to create a manifest dict for testing."""
    return {
        "name": name,
        "version": "1.0.0",
        "type": "launcher",
        "description": f"Test module {name}",
        "dependencies": {
            "requires": requires or [],
            "conflicts": conflicts or [],
            "recommends": recommends or [],
        },
        "profiles": profiles or {},
    }


def test_parse_manifest_from_dict():
    from dep_resolver import parse_manifest_dict
    data = _make_manifest("crm", requires=["accounts"], recommends=["intelligence"])
    m = parse_manifest_dict(data)
    assert m.name == "crm"
    assert m.version == "1.0.0"
    assert "accounts" in m.dependencies.requires
    assert "intelligence" in m.dependencies.recommends


def test_parse_manifest_minimal():
    from dep_resolver import parse_manifest_dict
    data = {"name": "simple", "version": "1.0.0", "type": "launcher", "description": "x"}
    m = parse_manifest_dict(data)
    assert m.name == "simple"
    assert m.dependencies.requires == []


def test_build_graph_edges():
    from dep_resolver import parse_manifest_dict, build_dependency_graph
    manifests = [
        parse_manifest_dict(_make_manifest("crm", requires=["accounts"])),
        parse_manifest_dict(_make_manifest("accounts")),
    ]
    graph = build_dependency_graph(manifests)
    assert "accounts" in graph.edges["crm"]
    assert "crm" in graph.reverse["accounts"]


def test_build_graph_profile_merge():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, _merge_profile_deps
    data = _make_manifest("crm", requires=["accounts"], profiles={
        "consulting": {"requires": ["customers"]}
    })
    m = parse_manifest_dict(data)
    merged = _merge_profile_deps(m, "consulting")
    assert "accounts" in merged.requires
    assert "customers" in merged.requires


def test_build_graph_profile_no_match():
    from dep_resolver import parse_manifest_dict, _merge_profile_deps
    data = _make_manifest("crm", requires=["accounts"], profiles={
        "consulting": {"requires": ["customers"]}
    })
    m = parse_manifest_dict(data)
    merged = _merge_profile_deps(m, "minimal")
    assert "accounts" in merged.requires
    assert "customers" not in merged.requires
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "manifest or graph" -v`
Expected: FAIL — `parse_manifest_dict` not defined

- [ ] **Step 3: Implement manifest parser and graph builder**

Add to `fleet/dep_resolver.py`:

```python
# ── Manifest Parsing ─────────────────────────────────────────────────────────

def parse_manifest(manifest_path: str) -> ModuleManifest:
    """Parse and validate a manifest.json file."""
    with open(manifest_path) as f:
        data = json.load(f)
    return parse_manifest_dict(data)


def parse_manifest_dict(data: dict) -> ModuleManifest:
    """Parse a manifest from a dict (for testing / DB-cached manifests)."""
    deps_data = data.get("dependencies", {})
    deps = Dependencies(
        requires=deps_data.get("requires", []),
        conflicts=deps_data.get("conflicts", []),
        recommends=deps_data.get("recommends", []),
    )
    profiles = {}
    for pname, pdata in data.get("profiles", {}).items():
        profiles[pname] = pdata
    return ModuleManifest(
        name=data["name"],
        version=data.get("version", "0.0.0"),
        type=data.get("type", "launcher"),
        description=data.get("description", ""),
        author=data.get("author", ""),
        dependencies=deps,
        profiles=profiles,
        schema_version=data.get("schema_version", "1"),
        rollback_safe=data.get("rollback_safe", True),
        min_fleet_version=data.get("min_fleet_version", ""),
    )


def _merge_profile_deps(manifest: ModuleManifest, profile: str) -> Dependencies:
    """Merge global dependencies with profile-specific overrides."""
    base = manifest.dependencies.copy()
    if profile in manifest.profiles:
        profile_deps = manifest.profiles[profile]
        base.requires = list(set(base.requires + profile_deps.get("requires", [])))
        base.conflicts = list(set(base.conflicts + profile_deps.get("conflicts", [])))
        base.recommends = list(set(base.recommends + profile_deps.get("recommends", [])))
    return base


# ── Graph Building ───────────────────────────────────────────────────────────

def build_dependency_graph(manifests: list[ModuleManifest]) -> DependencyGraph:
    """Build a directed acyclic graph from all module manifests."""
    graph = DependencyGraph()
    for m in manifests:
        graph.add_node(m)
    # Add edges: each module → its required dependencies (name only, strip version)
    for m in manifests:
        for req in m.dependencies.requires:
            dep_name, _, _ = parse_constraint(req)
            graph.add_edge(m.name, dep_name)
    return graph
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "manifest or graph" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/dep_resolver.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): manifest parser + dependency graph builder"
```

---

### Task 6: Dependency Resolver — Topological Sort + Cycle Detection

**Files:**
- Modify: `fleet/dep_resolver.py`
- Test: `fleet/tests/test_dep_resolver.py`

- [ ] **Step 1: Write failing tests**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def test_topological_sort_linear():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, topological_sort
    manifests = [
        parse_manifest_dict(_make_manifest("c", requires=["b"])),
        parse_manifest_dict(_make_manifest("b", requires=["a"])),
        parse_manifest_dict(_make_manifest("a")),
    ]
    graph = build_dependency_graph(manifests)
    order = topological_sort(graph)
    assert order.index("a") < order.index("b") < order.index("c")


def test_topological_sort_diamond():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, topological_sort
    manifests = [
        parse_manifest_dict(_make_manifest("d", requires=["b", "c"])),
        parse_manifest_dict(_make_manifest("b", requires=["a"])),
        parse_manifest_dict(_make_manifest("c", requires=["a"])),
        parse_manifest_dict(_make_manifest("a")),
    ]
    graph = build_dependency_graph(manifests)
    order = topological_sort(graph)
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_topological_sort_cycle_detected():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, topological_sort, CycleError
    manifests = [
        parse_manifest_dict(_make_manifest("a", requires=["b"])),
        parse_manifest_dict(_make_manifest("b", requires=["a"])),
    ]
    graph = build_dependency_graph(manifests)
    try:
        topological_sort(graph)
        assert False, "Should have raised CycleError"
    except CycleError as e:
        assert "a" in str(e) or "b" in str(e)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "topological" -v`
Expected: FAIL

- [ ] **Step 3: Implement topological sort with cycle detection**

Add to `fleet/dep_resolver.py`:

```python
class CycleError(Exception):
    """Raised when a dependency cycle is detected."""
    pass


def topological_sort(graph: DependencyGraph) -> list[str]:
    """Return modules in dependency order (leaves first).

    Raises CycleError if a cycle is detected, including the cycle path.
    """
    visited: set[str] = set()
    in_stack: set[str] = set()
    order: list[str] = []

    def _visit(node: str, path: list[str]):
        if node in in_stack:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            raise CycleError(f"Dependency cycle detected: {' → '.join(cycle)}")
        if node in visited:
            return
        in_stack.add(node)
        path.append(node)
        for dep in graph.edges.get(node, []):
            if dep in graph.nodes:
                _visit(dep, path)
        path.pop()
        in_stack.remove(node)
        visited.add(node)
        order.append(node)

    for node in graph.nodes:
        if node not in visited:
            _visit(node, [])

    return order
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "topological" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/dep_resolver.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): topological sort with cycle detection"
```

---

### Task 7: Dependency Resolver — resolve() + detect_conflicts()

**Files:**
- Modify: `fleet/dep_resolver.py`
- Test: `fleet/tests/test_dep_resolver.py`

- [ ] **Step 1: Write failing tests for resolution**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def test_resolve_enable_pulls_deps():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    manifests = [
        parse_manifest_dict(_make_manifest("crm", requires=["accounts"])),
        parse_manifest_dict(_make_manifest("accounts")),
        parse_manifest_dict(_make_manifest("outputs")),
    ]
    graph = build_dependency_graph(manifests)
    state = {"accounts": {"enabled": False, "version": "1.0.0"},
             "crm": {"enabled": False, "version": "1.0.0"},
             "outputs": {"enabled": True, "version": "1.0.0"}}
    cs = resolve(graph, "enable", "crm", "default", state)
    assert "accounts" in cs.enable
    assert "crm" in cs.enable


def test_resolve_enable_already_enabled_dep():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    manifests = [
        parse_manifest_dict(_make_manifest("crm", requires=["accounts"])),
        parse_manifest_dict(_make_manifest("accounts")),
    ]
    graph = build_dependency_graph(manifests)
    state = {"accounts": {"enabled": True, "version": "1.0.0"},
             "crm": {"enabled": False, "version": "1.0.0"}}
    cs = resolve(graph, "enable", "crm", "default", state)
    assert "accounts" not in cs.enable  # already enabled
    assert "crm" in cs.enable


def test_resolve_disable_cascades():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    manifests = [
        parse_manifest_dict(_make_manifest("crm", requires=["accounts"])),
        parse_manifest_dict(_make_manifest("accounts")),
    ]
    graph = build_dependency_graph(manifests)
    state = {"accounts": {"enabled": True, "version": "1.0.0"},
             "crm": {"enabled": True, "version": "1.0.0"}}
    cs = resolve(graph, "disable", "accounts", "default", state)
    assert "accounts" in cs.disable
    assert "crm" in cs.disable  # depends on accounts


def test_resolve_conflict_detected():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    manifests = [
        parse_manifest_dict(_make_manifest("crm", conflicts=["crm_lite"])),
        parse_manifest_dict(_make_manifest("crm_lite", conflicts=["crm"])),
    ]
    graph = build_dependency_graph(manifests)
    state = {"crm": {"enabled": True, "version": "1.0.0"},
             "crm_lite": {"enabled": False, "version": "1.0.0"}}
    cs = resolve(graph, "enable", "crm_lite", "default", state)
    assert len(cs.conflicts) > 0
    assert any(c.module_a == "crm_lite" or c.module_b == "crm_lite" for c in cs.conflicts)


def test_resolve_recommends_warning():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    manifests = [
        parse_manifest_dict(_make_manifest("crm", recommends=["intelligence"])),
        parse_manifest_dict(_make_manifest("intelligence")),
    ]
    graph = build_dependency_graph(manifests)
    state = {"crm": {"enabled": False, "version": "1.0.0"},
             "intelligence": {"enabled": False, "version": "1.0.0"}}
    cs = resolve(graph, "enable", "crm", "default", state)
    assert any("intelligence" in w for w in cs.warnings)


def test_resolve_profile_aware():
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    manifests = [
        parse_manifest_dict(_make_manifest("crm", requires=["accounts"], profiles={
            "consulting": {"requires": ["customers"]}
        })),
        parse_manifest_dict(_make_manifest("accounts")),
        parse_manifest_dict(_make_manifest("customers", requires=["accounts"])),
    ]
    graph = build_dependency_graph(manifests)
    state = {"crm": {"enabled": False, "version": "1.0.0"},
             "accounts": {"enabled": False, "version": "1.0.0"},
             "customers": {"enabled": False, "version": "1.0.0"}}
    cs = resolve(graph, "enable", "crm", "consulting", state)
    assert "accounts" in cs.enable
    assert "customers" in cs.enable
    assert "crm" in cs.enable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "resolve" -v`
Expected: FAIL

- [ ] **Step 3: Implement resolve() and detect_conflicts()**

Add to `fleet/dep_resolver.py`:

```python
# ── Conflict Detection ───────────────────────────────────────────────────────

def detect_conflicts(graph: DependencyGraph, proposed_enabled: set[str]) -> list[Conflict]:
    """Check proposed state for mutual exclusion violations."""
    conflicts = []
    for name in proposed_enabled:
        manifest = graph.nodes.get(name)
        if not manifest:
            continue
        for conflict_name in manifest.dependencies.conflicts:
            if conflict_name in proposed_enabled:
                conflicts.append(Conflict(
                    module_a=name,
                    module_b=conflict_name,
                    reason=f"'{name}' conflicts with '{conflict_name}'",
                ))
    # Deduplicate (A conflicts B == B conflicts A)
    seen = set()
    unique = []
    for c in conflicts:
        key = tuple(sorted([c.module_a, c.module_b]))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


# ── Resolution ───────────────────────────────────────────────────────────────

def resolve(
    graph: DependencyGraph,
    action: str,
    target: str,
    active_profile: str,
    current_state: dict,
) -> ChangeSet:
    """Resolve dependencies for a proposed action.

    Args:
        graph: The dependency graph built from all manifests.
        action: "enable" | "disable" | "install" | "uninstall"
        target: Module name to act on.
        active_profile: Current launcher profile name.
        current_state: {module_name: {"enabled": bool, "version": str}}

    Returns:
        ChangeSet with all required changes, conflicts, and warnings.
    """
    cs = ChangeSet()

    if target not in graph.nodes:
        cs.warnings.append(f"Unknown module: '{target}'")
        return cs

    if action in ("enable", "install"):
        _resolve_enable(graph, target, active_profile, current_state, cs, set())
    elif action in ("disable", "uninstall"):
        _resolve_disable(graph, target, current_state, cs, set())

    return cs


def _resolve_enable(graph, target, profile, state, cs, visited):
    """Forward resolve: collect transitive deps that need enabling."""
    if target in visited:
        return
    visited.add(target)

    manifest = graph.nodes.get(target)
    if not manifest:
        return

    # Merge profile-specific deps
    merged_deps = _merge_profile_deps(manifest, profile)

    # Resolve required deps first (recursive)
    for req_spec in merged_deps.requires:
        dep_name, op, ver = parse_constraint(req_spec)
        dep_state = state.get(dep_name, {})
        if not dep_state.get("enabled", False):
            _resolve_enable(graph, dep_name, profile, state, cs, visited)
        # Version check
        if op and dep_state.get("version"):
            if not check_version_constraint(dep_state["version"], op, ver):
                cs.warnings.append(
                    f"'{dep_name}' version {dep_state['version']} "
                    f"does not satisfy {req_spec}"
                )

    # Add target if not already enabled
    target_state = state.get(target, {})
    if not target_state.get("enabled", False) and target not in cs.enable:
        if manifest.type == "marketplace" and target not in [s.get("name") for s in state.values() if isinstance(s, dict)]:
            cs.install.append(target)
        cs.enable.append(target)

    # Check for conflicts in proposed new state
    proposed_enabled = {
        name for name, s in state.items()
        if isinstance(s, dict) and s.get("enabled", False)
    } | set(cs.enable)
    new_conflicts = detect_conflicts(graph, proposed_enabled)
    for c in new_conflicts:
        if c not in cs.conflicts:
            cs.conflicts.append(c)

    # Soft dep warnings
    for rec in merged_deps.recommends:
        rec_name, _, _ = parse_constraint(rec)
        rec_state = state.get(rec_name, {})
        if not rec_state.get("enabled", False) and rec_name not in cs.enable:
            warning = f"Module '{rec_name}' is recommended by '{target}' but not included"
            if warning not in cs.warnings:
                cs.warnings.append(warning)


def _resolve_disable(graph, target, state, cs, visited):
    """Reverse resolve: collect dependents that would break."""
    if target in visited:
        return
    visited.add(target)

    target_state = state.get(target, {})
    if target_state.get("enabled", False) and target not in cs.disable:
        cs.disable.append(target)

    # Find all modules that depend on target and are enabled
    for dependent in graph.reverse.get(target, []):
        dep_state = state.get(dependent, {})
        if dep_state.get("enabled", False) and dependent not in cs.disable:
            _resolve_disable(graph, dependent, state, cs, visited)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "resolve" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/dep_resolver.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): resolve() and detect_conflicts() with profile-aware deps"
```

---

### Task 8: Module Snapshotter

**Files:**
- Create: `fleet/module_snapshotter.py`
- Test: `fleet/tests/test_dep_resolver.py` (add snapshotter tests)

- [ ] **Step 1: Write failing tests**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def test_take_snapshot_returns_id():
    from module_snapshotter import take_snapshot
    import db
    db.init_db()
    sid = take_snapshot("test-snapshot-1", created_by="test")
    assert isinstance(sid, int)
    assert sid > 0


def test_list_snapshots():
    from module_snapshotter import take_snapshot, list_snapshots
    import db
    db.init_db()
    take_snapshot("snap-a", created_by="test")
    take_snapshot("snap-b", created_by="test")
    snaps = list_snapshots(limit=10)
    assert len(snaps) >= 2
    # Newest first
    assert snaps[0]["label"] == "snap-b"


def test_get_snapshot():
    from module_snapshotter import take_snapshot, get_snapshot
    import db
    db.init_db()
    sid = take_snapshot("snap-get-test", created_by="test")
    snap = get_snapshot(sid)
    assert snap is not None
    assert snap["label"] == "snap-get-test"
    assert "state" in snap


def test_diff_snapshot():
    from module_snapshotter import take_snapshot, diff_snapshot
    import db
    db.init_db()
    sid = take_snapshot("snap-diff-test", created_by="test")
    diff = diff_snapshot(sid)
    assert "added" in diff
    assert "removed" in diff
    assert "changed" in diff


def test_prune_snapshots():
    from module_snapshotter import take_snapshot, prune_snapshots, list_snapshots
    import db
    db.init_db()
    for i in range(5):
        take_snapshot(f"prune-test-{i}", created_by="test")
    removed = prune_snapshots(keep=2)
    assert removed >= 3
    remaining = list_snapshots(limit=100)
    # Filter to only our test snapshots
    ours = [s for s in remaining if s["label"].startswith("prune-test-")]
    assert len(ours) <= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "snapshot" -v`
Expected: FAIL — `module_snapshotter` doesn't exist

- [ ] **Step 3: Implement module_snapshotter.py**

Create `fleet/module_snapshotter.py`:

```python
"""Module Snapshotter — point-in-time snapshots of module system state.

Captures and restores: enabled modules, package versions, dependency graph,
schema versions, and fleet.toml module config. No dependency resolution
logic — pure persistence.

All DB writes use db._retry_write() for WAL busy handling.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("module_snapshotter")

FLEET_DIR = Path(__file__).parent


def _load_toml_config():
    """Load fleet.toml as dict."""
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


def _get_current_state() -> dict:
    """Capture current module system state."""
    cfg = _load_toml_config()
    tabs = cfg.get("launcher", {}).get("tabs", {})
    modules_cfg = cfg.get("modules", {})
    profiles = cfg.get("launcher", {}).get("profiles", {})

    # Scan manifests for version info
    manifest_dir = FLEET_DIR / modules_cfg.get("manifest_dir", "modules")
    enabled = {}
    disabled = {}
    if manifest_dir.exists():
        for mdir in sorted(manifest_dir.iterdir()):
            mf = mdir / "manifest.json"
            if mf.exists():
                try:
                    with open(mf) as f:
                        data = json.load(f)
                    name = data.get("name", mdir.name)
                    version = data.get("version", "0.0.0")
                    if tabs.get(name, False):
                        enabled[name] = version
                    else:
                        disabled[name] = version
                except Exception as e:
                    log.warning("Failed to read manifest %s: %s", mf, e)

    # Detect active profile
    active_profile = "default"
    enabled_set = set(enabled.keys())
    for pname, pdata in profiles.items():
        if set(pdata.get("modules", [])).issubset(enabled_set):
            active_profile = pname

    # Schema versions from manifests
    schema_versions = {}
    for mdir in sorted(manifest_dir.iterdir()) if manifest_dir.exists() else []:
        mf = mdir / "manifest.json"
        if mf.exists():
            try:
                with open(mf) as f:
                    data = json.load(f)
                sv = data.get("schema_version")
                if sv:
                    schema_versions[data["name"]] = sv
            except Exception:
                pass

    state = {
        "enabled_modules": enabled,
        "disabled_modules": disabled,
        "installed_packages": {},  # populated from marketplace DB if available
        "active_profile": active_profile,
        "schema_versions": schema_versions,
        "fleet_toml_modules_section": {
            "tabs": tabs,
            "modules": modules_cfg,
        },
    }

    # Compute dep graph hash
    graph_str = json.dumps(state, sort_keys=True)
    state["dep_graph_hash"] = hashlib.sha256(graph_str.encode()).hexdigest()

    return state


def take_snapshot(label: str, created_by: str = "system") -> int:
    """Capture current module state. Returns snapshot ID."""
    import db
    state = _get_current_state()
    state_json = json.dumps(state, sort_keys=True)
    dep_graph_hash = state.get("dep_graph_hash", "")

    result = {"id": None}

    def _do():
        with db.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO module_snapshots (label, state_json, dep_graph_hash, created_by) "
                "VALUES (?, ?, ?, ?)",
                (label, state_json, dep_graph_hash, created_by),
            )
            result["id"] = cur.lastrowid

    db._retry_write(_do)

    # Auto-prune
    cfg = _load_toml_config()
    retention = cfg.get("modules", {}).get("snapshot_retention", 20)
    prune_snapshots(keep=retention)

    log.info("Snapshot taken: id=%s label=%s", result["id"], label)
    return result["id"]


def list_snapshots(limit: int = 20) -> list[dict]:
    """List available snapshots, newest first."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, label, dep_graph_hash, created_at, created_by "
            "FROM module_snapshots ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshot(snapshot_id: int) -> dict | None:
    """Retrieve a specific snapshot with parsed state."""
    import db
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, label, state_json, dep_graph_hash, created_at, created_by "
            "FROM module_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["state"] = json.loads(result.pop("state_json"))
    return result


def diff_snapshot(snapshot_id: int) -> dict:
    """Compare a snapshot against current state."""
    snap = get_snapshot(snapshot_id)
    if not snap:
        return {"error": "Snapshot not found"}

    current = _get_current_state()
    snap_state = snap["state"]

    snap_enabled = set(snap_state.get("enabled_modules", {}).keys())
    curr_enabled = set(current.get("enabled_modules", {}).keys())

    added = curr_enabled - snap_enabled
    removed = snap_enabled - curr_enabled

    # Version changes for modules in both
    changed = {}
    for name in snap_enabled & curr_enabled:
        snap_ver = snap_state["enabled_modules"].get(name, "")
        curr_ver = current["enabled_modules"].get(name, "")
        if snap_ver != curr_ver:
            changed[name] = {"from": snap_ver, "to": curr_ver}

    return {
        "snapshot_id": snapshot_id,
        "snapshot_label": snap["label"],
        "added": sorted(added),
        "removed": sorted(removed),
        "changed": changed,
        "profile_changed": snap_state.get("active_profile") != current.get("active_profile"),
    }


def restore_snapshot(snapshot_id: int):
    """Generate a changeset dict that would restore the snapshot state.

    Does NOT execute — returns data for interactive approval.
    """
    diff = diff_snapshot(snapshot_id)
    if "error" in diff:
        return diff

    snap = get_snapshot(snapshot_id)
    snap_state = snap["state"]

    # Check rollback safety
    warnings = []
    manifest_dir = FLEET_DIR / "modules"
    for name in diff.get("removed", []):
        mf = manifest_dir / name / "manifest.json"
        if mf.exists():
            try:
                with open(mf) as f:
                    data = json.load(f)
                if not data.get("rollback_safe", True):
                    warnings.append(f"Module '{name}' declares rollback_safe=false — requires explicit confirmation")
            except Exception:
                pass

    # Check for missing down-migrations
    for name, ver_change in diff.get("changed", {}).items():
        migration_dir = manifest_dir / name / "migrations"
        if migration_dir.exists():
            down_file = migration_dir / f"{ver_change['to']}_down.sql"
            if not down_file.exists():
                warnings.append(f"Module '{name}' cannot be rolled back — migration file missing")

    return {
        "snapshot_id": snapshot_id,
        "enable": sorted(diff.get("removed", [])),    # re-enable what was removed
        "disable": sorted(diff.get("added", [])),     # disable what was added
        "version_changes": diff.get("changed", {}),
        "restore_profile": snap_state.get("active_profile", "default"),
        "warnings": warnings,
    }


def prune_snapshots(keep: int = 20) -> int:
    """Remove oldest snapshots beyond the keep limit. Returns count removed."""
    import db
    removed = {"count": 0}

    def _do():
        with db.get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM module_snapshots").fetchone()[0]
            if total <= keep:
                return
            to_delete = total - keep
            conn.execute(
                "DELETE FROM module_snapshots WHERE id IN "
                "(SELECT id FROM module_snapshots ORDER BY created_at ASC LIMIT ?)",
                (to_delete,),
            )
            removed["count"] = to_delete

    db._retry_write(_do)
    return removed["count"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "snapshot" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/module_snapshotter.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): module_snapshotter with take/list/get/diff/restore/prune"
```

---

### Task 9: Coordinator — Extend modules_blueprint.py

**Files:**
- Modify: `fleet/modules_blueprint.py`
- Test: `fleet/tests/test_dep_resolver.py`

- [ ] **Step 1: Write failing tests for new endpoints**

Add to `fleet/tests/test_dep_resolver.py`:

```python
def test_modules_blueprint_has_preview_endpoint():
    """modules_blueprint exposes /api/modules/preview-change."""
    from modules_blueprint import modules_bp
    rules = [r.rule for r in modules_bp.deferred_functions or []]
    # Check by importing and inspecting registered routes
    route_rules = {rule.rule for rule in modules_bp.deferred_functions} if hasattr(modules_bp, 'deferred_functions') else set()
    # Simpler: just check the function exists
    assert hasattr(modules_bp, 'name')
    from modules_blueprint import api_modules_preview_change
    assert callable(api_modules_preview_change)


def test_modules_blueprint_has_snapshot_endpoints():
    from modules_blueprint import api_modules_snapshots, api_modules_snapshot_detail
    assert callable(api_modules_snapshots)
    assert callable(api_modules_snapshot_detail)


def test_modules_blueprint_has_rollback_endpoint():
    from modules_blueprint import api_modules_rollback_preview
    assert callable(api_modules_rollback_preview)


def test_modules_blueprint_has_registry_endpoint():
    from modules_blueprint import api_modules_registry
    assert callable(api_modules_registry)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "blueprint" -v`
Expected: FAIL — functions don't exist yet

- [ ] **Step 3: Add new endpoints to modules_blueprint.py**

Add the following routes to `fleet/modules_blueprint.py` after the existing endpoints:

```python
# ── Dependency Resolution + Snapshots (v0.400.00b) ─────────────────────────

@modules_bp.route("/api/modules/preview-change", methods=["POST"])
def api_modules_preview_change():
    """Preview a proposed module change — returns changeset without executing."""
    body = request.get_json(silent=True) or {}
    action = body.get("action", "").strip()
    target = body.get("target", "").strip()
    profile = body.get("profile", "default").strip()
    if not action or not target:
        return jsonify({"error": "Missing 'action' and/or 'target'"}), 400
    if action not in ("enable", "disable", "install", "uninstall"):
        return jsonify({"error": f"Invalid action: {action}"}), 400
    try:
        from dep_resolver import resolve, build_dependency_graph, parse_manifest
        from config import load_config
        import db

        cfg = load_config()
        manifests = _load_all_manifests(cfg)
        graph = build_dependency_graph(manifests)
        state = _get_module_state(cfg)
        cs = resolve(graph, action, target, profile, state)
        from datetime import datetime
        label = f"auto:{action}:{target}:{datetime.utcnow().isoformat()}"
        return jsonify({
            "changeset": {
                "enable": cs.enable,
                "disable": cs.disable,
                "install": cs.install,
                "uninstall": cs.uninstall,
                "upgrade": cs.upgrade,
                "conflicts": [{"module_a": c.module_a, "module_b": c.module_b, "reason": c.reason} for c in cs.conflicts],
                "warnings": cs.warnings,
            },
            "snapshot_label": label,
            "requires_approval": bool(cs.enable or cs.disable or cs.install or cs.uninstall or cs.conflicts),
        })
    except Exception as e:
        log.warning("preview-change failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/apply-change", methods=["POST"])
def api_modules_apply_change():
    """Execute an approved changeset (auto-snapshots first)."""
    body = request.get_json(silent=True) or {}
    changeset = body.get("changeset", {})
    approved = body.get("approved", False)
    snapshot_label = body.get("snapshot_label", "manual")
    if not approved:
        return jsonify({"error": "Changeset not approved"}), 400
    try:
        # Re-validate changeset against current state
        from dep_resolver import resolve, build_dependency_graph
        from config import load_config
        cfg = load_config()
        manifests = _load_all_manifests(cfg)
        graph = build_dependency_graph(manifests)
        state = _get_module_state(cfg)

        # Verify proposed changes are still valid
        for target in changeset.get("enable", []):
            if target in state and state[target].get("enabled"):
                continue  # already enabled, skip
            cs = resolve(graph, "enable", target, body.get("profile", "default"), state)
            if cs.conflicts:
                return jsonify({"error": "Changeset is stale — conflicts detected", "conflicts": [
                    {"module_a": c.module_a, "module_b": c.module_b, "reason": c.reason}
                    for c in cs.conflicts
                ]}), 409

        # Take snapshot before changes
        from module_snapshotter import take_snapshot
        snap_id = take_snapshot(snapshot_label, created_by="api")

        # Apply changes — update fleet.toml tabs
        _apply_tab_changes(changeset.get("enable", []), changeset.get("disable", []))

        # Rebuild registry cache
        _rebuild_registry(cfg)

        return jsonify({"ok": True, "snapshot_id": snap_id, "applied": changeset})
    except Exception as e:
        log.warning("apply-change failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/snapshots")
def api_modules_snapshots():
    """List available rollback points."""
    try:
        from module_snapshotter import list_snapshots
        limit = request.args.get("limit", 20, type=int)
        return jsonify({"snapshots": list_snapshots(limit=limit)})
    except Exception as e:
        log.warning("list snapshots failed: %s", e)
        return jsonify({"snapshots": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/snapshots/<int:sid>")
def api_modules_snapshot_detail(sid):
    """Get snapshot details."""
    try:
        from module_snapshotter import get_snapshot
        snap = get_snapshot(sid)
        if not snap:
            return jsonify({"error": "Snapshot not found"}), 404
        return jsonify(snap)
    except Exception as e:
        log.warning("get snapshot failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/snapshots/<int:sid>/diff")
def api_modules_snapshot_diff(sid):
    """Diff snapshot against current state."""
    try:
        from module_snapshotter import diff_snapshot
        return jsonify(diff_snapshot(sid))
    except Exception as e:
        log.warning("diff snapshot failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/rollback/<int:sid>", methods=["POST"])
def api_modules_rollback_preview(sid):
    """Preview restore changeset for approval."""
    try:
        from module_snapshotter import restore_snapshot
        result = restore_snapshot(sid)
        return jsonify(result)
    except Exception as e:
        log.warning("rollback preview failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/rollback/<int:sid>/apply", methods=["POST"])
def api_modules_rollback_apply(sid):
    """Execute approved rollback."""
    try:
        from module_snapshotter import restore_snapshot, take_snapshot
        result = restore_snapshot(sid)
        if "error" in result:
            return jsonify(result), 400
        # Snapshot current state before rollback
        take_snapshot(f"pre-rollback:{sid}", created_by="api")
        # Apply
        _apply_tab_changes(result.get("enable", []), result.get("disable", []))
        from config import load_config
        _rebuild_registry(load_config())
        return jsonify({"ok": True, "restored_snapshot": sid, "changes": result})
    except Exception as e:
        log.warning("rollback apply failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/registry")
def api_modules_registry():
    """Cached dependency graph."""
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT name, version, type, resolved_requires, resolved_conflicts, updated_at "
                "FROM module_registry ORDER BY name"
            ).fetchall()
        return jsonify({"registry": [dict(r) for r in rows]})
    except Exception as e:
        log.warning("registry query failed: %s", e)
        return jsonify({"registry": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/registry/rebuild", methods=["POST"])
def api_modules_registry_rebuild():
    """Force rebuild registry cache from manifests."""
    try:
        from config import load_config
        cfg = load_config()
        count = _rebuild_registry(cfg)
        return jsonify({"ok": True, "modules_registered": count})
    except Exception as e:
        log.warning("registry rebuild failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/manifests/<name>")
def api_modules_manifest(name):
    """Get a module's manifest."""
    try:
        from config import load_config
        cfg = load_config()
        manifest_dir = Path(__file__).parent / cfg.get("modules", {}).get("manifest_dir", "modules")
        mf = manifest_dir / name / "manifest.json"
        if not mf.exists():
            return jsonify({"error": f"Manifest not found for '{name}'"}), 404
        with open(mf) as f:
            return jsonify(json.load(f))
    except Exception as e:
        log.warning("manifest read failed: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_all_manifests(cfg):
    """Load all manifests from disk (launcher + marketplace)."""
    from dep_resolver import parse_manifest, parse_manifest_dict
    manifests = []
    manifest_dir = Path(__file__).parent / cfg.get("modules", {}).get("manifest_dir", "modules")
    if manifest_dir.exists():
        for mdir in sorted(manifest_dir.iterdir()):
            mf = mdir / "manifest.json"
            if mf.exists():
                try:
                    manifests.append(parse_manifest(str(mf)))
                except Exception as e:
                    log.warning("Failed to parse manifest %s: %s", mf, e)
    # Also load marketplace package manifests from DB
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT manifest_json FROM module_registry WHERE type = 'marketplace'"
            ).fetchall()
        for row in rows:
            try:
                manifests.append(parse_manifest_dict(json.loads(row["manifest_json"])))
            except Exception:
                pass
    except Exception:
        pass
    return manifests


def _get_module_state(cfg):
    """Get current enabled/disabled state of all modules."""
    tabs = cfg.get("launcher", {}).get("tabs", {})
    manifest_dir = Path(__file__).parent / cfg.get("modules", {}).get("manifest_dir", "modules")
    state = {}
    if manifest_dir.exists():
        for mdir in sorted(manifest_dir.iterdir()):
            mf = mdir / "manifest.json"
            if mf.exists():
                try:
                    with open(mf) as f:
                        data = json.load(f)
                    name = data.get("name", mdir.name)
                    state[name] = {
                        "enabled": tabs.get(name, False),
                        "version": data.get("version", "0.0.0"),
                    }
                except Exception:
                    pass
    return state


def _apply_tab_changes(enable_list, disable_list):
    """Update fleet.toml [launcher.tabs] to enable/disable modules."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            log.warning("No TOML library available for writing")
            return
    toml_path = Path(__file__).parent / "fleet.toml"
    with open(toml_path, "r") as f:
        content = f.read()
    for name in enable_list:
        # Replace 'name = false' with 'name = true'
        content = content.replace(f"{name} = false", f"{name} = true")
    for name in disable_list:
        content = content.replace(f"{name} = true", f"{name} = false")
    with open(toml_path, "w") as f:
        f.write(content)


def _rebuild_registry(cfg):
    """Rebuild module_registry table from manifests."""
    from dep_resolver import build_dependency_graph, parse_constraint
    import db
    manifests = _load_all_manifests(cfg)
    graph = build_dependency_graph(manifests)
    count = {"n": 0}

    def _do():
        with db.get_conn() as conn:
            conn.execute("DELETE FROM module_registry")
            for m in manifests:
                requires = [parse_constraint(r)[0] for r in m.dependencies.requires]
                conn.execute(
                    "INSERT OR REPLACE INTO module_registry "
                    "(name, version, type, manifest_json, resolved_requires, resolved_conflicts, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        m.name, m.version, m.type,
                        json.dumps({"name": m.name, "version": m.version, "type": m.type,
                                    "description": m.description, "dependencies": {
                                        "requires": m.dependencies.requires,
                                        "conflicts": m.dependencies.conflicts,
                                        "recommends": m.dependencies.recommends,
                                    }}),
                        json.dumps(requires),
                        json.dumps(m.dependencies.conflicts),
                    ),
                )
                count["n"] += 1

    db._retry_write(_do)
    log.info("Registry rebuilt: %d modules", count["n"])
    return count["n"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -k "blueprint" -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/modules_blueprint.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): coordinator API — preview, apply, snapshots, rollback, registry"
```

---

### Task 10: Smoke Test + Integration Verification

**Files:**
- Modify: `fleet/smoke_test.py`

- [ ] **Step 1: Update existing smoke test**

The existing `test_modules_api()` at line 937 already tests the basic `/api/modules` endpoint. Add a new test below it for the dependency features:

```python
def test_dep_resolver_importable():
    """Dependency resolver module imports and core functions exist."""
    from dep_resolver import (
        parse_manifest_dict, build_dependency_graph, resolve,
        topological_sort, check_version_constraint, parse_constraint,
    )
    # Quick sanity: parse a minimal manifest
    m = parse_manifest_dict({
        "name": "test", "version": "1.0.0", "type": "launcher",
        "description": "smoke test", "dependencies": {"requires": [], "conflicts": [], "recommends": []},
    })
    assert m.name == "test"
    graph = build_dependency_graph([m])
    assert "test" in graph.nodes
    return True, "dep_resolver importable + graph builds"


def test_snapshotter_importable():
    """Module snapshotter module imports and core functions exist."""
    from module_snapshotter import (
        take_snapshot, list_snapshots, get_snapshot,
        diff_snapshot, restore_snapshot, prune_snapshots,
    )
    assert callable(take_snapshot)
    assert callable(restore_snapshot)
    return True, "module_snapshotter importable"
```

- [ ] **Step 2: Run full smoke test**

Run: `cd fleet && python smoke_test.py --fast`
Expected: all tests PASS (including the two new ones)

- [ ] **Step 3: Run full unit test suite**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/smoke_test.py
git commit -m "test(modules): add dep_resolver and snapshotter smoke tests"
```

---

### Task 11: Final Integration — Run All Tests

- [ ] **Step 1: Run full smoke test suite**

Run: `cd fleet && python smoke_test.py --fast`
Expected: all tests PASS

- [ ] **Step 2: Run unit test suite**

Run: `cd fleet && python -m pytest tests/test_dep_resolver.py -v --tb=short`
Expected: all tests PASS

- [ ] **Step 3: Verify manifests load correctly**

Run: `cd fleet && python -c "from dep_resolver import parse_manifest; import glob; ms = [parse_manifest(p) for p in glob.glob('modules/*/manifest.json')]; print(f'{len(ms)} manifests loaded'); [print(f'  {m.name} v{m.version} requires={m.dependencies.requires}') for m in ms]"`
Expected: 11 manifests loaded with correct dependency info

- [ ] **Step 4: Verify registry rebuild**

Run: `cd fleet && python -c "import db; db.init_db(); from modules_blueprint import _rebuild_registry; from config import load_config; n = _rebuild_registry(load_config()); print(f'{n} modules registered')"`
Expected: 11 modules registered

- [ ] **Step 5: Final commit (if any remaining unstaged changes)**

```bash
git add fleet/dep_resolver.py fleet/module_snapshotter.py fleet/modules_blueprint.py fleet/db.py fleet/fleet.toml fleet/modules/ fleet/smoke_test.py fleet/tests/test_dep_resolver.py
git commit -m "feat(modules): complete dependency resolution + versioning/rollback system"
```
