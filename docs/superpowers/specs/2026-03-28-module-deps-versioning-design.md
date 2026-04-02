# Module Dependency Resolution & Versioning/Rollback — Design Spec

**Date:** 2026-03-28
**Status:** Approved
**Goal:** Unified dependency resolution with profile-aware rules and full snapshot rollback for both launcher modules and marketplace packages.
**Prerequisite:** Module Manager UI spec (2026-03-28-module-manager-design.md)

---

## Problem

The module system has no dependency resolution — enabling a module that requires another doesn't auto-detect the missing dependency. There's no versioning contract for launcher modules, and no rollback mechanism to restore a known-good module configuration after a bad change. Marketplace packages track versions in the DB but have no constraint solver or restore path.

## Decision Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | Unified (launcher + marketplace) | Single resolver sees complete dependency picture |
| Dependency model | Profile-aware (requires, conflicts, recommends) | Profiles already define module sets; deps should respect them |
| Rollback depth | Full snapshot (config + packages + deps + schema) | Point-in-time recovery for the entire module state |
| Conflict UX | Interactive approval before execution | Safest — user sees and approves all proposed changes |
| Metadata location | Hybrid (manifests + registry DB cache) | Manifests are portable source of truth; DB cache for fast runtime lookups |
| Architecture | Layered (resolver + snapshotter + coordinator) | Clean SoC, independently auditable, testable in isolation |

---

## Architecture

Three components with strict boundaries:

```
User action (enable/install/rollback)
  → modules_blueprint.py (coordinator)
    → dep_resolver.py: "what changes are needed?"
    → Return proposed changeset to user (interactive approval)
    → User approves
    → module_snapshotter.py: take snapshot of current state
    → Execute changes (enable/install/downgrade)
    → Update registry DB cache
```

| Component | File | Responsibility | Est. Lines |
|-----------|------|---------------|------------|
| Dependency Resolver | `fleet/dep_resolver.py` | Parse manifests, build DAG, detect conflicts, topological sort, profile-aware resolution | ~200 |
| Module Snapshotter | `fleet/module_snapshotter.py` | Point-in-time snapshots, diff, restore, auto-prune | ~200 |
| Coordinator | `fleet/modules_blueprint.py` | REST API wiring resolver + snapshotter, interactive approval | ~250 |

**Key principle:** The resolver is pure logic (no DB writes, no side effects). The snapshotter is pure persistence. The coordinator is the only component that orchestrates and mutates state.

---

## 1. Module Manifest Schema

Every module (launcher or marketplace) declares metadata in a `manifest.json`. For launcher modules these live at `fleet/modules/<name>/manifest.json`. For marketplace packages they're bundled in the package archive.

```json
{
  "name": "crm",
  "version": "1.2.0",
  "type": "launcher",
  "description": "Customer relationship management module",
  "author": "BigEd",
  "dependencies": {
    "requires": ["outputs@>=1.0", "accounts"],
    "conflicts": ["crm_lite"],
    "recommends": ["intelligence"]
  },
  "profiles": {
    "consulting": {
      "requires": ["customers"]
    }
  },
  "schema_version": "2",
  "rollback_safe": true,
  "min_fleet_version": "0.300.00b"
}
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique module identifier |
| `version` | string (semver) | yes | Module version |
| `type` | `"launcher"` or `"marketplace"` | yes | Module category |
| `description` | string | yes | Human-readable description |
| `author` | string | no | Module author/publisher |
| `dependencies.requires` | string[] | no | Hard dependencies — must be enabled. Supports version constraints: `name`, `name@>=1.0`, `name@^2.0`, `name@==1.5.0` |
| `dependencies.conflicts` | string[] | no | Mutually exclusive modules — cannot be enabled simultaneously |
| `dependencies.recommends` | string[] | no | Soft dependencies — suggested but not enforced |
| `profiles` | object | no | Profile-specific dependency overrides. Keys are profile names, values have same shape as `dependencies` |
| `schema_version` | string | no | Database schema version this module expects (for migration tracking) |
| `rollback_safe` | boolean | no | Whether rollback can safely revert this module's changes (default: true) |
| `min_fleet_version` | string | no | Minimum BigEd fleet version required |

### Version Constraint Syntax

| Syntax | Meaning | Example |
|--------|---------|---------|
| `name` | Any version | `outputs` |
| `name@>=1.0` | Greater than or equal | `outputs@>=1.0` |
| `name@==1.5.0` | Exact match | `analytics@==1.5.0` |
| `name@^2.0` | Compatible (same major) | `crm@^2.0` matches 2.x.x |

---

## 2. Dependency Resolver (`fleet/dep_resolver.py`)

Pure-logic module. No database access, no side effects. Takes manifests in, returns changesets out.

### Core Functions

```python
def parse_manifest(manifest_path: str) -> ModuleManifest:
    """Parse and validate a manifest.json file."""

def build_dependency_graph(manifests: list[ModuleManifest]) -> DependencyGraph:
    """Build a directed acyclic graph from all module manifests."""

def resolve(
    graph: DependencyGraph,
    action: str,           # "enable" | "disable" | "install" | "uninstall"
    target: str,           # module name
    active_profile: str,   # current launcher profile
    current_state: dict,   # {module_name: {enabled: bool, version: str}}
) -> ChangeSet:
    """Resolve dependencies for a proposed action. Returns the full changeset."""

def detect_conflicts(graph: DependencyGraph, proposed_state: dict) -> list[Conflict]:
    """Check proposed state for dependency violations and conflicts."""

def topological_sort(graph: DependencyGraph) -> list[str]:
    """Return modules in dependency order (leaves first)."""

def check_version_constraint(installed: str, constraint: str) -> bool:
    """Check if an installed version satisfies a constraint string."""
```

### ChangeSet Structure

```python
@dataclass
class ChangeSet:
    enable: list[str]       # Modules to enable (dependency cascade)
    disable: list[str]      # Modules to disable (reverse dependency cascade)
    install: list[str]      # Marketplace packages to install
    uninstall: list[str]    # Marketplace packages to remove
    upgrade: list[tuple[str, str, str]]  # (name, from_version, to_version)
    conflicts: list[Conflict]  # Unresolvable conflicts requiring user decision
    warnings: list[str]     # Soft dependency recommendations
```

### Resolution Algorithm

1. **Load manifests** — scan `fleet/modules/*/manifest.json` + marketplace package manifests
2. **Build DAG** — edges from each module to its `requires` dependencies
3. **Merge profile deps** — overlay active profile's deps onto global deps
4. **Forward resolve** (enable/install): walk `requires` edges, collect all transitive deps not yet enabled
5. **Reverse resolve** (disable/uninstall): walk reverse edges, collect all dependents that would break
6. **Conflict check** — scan `conflicts` lists for mutual exclusions in proposed state
7. **Version check** — verify all version constraints are satisfiable
8. **Cycle detection** — topological sort; if cycle found, return error with cycle path
9. **Return changeset** — the full set of changes needed, with conflicts and warnings

### Profile-Aware Resolution

```python
def _merge_profile_deps(manifest: ModuleManifest, profile: str) -> Dependencies:
    """Merge global dependencies with profile-specific overrides."""
    base = manifest.dependencies.copy()
    if profile in manifest.profiles:
        profile_deps = manifest.profiles[profile]
        base.requires = list(set(base.requires + profile_deps.get("requires", [])))
        base.conflicts = list(set(base.conflicts + profile_deps.get("conflicts", [])))
        base.recommends = list(set(base.recommends + profile_deps.get("recommends", [])))
    return base
```

---

## 3. Module Snapshotter (`fleet/module_snapshotter.py`)

Persistence module. Manages point-in-time snapshots of the entire module system state.

### Core Functions

```python
def take_snapshot(label: str, created_by: str = "system") -> int:
    """Capture current module state. Returns snapshot ID."""

def list_snapshots(limit: int = 20) -> list[Snapshot]:
    """List available snapshots, newest first."""

def get_snapshot(snapshot_id: int) -> Snapshot:
    """Retrieve a specific snapshot."""

def diff_snapshot(snapshot_id: int) -> SnapshotDiff:
    """Compare a snapshot against current state. Returns what would change on restore."""

def restore_snapshot(snapshot_id: int) -> ChangeSet:
    """Generate a changeset that would restore the snapshot state.
    Does NOT execute — returns changeset for interactive approval."""

def prune_snapshots(keep: int = 20) -> int:
    """Remove oldest snapshots beyond the keep limit. Returns count removed."""
```

### Snapshot Contents

```python
@dataclass
class Snapshot:
    id: int
    label: str
    created_at: str
    created_by: str
    state: SnapshotState

@dataclass
class SnapshotState:
    enabled_modules: dict[str, str]       # {name: version}
    disabled_modules: dict[str, str]      # {name: version}
    installed_packages: dict[str, str]    # {name: version}
    active_profile: str                   # e.g. "consulting"
    dep_graph_hash: str                   # SHA-256 of resolved dependency graph
    schema_versions: dict[str, str]       # {module_name: schema_version}
    fleet_toml_modules_section: dict       # Parsed dict of [launcher.tabs] + [modules] keys (restore merges keys, not raw text)
```

### Auto-Snapshot Triggers

Snapshots are taken automatically before any mutating module operation:
- Enable/disable a module
- Install/uninstall a marketplace package
- Change active profile
- Module update (version change)

Label format: `auto:<action>:<target>:<timestamp>` (e.g., `auto:enable:crm:2026-03-28T14:30:00`)

### Schema Migration Rollback

For modules with `schema_version`, the snapshotter tracks which schema version was active at snapshot time. On restore:

1. Compare current schema version to snapshot schema version
2. If current > snapshot, run down-migrations (if `rollback_safe: true`)
3. If module declares `rollback_safe: false`, warn user and require explicit confirmation
4. If down-migration files are missing, block rollback for that module and surface a warning: "Module X cannot be rolled back — migration file missing"
5. Migration files follow naming: `fleet/modules/<name>/migrations/<version>_up.sql` and `<version>_down.sql`

### Storage

Snapshots stored in the `module_snapshots` table (see DB Schema section). The `state` field is JSON-serialized `SnapshotState`. Default retention: 20 snapshots, configurable via `fleet.toml`:

```toml
[modules]
snapshot_retention = 20
auto_snapshot = true
```

---

## 4. Coordinator API (`fleet/modules_blueprint.py`)

Flask blueprint that wires the resolver and snapshotter together. This is the only component that performs side effects (DB writes, fleet.toml updates, package file operations).

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/modules/preview-change` | POST | Preview a proposed change — returns changeset without executing |
| `/api/modules/apply-change` | POST | Execute an approved changeset (auto-snapshots first) |
| `/api/modules/snapshots` | GET | List available rollback points |
| `/api/modules/snapshots/<id>` | GET | Get snapshot details |
| `/api/modules/snapshots/<id>/diff` | GET | Diff snapshot against current state |
| `/api/modules/rollback/<id>` | POST | Preview restore changeset for approval |
| `/api/modules/rollback/<id>/apply` | POST | Execute approved rollback |
| `/api/modules/registry` | GET | Cached dependency graph (rebuilt from manifests) |
| `/api/modules/registry/rebuild` | POST | Force rebuild registry cache from manifests |
| `/api/modules/manifests/<name>` | GET | Get a module's manifest |

### Preview-Change Request

```json
POST /api/modules/preview-change
{
  "action": "enable",
  "target": "crm",
  "profile": "consulting"
}
```

### Preview-Change Response

```json
{
  "changeset": {
    "enable": ["outputs", "accounts", "customers", "crm"],
    "disable": [],
    "install": [],
    "conflicts": [],
    "warnings": ["Module 'intelligence' is recommended by 'crm' but not included"]
  },
  "snapshot_label": "auto:enable:crm:2026-03-28T14:30:00",
  "requires_approval": true
}
```

### Apply-Change Request

```json
POST /api/modules/apply-change
{
  "changeset": {
    "enable": ["outputs", "accounts", "customers", "crm"],
    "disable": []
  },
  "approved": true,
  "snapshot_label": "auto:enable:crm:2026-03-28T14:30:00"
}
```

**Server-side re-validation:** The coordinator MUST re-run `dep_resolver.resolve()` against current state before executing. A stale or tampered changeset is rejected with a 409 Conflict response prompting the user to re-preview.

### Interactive Approval Flow

1. User triggers action (enable/disable/install) from dashboard UI
2. UI calls `POST /api/modules/preview-change`
3. Dashboard shows changeset: "Enabling CRM will also enable: Outputs, Accounts, Customers. Proceed?"
4. User approves or cancels
5. On approve: UI calls `POST /api/modules/apply-change` with the changeset
6. Coordinator takes snapshot, executes changes, updates registry cache
7. On cancel: no-op

---

## 5. Module Registry DB Cache

The registry table caches the resolved dependency graph for fast runtime lookups. Rebuilt from manifests on boot and whenever modules change.

### DB Schema

```sql
-- Cached dependency graph (source of truth: manifest.json files)
CREATE TABLE IF NOT EXISTS module_registry (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('launcher', 'marketplace')),
    manifest_json TEXT NOT NULL,
    resolved_requires TEXT DEFAULT '[]',   -- JSON array of resolved dependency names
    resolved_conflicts TEXT DEFAULT '[]',  -- JSON array of conflict module names
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Point-in-time module state snapshots
CREATE TABLE IF NOT EXISTS module_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    state_json TEXT NOT NULL,
    dep_graph_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    created_by TEXT DEFAULT 'system'
);

-- Index for fast snapshot lookup
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON module_snapshots(created_at DESC);
```

### Registry Rebuild

On boot or `POST /api/modules/registry/rebuild`:

1. Scan `fleet/modules/*/manifest.json` for launcher modules
2. Query `marketplace_packages` for installed marketplace manifests
3. Parse all manifests via `dep_resolver.parse_manifest()`
4. Build graph via `dep_resolver.build_dependency_graph()`
5. Write each module's resolved deps to `module_registry`
6. Log rebuild time and module count

---

## 6. Launcher Module Manifest Locations

Launcher modules need manifest files. Create `fleet/modules/<name>/manifest.json` for each existing launcher tab module:

| Module | Path |
|--------|------|
| `command_center` | `fleet/modules/command_center/manifest.json` |
| `agents` | `fleet/modules/agents/manifest.json` |
| `crm` | `fleet/modules/crm/manifest.json` |
| `ingestion` | `fleet/modules/ingestion/manifest.json` |
| `outputs` | `fleet/modules/outputs/manifest.json` |
| `intelligence` | `fleet/modules/intelligence/manifest.json` |
| `manual_mode` | `fleet/modules/manual_mode/manifest.json` |
| `onboarding` | `fleet/modules/onboarding/manifest.json` |
| `customers` | `fleet/modules/customers/manifest.json` |
| `accounts` | `fleet/modules/accounts/manifest.json` |
| `owner_core` | `fleet/modules/owner_core/manifest.json` |

Initial manifests will have `version: "1.0.0"`, no dependencies (to be populated incrementally as relationships are mapped).

---

## 7. fleet.toml Changes

```toml
[modules]
hub_url = "https://github.com/mbachaud/BigEd-ModuleHub"
enterprise_hub_url = ""
auto_update = false
verify_checksums = true
# New fields:
snapshot_retention = 20       # Max snapshots to keep
auto_snapshot = true          # Snapshot before every mutating operation
manifest_dir = "fleet/modules"  # Where launcher module manifests live
```

---

## Files Changed

| File | Action | Purpose |
|------|--------|---------|
| `fleet/dep_resolver.py` | Create (~200 lines) | Pure dependency resolution logic |
| `fleet/module_snapshotter.py` | Create (~200 lines) | Snapshot/restore/prune |
| `fleet/modules_blueprint.py` | Create (~250 lines) | Coordinator REST API |
| `fleet/db.py` | Modify (~15 lines) | Add module_registry + module_snapshots tables |
| `fleet/dashboard.py` | Modify (~3 lines) | Register modules_blueprint |
| `fleet/fleet.toml` | Modify (~5 lines) | Add snapshot/manifest config |
| `fleet/modules/*/manifest.json` | Create (11 files, ~15 lines each) | Launcher module manifests |
| `fleet/smoke_test.py` | Modify (~20 lines) | Add dep resolver + snapshotter tests |

## Out of Scope

- Dashboard UI for interactive approval (covered by Module Manager UI spec)
- Automatic dependency auto-install without user approval
- Distributed/multi-fleet snapshot sync
- Marketplace package signing (separate security concern)
- Migration file authoring tooling
