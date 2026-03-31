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


def test_checkpoint_rotation(tmp_path):
    """Only max_checkpoints files are kept; oldest deleted."""
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    trainer = PPOTrainer(
        policy, lr=3e-4, checkpoint_dir=str(tmp_path),
        device="cpu", max_checkpoints=3,
    )
    for ep in range(1, 6):
        trainer.save_checkpoint(episode=ep)
    pts = sorted(tmp_path.glob("ppo_ep*.pt"))
    assert len(pts) == 3
    # Should keep the 3 most recent
    names = [p.name for p in pts]
    assert "ppo_ep000003.pt" in names
    assert "ppo_ep000004.pt" in names
    assert "ppo_ep000005.pt" in names
    assert "ppo_ep000001.pt" not in names


def test_gae_vectorized_matches_list():
    """Vectorized GAE produces same results as the list-based version."""
    trainer = PPOTrainer.__new__(PPOTrainer)
    trainer._gamma = 0.99
    trainer._gae_lambda = 0.95
    rewards = [0.1, 0.2, -0.1, 0.5, 0.0]
    values = [1.0, 0.9, 0.8, 0.7, 0.6]
    dones = [False, False, True, False, False]
    next_value = 0.5
    result = trainer._compute_gae(rewards, values, dones, next_value)
    assert len(result) == 5
    # Verify manually: done at step 2 resets GAE
    # Step 4: delta = 0 + 0.99*0.5*1 - 0.6 = -0.105, gae=−0.105
    # Step 3: delta = 0.5 + 0.99*0.6*1 - 0.7 = 0.394, gae=0.394 + 0.99*0.95*1*(-0.105) = 0.295
    # Step 2: delta = -0.1 + 0.99*0*0 - 0.8 = -0.9 (done=True, mask=0), gae=-0.9
    # Step 1: delta = 0.2 + 0.99*0.8*1 - 0.9 = 0.092, gae=0.092+0.99*0.95*1*(-0.9) = -0.754
    # Step 0: delta = 0.1 + 0.99*0.9*1 - 1.0 = -0.009, gae=-0.009+0.99*0.95*1*(-0.754) = -0.718
    assert abs(result[4] - (-0.105)) < 0.01
    assert abs(result[2] - (-0.9)) < 0.01


def test_terminal_bootstrap_true_terminal():
    """True terminal episodes use next_value=0."""
    trainer = PPOTrainer.__new__(PPOTrainer)
    trainer._gamma = 0.99
    trainer._gae_lambda = 0.95
    # done=True on last step -> next_value should be 0
    rewards = [0.1, 0.2]
    values = [0.5, 0.3]
    dones = [False, True]
    adv_terminal = trainer._compute_gae(rewards, values, dones, 0.0)
    adv_truncated = trainer._compute_gae(rewards, values, [False, False], 0.3)
    # Last step: terminal uses 0, truncated uses 0.3 — different advantages
    assert adv_terminal[1] != adv_truncated[1]
