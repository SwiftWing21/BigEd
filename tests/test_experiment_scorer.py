"""Tests for phase-gated experiment scoring."""
from factorio.experiment_scorer import compute_score


def test_phase1_pure_lessons():
    score = compute_score(phase=1, lessons_passed=3, total_actions=100,
                          total_failures=5, throughput=0.0)
    assert score == 3.0


def test_phase1_zero_lessons():
    score = compute_score(phase=1, lessons_passed=0, total_actions=50,
                          total_failures=10, throughput=0.0)
    assert score == 0.0


def test_phase2_includes_efficiency():
    score = compute_score(phase=2, lessons_passed=4, total_actions=20,
                          total_failures=0, throughput=0.0)
    # 4 + (1/20) = 4.05
    assert abs(score - 4.05) < 0.001


def test_phase3_penalizes_failures():
    score = compute_score(phase=3, lessons_passed=4, total_actions=20,
                          total_failures=4, throughput=0.0)
    # 4 + (1/20) - 0.1*(4/20) = 4 + 0.05 - 0.02 = 4.03
    assert abs(score - 4.03) < 0.001


def test_phase4_adds_throughput():
    score = compute_score(phase=4, lessons_passed=4, total_actions=20,
                          total_failures=2, throughput=0.5)
    # 4 + (1/20) - 0.1*(2/20) + 0.5 = 4 + 0.05 - 0.01 + 0.5 = 4.54
    assert abs(score - 4.54) < 0.001


def test_zero_actions_no_division_error():
    score = compute_score(phase=2, lessons_passed=1, total_actions=0,
                          total_failures=0, throughput=0.0)
    assert score == 1.0
