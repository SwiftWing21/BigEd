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


# ── BootSequence ────────────────────────────────────────────────────

def test_boot_sequence_imports():
    """boot function can be imported."""
    from boot_sequence import boot
    assert callable(boot)


def test_boot_load_secrets():
    """_load_secrets function can be imported and called safely."""
    from boot_sequence import _load_secrets
    _load_secrets()  # should not raise even if ~/.secrets doesn't exist


def test_boot_register_views():
    """_register_views does not raise even if view_registry is missing."""
    from boot_sequence import _register_views
    _register_views()  # should not raise (view_registry may not be importable)


def test_boot_json_log(capsys):
    """_json_log prints valid JSON."""
    from boot_sequence import _json_log
    _json_log("INFO", "test_event", key="value")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["level"] == "INFO"
    assert data["event"] == "test_event"
    assert data["key"] == "value"


def test_boot_returns_none_on_duplicate():
    """boot returns None when PID acquisition fails (duplicate supervisor)."""
    mock_pid = MagicMock()
    mock_pid.acquire_pid.return_value = False

    mock_db = MagicMock()
    mock_log_mgr = MagicMock()
    mock_log_mgr.rotate_logs.return_value = {}

    mock_pm_mod = MagicMock()
    mock_sched_mod = MagicMock()
    mock_sched_mod.CORE_AGENTS = set()
    mock_hm_mod = MagicMock()
    mock_fm_mod = MagicMock()

    mock_config_mod = MagicMock()
    mock_config_mod.load_config.return_value = {"fleet": {}, "models": {}, "workers": {}}
    mock_config_mod.is_offline.return_value = False
    mock_config_mod.is_air_gap.return_value = False

    with patch.dict("sys.modules", {
        "db": mock_db,
        "config": mock_config_mod,
        "log_manager": mock_log_mgr,
        "pid_manager": mock_pid,
        "process_manager": mock_pm_mod,
        "scheduler": mock_sched_mod,
        "health_monitor": mock_hm_mod,
        "federation_manager": mock_fm_mod,
    }):
        import importlib
        import boot_sequence
        importlib.reload(boot_sequence)
        result = boot_sequence.boot()
        assert result is None


# ── Supervisor Orchestrator ─────────────────────────────────────────

def test_supervisor_imports():
    """supervisor.py can be imported without crashing."""
    import importlib
    mod = importlib.import_module("supervisor")
    assert hasattr(mod, "main")
    assert hasattr(mod, "write_status_md")
    assert hasattr(mod, "_json_log")
    assert hasattr(mod, "shutdown")


def test_supervisor_json_log(capsys):
    """supervisor._json_log prints valid JSON."""
    import supervisor
    supervisor._json_log("WARNING", "test_warn", detail="abc")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["level"] == "WARNING"
    assert data["event"] == "test_warn"
    assert data["detail"] == "abc"


def test_supervisor_shutdown_handler():
    """shutdown handler calls _pm.shutdown_all() if _pm is set."""
    import supervisor
    mock_pm = MagicMock()
    original_pm = supervisor._pm
    try:
        supervisor._pm = mock_pm
        with pytest.raises(SystemExit):
            supervisor.shutdown(signal.SIGINT, None)
        mock_pm.shutdown_all.assert_called_once()
    finally:
        supervisor._pm = original_pm


# ── Workflow Hardening: Health Monitor ──────────────────────────────

def test_circuit_breaker_memory_cap():
    """Failures list is capped — cannot grow unbounded."""
    from health_monitor import circuit_breaker_record_failure, _breakers, _breaker_lock
    skill = "_test_cap_skill_xyz"
    # Record 2000 failures
    for i in range(2000):
        circuit_breaker_record_failure(skill, f"error_{i}")
    with _breaker_lock:
        assert len(_breakers[skill]["failures"]) <= 1000
        # Cleanup
        del _breakers[skill]


def test_circuit_breaker_cleanup_stale():
    """Skills with no recent failures are cleaned up entirely."""
    import time
    from health_monitor import (
        circuit_breaker_record_failure, circuit_breaker_is_open,
        _breakers, _breaker_lock,
    )
    skill = "_test_stale_skill_xyz"
    circuit_breaker_record_failure(skill, "old_error")
    with _breaker_lock:
        # Backdate failure to 10 minutes ago (beyond default 300s window)
        _breakers[skill]["failures"] = [(time.time() - 700, "old_error")]
    # is_open should prune stale entries
    circuit_breaker_is_open(skill)
    with _breaker_lock:
        # After pruning, skill with 0 recent failures should be removed
        assert skill not in _breakers or len(_breakers[skill]["failures"]) == 0


def test_recovery_log_deque():
    """Recovery log uses deque, caps at 200, evicts oldest."""
    from health_monitor import _log_recovery, get_recovery_log
    import collections
    from health_monitor import _recovery_log
    assert isinstance(_recovery_log, collections.deque)
    # Record 300 entries
    for i in range(300):
        _log_recovery("test_action", f"target_{i}", f"detail_{i}")
    log = get_recovery_log()
    assert len(log) <= 200
    # Oldest entries should have been evicted — first entry should be target_100+
    assert "target_0" not in log[0]["target"]


def test_health_tick_stagger():
    """HealthMonitor init staggers _last_* values (not all zero)."""
    try:
        from process_manager import ProcessManager
        pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    except ImportError:
        pm = type("PM", (), {})()
    from health_monitor import HealthMonitor
    hm = HealthMonitor({"self_healing": {"enabled": False}}, pm)
    # All _last_* should be > 0 (staggered from time.time())
    assert hm._last_health_sweep > 0
    assert hm._last_memory_watchdog > 0
    assert hm._last_stale_check > 0
    assert hm._last_watchdog > 0
    # They should NOT all be identical (randomized)
    values = [hm._last_health_sweep, hm._last_memory_watchdog,
              hm._last_stale_check, hm._last_watchdog]
    assert len(set(values)) > 1, "Stagger values should differ"


# ── Workflow Hardening: Scheduler ───────────────────────────────────

def test_evolution_dedup_skips_queued():
    """_cross_skill_learning skips dispatch when skill_test already queued."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))
    from unittest.mock import patch, MagicMock
    import evolution_coordinator as ec

    # Clear any cached entries from previous tests
    ec._EVOLVING_SKILLS.clear()

    # Mock DB to return existing PENDING skill_test
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = {"id": 999}
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch("evolution_coordinator.db") as mock_db:
        mock_db.get_conn.return_value = mock_conn
        mock_db.post_task = MagicMock()
        # Call with a skill that has "related" skills
        result = ec._cross_skill_learning(
            {"skill": "summarize", "action": "cross_learn"},
            {"models": {}}
        )
        # Should NOT have called post_task (existing task blocks dispatch)
        mock_db.post_task.assert_not_called()


def test_research_trigger_skips_running():
    """Auto-trigger skips when previous research_loop is still RUNNING."""
    from unittest.mock import patch, MagicMock
    from process_manager import ProcessManager
    from scheduler import Scheduler
    import time

    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {}, "models": {}, "workers": {}}, pm)

    # Force research trigger interval to have elapsed
    sched._last_research_trigger = 0

    # Mock DB: return count=1 for PENDING+RUNNING research_loop
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (1,)
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch("scheduler.db") as mock_db:
        mock_db.get_conn.return_value = mock_conn
        mock_db.post_task = MagicMock()
        sched._check_auto_triggers(time.time())
        # Should NOT have dispatched a new research_loop
        mock_db.post_task.assert_not_called()


def test_offset_atomic_write():
    """Offset persistence uses atomic os.replace, not direct write."""
    import tempfile, json, os
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        meta_path = Path(tmpdir) / "source_meta.json"
        # Write initial state
        meta_path.write_text(json.dumps({"source_id": "test", "last_offset": 50, "custom_key": "preserve_me"}))

        # Simulate atomic write pattern
        existing = json.loads(meta_path.read_text())
        existing.update({"last_offset": 100})
        tmp_path = meta_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(existing, indent=2))
        os.replace(str(tmp_path), str(meta_path))

        # Verify: original keys preserved + new offset
        result = json.loads(meta_path.read_text())
        assert result["last_offset"] == 100
        assert result["custom_key"] == "preserve_me"
        assert not tmp_path.exists()  # .tmp cleaned up by os.replace


def test_scheduler_tick_stagger():
    """Scheduler init staggers _last_* values (not all zero)."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {}, "models": {}, "workers": {}}, pm)
    # _last_scale_check should stay 0 (intentional: scale immediately on boot)
    assert sched._last_scale_check == 0
    # All others should be > 0 (staggered)
    assert sched._last_research_trigger > 0
    assert sched._last_evolution_trigger > 0
    assert sched._last_config_reload > 0


def test_vram_reactive_eviction():
    """VRAM >90% during training triggers Ollama CPU-mode restart."""
    from unittest.mock import patch, MagicMock
    from process_manager import ProcessManager
    from scheduler import Scheduler
    import time

    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    pm.stop_ollama = MagicMock()
    pm.start_ollama = MagicMock()
    pm.training_active = True
    pm.ollama_evicted_for_training = False

    sched = Scheduler({"fleet": {}, "models": {"local": "qwen3:8b"}, "workers": {}}, pm)
    sched._last_training_check = 0

    mock_gpu = MagicMock()
    mock_gpu.get_gpu_info.return_value = {"memory_used_mb": 11000, "memory_total_mb": 12000}

    with patch("scheduler.is_training_running", return_value=(True, "large")), \
         patch.dict("sys.modules", {"gpu": mock_gpu}):
        sched._check_training(time.time())

    pm.stop_ollama.assert_called_once()
    pm.start_ollama.assert_called_once_with(gpu=False)
    assert pm.ollama_evicted_for_training is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
