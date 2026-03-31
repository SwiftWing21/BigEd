"""Unit tests for fleet/config.py — TOML config loader."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest

import config as cfg_module


# ── Helpers ───────────────────────────────────────────────────────────────────

MINIMAL_TOML = b"""
[fleet]
offline_mode = false
air_gap_mode = false
"""

OFFLINE_TOML = b"""
[fleet]
offline_mode = true
air_gap_mode = false
"""

AIR_GAP_TOML = b"""
[fleet]
offline_mode = false
air_gap_mode = true
"""


def _patch_config_path(tmp_path, content: bytes):
    """Write a TOML file and patch CONFIG_PATH to point to it."""
    p = tmp_path / "test_fleet.toml"
    p.write_bytes(content)
    return mock.patch.object(cfg_module, "CONFIG_PATH", p)


# ── 1. load_config — reads valid TOML ────────────────────────────────────────

def test_load_config_reads_valid_toml(tmp_path):
    with _patch_config_path(tmp_path, MINIMAL_TOML):
        result = cfg_module.load_config()
    assert isinstance(result, dict)
    assert "fleet" in result


# ── 2. load_config — air_gap_mode forces offline_mode ────────────────────────

def test_load_config_air_gap_implies_offline(tmp_path):
    with _patch_config_path(tmp_path, AIR_GAP_TOML):
        result = cfg_module.load_config()
    assert result["fleet"]["offline_mode"] is True
    assert result["fleet"]["air_gap_mode"] is True


# ── 3. load_config — offline_mode without air_gap ────────────────────────────

def test_load_config_offline_only(tmp_path):
    with _patch_config_path(tmp_path, OFFLINE_TOML):
        result = cfg_module.load_config()
    assert result["fleet"]["offline_mode"] is True
    assert result["fleet"]["air_gap_mode"] is False


# ── 4. load_config — fails on invalid TOML ───────────────────────────────────

def test_load_config_raises_on_invalid_toml(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_bytes(b"[broken\n")
    with mock.patch.object(cfg_module, "CONFIG_PATH", p):
        with pytest.raises(Exception):
            cfg_module.load_config()


# ── 5. is_offline — True when offline_mode is True ───────────────────────────

def test_is_offline_true():
    c = {"fleet": {"offline_mode": True}}
    assert cfg_module.is_offline(c) is True


def test_is_offline_false():
    c = {"fleet": {"offline_mode": False}}
    assert cfg_module.is_offline(c) is False


def test_is_offline_missing_key():
    assert cfg_module.is_offline({}) is False


# ── 6. is_air_gap — True when air_gap_mode is True ────────────────────────────

def test_is_air_gap_true():
    c = {"fleet": {"air_gap_mode": True}}
    assert cfg_module.is_air_gap(c) is True


def test_is_air_gap_false():
    c = {"fleet": {"air_gap_mode": False}}
    assert cfg_module.is_air_gap(c) is False


def test_is_air_gap_missing_key():
    assert cfg_module.is_air_gap({}) is False


# ── 7. AIR_GAP_SKILLS — known safe skills are present ────────────────────────

def test_air_gap_skills_contains_expected():
    expected = {"summarize", "code_review", "rag_query", "benchmark", "ingest"}
    assert expected.issubset(cfg_module.AIR_GAP_SKILLS)


# ── 8. reload_config — clears cached config ──────────────────────────────────

def test_reload_config_clears_cache(tmp_path):
    """reload_config resets _cfg so the next call reads from disk."""
    with _patch_config_path(tmp_path, MINIMAL_TOML):
        cfg_module.reload_config()
        # Force the cache to clear and reload
        r1 = cfg_module.load_config()
    assert isinstance(r1, dict)


# ── 9. is_native_windows — returns bool ──────────────────────────────────────

def test_is_native_windows_returns_bool():
    result = cfg_module.is_native_windows()
    assert isinstance(result, bool)


# ── 10. detect_cli — returns expected keys ───────────────────────────────────

def test_detect_cli_returns_expected_keys():
    result = cfg_module.detect_cli()
    for key in ("platform", "shell", "network_tool", "hw_tool", "bridge", "recommended"):
        assert key in result
