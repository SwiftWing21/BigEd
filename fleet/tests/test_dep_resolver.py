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
