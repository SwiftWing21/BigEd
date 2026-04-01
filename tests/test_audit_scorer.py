"""Tests for the two-brain audit scoring engine."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))
os.environ.setdefault("FLEET_TEST_DB", ":memory:")


def test_audit_scores_table_exists():
    """audit_scores table is created by init_db."""
    import db
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_scores'"
        ).fetchall()
    assert len(rows) == 1, "audit_scores table not found"


def test_user_feedback_table_exists():
    """user_feedback table is created by init_db."""
    import db
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_feedback'"
        ).fetchall()
    assert len(rows) == 1, "user_feedback table not found"
