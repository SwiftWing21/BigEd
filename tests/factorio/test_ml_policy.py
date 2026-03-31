import numpy as np
import torch
import pytest
from factorio.ml_policy import FactorioPolicy
from factorio.action_space import ActionType

def test_policy_forward_shapes():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    grid = torch.randn(1, 4, 64, 64)
    features = torch.randn(1, 64)
    action_logits, value = policy(grid, features)
    assert action_logits.shape == (1, 8)
    assert value.shape == (1, 1)

def test_policy_act_returns_valid_action():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    grid = torch.randn(1, 4, 64, 64)
    features = torch.randn(1, 64)
    action, log_prob, value, params = policy.act(grid, features)
    assert 0 <= action.item() < 8
    assert log_prob.shape == (1,)
    assert value.shape == (1,)
    assert isinstance(params, dict)

def test_policy_parameter_heads_exist():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    shared = torch.randn(1, 128)  # shared features size
    params = policy.get_action_params(shared, action_type=ActionType.PLACE.value)
    assert "entity_logits" in params
    assert "dx_logits" in params
    assert "dy_logits" in params
    assert "direction_logits" in params

def test_policy_batch_forward():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    batch = 16
    grid = torch.randn(batch, 4, 64, 64)
    features = torch.randn(batch, 64)
    action_logits, value = policy(grid, features)
    assert action_logits.shape == (batch, 8)
    assert value.shape == (batch, 1)

def test_policy_save_load(tmp_path):
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    path = tmp_path / "test_checkpoint.pt"
    policy.save(str(path))
    policy2 = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    policy2.load(str(path))
    for p1, p2 in zip(policy.parameters(), policy2.parameters()):
        assert torch.allclose(p1, p2)

def test_policy_param_count():
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=64,
        num_action_types=8, num_entities=20, num_recipes=30, num_techs=20,
    )
    total = sum(p.numel() for p in policy.parameters())
    assert total < 2_000_000
    assert total > 100_000


def test_evaluate_action_respects_mask():
    """evaluate_action must produce same log_probs as act() for masked actions."""
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=12, num_entities=8, num_recipes=10, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)

    # Mask: only WAIT(0) and MINE(1) allowed
    mask = torch.zeros(1, 12, dtype=torch.bool)
    mask[0, 0] = True  # WAIT
    mask[0, 1] = True  # MINE

    # Get action with mask
    action_type, log_prob_act, _, _ = policy.act(grid, feat, mask)

    # evaluate_action with same mask should give same log_prob
    log_prob_eval, _, _ = policy.evaluate_action(
        grid, feat, action_type, action_mask=mask,
    )

    assert torch.allclose(log_prob_act, log_prob_eval, atol=1e-5), (
        f"act log_prob={log_prob_act.item():.6f} != evaluate log_prob={log_prob_eval.item():.6f}"
    )
