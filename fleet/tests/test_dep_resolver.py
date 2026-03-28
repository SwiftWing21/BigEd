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


# ---------------------------------------------------------------------------
# Task 4: Version constraint parser
# ---------------------------------------------------------------------------

def test_parse_version_constraint_any():
    """'outputs' parses to (name, None, None) — no constraint."""
    from dep_resolver import parse_constraint
    assert parse_constraint("outputs") == ("outputs", None, None)


def test_parse_version_constraint_gte():
    """'outputs@>=1.0' parses correctly."""
    from dep_resolver import parse_constraint
    assert parse_constraint("outputs@>=1.0") == ("outputs", ">=", "1.0")


def test_parse_version_constraint_exact():
    """'analytics@==1.5.0' parses correctly."""
    from dep_resolver import parse_constraint
    assert parse_constraint("analytics@==1.5.0") == ("analytics", "==", "1.5.0")


def test_parse_version_constraint_compatible():
    """'crm@^2.0' parses correctly."""
    from dep_resolver import parse_constraint
    assert parse_constraint("crm@^2.0") == ("crm", "^", "2.0")


def test_check_version_any():
    """Any installed version passes when op/required are None."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("3.1.4", None, None) is True


def test_check_version_gte_pass():
    """1.5.0 >= 1.0 should be True."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("1.5.0", ">=", "1.0") is True


def test_check_version_gte_fail():
    """0.9.0 >= 1.0 should be False."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("0.9.0", ">=", "1.0") is False


def test_check_version_exact_pass():
    """1.5.0 == 1.5.0 should be True."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("1.5.0", "==", "1.5.0") is True


def test_check_version_exact_fail():
    """1.5.1 == 1.5.0 should be False."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("1.5.1", "==", "1.5.0") is False


def test_check_version_compatible_pass():
    """2.3.1 ^ 2.0 should be True (same major, >= minor)."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("2.3.1", "^", "2.0") is True


def test_check_version_compatible_fail_major():
    """3.0.0 ^ 2.0 should be False (different major)."""
    from dep_resolver import check_version_constraint
    assert check_version_constraint("3.0.0", "^", "2.0") is False


# ---------------------------------------------------------------------------
# Task 5: Manifest parser + graph builder
# ---------------------------------------------------------------------------

def _make_manifest(name, requires=None, conflicts=None, recommends=None, profiles=None):
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
    """parse_manifest_dict correctly parses a crm-like manifest."""
    from dep_resolver import parse_manifest_dict, ModuleManifest
    data = {
        "name": "crm",
        "version": "1.0.0",
        "type": "launcher",
        "description": "CRM module",
        "author": "BigEd",
        "dependencies": {
            "requires": ["accounts"],
            "conflicts": [],
            "recommends": ["intelligence"],
        },
        "profiles": {"consulting": {"requires": ["customers"]}},
        "schema_version": "1",
        "rollback_safe": True,
        "min_fleet_version": "0.300.00b",
    }
    m = parse_manifest_dict(data)
    assert isinstance(m, ModuleManifest)
    assert m.name == "crm"
    assert m.version == "1.0.0"
    assert "accounts" in m.dependencies.requires
    assert "intelligence" in m.dependencies.recommends
    assert m.rollback_safe is True
    assert m.min_fleet_version == "0.300.00b"


def test_parse_manifest_minimal():
    """parse_manifest_dict works with minimal required fields."""
    from dep_resolver import parse_manifest_dict
    data = _make_manifest("accounts")
    m = parse_manifest_dict(data)
    assert m.name == "accounts"
    assert m.dependencies.requires == []
    assert m.dependencies.conflicts == []


def test_build_graph_edges():
    """crm requires accounts → edge crm→accounts, reverse accounts→crm."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph
    crm = parse_manifest_dict(_make_manifest("crm", requires=["accounts"]))
    accounts = parse_manifest_dict(_make_manifest("accounts"))
    graph = build_dependency_graph([crm, accounts])
    assert "accounts" in graph.edges.get("crm", [])
    assert "crm" in graph.reverse.get("accounts", [])


def test_build_graph_profile_merge():
    """consulting profile adds customers to crm requires."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, _merge_profile_deps
    crm = parse_manifest_dict(_make_manifest(
        "crm",
        requires=["accounts"],
        profiles={"consulting": {"requires": ["customers"]}},
    ))
    deps = _merge_profile_deps(crm, "consulting")
    assert "accounts" in deps.requires
    assert "customers" in deps.requires


def test_build_graph_profile_no_match():
    """A non-matching profile doesn't add profile-specific requires."""
    from dep_resolver import parse_manifest_dict, _merge_profile_deps
    crm = parse_manifest_dict(_make_manifest(
        "crm",
        requires=["accounts"],
        profiles={"consulting": {"requires": ["customers"]}},
    ))
    deps = _merge_profile_deps(crm, "minimal")
    assert "accounts" in deps.requires
    assert "customers" not in deps.requires


# ---------------------------------------------------------------------------
# Task 6: Topological sort + cycle detection
# ---------------------------------------------------------------------------

def test_topological_sort_linear():
    """c→b→a: a must appear before b, b before c."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, topological_sort
    a = parse_manifest_dict(_make_manifest("a"))
    b = parse_manifest_dict(_make_manifest("b", requires=["a"]))
    c = parse_manifest_dict(_make_manifest("c", requires=["b"]))
    graph = build_dependency_graph([a, b, c])
    order = topological_sort(graph)
    assert order.index("a") < order.index("b")
    assert order.index("b") < order.index("c")


def test_topological_sort_diamond():
    """d→(b,c)→a: a appears before b and c; b,c before d."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, topological_sort
    a = parse_manifest_dict(_make_manifest("a"))
    b = parse_manifest_dict(_make_manifest("b", requires=["a"]))
    c = parse_manifest_dict(_make_manifest("c", requires=["a"]))
    d = parse_manifest_dict(_make_manifest("d", requires=["b", "c"]))
    graph = build_dependency_graph([a, b, c, d])
    order = topological_sort(graph)
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_topological_sort_cycle_detected():
    """a→b→a raises CycleError with relevant module names."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, topological_sort, CycleError
    a = parse_manifest_dict(_make_manifest("a", requires=["b"]))
    b = parse_manifest_dict(_make_manifest("b", requires=["a"]))
    graph = build_dependency_graph([a, b])
    try:
        topological_sort(graph)
        assert False, "Expected CycleError"
    except CycleError as e:
        msg = str(e)
        assert "a" in msg or "b" in msg


# ---------------------------------------------------------------------------
# Task 7: resolve() + detect_conflicts()
# ---------------------------------------------------------------------------

def test_resolve_enable_pulls_deps():
    """Enabling crm also schedules accounts for enable."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    crm = parse_manifest_dict(_make_manifest("crm", requires=["accounts"]))
    accounts = parse_manifest_dict(_make_manifest("accounts"))
    graph = build_dependency_graph([crm, accounts])
    cs = resolve(graph, "enable", "crm", None, set())
    assert "crm" in cs.enable
    assert "accounts" in cs.enable


def test_resolve_enable_already_enabled_dep():
    """Already-enabled deps are not re-added to the enable list."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    crm = parse_manifest_dict(_make_manifest("crm", requires=["accounts"]))
    accounts = parse_manifest_dict(_make_manifest("accounts"))
    graph = build_dependency_graph([crm, accounts])
    cs = resolve(graph, "enable", "crm", None, {"accounts"})
    assert "crm" in cs.enable
    assert "accounts" not in cs.enable


def test_resolve_disable_cascades():
    """Disabling accounts also marks crm for disable."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    crm = parse_manifest_dict(_make_manifest("crm", requires=["accounts"]))
    accounts = parse_manifest_dict(_make_manifest("accounts"))
    graph = build_dependency_graph([crm, accounts])
    cs = resolve(graph, "disable", "accounts", None, {"crm", "accounts"})
    assert "accounts" in cs.disable
    assert "crm" in cs.disable


def test_resolve_conflict_detected():
    """Enabling crm_lite when crm is already enabled yields a conflict."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    crm = parse_manifest_dict(_make_manifest("crm", conflicts=["crm_lite"]))
    crm_lite = parse_manifest_dict(_make_manifest("crm_lite", conflicts=["crm"]))
    graph = build_dependency_graph([crm, crm_lite])
    cs = resolve(graph, "enable", "crm_lite", None, {"crm"})
    assert len(cs.conflicts) > 0
    conflict_modules = {(c.module_a, c.module_b) for c in cs.conflicts}
    pairs = conflict_modules | {(b, a) for a, b in conflict_modules}
    assert ("crm", "crm_lite") in pairs or ("crm_lite", "crm") in pairs


def test_resolve_recommends_warning():
    """Enabling crm warns about recommended but not-enabled intelligence."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    crm = parse_manifest_dict(_make_manifest("crm", recommends=["intelligence"]))
    intelligence = parse_manifest_dict(_make_manifest("intelligence"))
    graph = build_dependency_graph([crm, intelligence])
    cs = resolve(graph, "enable", "crm", None, set())
    assert any("intelligence" in w for w in cs.warnings)


def test_resolve_profile_aware():
    """Enabling crm in consulting profile also enables customers."""
    from dep_resolver import parse_manifest_dict, build_dependency_graph, resolve
    crm = parse_manifest_dict(_make_manifest(
        "crm",
        requires=["accounts"],
        profiles={"consulting": {"requires": ["customers"]}},
    ))
    accounts = parse_manifest_dict(_make_manifest("accounts"))
    customers = parse_manifest_dict(_make_manifest("customers"))
    graph = build_dependency_graph([crm, accounts, customers])
    cs = resolve(graph, "enable", "crm", "consulting", set())
    assert "customers" in cs.enable


# ---------------------------------------------------------------------------
# Task 8: Module Snapshotter
# ---------------------------------------------------------------------------


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
    ours = [s for s in remaining if s["label"].startswith("prune-test-")]
    assert len(ours) <= 2
