# _flywheel_core.py — backward compatibility re-exports
# Original 891-line file decomposed into 3 focused modules:
#   _flywheel_rubric.py  — RUBRIC dict, score_to_grade(), path constants
#   _flywheel_grading.py — grade_* functions (context + output quality)
#   _flywheel_audit.py   — run_full_audit(), run_evidence_audit(), format_audit_report(), etc.
from skills._flywheel_rubric import *    # noqa: F401,F403
from skills._flywheel_grading import *   # noqa: F401,F403
from skills._flywheel_audit import *     # noqa: F401,F403
