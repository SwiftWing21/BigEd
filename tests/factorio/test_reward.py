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
    # Failed action: no success bonus, so time penalty + failure penalty dominate
    reward = rc.compute(s1, s2, action_success=False, lesson_passed=False, phase_complete=False)
    assert reward < 0  # time penalty + failed action penalty


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


def test_consecutive_move_penalty():
    """Agents that keep walking without doing anything get penalized."""
    rc = RewardComputer(phase=1)
    s1 = _make_state()
    s2 = _make_state()
    # First 2 moves: no consecutive penalty
    r1 = rc.compute(s1, s2, True, False, False, action_type=3)  # MOVE=3
    r2 = rc.compute(s1, s2, True, False, False, action_type=3)
    # 3rd+ consecutive move: penalty kicks in
    r3 = rc.compute(s1, s2, True, False, False, action_type=3)
    assert r3 < r2  # 3rd move is penalized more
    # Non-move resets the counter
    r_place = rc.compute(s1, s2, True, False, False, action_type=0)  # PLACE=0
    r_after = rc.compute(s1, s2, True, False, False, action_type=3)
    assert r_after > r3  # counter was reset, so this move is cheaper than 3rd consecutive


def test_reset_normalizer():
    rc = RewardComputer(phase=1)
    rc.compute(_make_state(), _make_state(), True, False, False)
    rc.compute(_make_state(), _make_state(), True, False, False)
    rc.reset_normalizer()
    reward = rc.compute(_make_state(), _make_state(), True, False, False)
    assert isinstance(reward, float)


def test_ore_proximity_bonus_lesson2():
    """Agent near ore in lesson 2 should get a proximity bonus."""
    from factorio.reward import RewardComputer
    from factorio.state_parser import GameState

    rc = RewardComputer(phase=1)
    prev = GameState(player_position={"x": 0, "y": 0}, inventory={}, entities=[])
    curr = GameState(player_position={"x": 5, "y": 5}, inventory={}, entities=[])

    r_base = rc.compute(prev, curr, action_success=True, lesson_passed=False,
                        phase_complete=False, action_type=0, lesson_index=3)

    rc2 = RewardComputer(phase=1)
    r_near = rc2.compute(prev, curr, action_success=True, lesson_passed=False,
                         phase_complete=False, action_type=0,
                         lesson_index=2, near_ore=True)

    assert r_near > r_base, "Near-ore bonus should increase reward in lesson 2"


# -------------------------------------------------------------------
# Configurable reward constants (Task 2)
# -------------------------------------------------------------------

def test_reward_constants_from_config():
    """RewardComputer reads constants from config when provided."""
    rc = RewardComputer(phase=1, reward_config={
        "time_penalty": -0.05,
        "lesson_pass_bonus": 5.0,
    })
    assert rc._time_penalty == -0.05
    assert rc._lesson_pass_bonus == 5.0


def test_reward_defaults_without_config():
    """RewardComputer uses hardcoded defaults when no config provided."""
    rc = RewardComputer(phase=1)
    assert rc._time_penalty == -0.001
    assert rc._lesson_pass_bonus == 2.0
    assert rc._failed_action_penalty == -0.02
    assert rc._successful_action_bonus == 0.05


def test_reward_partial_config():
    """Partial config overrides only specified keys."""
    rc = RewardComputer(phase=1, reward_config={"time_penalty": -0.1})
    assert rc._time_penalty == -0.1
    assert rc._lesson_pass_bonus == 2.0  # uses default
