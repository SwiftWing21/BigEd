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


def test_grade_to_score_mapping():
    """All grades map to expected numeric values."""
    from audit_scorer import grade_to_score
    assert grade_to_score("S") == 1.0
    assert grade_to_score("A+") == 0.95
    assert grade_to_score("A") == 0.90
    assert grade_to_score("A-") == 0.85
    assert grade_to_score("B+") == 0.80
    assert grade_to_score("B") == 0.75
    assert grade_to_score("B-") == 0.70
    assert grade_to_score("C+") == 0.65
    assert grade_to_score("C") == 0.60
    assert grade_to_score("D") == 0.50
    assert grade_to_score("F") == 0.30


def test_score_to_grade_mapping():
    """Numeric scores map back to letter grades."""
    from audit_scorer import score_to_grade
    assert score_to_grade(1.0) == "S"
    assert score_to_grade(0.92) == "A+"
    assert score_to_grade(0.90) == "A"
    assert score_to_grade(0.76) == "B"
    assert score_to_grade(0.50) == "D"
    assert score_to_grade(0.20) == "F"


def test_load_baseline():
    """Baseline sidecar loads and returns dimension grades."""
    from audit_scorer import load_baseline
    baseline = load_baseline()
    assert "dimensions" in baseline
    assert "testing" in baseline["dimensions"]
    assert "grade" in baseline["dimensions"]["testing"]
