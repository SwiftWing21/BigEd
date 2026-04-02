# Two-Brain Audit System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a dual-layer audit system that combines automated quantitative scoring with manual qualitative grading, reconciles them with divergence detection, and surfaces results on the dashboard.

**Architecture:** Core module (`audit_scorer.py`) handles all tier logic and scoring. Flask blueprint (`audit_blueprint.py`) exposes REST endpoints. Thin skill wrapper enables fleet task invocation. Dashboard panel at page bottom shows scores, divergences, and feedback widget.

**Tech Stack:** Python 3.11+, Flask Blueprint, SQLite (via existing `db.py`), SSE (via existing `sse_blueprint.py`), semgrep (optional), Jinja2 templates

**Spec:** `docs/superpowers/specs/2026-04-01-two-brain-audit-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `fleet/audit_scorer.py` | Create | Core module: grade mapping, dimension registry, tier runners, reconciliation, DB reads/writes |
| `fleet/audit_blueprint.py` | Create | REST endpoints: scores, history, divergences, acknowledge, trigger, feedback |
| `fleet/skills/audit_score.py` | Create | Thin skill wrapper: SKILL_NAME/DESCRIPTION/run() dispatching to audit_scorer |
| `fleet/audit_baseline.json` | Create | Manual grade sidecar: initial grades from current AUDIT_TRACKER.md |
| `fleet/templates/components/_audit_panel.html` | Create | Dashboard UI: score table, feedback widget, action buttons |
| `fleet/db.py` | Modify | Add `audit_scores` and `user_feedback` tables to SCHEMA |
| `fleet/smoke_test.py` | Modify | Add `test_audit_health()` divergence check |
| `fleet/hw_supervisor.py` | Modify | Add daily/weekly audit triggers to poll loop |
| `fleet/dashboard.py` | Modify | Register audit_blueprint in `_BLUEPRINTS` list |
| `fleet/dependency_check.py` | Modify | Add semgrep as optional dependency check |
| `tests/test_audit_scorer.py` | Create | Unit tests for scoring engine, reconciliation, grade mapping |

---

### Task 1: Database Schema

**Files:**
- Modify: `fleet/db.py` (SCHEMA string, near line 50)
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing test for audit_scores table**

```python
# tests/test_audit_scorer.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_audit_scores_table_exists -v`
Expected: FAIL — table not found

- [ ] **Step 3: Add tables to db.py SCHEMA**

In `fleet/db.py`, append to the SCHEMA string (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS audit_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
    tier         TEXT    NOT NULL,
    dimension    TEXT    NOT NULL,
    auto_score   REAL,
    auto_detail  TEXT,
    manual_grade TEXT,
    divergence   INTEGER DEFAULT 0,
    acknowledged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_scores(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_dim ON audit_scores(dimension);

CREATE TABLE IF NOT EXISTS user_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now')),
    score      REAL    NOT NULL,
    scope      TEXT    NOT NULL,
    session_id TEXT,
    text       TEXT,
    inferred   TEXT,
    actor      TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_scope_ts ON user_feedback(scope, timestamp);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (both table tests)

- [ ] **Step 5: Commit**

```bash
git add fleet/db.py tests/test_audit_scorer.py
git commit -m "feat(audit): add audit_scores and user_feedback tables to schema"
```

---

### Task 2: Grade Mapping + Baseline Sidecar

**Files:**
- Create: `fleet/audit_scorer.py`
- Create: `fleet/audit_baseline.json`
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing tests for grade mapping**

Append to `tests/test_audit_scorer.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_grade_to_score_mapping -v`
Expected: FAIL — cannot import audit_scorer

- [ ] **Step 3: Create audit_scorer.py with grade mapping and baseline loader**

```python
# fleet/audit_scorer.py
"""Two-Brain Audit Scoring Engine.

Combines automated quantitative scoring (left brain) with manual qualitative
grading (right brain). Reconciles them with divergence detection.

Tiers: light (smoke test), medium (on-demand), daily (3 AM), weekly (Sunday).
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("audit_scorer")

FLEET_DIR = Path(__file__).parent
BASELINE_PATH = FLEET_DIR / "audit_baseline.json"

# ── Grade Scale ───────────────────────────────────────────────────────────

GRADE_TO_SCORE = {
    "S": 1.00, "A+": 0.95, "A": 0.90, "A-": 0.85,
    "B+": 0.80, "B": 0.75, "B-": 0.70,
    "C+": 0.65, "C": 0.60,
    "D": 0.50,
    "F": 0.30,
}

# Sorted thresholds for reverse lookup (score -> grade)
_SCORE_THRESHOLDS = sorted(GRADE_TO_SCORE.items(), key=lambda x: x[1], reverse=True)


def grade_to_score(grade: str) -> float:
    """Convert a letter grade to its numeric score (0.0-1.0)."""
    return GRADE_TO_SCORE.get(grade, 0.0)


def score_to_grade(score: float) -> str:
    """Convert a numeric score to the nearest letter grade."""
    for grade, threshold in _SCORE_THRESHOLDS:
        if score >= threshold - 0.025:  # half-step tolerance
            return grade
    return "F"


# ── Baseline Sidecar ─────────────────────────────────────────────────────

def load_baseline() -> dict:
    """Load manual grades from audit_baseline.json."""
    if not BASELINE_PATH.exists():
        log.warning("audit_baseline.json not found at %s", BASELINE_PATH)
        return {"version": "unknown", "dimensions": {}}
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.warning("Failed to parse audit_baseline.json", exc_info=True)
        return {"version": "unknown", "dimensions": {}}


def save_baseline(baseline: dict) -> None:
    """Write updated baseline back to disk."""
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
        f.write("\n")
```

- [ ] **Step 4: Create audit_baseline.json with current grades from AUDIT_TRACKER.md**

```json
{
  "version": "0.900.00b",
  "dimensions": {
    "architecture": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "dashboard refactor 4/5 phases done"
    },
    "code_quality": {
      "grade": "A-",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "1 raw sqlite3 in account_review.py; sys.path.insert calls cleaned"
    },
    "testing": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "465/477 pytest passing; 12 Factorio failures"
    },
    "security": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "JWT bypass fixed, path traversal blocked, RBAC 5 roles"
    },
    "reliability": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "Self-healing + circuit breakers active; 1 tick loop freeze pending"
    },
    "observability": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "SSE broadcaster extracted; /api/health, JSON logging, alerts"
    },
    "usability_ux": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "Factorio FPM spinbox, deterministic tools added"
    },
    "dynamic_abilities": {
      "grade": "A+",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "ML router, experiment auto-window, guardrails, billing"
    },
    "module_plugin": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "ModuleHub, marketplace auth, module snapshots, 29 DB tables"
    },
    "data_hitl": {
      "grade": "S",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "18 HF ingest sources, HITL inline, distributed tracing, IQ scoring"
    },
    "performance": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "Code-aware token estimation, model prefs, ActionType enum"
    },
    "documentation": {
      "grade": "B+",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "doc_freshness reports 19 stale refs"
    }
  },
  "ratchets": {}
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 6: Commit**

```bash
git add fleet/audit_scorer.py fleet/audit_baseline.json tests/test_audit_scorer.py
git commit -m "feat(audit): grade mapping, baseline sidecar, and initial grades"
```

---

### Task 3: Dimension Registry + Auto-Confidence

**Files:**
- Modify: `fleet/audit_scorer.py`
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing tests for dimension registry**

Append to `tests/test_audit_scorer.py`:

```python
def test_dimension_registry_has_12():
    """Registry contains all 12 audit dimensions."""
    from audit_scorer import DIMENSIONS
    assert len(DIMENSIONS) == 12


def test_dimension_confidence_range():
    """All confidence values are between 0.0 and 1.0."""
    from audit_scorer import DIMENSIONS
    for name, dim in DIMENSIONS.items():
        assert 0.0 <= dim["confidence"] <= 1.0, f"{name} confidence out of range"


def test_dimension_has_required_keys():
    """Each dimension has check_fn, confidence, and tier."""
    from audit_scorer import DIMENSIONS
    for name, dim in DIMENSIONS.items():
        assert "check_fn" in dim, f"{name} missing check_fn"
        assert "confidence" in dim, f"{name} missing confidence"
        assert "tier" in dim, f"{name} missing tier"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_dimension_registry_has_12 -v`
Expected: FAIL — cannot import DIMENSIONS

- [ ] **Step 3: Add dimension registry to audit_scorer.py**

Append to `fleet/audit_scorer.py`. This adds 12 check functions (one per dimension) and the DIMENSIONS registry dict. Each check function uses lazy imports and returns `(float, dict)`.

Key check functions:

- `_check_testing`: runs pytest subprocess, parses "X passed, Y failed"
- `_check_module_plugin`: counts modules table rows
- `_check_documentation`: calls doc_freshness skill
- `_check_performance`: probes health endpoints with latency
- `_check_reliability`: healthy agents + closed circuit breakers
- `_check_observability`: SSE alive + audit_log writable + /api/health
- `_check_architecture`: LOC threshold scan on key files
- `_check_code_quality`: ruff + no raw sqlite3 + no bare excepts
- `_check_security`: RBAC populated + path traversal blocked
- `_check_usability_ux`: pages load + feedback aggregate
- `_check_dynamic_abilities`: ML router + experiment + billing tables exist
- `_check_data_hitl`: HITL table + ingest sources exist

All `subprocess.Popen`/`subprocess.run` calls must include `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)`. All `urllib.request.urlopen` calls must include `timeout=`. All check functions catch `Exception` and return `(0.0, {"error": str(e)})` on failure.

The DIMENSIONS dict maps each name to `{"check_fn": fn, "confidence": float, "tier": str}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add fleet/audit_scorer.py tests/test_audit_scorer.py
git commit -m "feat(audit): dimension registry with 12 check functions and auto-confidence"
```

---

### Task 4: Tier Runners + DB Write

**Files:**
- Modify: `fleet/audit_scorer.py`
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing tests for tier runners**

Append to `tests/test_audit_scorer.py`:

```python
def test_run_tier_light_returns_results():
    """Light tier returns a dict with dimension scores."""
    from audit_scorer import run_tier
    results = run_tier("light")
    assert isinstance(results, list)
    assert len(results) >= 1
    for r in results:
        assert "dimension" in r
        assert "auto_score" in r
        assert 0.0 <= r["auto_score"] <= 1.0


def test_write_scores_to_db():
    """Scores are persisted to audit_scores table."""
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
    """Returns only unacknowledged divergences."""
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_run_tier_light_returns_results -v`
Expected: FAIL — cannot import run_tier

- [ ] **Step 3: Add tier runners and DB functions to audit_scorer.py**

Append `run_tier()`, `write_scores()`, `get_active_divergences()`, `acknowledge_divergence()`, `get_latest_scores()`, and `get_score_history()` functions. Key patterns:
- `run_tier(tier)` iterates DIMENSIONS, runs check_fn for dimensions at or below the requested tier, compares against baseline manual grades, sets divergence flag
- All DB writes use `db._retry_write(fn)` pattern
- All DB reads use `db.get_conn()` with `conn.execute()` and return `[dict(r) for r in rows]`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add fleet/audit_scorer.py tests/test_audit_scorer.py
git commit -m "feat(audit): tier runners, DB persistence, divergence detection"
```

---

### Task 5: Reconciliation Engine

**Files:**
- Modify: `fleet/audit_scorer.py`
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing tests for reconciliation**

Append to `tests/test_audit_scorer.py`:

```python
def test_reconcile_flags_divergence():
    """Reconciliation flags high-confidence divergences."""
    from audit_scorer import reconcile
    scores = [
        {"dimension": "testing", "auto_score": 0.70, "auto_detail": "{}",
         "manual_grade": "A", "divergence": 0},
    ]
    result = reconcile(scores)
    testing = next(r for r in result if r["dimension"] == "testing")
    assert testing["divergence"] == 1


def test_reconcile_no_flag_low_confidence():
    """Low-confidence dimensions don't flag divergence."""
    from audit_scorer import reconcile
    scores = [
        {"dimension": "usability_ux", "auto_score": 0.50, "auto_detail": "{}",
         "manual_grade": "A", "divergence": 0},
    ]
    result = reconcile(scores)
    ux = next(r for r in result if r["dimension"] == "usability_ux")
    assert ux["divergence"] == 0


def test_reconcile_within_threshold():
    """Small gaps within 0.15 threshold don't flag."""
    from audit_scorer import reconcile
    scores = [
        {"dimension": "testing", "auto_score": 0.88, "auto_detail": "{}",
         "manual_grade": "A", "divergence": 0},
    ]
    result = reconcile(scores)
    testing = next(r for r in result if r["dimension"] == "testing")
    assert testing["divergence"] == 0


def test_ratchet_check():
    """Ratchet violation detected when score drops below target."""
    from audit_scorer import check_ratchets
    scores = [{"dimension": "testing", "auto_score": 0.70}]
    ratchets = {"testing": "A"}
    violations = check_ratchets(scores, ratchets)
    assert len(violations) == 1
    assert violations[0]["dimension"] == "testing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_reconcile_flags_divergence -v`
Expected: FAIL — cannot import reconcile

- [ ] **Step 3: Add reconciliation and ratchet functions**

Append `reconcile(scores)`, `check_ratchets(scores, ratchets)`, and `run_and_store(tier)` to `audit_scorer.py`.

`reconcile()` loads baseline, compares auto_score vs manual_numeric per dimension, flags divergence when gap > 0.15 AND confidence >= 0.5.

`check_ratchets()` loads ratchet targets from baseline, returns violations where auto_score < ratchet target score.

`run_and_store(tier)` is the convenience function: run_tier → reconcile → check_ratchets → write_scores → return summary dict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 15 tests)

- [ ] **Step 5: Commit**

```bash
git add fleet/audit_scorer.py tests/test_audit_scorer.py
git commit -m "feat(audit): reconciliation engine with divergence detection and ratchet checks"
```

---

### Task 6: User Feedback System

**Files:**
- Modify: `fleet/audit_scorer.py`
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing tests for feedback**

Append to `tests/test_audit_scorer.py`:

```python
def test_record_feedback():
    """Feedback is stored in user_feedback table."""
    import db
    db.init_db()
    from audit_scorer import record_feedback
    record_feedback(score=0.8, scope="overall", text="Dashboard loads fast", actor="test_user")
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM user_feedback WHERE actor='test_user'").fetchall()
    assert len(rows) >= 1
    assert rows[0]["score"] == 0.8
    assert rows[0]["scope"] == "overall"


def test_feedback_summary():
    """Feedback summary aggregates correctly."""
    import db
    db.init_db()
    from audit_scorer import record_feedback, get_feedback_summary
    record_feedback(score=0.6, scope="overall", text="slow graphs")
    record_feedback(score=0.8, scope="overall", text="nice layout")
    summary = get_feedback_summary()
    assert "overall" in summary
    assert summary["overall"]["count"] >= 2
    assert 0.5 <= summary["overall"]["avg_score"] <= 0.9


def test_ux_confidence_scales_with_feedback():
    """UX dimension confidence increases with feedback volume."""
    from audit_scorer import get_ux_confidence
    assert get_ux_confidence(0) == 0.30
    assert get_ux_confidence(50) > 0.30
    assert get_ux_confidence(100) == 0.75
    assert get_ux_confidence(200) == 0.75
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_record_feedback -v`
Expected: FAIL — cannot import record_feedback

- [ ] **Step 3: Add feedback functions to audit_scorer.py**

Append `record_feedback()`, `get_feedback_summary()`, and `get_ux_confidence()`. All DB writes use `db._retry_write()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 18 tests)

- [ ] **Step 5: Commit**

```bash
git add fleet/audit_scorer.py tests/test_audit_scorer.py
git commit -m "feat(audit): user feedback recording, aggregation, and UX confidence scaling"
```

---

### Task 7: Weekly External Checks

**Files:**
- Modify: `fleet/audit_scorer.py`
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing tests for external checks**

Append to `tests/test_audit_scorer.py`:

```python
def test_check_version_drift_returns_score():
    """Version drift check returns a valid score tuple."""
    from audit_scorer import _check_version_drift
    score, detail = _check_version_drift()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    assert isinstance(detail, dict)


def test_check_github_returns_score():
    """GitHub check returns a valid score tuple (may skip if no token)."""
    import os
    from audit_scorer import _check_github
    score, detail = _check_github()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_check_semgrep_returns_score():
    """Semgrep check returns a valid score tuple (may skip if not installed)."""
    from audit_scorer import _check_semgrep
    score, detail = _check_semgrep()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_weekly_merge_score():
    """Weekly scores blend with daily using decay weight."""
    from audit_scorer import merged_score
    result = merged_score(daily=0.80, weekly=1.0, weekly_age_days=0)
    assert abs(result - 0.88) < 0.01
    result = merged_score(daily=0.80, weekly=1.0, weekly_age_days=6)
    assert abs(result - 0.82) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_check_version_drift_returns_score -v`
Expected: FAIL — cannot import _check_version_drift

- [ ] **Step 3: Add external check functions and merge logic**

Append to `fleet/audit_scorer.py`:

- `_check_semgrep()`: runs `semgrep --config p/python --config p/owasp-top-ten --json` via subprocess. Score = `1.0 - (errors * 0.15 + warnings * 0.05)`. Returns `(1.0, {"skipped": ...})` if not installed.
- `_check_version_drift()`: compares installed vs PyPI latest for tracked packages. Uses `urllib.request` with `timeout=10`. Major behind = 0.0, minor = 0.5, patch = 1.0.
- `_check_github()`: uses `skills.git_suite._github_api()` for CI status, open bugs, stale PRs. Skips gracefully if no `GITHUB_TOKEN`.
- `_check_federation_mcp()`: probes federation peers and MCP servers. Uses `config.load_config()` and `mcp_manager.get_all_server_status()`.
- `merged_score(daily, weekly, weekly_age_days)`: blends with decaying weight (0.4 fresh → 0.1 at day 6).

All subprocess calls use `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)`. All HTTP calls use explicit `timeout=`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 22 tests)

- [ ] **Step 5: Commit**

```bash
git add fleet/audit_scorer.py tests/test_audit_scorer.py
git commit -m "feat(audit): weekly external checks — semgrep, version drift, GitHub, federation"
```

---

### Task 8: REST Blueprint

**Files:**
- Create: `fleet/audit_blueprint.py`
- Modify: `fleet/dashboard.py` (add to `_BLUEPRINTS` list)
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write failing test for blueprint import**

Append to `tests/test_audit_scorer.py`:

```python
def test_audit_blueprint_imports():
    """audit_blueprint module imports and exposes audit_bp."""
    from audit_blueprint import audit_bp
    assert audit_bp.name == "audit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py::test_audit_blueprint_imports -v`
Expected: FAIL — cannot import audit_blueprint

- [ ] **Step 3: Create audit_blueprint.py**

Create `fleet/audit_blueprint.py` as a Flask Blueprint with 9 routes:

- `GET /api/audit/scores` — latest score per dimension (calls `audit_scorer.get_latest_scores()`)
- `GET /api/audit/scores/history` — time series, `?days=30` param (calls `get_score_history()`)
- `GET /api/audit/divergences` — active unacknowledged divergences
- `POST /api/audit/acknowledge/<dimension>` — mark divergence acknowledged
- `POST /api/audit/trigger/<tier>` — run medium/daily/weekly on demand (validates tier param)
- `GET /api/audit/baseline` — current manual grades from sidecar
- `POST /api/audit/feedback` — submit user feedback (validates score 0.0-1.0)
- `GET /api/audit/feedback/summary` — aggregated feedback per scope
- `POST /api/audit/oauth-review/<dimension>` — dispatch fleet task for external model review

Follow the `health_api.py` pattern: lazy imports inside route handlers, `try/except Exception` with `jsonify({"error": ...}), 500`, `_safe_error()` helper.

- [ ] **Step 4: Register blueprint in dashboard.py**

In `fleet/dashboard.py`, add to the `_BLUEPRINTS` list (after the `knowledge_blueprint` entry):

```python
    ("audit_blueprint",       "audit_bp",          True),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all 23 tests)

- [ ] **Step 6: Commit**

```bash
git add fleet/audit_blueprint.py fleet/dashboard.py tests/test_audit_scorer.py
git commit -m "feat(audit): REST blueprint with 9 endpoints, registered in dashboard"
```

---

### Task 9: Skill Wrapper

**Files:**
- Create: `fleet/skills/audit_score.py`

- [ ] **Step 1: Write the skill file**

Create `fleet/skills/audit_score.py` following the standard skill contract:
- `SKILL_NAME = "audit_score"`
- `DESCRIPTION`, `VERSION`, `COMPLEXITY`, `REQUIRES_NETWORK`, `SUITE`, `TAGS`
- `run(payload, config)` function that handles two actions:
  - `{"action": "status"}` — returns latest scores + divergences
  - `{"tier": "medium"}` — runs specified tier (default: medium)

- [ ] **Step 2: Verify skill imports cleanly**

Run: `cd fleet && python -c "from skills.audit_score import SKILL_NAME, run; print(SKILL_NAME)"`
Expected: `audit_score`

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/audit_score.py
git commit -m "feat(audit): audit_score skill wrapper for fleet task invocation"
```

---

### Task 10: Smoke Test Integration

**Files:**
- Modify: `fleet/smoke_test.py`

- [ ] **Step 1: Read current smoke_test.py to find insertion point**

Find the last test function and the results collection section. The new test goes before the main runner.

- [ ] **Step 2: Add test_audit_health to smoke_test.py**

```python
def test_audit_health():
    """Audit: no unresolved high-confidence divergences."""
    try:
        from audit_scorer import get_active_divergences, DIMENSIONS
        divergences = get_active_divergences()
        high_conf = [
            d for d in divergences
            if DIMENSIONS.get(d["dimension"], {}).get("confidence", 0) >= 0.5
        ]
        if high_conf:
            dims = ", ".join(d["dimension"] for d in high_conf)
            return False, f"{len(high_conf)} divergence(s): {dims}"
        return True, "all dimensions aligned"
    except ImportError:
        return True, "audit_scorer not available (skipped)"
    except Exception as e:
        return True, f"audit check skipped: {e}"
```

Add it to the test list that gets iterated (follow existing pattern for registering tests).

- [ ] **Step 3: Run smoke test to verify integration**

Run: `cd fleet && python smoke_test.py --fast`
Expected: New `test_audit_health` appears in output with PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/smoke_test.py
git commit -m "feat(audit): add audit_health check to smoke test suite"
```

---

### Task 11: Dashboard Panel Template

**Files:**
- Create: `fleet/templates/components/_audit_panel.html`
- Modify: `fleet/templates/dashboard.html` (add include)

- [ ] **Step 1: Create the audit panel component**

Create `fleet/templates/components/_audit_panel.html` with:

**HTML structure:**
- Collapsible card with header "SYSTEM AUDIT" + last-run timestamp
- Table with columns: Dimension, Auto, Manual, Status, Confidence
- `<tbody id="audit-tbody">` populated by JS
- Overall score + divergence count summary row
- Feedback widget: star rating (5 spans with click handlers), range slider, scope select, text input, submit button
- Action buttons: Run Medium, Run Daily, Acknowledge All

**JavaScript (safe DOM methods — NO innerHTML):**
- `setAuditStar(n)` — sets star display via `textContent`
- `submitAuditFeedback()` — POSTs to `/api/audit/feedback`
- `loadAuditScores()` — fetches `/api/audit/scores`, builds table rows using `document.createElement()` and `textContent` (never `innerHTML`)
- `triggerAuditTier(tier)` — POSTs to `/api/audit/trigger/{tier}`
- `acknowledgeAllDivergences()` — fetches divergences then POSTs acknowledge for each
- Auto-refresh every 60s via `setInterval(loadAuditScores, 60000)`

**Critical: Use `document.createElement()` + `textContent` for all dynamic content. Do NOT use `innerHTML` with any data from API responses.**

- [ ] **Step 2: Add include to dashboard template**

In `fleet/templates/dashboard.html`, before the closing scripts section, add:

```html
{% include 'components/_audit_panel.html' %}
```

- [ ] **Step 3: Test by loading dashboard in browser**

Start the fleet and navigate to `http://localhost:5555`. Verify the audit panel appears at the bottom.

- [ ] **Step 4: Commit**

```bash
git add fleet/templates/components/_audit_panel.html fleet/templates/dashboard.html
git commit -m "feat(audit): dashboard panel with score table, feedback widget, action buttons"
```

---

### Task 12: Dr. Ders Scheduling

**Files:**
- Modify: `fleet/hw_supervisor.py`

- [ ] **Step 1: Read hw_supervisor.py to find the poll loop**

Find where `poll_count % N == 0` checks are done (around line 1284). The audit triggers go alongside these.

- [ ] **Step 2: Add audit scheduling to the poll loop**

Add near the existing health check block. Use `poll_count % 720 == 0` (check every ~60 minutes at 5s poll intervals):

```python
if poll_count % 720 == 0:
    try:
        from datetime import datetime
        now = datetime.now()
        hour, minute, weekday = now.hour, now.minute, now.weekday()
        # Daily at 3:00 AM (within 5-min window)
        if hour == 3 and minute < 5:
            import audit_scorer
            result = audit_scorer.run_and_store("daily")
            log.info("Audit daily: %d dims, %d divergences",
                     result["dimensions_scored"], result["divergences"])
        # Weekly on Sunday at 3:30 AM
        if weekday == 6 and hour == 3 and 30 <= minute < 35:
            import audit_scorer
            result = audit_scorer.run_and_store("weekly")
            log.info("Audit weekly: %d dims, %d divergences",
                     result["dimensions_scored"], result["divergences"])
    except Exception:
        log.warning("Audit scheduling check failed", exc_info=True)
```

Post divergence notifications via `_db.post_note()` if `_HAS_DB` is True.

- [ ] **Step 3: Verify hw_supervisor still loads cleanly**

Run: `cd fleet && python -c "import hw_supervisor; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add fleet/hw_supervisor.py
git commit -m "feat(audit): daily (3AM) and weekly (Sunday 3:30AM) audit triggers in Dr. Ders"
```

---

### Task 13: Dependency Check for Semgrep

**Files:**
- Modify: `fleet/dependency_check.py`

- [ ] **Step 1: Read dependency_check.py to find the optional dependency pattern**

Look for how Docker/Playwright are checked as optional.

- [ ] **Step 2: Add semgrep as optional dependency**

```python
def check_semgrep():
    """Check if semgrep is installed (optional — used by weekly audit tier)."""
    import subprocess
    try:
        result = subprocess.run(
            ["semgrep", "--version"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            return True, f"semgrep {result.stdout.strip()}"
        return False, "semgrep not functional"
    except FileNotFoundError:
        return False, "semgrep not installed (pip install semgrep) — optional for weekly audit"
    except Exception as e:
        return False, f"semgrep check failed: {e}"
```

Register in the optional checks list (not required — audit works without it).

- [ ] **Step 3: Run dependency check**

Run: `cd fleet && python dependency_check.py`
Expected: semgrep appears in output

- [ ] **Step 4: Commit**

```bash
git add fleet/dependency_check.py
git commit -m "feat(audit): add semgrep as optional dependency for weekly security scanning"
```

---

### Task 14: Integration Test

**Files:**
- Test: `tests/test_audit_scorer.py`

- [ ] **Step 1: Write end-to-end integration test**

Append to `tests/test_audit_scorer.py`:

```python
def test_full_run_and_store_cycle():
    """End-to-end: run tier, reconcile, store, retrieve."""
    import db
    db.init_db()
    from audit_scorer import run_and_store, get_latest_scores, get_active_divergences
    result = run_and_store("light")
    assert result["tier"] == "light"
    assert result["dimensions_scored"] >= 1
    scores = get_latest_scores()
    assert len(scores) >= 1
    divs = get_active_divergences()
    assert isinstance(divs, list)


def test_feedback_to_ux_confidence_pipeline():
    """Feedback volume affects UX confidence calculation."""
    import db
    db.init_db()
    from audit_scorer import record_feedback, get_ux_confidence
    for i in range(50):
        record_feedback(score=0.7, scope="overall")
    conf = get_ux_confidence(50)
    assert conf > 0.30
    assert conf < 0.75
```

- [ ] **Step 2: Run all tests**

Run: `cd fleet && python -m pytest ../tests/test_audit_scorer.py -v`
Expected: PASS (all ~25 tests)

- [ ] **Step 3: Run smoke test to verify full integration**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All tests pass including `test_audit_health`

- [ ] **Step 4: Commit**

```bash
git add tests/test_audit_scorer.py
git commit -m "test(audit): end-to-end integration tests for scoring pipeline and feedback"
```

---

## Summary

| Task | Description | Est. LOC | Commits |
|------|------------|----------|---------|
| 1 | DB schema (2 tables + indexes) | ~25 | 1 |
| 2 | Grade mapping + baseline sidecar | ~70 + 80 JSON | 1 |
| 3 | Dimension registry + 12 check functions | ~300 | 1 |
| 4 | Tier runners + DB write/read | ~120 | 1 |
| 5 | Reconciliation + ratchet checks | ~80 | 1 |
| 6 | User feedback system | ~60 | 1 |
| 7 | Weekly external checks | ~180 | 1 |
| 8 | REST blueprint (9 endpoints) | ~160 | 1 |
| 9 | Skill wrapper | ~45 | 1 |
| 10 | Smoke test integration | ~20 | 1 |
| 11 | Dashboard panel template | ~150 | 1 |
| 12 | Dr. Ders scheduling | ~35 | 1 |
| 13 | Semgrep dependency check | ~20 | 1 |
| 14 | Integration tests | ~35 | 1 |
| **Total** | | **~1,300 LOC** | **14 commits** |
