# tests/test_cadence.py
"""Tests for CadenceController — mode switching + adaptive boost."""
import time
import pytest


def test_fixed_modes():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    cc.set_mode("fast")
    assert cc.get_interval_secs() == 1.0
    cc.set_mode("medium")
    assert cc.get_interval_secs() == 5.0
    cc.set_mode("slow")
    assert cc.get_interval_secs() == 30.0


def test_adaptive_default_is_slow():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    cc.set_mode("adaptive")
    assert cc.get_interval_secs() == 30.0


def test_adaptive_boost_on_event():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    cc.set_mode("adaptive")
    cc.on_event("entity_destroyed")
    assert cc.get_interval_secs() == 1.5


def test_adaptive_ignores_unknown_events():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30,
                           adaptive_events=["research_complete"])
    cc.set_mode("adaptive")
    cc.on_event("entity_destroyed")
    assert cc.get_interval_secs() == 30.0


def test_mode_rejects_invalid():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    with pytest.raises(ValueError):
        cc.set_mode("turbo")
