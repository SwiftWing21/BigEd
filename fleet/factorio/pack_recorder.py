"""PackRecorder — records agent actions and extracts candidate packs.

When the agent completes a checkpoint, the recent action sequence is
extracted as a candidate learned pack for evaluation by
PackRegistry.promote_learned().
"""
from __future__ import annotations

import time
from collections import deque

from factorio.pack_registry import ActionPack

_MIN_PACK_LENGTH = 5
_MAX_PACK_LENGTH = 50


class PackRecorder:
    """Records primitive actions during training and extracts candidate packs."""

    def __init__(self, max_length: int = 100) -> None:
        self.buffer: deque[dict] = deque(maxlen=max_length)

    def record(self, action: dict) -> None:
        """Append an action to the buffer."""
        self.buffer.append(action)

    def on_checkpoint_complete(self, checkpoint_id: int) -> ActionPack | None:
        """Extract a candidate pack from the buffer.

        Returns None if fewer than _MIN_PACK_LENGTH actions recorded.
        If more than _MAX_PACK_LENGTH actions, takes the most recent 50.
        """
        actions = list(self.buffer)
        if len(actions) < _MIN_PACK_LENGTH:
            return None
        if len(actions) > _MAX_PACK_LENGTH:
            actions = actions[-_MAX_PACK_LENGTH:]
        name = f"learned_{checkpoint_id}_{int(time.time())}"
        return ActionPack(
            name=name,
            actions=actions,
            phase_required=checkpoint_id,
            origin="learned",
        )

    def clear(self) -> None:
        """Clear the buffer."""
        self.buffer.clear()
