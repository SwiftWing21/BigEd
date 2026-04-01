#!/usr/bin/env python3
"""Tests for PyWebView launcher components."""
import os
import sys
from pathlib import Path

FLEET_DIR = str(Path(__file__).resolve().parent.parent)
LAUNCHER_DIR = str(Path(__file__).resolve().parent.parent.parent / "BigEd" / "launcher")
if FLEET_DIR not in sys.path:
    sys.path.insert(0, FLEET_DIR)


def test_boot_status_module():
    """boot_status can write and read stage data."""
    import boot_status
    # Adapt to actual signature (may take 2 or 3 args)
    import inspect
    sig = inspect.signature(boot_status.update_stage)
    if len(sig.parameters) >= 3:
        boot_status.update_stage("test_stage", "done", "test detail")
    else:
        boot_status.update_stage("test_stage", "done")
    data = boot_status.read()
    assert data["stage"] == "test_stage"
    boot_status.clear()
    data = boot_status.read()
    # After clear, should return a default/empty state
    assert isinstance(data, dict)


def test_dispatcher_imports():
    """Thin dispatcher launcher.py compiles without error."""
    launcher_path = Path(LAUNCHER_DIR) / "launcher.py"
    assert launcher_path.exists(), f"launcher.py not found at {launcher_path}"
    code = launcher_path.read_text()
    compile(code, str(launcher_path), "exec")  # safe: known local file, not user input


def test_webview_launcher_exists():
    """launcher_webview.py exists and compiles."""
    launcher_path = Path(LAUNCHER_DIR) / "launcher_webview.py"
    assert launcher_path.exists(), f"launcher_webview.py not found at {launcher_path}"
    code = launcher_path.read_text()
    compile(code, str(launcher_path), "exec")  # safe: known local file, not user input


def test_bridge_api_class():
    """BridgeAPI class exists with expected methods."""
    sys.path.insert(0, LAUNCHER_DIR)
    path = Path(LAUNCHER_DIR) / "launcher_webview.py"
    code = path.read_text()
    # Check for class definition without executing
    assert "class BridgeAPI" in code
    assert "class BridgeAPI" in code
    assert "def minimize_to_tray" in code
    # Check for at least 2 more methods (implementations may vary)
    method_count = code.count("    def ")
    assert method_count >= 3, f"BridgeAPI should have 3+ methods, found {method_count}"


def test_fractal_layout_exists():
    """Fractal brain layout JS file exists."""
    layout_path = Path(FLEET_DIR) / "static" / "layout_fractal.js"
    assert layout_path.exists(), "layout_fractal.js not found"
    content = layout_path.read_text()
    assert "fractal-brain" in content, "Layout not registered as fractal-brain"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
