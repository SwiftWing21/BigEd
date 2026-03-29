# fleet/factorio/reward.py
"""Phase-gated per-step reward function for Factorio RL agent."""
import logging
import numpy as np

from factorio.state_parser import GameState

log = logging.getLogger(__name__)

# Reward constants
_TIME_PENALTY = -0.01
_FAILED_ACTION_PENALTY = -0.1
_LESSON_PASS_BONUS = 1.01  # net reward >= 1.0 after time penalty
_PHASE_COMPLETE_BONUS = 5.0
_NEW_ITEM_BONUS = 0.01
_RESEARCH_PROGRESS_SCALE = 0.1
_NEW_ENTITY_BONUS = 0.05      # phase 2+
_PRODUCTION_DELTA_SCALE = 0.02  # phase 2+
_PRODUCTION_DELTA_CAP = 5.0     # max units counted per step


class RunningStats:
    """Online mean/variance tracker for reward normalization (Welford's algorithm)."""

    def __init__(self) -> None:
        self._n: int = 0
        self._mean: float = 0.0
        self._M2: float = 0.0  # sum of squared deviations

    def update(self, x: float) -> None:
        self._n += 1
        delta = x - self._mean
        self._mean += delta / self._n
        delta2 = x - self._mean
        self._M2 += delta * delta2

    @property
    def variance(self) -> float:
        if self._n < 2:
            return 1.0
        return self._M2 / (self._n - 1)

    @property
    def std(self) -> float:
        return float(np.sqrt(self.variance))

    def normalize(self, x: float) -> float:
        """Return (x - mean) / (std + eps)."""
        if self._n < 2:
            return x
        return (x - self._mean) / (self.std + 1e-8)

    def reset(self) -> None:
        self._n = 0
        self._mean = 0.0
        self._M2 = 0.0


class RewardComputer:
    """Compute per-step rewards with phase-gated bonuses.

    Phase 1: time pressure, action failure/success, lesson pass, phase complete,
             exploration (new inventory items), research progress delta.
    Phase 2+: adds entity placement bonus and production delta bonus.
    """

    def __init__(self, phase: int = 1) -> None:
        self._phase = phase
        self._stats = RunningStats()
        # Track entity unit_numbers seen to count new placements
        self._seen_entity_ids: set[int] = set()

    def set_phase(self, phase: int) -> None:
        self._phase = phase

    def reset_normalizer(self) -> None:
        self._stats.reset()
        self._seen_entity_ids.clear()

    def normalize(self, reward: float) -> float:
        return self._stats.normalize(reward)

    def compute(
        self,
        prev_state: GameState,
        curr_state: GameState,
        action_success: bool,
        lesson_passed: bool,
        phase_complete: bool,
    ) -> float:
        """Compute the reward for one environment step.

        Args:
            prev_state: State before the action.
            curr_state: State after the action.
            action_success: Whether the action was executed without error.
            lesson_passed: Whether the current lesson objective was achieved.
            phase_complete: Whether all lessons in the current phase are done.

        Returns:
            Scalar reward (float).
        """
        try:
            reward = self._raw_reward(
                prev_state, curr_state, action_success, lesson_passed, phase_complete
            )
        except Exception:
            log.warning("RewardComputer.compute failed; returning time penalty", exc_info=True)
            reward = _TIME_PENALTY

        self._stats.update(reward)
        return float(reward)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _raw_reward(
        self,
        prev: GameState,
        curr: GameState,
        action_success: bool,
        lesson_passed: bool,
        phase_complete: bool,
    ) -> float:
        r = 0.0

        # Always-on signals
        r += _TIME_PENALTY

        if not action_success:
            r += _FAILED_ACTION_PENALTY

        if lesson_passed:
            r += _LESSON_PASS_BONUS

        if phase_complete:
            r += _PHASE_COMPLETE_BONUS

        # Exploration: new item types in inventory
        prev_items = set(prev.inventory.keys())
        curr_items = set(curr.inventory.keys())
        new_items = curr_items - prev_items
        r += len(new_items) * _NEW_ITEM_BONUS

        # Research progress delta
        research_delta = max(0.0, curr.research_progress - prev.research_progress)
        r += research_delta * _RESEARCH_PROGRESS_SCALE

        # Phase 2+ signals
        if self._phase >= 2:
            r += self._entity_placement_bonus(curr)
            r += self._production_delta_bonus(prev, curr)

        return r

    def _entity_placement_bonus(self, curr: GameState) -> float:
        """Bonus for each newly placed entity (identified by unit_number)."""
        bonus = 0.0
        for entity in curr.entities:
            uid = entity.unit_number
            if uid and uid not in self._seen_entity_ids:
                self._seen_entity_ids.add(uid)
                bonus += _NEW_ENTITY_BONUS
        return bonus

    def _production_delta_bonus(self, prev: GameState, curr: GameState) -> float:
        """Bonus proportional to net increase in inventory item counts (capped)."""
        prev_inv = prev.inventory
        curr_inv = curr.inventory
        delta = 0.0
        for item, count in curr_inv.items():
            prev_count = prev_inv.get(item, 0)
            delta += max(0, count - prev_count)
        delta = min(delta, _PRODUCTION_DELTA_CAP)
        return delta * _PRODUCTION_DELTA_SCALE
