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


def test_dimension_registry_has_12():
    from audit_scorer import DIMENSIONS
    assert len(DIMENSIONS) == 12


def test_dimension_confidence_range():
    from audit_scorer import DIMENSIONS
    for name, dim in DIMENSIONS.items():
        assert 0.0 <= dim["confidence"] <= 1.0, f"{name} confidence out of range"


def test_dimension_has_required_keys():
    from audit_scorer import DIMENSIONS
    for name, dim in DIMENSIONS.items():
        assert "check_fn" in dim, f"{name} missing check_fn"
        assert "confidence" in dim, f"{name} missing confidence"
        assert "tier" in dim, f"{name} missing tier"


def test_run_tier_light_returns_results():
    from audit_scorer import run_tier
    results = run_tier("light")
    assert isinstance(results, list)
    assert len(results) >= 1
    for r in results:
        assert "dimension" in r
        assert "auto_score" in r
        assert 0.0 <= r["auto_score"] <= 1.0


def test_write_scores_to_db():
    import db
    db.init_db()
    from audit_scorer import write_scores
    scores = [
        {"dimension": "testing", "auto_score": 0.93, "auto_detail": '{"passed": 93}',
         "manual_grade": "A", "divergence": 0},
    ]
    write_scores("light", scores)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_scores WHERE dimension='testing' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["auto_score"] == 0.93
    assert rows[0]["tier"] == "light"


def test_get_active_divergences():
    import db
    db.init_db()
    from audit_scorer import write_scores, get_active_divergences
    scores = [
        {"dimension": "security", "auto_score": 0.70, "auto_detail": "{}",
         "manual_grade": "A", "divergence": 1},
        {"dimension": "testing", "auto_score": 0.90, "auto_detail": "{}",
         "manual_grade": "A", "divergence": 0},
    ]
    write_scores("daily", scores)
    divergences = get_active_divergences()
    dims = [d["dimension"] for d in divergences]
    assert "security" in dims
    assert "testing" not in dims


# ── Task 5: Reconciliation Engine ─────────────────────────────────────────

def test_reconcile_flags_divergence():
    from audit_scorer import reconcile
    # testing has confidence 0.95 (>= gate 0.50); auto_score 0.70 vs A (0.90) => gap 0.20 > 0.15
    scores = [{"dimension": "testing", "auto_score": 0.70, "auto_detail": "{}", "manual_grade": "A", "divergence": 0}]
    result = reconcile(scores)
    testing = next(r for r in result if r["dimension"] == "testing")
    assert testing["divergence"] == 1  # confidence 0.95, gap 0.20


def test_reconcile_no_flag_low_confidence():
    from audit_scorer import reconcile
    # usability_ux has confidence 0.30 (< gate 0.50); gap should not be flagged
    scores = [{"dimension": "usability_ux", "auto_score": 0.50, "auto_detail": "{}", "manual_grade": "A", "divergence": 0}]
    result = reconcile(scores)
    ux = next(r for r in result if r["dimension"] == "usability_ux")
    assert ux["divergence"] == 0  # confidence 0.30 < 0.50 gate


def test_reconcile_within_threshold():
    from audit_scorer import reconcile
    # testing has confidence 0.95; auto_score 0.88 vs A (0.90) => gap 0.02 <= 0.15
    scores = [{"dimension": "testing", "auto_score": 0.88, "auto_detail": "{}", "manual_grade": "A", "divergence": 0}]
    result = reconcile(scores)
    testing = next(r for r in result if r["dimension"] == "testing")
    assert testing["divergence"] == 0  # gap 0.02, under 0.15


def test_ratchet_check():
    from audit_scorer import check_ratchets
    # auto_score 0.70 < A (0.90) => violation
    scores = [{"dimension": "testing", "auto_score": 0.70}]
    violations = check_ratchets(scores, {"testing": "A"})
    assert len(violations) == 1
    assert violations[0]["dimension"] == "testing"


def test_ratchet_check_no_violation():
    from audit_scorer import check_ratchets
    # auto_score 0.95 >= B+ (0.80) => no violation
    scores = [{"dimension": "testing", "auto_score": 0.95}]
    violations = check_ratchets(scores, {"testing": "B+"})
    assert len(violations) == 0


# ── Task 6: User Feedback ─────────────────────────────────────────────────

def test_record_feedback():
    import db
    db.init_db()
    from audit_scorer import record_feedback
    record_feedback(score=0.8, scope="overall", text="Dashboard loads fast", actor="test_user")
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM user_feedback WHERE actor='test_user'").fetchall()
    assert len(rows) >= 1
    assert rows[0]["score"] == 0.8


def test_feedback_summary():
    import db
    db.init_db()
    from audit_scorer import record_feedback, get_feedback_summary
    record_feedback(score=0.6, scope="overall", text="slow graphs")
    record_feedback(score=0.8, scope="overall", text="nice layout")
    summary = get_feedback_summary()
    assert "overall" in summary
    assert summary["overall"]["count"] >= 2


def test_ux_confidence_scales_with_feedback():
    from audit_scorer import get_ux_confidence
    assert get_ux_confidence(0) == 0.30
    assert get_ux_confidence(50) > 0.30
    assert get_ux_confidence(100) == 0.75
    assert get_ux_confidence(200) == 0.75


# ── Task 7: Weekly External Checks ────────────────────────────────────────

def test_check_version_drift_returns_score():
    from audit_scorer import _check_version_drift
    score, detail = _check_version_drift()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_check_github_returns_score():
    from audit_scorer import _check_github
    score, detail = _check_github()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_check_semgrep_returns_score():
    from audit_scorer import _check_semgrep
    score, detail = _check_semgrep()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_weekly_merge_score():
    from audit_scorer import merged_score
    result = merged_score(daily=0.80, weekly=1.0, weekly_age_days=0)
    assert abs(result - 0.88) < 0.01
    result = merged_score(daily=0.80, weekly=1.0, weekly_age_days=6)
    assert abs(result - 0.82) < 0.01


def test_audit_blueprint_imports():
    """audit_blueprint module imports and exposes audit_bp."""
    from audit_blueprint import audit_bp
    assert audit_bp.name == "audit"
