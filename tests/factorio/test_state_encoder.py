import numpy as np
import pytest
from factorio.state_parser import GameState, Entity
from factorio.state_encoder import StateEncoder


def _make_state(entities=None, inventory=None, player_pos=None,
                research_name="", research_progress=0.0, tick=0):
    return GameState(
        tick=tick,
        player_position=player_pos or {"x": 0.0, "y": 0.0},
        inventory=inventory or {},
        entities=entities or [],
        research_name=research_name,
        research_progress=research_progress,
    )


def test_encoder_output_shapes():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    grid, features = encoder.encode(state)
    assert grid.shape == (4, 64, 64)
    assert features.shape[0] > 50
    assert grid.dtype == np.float32
    assert features.dtype == np.float32


def test_entity_appears_on_grid():
    encoder = StateEncoder(phase=1, grid_size=64)
    entity = Entity(name="stone-furnace", position={"x": 3.0, "y": -2.0}, direction=4)
    state = _make_state(entities=[entity])
    grid, _ = encoder.encode(state)
    gx, gy = 3 + 32, -2 + 32
    assert grid[0, gy, gx] > 0


def test_entity_outside_grid_ignored():
    encoder = StateEncoder(phase=1, grid_size=64)
    entity = Entity(name="stone-furnace", position={"x": 100.0, "y": 100.0})
    state = _make_state(entities=[entity])
    grid, _ = encoder.encode(state)
    assert grid[0].sum() == 0


def test_inventory_in_features():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state(inventory={"iron-plate": 50, "copper-plate": 25})
    _, features = encoder.encode(state)
    assert features.sum() > 0


def test_research_in_features():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state(research_name="automation", research_progress=0.5)
    _, features = encoder.encode(state)
    assert features.sum() > 0


def test_curriculum_context_in_features():
    encoder = StateEncoder(phase=2, grid_size=64, lesson_index=3)
    state = _make_state()
    _, features = encoder.encode(state)
    assert features.shape[0] > 50


def test_feature_dim_property():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    _, features = encoder.encode(state)
    assert features.shape[0] == encoder.feature_dim
