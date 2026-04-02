import pytest
torch = pytest.importorskip("torch")


def test_policy_has_pack_heads():
    from factorio.ml_policy import FactorioPolicy
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=12, num_entities=37, num_recipes=69, num_techs=20,
    )
    assert hasattr(policy, "pack_head")
    assert hasattr(policy, "pack_offset_dx")
    assert hasattr(policy, "pack_offset_dy")
    dummy_trunk = torch.randn(1, 128)
    assert policy.pack_head(dummy_trunk).shape == (1, 64)
    assert policy.pack_offset_dx(dummy_trunk).shape == (1, 11)
    assert policy.pack_offset_dy(dummy_trunk).shape == (1, 11)


def test_policy_get_action_params_pack():
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionType
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=12, num_entities=37, num_recipes=69, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)
    shared = policy._shared_forward(grid, feat)
    params = policy.get_action_params(shared, ActionType.PACK.value)
    assert "pack_logits" in params
    assert "offset_dx_logits" in params
    assert "offset_dy_logits" in params
    assert params["pack_logits"].shape == (1, 64)


def test_policy_get_action_params_stamp():
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionType
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=12, num_entities=37, num_recipes=69, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)
    shared = policy._shared_forward(grid, feat)
    params = policy.get_action_params(shared, ActionType.STAMP.value)
    assert "pack_logits" in params


def test_policy_action_head_outputs_12():
    from factorio.ml_policy import FactorioPolicy
    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=12, num_entities=37, num_recipes=69, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)
    action_logits, value = policy.forward(grid, feat)
    assert action_logits.shape == (1, 12)
