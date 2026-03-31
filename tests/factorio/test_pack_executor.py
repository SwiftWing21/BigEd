"""Tests for PackExecutor — multi-tick state machine for action pack execution."""

import copy
import pytest

# Task 1 may not exist yet; provide a minimal stub if needed
try:
    from factorio.pack_registry import ActionPack
except ImportError:
    from dataclasses import dataclass, field
    from typing import List

    @dataclass
    class ActionPack:
        name: str
        actions: List[dict] = field(default_factory=list)
        phase_required: int = 0
        origin: str = "hardcoded"

from factorio.pack_executor import PackExecutor


def _make_pack(actions, name="test_pack"):
    return ActionPack(name=name, actions=actions, phase_required=0, origin="hardcoded")


class TestPackExecutorStart:
    def test_executor_start_returns_first_action(self):
        """start() returns first action with position offset applied."""
        actions = [
            {"type": "place", "item": "belt", "position": {"x": 0, "y": 0}},
            {"type": "place", "item": "inserter", "position": {"x": 1, "y": 0}},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()

        result = executor.start(pack, offset=(10, 20))

        assert result["type"] == "place"
        assert result["item"] == "belt"
        assert result["position"]["x"] == 10
        assert result["position"]["y"] == 20
        assert executor.is_active

    def test_start_without_position_key(self):
        """Actions without position key are returned without modification."""
        actions = [{"type": "craft", "item": "gear"}]
        pack = _make_pack(actions)
        executor = PackExecutor()

        result = executor.start(pack, offset=(5, 5))

        assert result["type"] == "craft"
        assert result["item"] == "gear"
        assert "position" not in result


class TestPackExecutorNextStep:
    def test_executor_next_step_advances(self):
        """next_step with success advances to the second action."""
        actions = [
            {"type": "place", "item": "belt", "position": {"x": 0, "y": 0}},
            {"type": "place", "item": "inserter", "position": {"x": 1, "y": 0}},
            {"type": "place", "item": "chest", "position": {"x": 2, "y": 0}},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()
        executor.start(pack, offset=(0, 0))

        result = executor.next_step({"success": True})

        assert result is not None
        assert result["item"] == "inserter"
        assert result["position"]["x"] == 1

    def test_executor_completes_after_last_step(self):
        """After the last action succeeds, executor marks completed."""
        actions = [
            {"type": "place", "item": "belt", "position": {"x": 0, "y": 0}},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()
        executor.start(pack, offset=(0, 0))

        result = executor.next_step({"success": True})

        assert result is None
        assert executor.completed
        assert not executor.is_active

    def test_executor_retries_on_failure(self):
        """On failure, next_step returns the same action (retry)."""
        actions = [
            {"type": "place", "item": "belt", "position": {"x": 0, "y": 0}},
            {"type": "place", "item": "inserter", "position": {"x": 1, "y": 0}},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()
        executor.start(pack, offset=(0, 0))

        # First failure — retry same action
        result = executor.next_step({"success": False})
        assert result is not None
        assert result["item"] == "belt"

    def test_executor_aborts_after_3_failures(self):
        """After 3 consecutive failures on the same step, executor aborts."""
        actions = [
            {"type": "place", "item": "belt", "position": {"x": 0, "y": 0}},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()
        executor.start(pack, offset=(0, 0))

        # Three failures
        result1 = executor.next_step({"success": False})
        assert result1 is not None  # retry 1
        result2 = executor.next_step({"success": False})
        assert result2 is not None  # retry 2
        result3 = executor.next_step({"success": False})

        assert result3 is None
        assert executor.abort_reason == "step_failed_3x"
        assert not executor.is_active


class TestPackExecutorProgress:
    def test_executor_progress_fraction(self):
        """Progress is step_index / total actions."""
        actions = [
            {"type": "a"},
            {"type": "b"},
            {"type": "c"},
            {"type": "d"},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()

        # Before start
        assert executor.progress == 0.0

        executor.start(pack, offset=(0, 0))
        assert executor.progress == pytest.approx(0.0)  # step 0 / 4

        executor.next_step({"success": True})
        assert executor.progress == pytest.approx(0.25)  # step 1 / 4

        executor.next_step({"success": True})
        assert executor.progress == pytest.approx(0.5)  # step 2 / 4


class TestPackExecutorReward:
    def test_executor_cumulative_reward_tracking(self):
        """accumulate_reward adds to running total."""
        executor = PackExecutor()
        assert executor.cumulative_reward == 0.0

        executor.accumulate_reward(1.5)
        assert executor.cumulative_reward == pytest.approx(1.5)

        executor.accumulate_reward(-0.3)
        assert executor.cumulative_reward == pytest.approx(1.2)


class TestPackExecutorAbort:
    def test_force_abort(self):
        """abort() force-stops execution with a reason."""
        actions = [{"type": "a"}, {"type": "b"}]
        pack = _make_pack(actions)
        executor = PackExecutor()
        executor.start(pack, offset=(0, 0))

        executor.abort("manual_stop")

        assert executor.abort_reason == "manual_stop"
        assert not executor.is_active

    def test_current_pack_property(self):
        """current_pack returns the active pack or None."""
        executor = PackExecutor()
        assert executor.current_pack is None

        pack = _make_pack([{"type": "a"}])
        executor.start(pack, offset=(0, 0))
        assert executor.current_pack is pack


class TestPackExecutorOffset:
    def test_offset_applied_to_all_actions(self):
        """Position offset applied consistently across steps."""
        actions = [
            {"type": "place", "position": {"x": 0, "y": 0}},
            {"type": "place", "position": {"x": 1, "y": 2}},
        ]
        pack = _make_pack(actions)
        executor = PackExecutor()

        first = executor.start(pack, offset=(100, 200))
        assert first["position"] == {"x": 100, "y": 200}

        second = executor.next_step({"success": True})
        assert second["position"] == {"x": 101, "y": 202}

    def test_offset_does_not_mutate_original(self):
        """Offset application uses deepcopy — original actions unchanged."""
        actions = [{"type": "place", "position": {"x": 5, "y": 10}}]
        pack = _make_pack(actions)
        executor = PackExecutor()

        result = executor.start(pack, offset=(10, 20))
        assert result["position"]["x"] == 15
        # Original unchanged
        assert pack.actions[0]["position"]["x"] == 5
