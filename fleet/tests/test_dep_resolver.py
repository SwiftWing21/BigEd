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
