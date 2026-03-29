"""Tests for PPOTrainer, TrajectoryBuffer, and Transition."""

import numpy as np
import pytest
import torch

from factorio.trainer import PPOTrainer, TrajectoryBuffer, Transition
from factorio.ml_policy import FactorioPolicy


@pytest.fixture
def policy():
    return FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )


def test_trajectory_buffer_add_and_size():
    buf = TrajectoryBuffer()
    t = Transition(
        grid=np.zeros((4, 64, 64), dtype=np.float32),
        features=np.zeros(64, dtype=np.float32),
        action_type=0, log_prob=-1.0, value=0.5, reward=0.1, done=False,
    )
    buf.add(t)
    assert len(buf) == 1


def test_trajectory_buffer_clear():
    buf = TrajectoryBuffer()
    t = Transition(
        grid=np.zeros((4, 64, 64), dtype=np.float32),
        features=np.zeros(64, dtype=np.float32),
        action_type=0, log_prob=-1.0, value=0.5, reward=0.1, done=False,
    )
    buf.add(t)
    buf.add(t)
    buf.clear()
    assert len(buf) == 0


def test_compute_gae():
    trainer = PPOTrainer.__new__(PPOTrainer)
    trainer._gamma = 0.99
    trainer._gae_lambda = 0.95
    rewards = [1.0, 0.0, 0.0]
    values = [0.5, 0.3, 0.1]
    dones = [False, False, True]
    next_value = 0.0
    advantages = trainer._compute_gae(rewards, values, dones, next_value)
    assert len(advantages) == 3
    assert isinstance(advantages[0], float)


def test_ppo_update_runs(policy):
    trainer = PPOTrainer(policy, lr=3e-4, device="cpu")
    buf = TrajectoryBuffer()
    for _ in range(64):
        t = Transition(
            grid=np.random.randn(4, 64, 64).astype(np.float32),
            features=np.random.randn(64).astype(np.float32),
            action_type=np.random.randint(0, 8),
            log_prob=-1.0,
            value=0.5,
            reward=0.1,
            done=False,
        )
        buf.add(t)
    stats = trainer.update(buf)
    assert "policy_loss" in stats
    assert "value_loss" in stats
    assert "entropy" in stats


def test_checkpoint_save_load(policy, tmp_path):
    trainer = PPOTrainer(policy, lr=3e-4, checkpoint_dir=str(tmp_path), device="cpu")
    trainer.save_checkpoint(episode=5)
    files = list(tmp_path.glob("*.pt"))
    assert len(files) == 1
