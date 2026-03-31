"""PackExecutor — multi-tick state machine for action pack execution.

Manages in-flight pack execution, emitting one primitive action per tick.
The bridge calls start() when a PACK action is selected, then next_step()
on each subsequent tick until completion or abort.
"""

from __future__ import annotations

import copy
from typing import Optional

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

_MAX_RETRIES = 3


class PackExecutor:
    """State machine that executes an ActionPack one step per tick."""

    def __init__(self) -> None:
        self._pack: Optional[ActionPack] = None
        self._step_index: int = 0
        self._retry_count: int = 0
        self._offset: tuple[int | float, int | float] = (0, 0)
        self._abort_reason: Optional[str] = None
        self._completed: bool = False
        self._cumulative_reward: float = 0.0

    # -- Properties ----------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True while a pack is loaded and neither completed nor aborted."""
        return self._pack is not None and not self._completed and self._abort_reason is None

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def abort_reason(self) -> Optional[str]:
        return self._abort_reason

    @property
    def progress(self) -> float:
        """Fraction of steps completed (0.0 .. 1.0)."""
        if self._pack is None or not self._pack.actions:
            return 0.0
        return self._step_index / len(self._pack.actions)

    @property
    def cumulative_reward(self) -> float:
        return self._cumulative_reward

    @property
    def current_pack(self) -> Optional[ActionPack]:
        return self._pack

    # -- Public methods ------------------------------------------------------

    def accumulate_reward(self, reward: float) -> None:
        """Add *reward* to the running total."""
        self._cumulative_reward += reward

    def start(self, pack: ActionPack, offset: tuple[int | float, int | float] = (0, 0)) -> dict:
        """Begin executing *pack*.  Returns the first action (offset-adjusted)."""
        self._pack = pack
        self._step_index = 0
        self._retry_count = 0
        self._offset = offset
        self._abort_reason = None
        self._completed = False
        return self._current_action()

    def next_step(self, prev_result: dict) -> Optional[dict]:
        """Advance (or retry) based on *prev_result*.

        Returns the next action dict, or ``None`` when the pack is finished
        or has been aborted.
        """
        if prev_result.get("success"):
            self._retry_count = 0
            self._step_index += 1
            if self._step_index >= len(self._pack.actions):
                self._completed = True
                return None
            return self._current_action()

        # Failure path — retry or abort
        self._retry_count += 1
        if self._retry_count >= _MAX_RETRIES:
            self._abort_reason = "step_failed_3x"
            return None
        return self._current_action()

    def abort(self, reason: str) -> None:
        """Force-abort with the given *reason*."""
        self._abort_reason = reason

    # -- Internal ------------------------------------------------------------

    def _current_action(self) -> dict:
        """Return a deepcopy of the current action with position offset applied."""
        action = copy.deepcopy(self._pack.actions[self._step_index])
        if "position" in action:
            action["position"]["x"] += self._offset[0]
            action["position"]["y"] += self._offset[1]
        return action
