"""Unit tests for fleet/ml_router.py — ML-based task routing."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest

import ml_router


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_paths(tmp_path):
    """Redirect model and meta file paths to a temp dir for each test."""
    old_data = ml_router._DATA_DIR
    old_model = ml_router._MODEL_PATH
    old_meta = ml_router._META_PATH

    ml_router._DATA_DIR = tmp_path
    ml_router._MODEL_PATH = tmp_path / "routing_model.pkl"
    ml_router._META_PATH = tmp_path / "routing_meta.json"

    yield tmp_path

    ml_router._DATA_DIR = old_data
    ml_router._MODEL_PATH = old_model
    ml_router._META_PATH = old_meta


# ── 1. get_model_status — no model returns 'no_model' ────────────────────────

def test_get_model_status_no_model():
    """Without a trained model, status should be 'no_model'."""
    status = ml_router.get_model_status()
    assert status["status"] == "no_model"
    assert "min_training_samples" in status
    assert "retrain_interval_hours" in status


# ── 2. _load_model — returns None when no model file ─────────────────────────

def test_load_model_returns_none_when_missing():
    """_load_model returns None when .pkl file does not exist."""
    result = ml_router._load_model()
    assert result is None


# ── 3. _load_meta — returns empty dict when no meta file ─────────────────────

def test_load_meta_returns_empty_when_missing():
    """_load_meta returns {} when meta file does not exist."""
    result = ml_router._load_meta()
    assert result == {}


# ── 4. train_routing_model — insufficient data returns error ─────────────────

def test_train_routing_model_insufficient_data(tmp_db):
    """Training returns an error dict when fewer than min_training_samples exist."""
    result = ml_router.train_routing_model()
    assert "error" in result
    assert "Insufficient" in result["error"] or "Missing" in result["error"]


# ── 5. predict_best_agent — returns None when no model ───────────────────────

def test_predict_best_agent_no_model(tmp_db):
    """Returns None when no trained model is present."""
    result = ml_router.predict_best_agent("summarize", ["agent_1", "agent_2"])
    assert result is None


# ── 6. predict_best_agent — returns None when ML disabled ────────────────────

def test_predict_best_agent_ml_disabled():
    """Returns None immediately when ml_enabled is False."""
    with mock.patch.object(ml_router, "_get_routing_config", return_value={
        "ml_enabled": False,
        "retrain_interval_hours": 24,
        "min_training_samples": 50,
        "fallback_to_iq": True,
    }):
        result = ml_router.predict_best_agent("summarize", ["agent_1"])
        assert result is None


# ── 7. retrain_if_stale — skips when ML disabled ─────────────────────────────

def test_retrain_if_stale_ml_disabled():
    """retrain_if_stale returns None immediately when ML is disabled."""
    with mock.patch.object(ml_router, "_get_routing_config", return_value={
        "ml_enabled": False,
        "retrain_interval_hours": 24,
        "min_training_samples": 50,
        "fallback_to_iq": True,
    }):
        result = ml_router.retrain_if_stale()
        assert result is None


# ── 8. retrain_if_stale — triggers when no meta exists ───────────────────────

def test_retrain_if_stale_triggers_when_no_meta(tmp_db):
    """retrain_if_stale triggers training (which fails with insufficient data) when no meta."""
    result = ml_router.retrain_if_stale()
    # No model exists so it attempts training → returns error or None
    # Either way, it must not raise
    assert result is None or isinstance(result, dict)


# ── 9. _get_routing_config — returns defaults on config failure ───────────────

def test_get_routing_config_defaults():
    """_get_routing_config returns sensible defaults even if config fails."""
    with mock.patch("config.load_config", side_effect=Exception("no file")):
        cfg = ml_router._get_routing_config()
    assert "ml_enabled" in cfg
    assert "retrain_interval_hours" in cfg
    assert "min_training_samples" in cfg
    assert "fallback_to_iq" in cfg
    assert cfg["ml_enabled"] is True


# ── 10. get_model_status — reads saved meta correctly ────────────────────────

def test_get_model_status_with_meta(tmp_path):
    """get_model_status reads meta file and returns active status."""
    meta = {
        "trained_at": "2026-03-31T00:00:00",
        "sample_count": 200,
        "accuracy": 0.85,
        "feature_importances": {"skill_encoded": 0.4},
        "skills": ["summarize", "web_search"],
        "agents": ["agent_1", "agent_2"],
        "tasks_at_train": 200,
    }
    ml_router._META_PATH.write_text(json.dumps(meta))

    status = ml_router.get_model_status()
    assert status["status"] in ("active", "disabled")
    assert status["sample_count"] == 200
    assert status["accuracy"] == pytest.approx(0.85)
    assert "summarize" in status["known_skills"]


# ── 11. _count_completed_tasks — returns int (may be 0) ──────────────────────

def test_count_completed_tasks(tmp_db):
    """_count_completed_tasks returns a non-negative int."""
    count = ml_router._count_completed_tasks()
    assert isinstance(count, int)
    assert count >= 0
