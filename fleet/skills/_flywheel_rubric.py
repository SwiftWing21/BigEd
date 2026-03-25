"""Quality Flywheel — rubric definitions and grade conversion."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
PROJECT_ROOT = FLEET_DIR.parent
FLYWHEEL_DIR = FLEET_DIR / "knowledge" / "flywheel"
DRAFTS_DIR = FLYWHEEL_DIR / "drafts"

# ── Rubric: 10 dimensions ──────────────────────────────────────────────────

RUBRIC = {
    # Part A: Context Quality (grade the docs)
    "completeness": {
        "weight": 0.15,
        "description": "Does CLAUDE.md cover conventions, gotchas, structure, workflows?",
        "part": "context",
    },
    "consistency": {
        "weight": 0.15,
        "description": "Do docs agree with each other and the actual code?",
        "part": "context",
    },
    "actionability": {
        "weight": 0.20,
        "description": "Are instructions specific enough for an AI to follow?",
        "part": "context",
    },
    "coverage": {
        "weight": 0.10,
        "description": "What % of the codebase has relevant context?",
        "part": "context",
    },
    "freshness": {
        "weight": 0.10,
        "description": "Are docs stale vs recent commits?",
        "part": "context",
    },
    # Part B: Output Quality (grade what the AI produces)
    "accuracy": {
        "weight": 0.10,
        "description": "Does the AI follow stated conventions?",
        "part": "output",
    },
    "first_attempt_rate": {
        "weight": 0.08,
        "description": "How often does AI get it right without correction?",
        "part": "output",
    },
    "regression_rate": {
        "weight": 0.05,
        "description": "Does quality degrade over sessions?",
        "part": "output",
    },
    "context_utilization": {
        "weight": 0.04,
        "description": "Does the AI actually reference the docs?",
        "part": "output",
    },
    "feedback_incorporation": {
        "weight": 0.03,
        "description": "Do corrections stick across sessions?",
        "part": "output",
    },
}


def score_to_grade(score: float, s_tier_eligible: bool = False) -> str:
    if s_tier_eligible and score >= 95:
        return "S"
    if score >= 90: return "A"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    if score >= 65: return "C+"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"
