"""Tests for the adaptive tick governor."""
from factorio.tick_governor import TickGovernor


def test_initial_delay_is_max():
    """Before any UPS samples, governor returns max delay (conservative)."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=120)
    # No samples yet — should default to max delay (conservative)
    assert gov.get_delay_ms() == 1000


def test_healthy_ups_returns_min_delay():
    """When observed UPS matches target, delay should be at minimum."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=120)
    # Simulate 1 second of perfect UPS: 120 ticks in 1.0s
    gov.record_tick(game_tick=0, wall_time=0.0)
    gov.record_tick(game_tick=120, wall_time=1.0)
    delay = gov.get_delay_ms()
    assert delay == 200, f"Expected 200ms at full UPS, got {delay}"


def test_half_ups_returns_midpoint_delay():
    """When UPS is 50% of target, delay should be roughly midpoint."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=120)
    gov.record_tick(game_tick=0, wall_time=0.0)
    gov.record_tick(game_tick=60, wall_time=1.0)  # 60 ticks/s = 50%
    delay = gov.get_delay_ms()
    assert 550 <= delay <= 650, f"Expected ~600ms at 50% UPS, got {delay}"


def test_zero_ups_returns_max_delay():
    """When game is stalled (0 tick progress), use max delay."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=120)
    gov.record_tick(game_tick=100, wall_time=0.0)
    gov.record_tick(game_tick=100, wall_time=1.0)  # no tick progress
    assert gov.get_delay_ms() == 1000


def test_teacher_mode_override():
    """When teacher mode is active, governor returns teacher delay."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=120,
                       teacher_delay_ms=750)
    gov.record_tick(game_tick=0, wall_time=0.0)
    gov.record_tick(game_tick=120, wall_time=1.0)
    # Normal mode: should be 200
    assert gov.get_delay_ms() == 200
    # Teacher mode: overrides to 750
    gov.set_teacher_mode(True)
    assert gov.get_delay_ms() == 750
    gov.set_teacher_mode(False)
    assert gov.get_delay_ms() == 200


def test_sliding_window_discards_old_samples():
    """Only recent samples affect UPS calculation."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=120,
                       window_size=3)
    # Old: bad UPS
    gov.record_tick(game_tick=0, wall_time=0.0)
    gov.record_tick(game_tick=10, wall_time=1.0)
    # Recent: perfect UPS
    gov.record_tick(game_tick=130, wall_time=2.0)
    gov.record_tick(game_tick=250, wall_time=3.0)
    # Window of 3 keeps last 3 samples: (10,1.0), (130,2.0), (250,3.0)
    # UPS = (250-10)/(3.0-1.0) = 120 → health=1.0 → min delay
    delay = gov.get_delay_ms()
    assert delay <= 400, f"Expected low delay with recent healthy UPS, got {delay}"


def test_observed_ups_property():
    """observed_ups exposes the current measurement for logging."""
    gov = TickGovernor(delay_min_ms=200, delay_max_ms=1000, target_ups=60)
    assert gov.observed_ups == 0.0
    gov.record_tick(game_tick=0, wall_time=0.0)
    gov.record_tick(game_tick=60, wall_time=1.0)
    assert abs(gov.observed_ups - 60.0) < 1.0
