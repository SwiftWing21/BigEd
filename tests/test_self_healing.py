"""Unit tests for fleet/self_healing.py — compatibility shim over health_monitor.py.

self_healing.py is a pure re-export shim. These tests verify the public API
it exposes is present and functional by testing through the shim.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import unittest.mock as mock
import pytest

import self_healing as sh
import health_monitor as hm


# ── Fixture: reset circuit breakers between tests ────────────────────────────

@pytest.fixture(autouse=True)
def _reset_breakers():
    with hm._breaker_lock:
        hm._breakers.clear()
    yield
    with hm._breaker_lock:
        hm._breakers.clear()


# ── 1. Public API exports ────────────────────────────────────────────────────

def test_public_api_present():
    """All expected symbols must be importable from self_healing."""
    expected = [
        "check_agent_health",
        "recover_agent",
        "retry_failed_task",
        "circuit_breaker_record_failure",
        "circuit_breaker_is_open",
        "get_circuit_breaker_status",
        "run_health_sweep",
        "detect_skill_regression",
        "get_rollback_candidates",
        "rollback_skill",
        "get_agent_health_summary",
        "get_skill_health_summary",
        "get_recovery_log",
    ]
    for name in expected:
        assert hasattr(sh, name), f"self_healing missing: {name}"


# ── 2. Circuit breaker trips (via shim) ──────────────────────────────────────

def test_circuit_breaker_trips():
    """Breaker should open after threshold failures (via self_healing shim)."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 3,
        "circuit_breaker_window": 300,
    }.get(k, fb)):
        assert not sh.circuit_breaker_is_open("shim_skill")

        sh.circuit_breaker_record_failure("shim_skill", "err1")
        sh.circuit_breaker_record_failure("shim_skill", "err2")
        assert not sh.circuit_breaker_is_open("shim_skill")

        sh.circuit_breaker_record_failure("shim_skill", "err3")
        assert sh.circuit_breaker_is_open("shim_skill")


# ── 3. Circuit breaker resets after window ────────────────────────────────────

def test_circuit_breaker_resets_after_window():
    """Breaker auto-resets after cooldown window elapses."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 2,
        "circuit_breaker_window": 10,
    }.get(k, fb)):
        sh.circuit_breaker_record_failure("cooldown_skill", "e1")
        sh.circuit_breaker_record_failure("cooldown_skill", "e2")
        assert sh.circuit_breaker_is_open("cooldown_skill")

        # Simulate window expiry
        with hm._breaker_lock:
            hm._breakers["cooldown_skill"]["tripped_at"] = time.time() - 11

        assert not sh.circuit_breaker_is_open("cooldown_skill")


# ── 4. get_circuit_breaker_status returns proper structure ────────────────────

def test_circuit_breaker_status_structure():
    """Status list returns correct keys after a trip."""
    with mock.patch.object(hm, "_default", side_effect=lambda k, fb: {
        "circuit_breaker_threshold": 2,
        "circuit_breaker_window": 300,
    }.get(k, fb)):
        sh.circuit_breaker_record_failure("status_skill", "boom")
        sh.circuit_breaker_record_failure("status_skill", "bang")
        sh.circuit_breaker_is_open("status_skill")  # trigger trip evaluation

        statuses = sh.get_circuit_breaker_status()
        assert len(statuses) >= 1
        s = next(x for x in statuses if x["skill"] == "status_skill")
        assert s["tripped"] is True
        assert s["recent_failures"] >= 2


# ── 5. check_agent_health for unknown agent ───────────────────────────────────

def test_check_agent_health_unknown(tmp_db):
    """Unknown agent returns healthy=False."""
    result = sh.check_agent_health("nonexistent_999")
    assert result["healthy"] is False
    assert "agent_not_found" in result["issues"]


# ── 6. check_agent_health for active agent ───────────────────────────────────

def test_check_agent_health_active(tmp_db):
    """Active agent with recent heartbeat returns healthy=True."""
    import db
    from datetime import datetime

    def _seed():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents "
                "(name, role, status, last_heartbeat, current_task_id, pid) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("shim_worker", "coder", "IDLE",
                 datetime.utcnow().isoformat(), None, None),
            )
    db._retry_write(_seed)

    result = sh.check_agent_health("shim_worker")
    assert result["healthy"] is True


# ── 7. get_agent_health_summary returns dict ─────────────────────────────────

def test_get_agent_health_summary(tmp_db):
    """get_agent_health_summary returns a list (possibly empty but not None)."""
    result = sh.get_agent_health_summary()
    assert isinstance(result, list)


# ── 8. get_skill_health_summary returns dict ─────────────────────────────────

def test_get_skill_health_summary():
    """get_skill_health_summary returns a list."""
    result = sh.get_skill_health_summary()
    assert isinstance(result, list)


# ── 9. get_recovery_log returns list ─────────────────────────────────────────

def test_get_recovery_log(tmp_db):
    """get_recovery_log returns a list (may be empty)."""
    result = sh.get_recovery_log()
    assert isinstance(result, list)
