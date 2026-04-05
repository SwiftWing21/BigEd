"""Adaptive tick governor — paces RL actions based on Factorio UPS.

Measures effective game UPS via a sliding window of (wall_time, game_tick)
samples, then computes an adaptive sleep so actions don't outrun the game.

When the LLM teacher has queued actions, the governor enters "teacher mode"
with a separate (typically slower) delay — success > speed.
"""
import logging
from collections import deque

log = logging.getLogger("biged.factorio.tick_governor")


class TickGovernor:
    """Adaptive delay calculator based on observed Factorio UPS.

    Args:
        delay_min_ms: Fastest action pace (healthy UPS).
        delay_max_ms: Slowest action pace (degraded UPS / no samples).
        target_ups: Expected ticks/sec at current game_speed (e.g. 120 at 2x).
        teacher_delay_ms: Fixed delay used during teacher-exclusive ticks.
        window_size: Number of (wall_time, game_tick) samples to keep.
    """

    def __init__(
        self,
        delay_min_ms: int = 200,
        delay_max_ms: int = 1000,
        target_ups: int = 120,
        teacher_delay_ms: int = 750,
        window_size: int = 10,
    ) -> None:
        self._delay_min = delay_min_ms
        self._delay_max = delay_max_ms
        self._target_ups = max(target_ups, 1)  # avoid division by zero
        self._teacher_delay = teacher_delay_ms
        self._teacher_mode = False
        self._samples: deque[tuple[int, float]] = deque(maxlen=window_size)

    # ── public API ──────────────────────────────────────────────────

    def record_tick(self, game_tick: int, wall_time: float) -> None:
        """Record a (game_tick, wall_time) sample for UPS calculation."""
        self._samples.append((game_tick, wall_time))

    def get_delay_ms(self) -> int:
        """Return the delay in ms that the main loop should sleep.

        In teacher mode, returns the teacher delay regardless of UPS.
        Otherwise, scales linearly between min and max based on UPS health.
        """
        if self._teacher_mode:
            return self._teacher_delay

        health = self._ups_health()
        # health=1.0 → min delay, health=0.0 → max delay
        delay = self._delay_max - health * (self._delay_max - self._delay_min)
        return round(delay)

    def set_teacher_mode(self, active: bool) -> None:
        """Enter or exit teacher-exclusive mode."""
        if active != self._teacher_mode:
            log.info("Tick governor: teacher mode %s", "ON" if active else "OFF")
        self._teacher_mode = active

    @property
    def is_teacher_mode(self) -> bool:
        return self._teacher_mode

    @property
    def observed_ups(self) -> float:
        """Current observed UPS (for logging/dashboard)."""
        if len(self._samples) < 2:
            return 0.0
        oldest_tick, oldest_wall = self._samples[0]
        newest_tick, newest_wall = self._samples[-1]
        dt = newest_wall - oldest_wall
        if dt <= 0:
            return 0.0
        return (newest_tick - oldest_tick) / dt

    # ── internals ───────────────────────────────────────────────────

    def _ups_health(self) -> float:
        """Return 0.0 (stalled) to 1.0 (at or above target UPS)."""
        ups = self.observed_ups
        if ups <= 0:
            return 0.0
        return min(1.0, ups / self._target_ups)
