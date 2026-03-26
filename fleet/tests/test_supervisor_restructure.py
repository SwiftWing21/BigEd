#!/usr/bin/env python3
"""Tests for supervisor restructure — process_manager, health_monitor,
scheduler, federation_manager, boot_sequence.

Usage:
    python -m pytest fleet/tests/test_supervisor_restructure.py -v
    python fleet/tests/test_supervisor_restructure.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

FLEET_DIR = str(Path(__file__).resolve().parent.parent)
if FLEET_DIR not in sys.path:
    sys.path.insert(0, FLEET_DIR)

os.environ.setdefault("FLEET_TEST_DB", ":memory:")


# ── ProcessManager ──────────────────────────────────────────────────

def test_process_manager_imports():
    """ProcessManager class can be imported."""
    from process_manager import ProcessManager
    assert ProcessManager is not None


def test_process_manager_init():
    """ProcessManager initializes with config dict."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {"eco_mode": False}, "models": {}, "workers": {}})
    assert pm.worker_procs == {}
    assert pm.ollama_proc is None
    assert pm.dashboard_proc is None
    assert pm.hw_supervisor_proc is None
    assert pm.discord_proc is None
    assert pm.openclaw_proc is None
    assert pm.training_active is False


def test_process_manager_get_running_workers_empty():
    """get_running_workers returns empty set when no workers."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    assert pm.get_running_workers() == set()


def test_process_manager_find_ollama_returns_string():
    """find_ollama always returns a string (path or fallback)."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    result = pm._find_ollama()
    assert isinstance(result, str)
    assert len(result) > 0


def test_process_manager_read_hw_state_missing_file():
    """read_hw_state returns None when hw_state.json doesn't exist."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    # Temporarily point HW_STATE_FILE to nonexistent path
    import process_manager as pm_mod
    original = pm_mod.HW_STATE_FILE
    pm_mod.HW_STATE_FILE = Path("/tmp/nonexistent_hw_state_test.json")
    try:
        assert pm.read_hw_state() is None
    finally:
        pm_mod.HW_STATE_FILE = original


def test_process_manager_shutdown_all_no_procs():
    """shutdown_all completes without error when no processes running."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    pm.shutdown_all()  # should not raise


def test_process_manager_check_alive_no_workers():
    """check_alive completes without error when worker_procs is empty."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {"disabled_agents": []}, "models": {}, "workers": {}})
    pm.check_alive()  # should not raise


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
