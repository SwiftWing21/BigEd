import numpy as np
import pytest
from factorio.state_parser import GameState, Entity
from factorio.state_encoder import StateEncoder, _BASE_FEATURE_DIM


def _make_state(entities=None, inventory=None, player_pos=None,
                research_name="", research_progress=0.0, tick=0):
    return GameState(
        tick=tick,
        player_position=player_pos or {"x": 0.0, "y": 0.0},
        player_health=250, player_max_health=250,
        player_has_character=True, player_alive=True,
        inventory=inventory or {},
        entities=entities or [],
        research_name=research_name,
        research_progress=research_progress,
    )


def test_encoder_output_shapes():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    grid, world_grid, features = encoder.encode(state)
    assert grid.shape == (5, 64, 64)
    assert world_grid.shape == (4, 64, 64)
    assert features.shape[0] >= 73
    assert grid.dtype == np.float32
    assert features.dtype == np.float32


def test_entity_appears_on_grid():
    encoder = StateEncoder(phase=1, grid_size=64)
    entity = Entity(name="stone-furnace", position={"x": 3.0, "y": -2.0}, direction=4)
    state = _make_state(entities=[entity])
    grid, _, _ = encoder.encode(state)
    gx, gy = 3 + 32, -2 + 32
    assert grid[0, gy, gx] > 0


def test_entity_outside_grid_ignored():
    encoder = StateEncoder(phase=1, grid_size=64)
    entity = Entity(name="stone-furnace", position={"x": 100.0, "y": 100.0})
    state = _make_state(entities=[entity])
    grid, _, _ = encoder.encode(state)
    assert grid[0].sum() == 0


def test_inventory_in_features():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state(inventory={"iron-plate": 50, "copper-plate": 25})
    _, _, features = encoder.encode(state)
    assert features.sum() > 0


def test_research_in_features():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state(research_name="automation", research_progress=0.5)
    _, _, features = encoder.encode(state)
    assert features.sum() > 0


def test_curriculum_context_in_features():
    encoder = StateEncoder(phase=2, grid_size=64, lesson_index=3)
    state = _make_state()
    _, _, features = encoder.encode(state)
    assert features.shape[0] >= 73


def test_feature_dim_property():
    encoder = StateEncoder(phase=1, grid_size=64)
    state = _make_state()
    _, _, features = encoder.encode(state)
    assert features.shape[0] == encoder.feature_dim


# -------------------------------------------------------------------
# Phase one-hot expansion tests (4 -> 8 slots)
# -------------------------------------------------------------------

def test_base_feature_dim_is_73():
    assert _BASE_FEATURE_DIM == 73


def test_phase_onehot_8_phases():
    """Phase one-hot uses 8 slots, not 4."""
    for phase in range(1, 9):
        enc = StateEncoder(phase=phase)
        assert enc.feature_dim >= 73
        assert enc._phase == phase  # no clamping


def test_phase_onehot_sets_correct_slot():
    """Each phase activates only its own slot in [56:64]."""
    for phase in range(1, 9):
        enc = StateEncoder(phase=phase, grid_size=64)
        state = _make_state()
        _, _, features = enc.encode(state)
        # Only slot 56 + (phase-1) should be 1.0
        for i in range(8):
            expected = 1.0 if i == phase - 1 else 0.0
            assert features[56 + i] == expected, \
                f"Phase {phase}: slot {56 + i} expected {expected}, got {features[56 + i]}"


def test_phase_clamp_upper():
    """Phase > 8 should clamp to 8."""
    enc = StateEncoder(phase=10)
    assert enc._phase == 8


def test_phase_clamp_lower():
    """Phase < 1 should clamp to 1."""
    enc = StateEncoder(phase=0)
    assert enc._phase == 1
