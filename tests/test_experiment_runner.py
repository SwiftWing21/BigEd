"""Tests for experiment runner — candidate loading, results logging, keep/discard."""
import json
import os
import pytest
from pathlib import Path


def test_load_candidate_from_toml(tmp_path):
    from factorio.experiment_runner import load_candidate

    (tmp_path / "test_candidate.toml").write_text('''
prompt = "compact_v1"
load_save = "my_save"
phase_override = 2

[params]
plan_size = 10
temperature = 0.5
failure_threshold = 2
''', encoding="utf-8")

    candidate = load_candidate(str(tmp_path / "test_candidate.toml"))
    assert candidate["prompt"] == "compact_v1"
    assert candidate["load_save"] == "my_save"
    assert candidate["phase_override"] == 2
    assert candidate["params"]["plan_size"] == 10
    assert candidate["params"]["temperature"] == 0.5


def test_load_candidate_defaults(tmp_path):
    from factorio.experiment_runner import load_candidate

    (tmp_path / "minimal.toml").write_text('prompt = "baseline"\n', encoding="utf-8")
    candidate = load_candidate(str(tmp_path / "minimal.toml"))
    assert candidate["prompt"] == "baseline"
    assert candidate.get("load_save") is None
    assert candidate.get("phase_override") is None
    assert candidate.get("params", {}) == {}


def test_append_result_tsv(tmp_path):
    from factorio.experiment_runner import append_result

    tsv_path = str(tmp_path / "results.tsv")
    append_result(tsv_path, experiment_id="exp_0001", phase=1, load_save=None,
                  prompt="baseline", metric=2.0, baseline=None, delta=None,
                  status="keep", description="initial baseline")

    lines = Path(tsv_path).read_text().strip().split("\n")
    assert len(lines) == 2  # header + 1 row
    assert "exp_0001" in lines[1]
    assert "keep" in lines[1]

    # Append another
    append_result(tsv_path, experiment_id="exp_0002", phase=1, load_save=None,
                  prompt="compact", metric=3.0, baseline=2.0, delta=1.0,
                  status="keep", description="better prompt")

    lines = Path(tsv_path).read_text().strip().split("\n")
    assert len(lines) == 3


def test_append_replay(tmp_path):
    from factorio.experiment_runner import append_replay

    jsonl_path = str(tmp_path / "replay.jsonl")
    append_replay(jsonl_path, experiment_id="exp_0001", phase=1,
                  lesson="Craft gears", state={"inventory": {"iron-plate": 5}},
                  plan=[{"action": "craft"}], actions_taken=1,
                  actions_succeeded=1, lesson_passed=True)

    lines = Path(jsonl_path).read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["experiment_id"] == "exp_0001"
    assert entry["lesson_passed"] is True


def test_load_baseline_from_tsv(tmp_path):
    from factorio.experiment_runner import append_result, load_baseline

    tsv_path = str(tmp_path / "results.tsv")
    append_result(tsv_path, "exp_0001", 1, None, "baseline", 2.0, None, None, "keep", "first")
    append_result(tsv_path, "exp_0002", 1, None, "compact", 1.5, 2.0, -0.5, "discard", "worse")
    append_result(tsv_path, "exp_0003", 1, None, "cot", 3.0, 2.0, 1.0, "keep", "better")

    best = load_baseline(tsv_path, phase=1)
    assert best == 3.0


def test_load_baseline_empty(tmp_path):
    from factorio.experiment_runner import load_baseline

    tsv_path = str(tmp_path / "results.tsv")
    best = load_baseline(tsv_path, phase=1)
    assert best is None


def test_build_experiment_config():
    from factorio.bridge_config import BridgeConfig
    from factorio.experiment_runner import build_experiment_config

    base = BridgeConfig(plan_max_actions=20, ollama_cooldown_secs=30)
    candidate = {
        "prompt": "compact_v1",
        "params": {
            "plan_size": 10,
            "temperature": 0.5,
            "cooldown_after_failure": 15,
            "failure_threshold": 2,
        },
    }
    cfg = build_experiment_config(base, candidate)
    assert cfg.prompt_template == "compact_v1"
    assert cfg.plan_max_actions == 10
    assert cfg.temperature == 0.5
    assert cfg.ollama_cooldown_secs == 15
    assert cfg.plan_invalidation_failures == 2
    # Base unchanged
    assert base.plan_max_actions == 20


def test_run_experiment_keep_discard_flow(tmp_path):
    """Test the full keep/discard decision flow (mocked bridge)."""
    from factorio.experiment_runner import (
        append_result, load_baseline, generate_experiment_id,
        build_experiment_config,
    )
    from factorio.experiment_scorer import compute_score
    from factorio.bridge_config import BridgeConfig

    results_path = str(tmp_path / "results.tsv")

    # Simulate experiment 1: baseline
    exp_id = generate_experiment_id(results_path)
    assert exp_id == "exp_0001"
    score = compute_score(phase=1, lessons_passed=2, total_actions=15,
                          total_failures=3, throughput=0.0)
    baseline = load_baseline(results_path, phase=1)
    assert baseline is None
    append_result(results_path, exp_id, 1, None, "baseline", score,
                  baseline, None, "keep", "initial")

    # Simulate experiment 2: better
    exp_id = generate_experiment_id(results_path)
    assert exp_id == "exp_0002"
    score2 = compute_score(phase=1, lessons_passed=3, total_actions=10,
                           total_failures=1, throughput=0.0)
    baseline = load_baseline(results_path, phase=1)
    assert baseline == 2.0
    delta = score2 - baseline
    status = "keep" if score2 > baseline else "discard"
    assert status == "keep"
    append_result(results_path, exp_id, 1, None, "compact", score2,
                  baseline, delta, status, "better prompt")

    # Simulate experiment 3: worse
    exp_id = generate_experiment_id(results_path)
    score3 = compute_score(phase=1, lessons_passed=1, total_actions=20,
                           total_failures=10, throughput=0.0)
    baseline = load_baseline(results_path, phase=1)
    assert baseline == 3.0  # from exp_0002
    status = "keep" if score3 > baseline else "discard"
    assert status == "discard"
