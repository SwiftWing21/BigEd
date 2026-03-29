"""Tests for the phase-gated reward function."""
import pytest
from factorio.state_parser import GameState
from factorio.reward import RewardComputer


def _make_state(**kwargs):
    defaults = dict(tick=0, player_position={"x": 0, "y": 0},
                    inventory={}, entities=[], resources=[],
                    research_name="", research_progress=0.0)
    defaults.update(kwargs)
    return GameState(**defaults)


def test_time_penalty():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    assert reward < 0  # time penalty


def test_lesson_passed_reward():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=True, phase_complete=False)
    assert reward >= 1.0


def test_phase_complete_reward():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=True, phase_complete=True)
    assert reward >= 5.0


def test_failed_action_penalty():
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    reward_ok = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    reward_fail = rc.compute(s1, s2, action_success=False, lesson_passed=False, phase_complete=False)
    assert reward_fail < reward_ok


def test_new_item_exploration_bonus():
    rc = RewardComputer(phase=1)
    s1 = _make_state(inventory={})
    s2 = _make_state(inventory={"iron-gear-wheel": 5})
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    assert reward > -0.01


def test_phase2_production_bonus():
    rc = RewardComputer(phase=2)
    s1 = _make_state(inventory={"iron-plate": 10})
    s2 = _make_state(inventory={"iron-plate": 20})
    reward = rc.compute(s1, s2, action_success=True, lesson_passed=False, phase_complete=False)
    assert reward > -0.01


def test_reset_normalizer():
    rc = RewardComputer(phase=1)
    rc.compute(_make_state(), _make_state(), True, False, False)
    rc.compute(_make_state(), _make_state(), True, False, False)
    rc.reset_normalizer()
    reward = rc.compute(_make_state(), _make_state(), True, False, False)
    assert isinstance(reward, float)
