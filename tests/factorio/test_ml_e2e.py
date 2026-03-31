"""End-to-end smoke test: state -> encode -> policy -> action -> reward -> train.
Uses mock data -- no Factorio server required.
"""
import numpy as np
import torch
import pytest

from factorio.state_parser import GameState, Entity
from factorio.state_encoder import StateEncoder
from factorio.action_space import ActionSpace, EncodedAction, ActionType
from factorio.ml_policy import FactorioPolicy
from factorio.reward import RewardComputer
from factorio.trainer import PPOTrainer, TrajectoryBuffer, Transition


def _make_policy(encoder, action_space):
    return FactorioPolicy(
        grid_channels=encoder.grid_channels,
        grid_size=encoder.grid_size,
        feature_dim=encoder.feature_dim,
        num_action_types=len(ActionType),
        num_entities=action_space.num_entity_types,
        num_recipes=action_space.num_recipe_types,
        num_techs=action_space.num_tech_types,
        world_grid_channels=encoder.world_grid_channels,
    )


def test_full_pipeline_smoke():
    """Run 100 steps through the full pipeline without crashing."""
    phase = 1
    encoder = StateEncoder(phase=phase)
    action_space = ActionSpace(phase=phase)
    policy = _make_policy(encoder, action_space)
    reward_computer = RewardComputer(phase=phase)
    trainer = PPOTrainer(policy, lr=3e-4, device="cpu")
    buffer = TrajectoryBuffer()

    prev_state = GameState(
        tick=0, player_position={"x": 0, "y": 0},
        player_health=250, player_max_health=250,
        player_has_character=True, player_alive=True,
        inventory={"iron-plate": 8}, entities=[], resources=[],
    )

    for step in range(100):
        entities = []
        if step > 20:
            entities.append(Entity(name="stone-furnace", position={"x": 2, "y": 3}, direction=0))
        state = GameState(
            tick=step * 60,
            player_position={"x": 0, "y": 0},
            player_health=250, player_max_health=250,
            player_has_character=True, player_alive=True,
            inventory={"iron-plate": 8 + step, "iron-gear-wheel": step // 10},
            entities=entities,
            resources=[],
            research_name="automation" if step > 50 else "",
            research_progress=min(step / 100.0, 1.0) if step > 50 else 0.0,
        )

        grid, world_grid, features = encoder.encode(state)
        grid_t = torch.tensor(grid).unsqueeze(0)
        world_t = torch.tensor(world_grid).unsqueeze(0)
        feat_t = torch.tensor(features).unsqueeze(0)

        mask = action_space.get_action_type_mask(state.inventory, phase)
        mask_t = torch.tensor([mask], dtype=torch.bool)
        action_type, log_prob, value, params = policy.act(
            grid_t, feat_t, mask_t, world_grid=world_t,
        )

        reward = reward_computer.compute(
            prev_state, state, action_success=(step % 5 != 0),
            lesson_passed=(step == 50), phase_complete=False,
        )

        buffer.add(Transition(
            grid=grid, features=features,
            action_type=action_type.item(),
            log_prob=log_prob.item(),
            value=value.item(),
            reward=reward,
            done=(step == 99),
            world_grid=world_grid,
            action_mask=mask,
        ))

        prev_state = state

    stats = trainer.update(buffer)
    assert "policy_loss" in stats
    assert stats["policy_loss"] < 100


def test_checkpoint_round_trip(tmp_path):
    """Save checkpoint, load into new trainer, verify weights match."""
    encoder = StateEncoder(phase=1)
    action_space = ActionSpace(phase=1)
    policy = _make_policy(encoder, action_space)
    trainer = PPOTrainer(policy, lr=3e-4, checkpoint_dir=str(tmp_path), device="cpu")
    ckpt_path = trainer.save_checkpoint(episode=42)

    policy2 = _make_policy(encoder, action_space)
    trainer2 = PPOTrainer(policy2, lr=3e-4, checkpoint_dir=str(tmp_path), device="cpu")
    ep = trainer2.load_checkpoint(ckpt_path)
    assert ep == 42

    for p1, p2 in zip(policy.parameters(), policy2.parameters()):
        assert torch.allclose(p1, p2)
