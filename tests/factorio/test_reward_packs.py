"""Tests for pack/stamp completion bonuses in the reward function."""
import pytest
from factorio.action_space import ActionType
from factorio.state_parser import GameState
from factorio.reward import RewardComputer


def _make_state(**kwargs):
    defaults = dict(tick=0, player_position={"x": 0, "y": 0},
                    inventory={}, entities=[], resources=[],
                    research_name="", research_progress=0.0)
    defaults.update(kwargs)
    return GameState(**defaults)


def test_pack_completion_bonus():
    """Pack completion with a non-STAMP action yields +1.0 bonus."""
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    baseline = rc.compute(s1, s2, action_success=True, lesson_passed=False,
                          phase_complete=False, action_type=9)
    rc2 = RewardComputer(phase=1)
    reward = rc2.compute(s1, s2, action_success=True, lesson_passed=False,
                         phase_complete=False, action_type=9, pack_completed=True)
    assert reward > baseline
    assert reward - baseline == pytest.approx(1.0, abs=0.01)


def test_stamp_completion_bonus():
    """Pack completion with STAMP action (11) yields +2.0 bonus (higher than pack)."""
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    baseline = rc.compute(s1, s2, action_success=True, lesson_passed=False,
                          phase_complete=False, action_type=ActionType.STAMP.value)
    rc2 = RewardComputer(phase=1)
    reward = rc2.compute(s1, s2, action_success=True, lesson_passed=False,
                         phase_complete=False, action_type=ActionType.STAMP.value, pack_completed=True)
    assert reward > baseline
    assert reward - baseline == pytest.approx(2.0, abs=0.01)
    # Stamp bonus should be higher than pack bonus
    rc3 = RewardComputer(phase=1)
    pack_reward = rc3.compute(s1, s2, action_success=True, lesson_passed=False,
                              phase_complete=False, action_type=9, pack_completed=True)
    assert reward > pack_reward


def test_pack_abort_penalty():
    """Pack abort yields -0.5 penalty component."""
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    baseline = rc.compute(s1, s2, action_success=True, lesson_passed=False,
                          phase_complete=False, action_type=0)
    rc2 = RewardComputer(phase=1)
    reward = rc2.compute(s1, s2, action_success=True, lesson_passed=False,
                         phase_complete=False, action_type=0, pack_aborted=True)
    assert reward < baseline
    assert baseline - reward == pytest.approx(0.5, abs=0.01)
