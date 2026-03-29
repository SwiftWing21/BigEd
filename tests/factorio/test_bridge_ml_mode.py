"""Integration smoke tests for bridge ML mode (Task 9)."""
import pytest
import numpy as np
from factorio.bridge_config import BridgeConfig


def test_bridge_config_selects_ml_mode():
    cfg = BridgeConfig(mode="ml")
    assert cfg.mode == "ml"


@pytest.mark.asyncio
async def test_ml_tick_calls_policy():
    """Verify the ML pipeline works end-to-end: encode -> policy -> decode."""
    from factorio.state_encoder import StateEncoder
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionSpace

    encoder = StateEncoder(phase=1)
    policy = FactorioPolicy(
        grid_channels=4, grid_size=64, feature_dim=encoder.feature_dim,
        num_action_types=8, num_entities=8, num_recipes=10, num_techs=20,
    )
    action_space = ActionSpace(phase=1)

    from factorio.state_parser import GameState
    state = GameState(
        tick=100, player_position={"x": 0, "y": 0},
        inventory={"iron-plate": 10}, entities=[], resources=[],
    )
    grid, features = encoder.encode(state)

    import torch
    grid_t = torch.tensor(grid).unsqueeze(0)
    feat_t = torch.tensor(features).unsqueeze(0)
    action_type, log_prob, value, params = policy.act(grid_t, feat_t)

    from factorio.action_space import EncodedAction
    encoded = EncodedAction(action_type=action_type.item())
    action_dict = action_space.decode_action(encoded)
    assert "action" in action_dict
