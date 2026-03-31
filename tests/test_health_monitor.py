"""Unit tests for fleet/health_monitor.py — circuit breakers and agent health."""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import unittest.mock as mock
import pytest

import health_monitor as hm


# ── Helpers ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_breakers():
    """Clear circuit breaker state between tests."""
    with hm._breaker_lock:
        hm._breakers.clear()
    yield
    with hm._breaker_lock:
        hm._breakers.clear()


# ── 1. Circuit breaker trips after configured failures ───────────────────

def test_circuit_breaker_trips_after_threshold():
    """Breaker should open after reaching the configured failure threshold."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 3,
        "circuit_breaker_window": 300,
    }.get(k, fb)):
        assert not hm.circuit_breaker_is_open("bad_skill")

        hm.circuit_breaker_record_failure("bad_skill", "err1")
        hm.circuit_breaker_record_failure("bad_skill", "err2")
        assert not hm.circuit_breaker_is_open("bad_skill")

        hm.circuit_breaker_record_failure("bad_skill", "err3")
        assert hm.circuit_breaker_is_open("bad_skill")


# ── 2. Circuit breaker resets after window expires ───────────────────────

def test_circuit_breaker_resets_after_window():
    """Once tripped, the breaker should reset after the window elapses."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 2,
        "circuit_breaker_window": 10,
    }.get(k, fb)):
        hm.circuit_breaker_record_failure("flaky", "e1")
        hm.circuit_breaker_record_failure("flaky", "e2")
        assert hm.circuit_breaker_is_open("flaky")

        # Simulate time passing beyond the window
        with hm._breaker_lock:
            hm._breakers["flaky"]["tripped_at"] = time.time() - 11

        assert not hm.circuit_breaker_is_open("flaky")


# ── 3. Circuit breaker respects time window (old failures expire) ────────

def test_circuit_breaker_respects_time_window():
    """Failures outside the window should not count toward the threshold."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 3,
        "circuit_breaker_window": 60,
    }.get(k, fb)):
        # Record 2 failures that are already "old"
        old_time = time.time() - 120
        with hm._breaker_lock:
            hm._breakers["aging"] = {
                "failures": [(old_time, "old1"), (old_time, "old2")],
                "tripped_at": None,
            }

        # These old ones should not trip the breaker
        hm.circuit_breaker_record_failure("aging", "recent1")
        assert not hm.circuit_breaker_is_open("aging")

        # A second recent failure (total recent = 2) still under threshold 3
        hm.circuit_breaker_record_failure("aging", "recent2")
        assert not hm.circuit_breaker_is_open("aging")

        # Third recent failure trips it
        hm.circuit_breaker_record_failure("aging", "recent3")
        assert hm.circuit_breaker_is_open("aging")


# ── 4. get_circuit_breaker_status() returns expected structure ───────────

def test_get_circuit_breaker_status_structure():
    """Status list should have the expected keys and values."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 2,
        "circuit_breaker_window": 300,
    }.get(k, fb)):
        hm.circuit_breaker_record_failure("stat_skill", "boom")
        hm.circuit_breaker_record_failure("stat_skill", "bang")
        # Trip it
        hm.circuit_breaker_is_open("stat_skill")

        statuses = hm.get_circuit_breaker_status()
        assert len(statuses) == 1
        s = statuses[0]
        assert s["skill"] == "stat_skill"
        assert s["tripped"] is True
        assert s["tripped_at"] is not None
        assert s["recent_failures"] >= 2
        assert s["last_error"] == "bang"
        assert isinstance(s["cooldown_remaining"], int)
        assert s["cooldown_remaining"] > 0


# ── 5. check_agent_health() handles unknown agent ───────────────────────

def test_check_agent_health_unknown_agent(tmp_db):
    """Unknown agent should return healthy=False with 'agent_not_found'."""
    result = hm.check_agent_health("nonexistent_agent_xyz")
    assert result["healthy"] is False
    assert "agent_not_found" in result["issues"]


# ── 6. check_agent_health() for active agent ────────────────────────────

def test_check_agent_health_active_agent(tmp_db):
    """An active agent with a recent heartbeat should be healthy."""
    import db
    from datetime import datetime

    def _seed():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents (name, role, status, last_heartbeat, current_task_id, pid) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("test_worker", "coder", "BUSY", datetime.utcnow().isoformat(), 42, None),
            )
    db._retry_write(_seed)

    result = hm.check_agent_health("test_worker")
    assert result["agent"] == "test_worker"
    assert result["healthy"] is True
    assert result["active_task"] == 42
    assert result["idle_secs"] < 10  # just inserted, should be ~0


# ── 7. Stale task detection via recover_stale_tasks interval gate ────────

def test_stale_task_recovery_interval_gate():
    """HealthMonitor._recover_stale_tasks should respect the interval gate."""
    monitor = hm.HealthMonitor.__new__(hm.HealthMonitor)
    monitor._last_stale_check = time.time()

    with mock.patch("db.recover_stale_tasks") as mock_recover:
        # Called immediately — should skip (interval not elapsed)
        monitor._recover_stale_tasks(time.time())
        mock_recover.assert_not_called()

        # Simulate enough time passing
        monitor._last_stale_check = time.time() - hm.STALE_TASK_RECOVERY_INTERVAL - 1
        mock_recover.return_value = []
        monitor._recover_stale_tasks(time.time())
        mock_recover.assert_called_once_with(hm.STALE_TASK_TIMEOUT)
