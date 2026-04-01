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

_SCORE_THRESHOLDS = sorted(GRADE_TO_SCORE.items(), key=lambda x: x[1], reverse=True)


def grade_to_score(grade: str) -> float:
    """Convert a letter grade to its numeric score (0.0-1.0)."""
    return GRADE_TO_SCORE.get(grade, 0.0)


def score_to_grade(score: float) -> str:
    """Convert a numeric score to the nearest letter grade.

    Uses a half-step tolerance: each grade covers scores from its midpoint
    with the grade below up to (but not including) the midpoint with the
    grade above.  Tolerance of 0.035 keeps the rounding intuitive across
    the full scale (S=1.0 needs score>=0.965, A+>=0.915, A>=0.865, …).
    """
    for grade, threshold in _SCORE_THRESHOLDS:
        if score >= threshold - 0.035:
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
