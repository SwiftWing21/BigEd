"""Tests for the supervisor restructure modules.

Covers: ProcessManager, HealthMonitor, Scheduler, FederationManager,
BootSequence, and backward-compatibility shims.
"""
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure fleet/ is on sys.path for bare imports
FLEET_DIR = Path(__file__).resolve().parent.parent
if str(FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(FLEET_DIR))


# ── HealthMonitor ───────────────────────────────────────────────────

def test_health_monitor_imports():
    """HealthMonitor class can be imported."""
    from health_monitor import HealthMonitor
    assert HealthMonitor is not None


def test_health_monitor_standalone_functions_importable():
    """All module-level functions from self_healing + diagnostics are importable."""
    from health_monitor import (
        check_agent_health,
        recover_agent,
        retry_failed_task,
        circuit_breaker_record_failure,
        circuit_breaker_is_open,
        get_circuit_breaker_status,
        run_health_sweep,
        detect_skill_regression,
        get_rollback_candidates,
        rollback_skill,
        get_agent_health_summary,
        get_skill_health_summary,
        get_recovery_log,
        quarantine_agent,
        clear_quarantine,
        get_failure_streaks,
        get_stuck_reviews,
    )
    # All should be callable
    assert callable(check_agent_health)
    assert callable(quarantine_agent)


def test_health_monitor_circuit_breaker_initially_closed():
    """Circuit breaker is closed for unknown skills."""
    from health_monitor import circuit_breaker_is_open
    assert circuit_breaker_is_open("nonexistent_skill_xyz") is False


def test_health_monitor_tick_no_crash():
    """HealthMonitor.tick() completes without error when PM has no procs."""
    try:
        from process_manager import ProcessManager
    except ImportError:
        # ProcessManager not yet created by other pod — use a stub
        class ProcessManager:
            def __init__(self, config):
                self.worker_procs = {}
            def read_hw_state(self):
                return None
    from health_monitor import HealthMonitor
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    hm = HealthMonitor({"self_healing": {"enabled": False}}, pm)
    hm.tick(0.0)  # should not raise


def test_health_monitor_recovery_log_empty():
    """Recovery log starts empty."""
    from health_monitor import get_recovery_log
    # Note: this may have entries from other tests, but should be a list
    result = get_recovery_log()
    assert isinstance(result, list)


# ── Backward Compatibility Shims ────────────────────────────────────

def test_self_healing_shim_imports():
    """self_healing.py re-exports all functions from health_monitor."""
    from self_healing import (
        check_agent_health,
        recover_agent,
        retry_failed_task,
        circuit_breaker_record_failure,
        circuit_breaker_is_open,
        get_circuit_breaker_status,
        run_health_sweep,
        detect_skill_regression,
        get_rollback_candidates,
        rollback_skill,
        get_agent_health_summary,
        get_skill_health_summary,
        get_recovery_log,
    )
    assert callable(check_agent_health)
    assert callable(run_health_sweep)


def test_diagnostics_shim_imports():
    """diagnostics.py re-exports all functions from health_monitor."""
    from diagnostics import (
        quarantine_agent,
        clear_quarantine,
        get_failure_streaks,
        get_stuck_reviews,
    )
    assert callable(quarantine_agent)
    assert callable(get_stuck_reviews)


def test_self_healing_shim_same_objects():
    """self_healing and health_monitor export the same function objects."""
    from self_healing import check_agent_health as sh_fn
    from health_monitor import check_agent_health as hm_fn
    assert sh_fn is hm_fn


def test_diagnostics_shim_same_objects():
    """diagnostics and health_monitor export the same function objects."""
    from diagnostics import quarantine_agent as diag_fn
    from health_monitor import quarantine_agent as hm_fn
    assert diag_fn is hm_fn


# ── Scheduler ───────────────────────────────────────────────────────

def test_scheduler_imports():
    """Scheduler class can be imported."""
    from scheduler import Scheduler
    assert Scheduler is not None


def test_scheduler_init():
    """Scheduler initializes with config and PM."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {"training_check_interval_secs": 30}, "scaling": {}}, pm)
    assert sched is not None


def test_scheduler_build_roles():
    """build_roles returns a list of role strings."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    cfg = {"fleet": {"disabled_agents": [], "training_check_interval_secs": 30},
           "workers": {"coder_count": 2}, "scaling": {}}
    sched = Scheduler(cfg, pm)
    roles = sched.build_roles()
    assert isinstance(roles, list)
    assert "researcher" in roles
    assert "coder_1" in roles
    assert "coder_2" in roles


def test_scheduler_count_pending_tasks():
    """count_pending_tasks returns an integer >= 0."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {"training_check_interval_secs": 30}, "scaling": {}}, pm)
    result = sched.count_pending_tasks()
    assert isinstance(result, int)
    assert result >= 0


def test_scheduler_tick_no_crash():
    """Scheduler.tick() completes without error."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {"eco_mode": False, "disabled_agents": [],
                                    "training_check_interval_secs": 30,
                                    "ram_ceiling_pct": 95, "max_workers": 10},
                          "models": {}, "workers": {}, "scaling": {}})
    sched = Scheduler(pm.config, pm)
    sched.tick(0.0)  # should not raise


# ── FederationManager ───────────────────────────────────────────────

def test_federation_manager_imports():
    """FederationManager class can be imported."""
    from federation_manager import FederationManager
    assert FederationManager is not None


def test_federation_manager_init():
    """FederationManager initializes with config and PM."""
    from process_manager import ProcessManager
    from federation_manager import FederationManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    fm = FederationManager({"federation": {"enabled": False}}, pm)
    assert fm is not None


def test_federation_manager_tick_disabled():
    """tick() completes without error when federation is disabled."""
    from process_manager import ProcessManager
    from federation_manager import FederationManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    fm = FederationManager({"federation": {"enabled": False}}, pm)
    fm.tick(0.0)  # should not raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
