import numpy as np
import pytest
from factorio.state_parser import GameState
from factorio.state_encoder import StateEncoder
from factorio.spatial_memory import SpatialMemory


def _make_state(player_pos=None, tick=0):
    return GameState(
        tick=tick,
        player_position=player_pos or {"x": 0.0, "y": 0.0},
        player_health=250, player_max_health=250,
        player_has_character=True, player_alive=True,
        inventory={},
        entities=[],
        research_name="",
        research_progress=0.0,
    )


def test_feature_dim_without_memory():
    encoder = StateEncoder(phase=1, grid_size=64)
    assert encoder.feature_dim == 64


def test_feature_dim_with_memory():
    mem = SpatialMemory()
    encoder = StateEncoder(phase=1, grid_size=64, spatial_memory=mem)
    assert encoder.feature_dim == 80


def test_encode_output_shape_without_memory():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    _, features = encoder.encode(state)
    assert features.shape == (64,)
    assert features.dtype == np.float32


def test_encode_output_shape_with_memory():
    mem = SpatialMemory()
    encoder = StateEncoder(phase=1, grid_size=64, spatial_memory=mem)
    state = _make_state()
    _, features = encoder.encode(state)
    assert features.shape == (80,)
    assert features.dtype == np.float32


def test_spatial_features_populated():
    """Spatial features reflect actual memory content."""
    mem = SpatialMemory()
    mem._upsert_resource("iron-ore", 10, 20, 500, tick=100)
    encoder = StateEncoder(phase=1, grid_size=64, spatial_memory=mem)
    state = _make_state(player_pos={"x": 0.0, "y": 0.0})
    _, features = encoder.encode(state)
    # The spatial portion (indices 64:80) should have nonzero entries
    spatial_part = features[64:]
    assert spatial_part.shape == (16,)
    assert spatial_part.sum() > 0, "Expected nonzero spatial features with memory data"
