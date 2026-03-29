"""Tests for EpisodeManager — soft/hard reset, step tracking, game speed."""
import sys
from pathlib import Path

# Ensure fleet/ is on sys.path
fleet_dir = str(Path(__file__).resolve().parent.parent.parent / "fleet")
if fleet_dir not in sys.path:
    sys.path.insert(0, fleet_dir)

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from factorio.episode_manager import EpisodeManager


@pytest.fixture
def mock_rcon():
    rcon = AsyncMock()
    rcon.remote_call = AsyncMock(return_value='{"ok": true}')
    rcon.command = AsyncMock(return_value="soft_reset_done")
    return rcon


@pytest.mark.asyncio
async def test_soft_reset(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    result = await em.soft_reset()
    assert result is True
    assert mock_rcon.command.call_count >= 1


@pytest.mark.asyncio
async def test_reset_increments_episode(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    assert em.episode_count == 0
    await em.reset()
    assert em.episode_count == 1
    await em.reset()
    assert em.episode_count == 2


@pytest.mark.asyncio
async def test_episode_info(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    await em.reset()
    info = em.get_episode_info()
    assert info["episode"] == 1
    assert info["phase"] == 1
    assert info["step"] == 0


def test_step_counting(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    em.record_step()
    em.record_step()
    assert em.get_episode_info()["step"] == 2


def test_max_steps_exceeded(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1, max_steps=100)
    for _ in range(100):
        em.record_step()
    assert em.is_episode_done(max_steps=100) is True


@pytest.mark.asyncio
async def test_set_game_speed(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    await em.set_game_speed(10)
    mock_rcon.command.assert_called()


# --- Additional coverage tests ---

def test_initial_state(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=2, max_steps=500)
    info = em.get_episode_info()
    assert info["episode"] == 0
    assert info["phase"] == 2
    assert info["step"] == 0
    assert info["max_steps"] == 500


def test_is_episode_done_not_yet(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1, max_steps=100)
    for _ in range(50):
        em.record_step()
    assert em.is_episode_done(max_steps=100) is False


def test_is_episode_done_uses_instance_max_steps(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1, max_steps=10)
    for _ in range(10):
        em.record_step()
    # No override — uses instance default
    assert em.is_episode_done() is True


def test_set_phase(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    em.set_phase(3)
    assert em.get_episode_info()["phase"] == 3


@pytest.mark.asyncio
async def test_soft_reset_false_on_bad_response(mock_rcon):
    """soft_reset returns False when response does not contain 'soft_reset_done'."""
    mock_rcon.command = AsyncMock(return_value="error: something went wrong")
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    result = await em.soft_reset()
    assert result is False


@pytest.mark.asyncio
async def test_reset_falls_back_to_hard_reset(mock_rcon):
    """reset() calls hard_reset() when soft_reset() returns False."""
    mock_rcon.command = AsyncMock(return_value="unexpected_response")
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    with patch.object(em, "hard_reset", new=AsyncMock()) as mock_hard:
        await em.reset()
        mock_hard.assert_called_once()
    assert em.episode_count == 1


@pytest.mark.asyncio
async def test_reset_resets_step_counter(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    em.record_step()
    em.record_step()
    await em.reset()
    assert em.get_episode_info()["step"] == 0


@pytest.mark.asyncio
async def test_give_starting_items_called_on_reset(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    with patch.object(em, "_give_starting_items", new=AsyncMock()) as mock_items:
        await em.reset()
        mock_items.assert_called_once()


@pytest.mark.asyncio
async def test_game_speed_command_format(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    await em.set_game_speed(5)
    call_args = mock_rcon.command.call_args[0][0]
    assert "game.speed" in call_args
    assert "5" in call_args


def test_episode_info_elapsed_secs_present(mock_rcon):
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    info = em.get_episode_info()
    assert "elapsed_secs" in info
    assert info["elapsed_secs"] >= 0.0


@pytest.mark.asyncio
async def test_phase_items_vary_by_phase(mock_rcon):
    """Different phases produce different give_starting_items calls."""
    for phase in (1, 2, 3, 4):
        em = EpisodeManager(rcon=mock_rcon, phase=phase)
        # Should not raise regardless of phase
        await em._give_starting_items()
    # command was called for each phase
    assert mock_rcon.command.call_count >= 4


@pytest.mark.asyncio
async def test_soft_reset_handles_exception(mock_rcon):
    """soft_reset returns False and does not raise on RCON error."""
    mock_rcon.command = AsyncMock(side_effect=ConnectionError("disconnected"))
    em = EpisodeManager(rcon=mock_rcon, phase=1)
    result = await em.soft_reset()
    assert result is False
