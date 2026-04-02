"""Tests for pack_progress feature in StateEncoder."""
import pytest
np = pytest.importorskip("numpy")


def test_feature_dim_includes_pack_progress():
    from factorio.state_encoder import StateEncoder, _BASE_FEATURE_DIM
    assert _BASE_FEATURE_DIM == 73
    enc = StateEncoder(phase=1, grid_size=64)
    assert enc.feature_dim >= 73


def test_pack_progress_default_zero():
    """pack_progress defaults to 0.0 when not supplied."""
    from factorio.state_encoder import StateEncoder
    from factorio.state_parser import GameState

    enc = StateEncoder(phase=1, grid_size=16)
    state = GameState(
        tick=100,
        player_position={"x": 0.0, "y": 0.0},
        inventory={},
        entities=[],
    )
    _grid, _world, features = enc.encode(state)
    assert features[72] == pytest.approx(0.0)


def test_pack_progress_encoded_at_index_68():
    """pack_progress=0.75 should appear at feature index 68."""
    from factorio.state_encoder import StateEncoder
    from factorio.state_parser import GameState

    enc = StateEncoder(phase=1, grid_size=16)
    state = GameState(
        tick=100,
        player_position={"x": 0.0, "y": 0.0},
        inventory={},
        entities=[],
    )
    _grid, _world, features = enc.encode(state, pack_progress=0.75)
    assert features[72] == pytest.approx(0.75)


def test_pack_progress_clipped():
    """Values outside [0, 1] should be clipped."""
    from factorio.state_encoder import StateEncoder
    from factorio.state_parser import GameState

    enc = StateEncoder(phase=1, grid_size=16)
    state = GameState(
        tick=0,
        player_position={"x": 0.0, "y": 0.0},
        inventory={},
        entities=[],
    )
    _grid, _world, features = enc.encode(state, pack_progress=1.5)
    assert features[72] == pytest.approx(1.0)

    _grid, _world, features = enc.encode(state, pack_progress=-0.3)
    assert features[72] == pytest.approx(0.0)
