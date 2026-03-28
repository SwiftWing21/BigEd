# tests/test_lua_installer.py
"""Tests for Lua mod installer — path detection + copy."""
import sys
import pytest
from pathlib import Path
from unittest.mock import patch


def test_detect_mods_dir_windows():
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    from factorio.lua_installer import detect_mods_dir
    result = detect_mods_dir()
    if result:
        assert "Factorio" in str(result)
        assert "mods" in str(result)


def test_detect_factorio_path_returns_none_when_not_found():
    from factorio.lua_installer import detect_factorio_path
    with patch("factorio.lua_installer._SEARCH_PATHS", []):
        result = detect_factorio_path()
        assert result is None


def test_get_lua_mod_source():
    from factorio.lua_installer import get_lua_mod_source
    src = get_lua_mod_source()
    assert src.exists()
    assert (src / "control.lua").exists()
    assert (src / "info.json").exists()


def test_install_mode_manual_returns_instructions():
    from factorio.lua_installer import install_lua_mod
    result = install_lua_mod(mode="manual")
    assert result["mode"] == "manual"
    assert "instructions" in result
