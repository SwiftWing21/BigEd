"""Phase-gated experiment scoring for Factorio autoresearch."""
import logging

log = logging.getLogger("biged.factorio.scorer")


def compute_score(phase: int, lessons_passed: int,
                  total_actions: int, total_failures: int,
                  throughput: float) -> float:
    """Compute phase-gated experiment score.

    Phase 1: lessons_passed only
    Phase 2: + action efficiency
    Phase 3: + failure penalty
    Phase 4: + throughput bonus

    All metrics are per-budget-window (aggregated across all plans in run).
    """
    score = float(lessons_passed)

    if phase >= 2 and total_actions > 0:
        score += 1.0 / total_actions

    if phase >= 3 and total_actions > 0:
        failure_rate = total_failures / total_actions
        score -= 0.1 * failure_rate

    if phase >= 4:
        score += throughput

    return score
