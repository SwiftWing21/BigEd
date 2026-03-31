# fleet/factorio/reward.py
"""Phase-gated per-step reward function for Factorio RL agent."""
import logging
import math
import numpy as np

from factorio.action_space import ActionType
from factorio.state_parser import GameState

log = logging.getLogger(__name__)

# Reward constants — tuned to break "shuffle" local minimum
_TIME_PENALTY = -0.001           # was -0.01 — reduced so non-move actions aren't punished as hard
_FAILED_ACTION_PENALTY = -0.02   # was -0.1 — reduced so agent isn't terrified of trying things
_SUCCESSFUL_ACTION_BONUS = 0.05  # NEW — any successful non-move action gets a bonus
_LESSON_PASS_BONUS = 2.0         # was 1.01 — bigger carrot for lesson completion
_PHASE_COMPLETE_BONUS = 10.0     # was 5.0
_NEW_ITEM_BONUS = 0.1            # was 0.01 — first time getting a new item type is exciting
_RESEARCH_PROGRESS_SCALE = 0.2   # was 0.1
_NEW_ENTITY_BONUS = 0.5          # was 0.05 — placing a building is a BIG deal
_PRODUCTION_DELTA_SCALE = 0.05   # was 0.02 — phase 2+
_PRODUCTION_DELTA_CAP = 5.0
# Distance-based — agents MUST stay near resources/infrastructure
_WANDER_PENALTY_SCALE = -0.02    # was -0.002 — 10x stronger to stop wandering
_WANDER_DISTANCE_THRESHOLD = 10  # was 20 — penalize sooner
_NEAR_RESOURCE_BONUS = 0.003     # was 0.001 — stronger magnet toward resources
# Economic score — THE primary reward signal
_ECONOMIC_SCALE = 0.05           # was 0.01 — 5x boost
_ECONOMIC_INVENTORY_SCALE = 0.005  # was 0.001 — inventory value matters more
# Consecutive-move penalty — walking repeatedly without doing anything useful
_CONSECUTIVE_MOVE_PENALTY = -0.01  # per consecutive MOVE beyond 2
# Agent clustering penalty — repel agents from each other
_CLUSTER_PENALTY_SCALE = -0.01     # penalty when agents are within threshold
_CLUSTER_DISTANCE_THRESHOLD = 8.0  # tiles — agents closer than this get penalized
# Pack / stamp completion bonuses
_PACK_COMPLETE_BONUS = 1.0
_STAMP_COMPLETE_BONUS = 2.0
_PACK_ABORT_PENALTY = -0.5
_ORE_PROXIMITY_BONUS = 0.1  # lesson 2: reward for being adjacent to ore


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

    def __init__(self, phase: int = 1, spatial_memory=None, economic_scorer=None,
                 reward_config: dict | None = None) -> None:
        self._phase = phase
        self._stats = RunningStats()
        # Track entity unit_numbers seen to count new placements
        self._seen_entity_ids: set[int] = set()
        self._spatial_memory = spatial_memory
        self._economic_scorer = economic_scorer
        self._prev_produced: dict[str, int] = {}
        self._prev_inventory_value: float = 0.0
        self._consecutive_moves: int = 0

        # Reward constants — configurable via fleet.toml [factorio.reward]
        cfg = reward_config or {}
        self._time_penalty = cfg.get("time_penalty", _TIME_PENALTY)
        self._failed_action_penalty = cfg.get("failed_action_penalty", _FAILED_ACTION_PENALTY)
        self._successful_action_bonus = cfg.get("successful_action_bonus", _SUCCESSFUL_ACTION_BONUS)
        self._lesson_pass_bonus = cfg.get("lesson_pass_bonus", _LESSON_PASS_BONUS)
        self._phase_complete_bonus = cfg.get("phase_complete_bonus", _PHASE_COMPLETE_BONUS)
        self._new_item_bonus = cfg.get("new_item_bonus", _NEW_ITEM_BONUS)
        self._research_progress_scale = cfg.get("research_progress_scale", _RESEARCH_PROGRESS_SCALE)
        self._new_entity_bonus = cfg.get("new_entity_bonus", _NEW_ENTITY_BONUS)
        self._production_delta_scale = cfg.get("production_delta_scale", _PRODUCTION_DELTA_SCALE)
        self._production_delta_cap = cfg.get("production_delta_cap", _PRODUCTION_DELTA_CAP)
        self._wander_penalty_scale = cfg.get("wander_penalty_scale", _WANDER_PENALTY_SCALE)
        self._wander_distance_threshold = cfg.get("wander_distance_threshold", _WANDER_DISTANCE_THRESHOLD)
        self._near_resource_bonus = cfg.get("near_resource_bonus", _NEAR_RESOURCE_BONUS)
        self._economic_scale = cfg.get("economic_scale", _ECONOMIC_SCALE)
        self._economic_inventory_scale = cfg.get("economic_inventory_scale", _ECONOMIC_INVENTORY_SCALE)
        self._consecutive_move_penalty = cfg.get("consecutive_move_penalty", _CONSECUTIVE_MOVE_PENALTY)
        self._cluster_penalty_scale = cfg.get("cluster_penalty_scale", _CLUSTER_PENALTY_SCALE)
        self._cluster_distance_threshold = cfg.get("cluster_distance_threshold", _CLUSTER_DISTANCE_THRESHOLD)
        self._pack_complete_bonus = cfg.get("pack_complete_bonus", _PACK_COMPLETE_BONUS)
        self._stamp_complete_bonus = cfg.get("stamp_complete_bonus", _STAMP_COMPLETE_BONUS)
        self._pack_abort_penalty = cfg.get("pack_abort_penalty", _PACK_ABORT_PENALTY)
        self._ore_proximity_bonus = cfg.get("ore_proximity_bonus", _ORE_PROXIMITY_BONUS)

    def set_phase(self, phase: int) -> None:
        self._phase = phase

    def reset_normalizer(self) -> None:
        self._stats.reset()
        self._seen_entity_ids.clear()
        self._consecutive_moves = 0

    def normalize(self, reward: float) -> float:
        return self._stats.normalize(reward)

    def compute(
        self,
        prev_state: GameState,
        curr_state: GameState,
        action_success: bool,
        lesson_passed: bool,
        phase_complete: bool,
        metrics=None,
        action_type: int = -1,
        other_agent_positions: list[tuple[float, float]] | None = None,
        pack_completed: bool = False,
        pack_aborted: bool = False,
        lesson_index: int = -1,
        near_ore: bool = False,
    ) -> float:
        """Compute the reward for one environment step.

        Args:
            prev_state: State before the action.
            curr_state: State after the action.
            action_success: Whether the action was executed without error.
            lesson_passed: Whether the current lesson objective was achieved.
            phase_complete: Whether all lessons in the current phase are done.
            metrics: Optional GameMetrics with production data.
            action_type: Integer action type (3=MOVE) for consecutive-move penalty.
            other_agent_positions: Positions of peer agents for clustering penalty.
            pack_completed: Whether the current pack was completed this step.
            pack_aborted: Whether the current pack was aborted this step.
            lesson_index: Current lesson index (used for shaped rewards).
            near_ore: Whether the agent is adjacent to an ore tile (lesson 2 bonus).

        Returns:
            Scalar reward (float).
        """
        try:
            reward = self._raw_reward(
                prev_state, curr_state, action_success, lesson_passed, phase_complete,
                metrics, action_type, other_agent_positions,
                pack_completed, pack_aborted,
                lesson_index, near_ore,
            )
        except Exception:
            log.warning("RewardComputer.compute failed; returning time penalty", exc_info=True)
            reward = self._time_penalty

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
        metrics=None,
        action_type: int = -1,
        other_agent_positions: list[tuple[float, float]] | None = None,
        pack_completed: bool = False,
        pack_aborted: bool = False,
        lesson_index: int = -1,
        near_ore: bool = False,
    ) -> float:
        r = 0.0

        # Always-on signals
        r += self._time_penalty

        # Consecutive-move penalty — stop aimless walking
        if action_type == ActionType.MOVE.value:
            self._consecutive_moves += 1
            if self._consecutive_moves > 2:
                r += self._consecutive_move_penalty * (self._consecutive_moves - 2)
        else:
            self._consecutive_moves = 0

        # Agent clustering penalty — repel from peers
        r += self._clustering_penalty(curr, other_agent_positions)

        if not action_success:
            r += self._failed_action_penalty
        elif action_type != ActionType.MOVE.value:
            r += self._successful_action_bonus  # reward successful non-MOVE actions only

        if lesson_passed:
            r += self._lesson_pass_bonus

        if phase_complete:
            r += self._phase_complete_bonus

        # Exploration: new item types in inventory
        prev_items = set(prev.inventory.keys())
        curr_items = set(curr.inventory.keys())
        new_items = curr_items - prev_items
        r += len(new_items) * self._new_item_bonus

        # Research progress delta
        research_delta = max(0.0, curr.research_progress - prev.research_progress)
        r += research_delta * self._research_progress_scale

        # Distance-from-resources penalty — discourages wandering when ore exists
        r += self._distance_reward(curr)

        # Economic production score — rewards producing valuable items
        r += self._economic_reward(curr, metrics)

        # Entity placement bonus — ALL phases (placing buildings is always good)
        r += self._entity_placement_bonus(curr)
        r += self._production_delta_bonus(prev, curr)

        # Pack / stamp completion bonuses
        if pack_completed:
            if action_type == ActionType.STAMP.value:
                r += self._stamp_complete_bonus
            else:
                r += self._pack_complete_bonus
        if pack_aborted:
            r += self._pack_abort_penalty

        # Ore proximity bonus — lesson 2 only, guides agent toward ore for drill placement
        if lesson_index == 2 and near_ore:
            r += self._ore_proximity_bonus

        return r

    def _clustering_penalty(
        self, curr: GameState,
        other_positions: list[tuple[float, float]] | None,
    ) -> float:
        """Penalize being too close to other agents — prevents convergence."""
        if not other_positions:
            return 0.0
        px = curr.player_position.get("x", 0)
        py = curr.player_position.get("y", 0)
        penalty = 0.0
        for ox, oy in other_positions:
            dist = math.sqrt((ox - px) ** 2 + (oy - py) ** 2)
            if dist < self._cluster_distance_threshold:
                # Stronger penalty the closer they are
                closeness = 1.0 - (dist / self._cluster_distance_threshold)
                penalty += self._cluster_penalty_scale * closeness
        return penalty

    def _distance_reward(self, curr: GameState) -> float:
        """Penalty for wandering far from resources AND infrastructure.

        No penalty if:
        - No known resources (exploring is correct)
        - Near own infrastructure (building production chains)
        - Resources are depleted

        Penalty only when far from BOTH resources AND built entities.
        """
        if self._spatial_memory is None:
            return 0.0

        summary = self._spatial_memory.resource_summary()
        if not summary:
            return 0.0  # no known resources — exploring is fine

        px = curr.player_position.get("x", 0)
        py = curr.player_position.get("y", 0)

        # Distance to nearest resource
        min_resource_dist = float("inf")
        for rtype in ("iron-ore", "copper-ore", "coal", "stone"):
            result = self._spatial_memory.nearest_resource(px, py, rtype)
            if result is not None:
                _, dist = result
                min_resource_dist = min(min_resource_dist, dist)

        # Distance to nearest own infrastructure
        min_entity_dist = float("inf")
        for ename in ("stone-furnace", "burner-mining-drill", "assembling-machine-1",
                       "transport-belt", "inserter", "lab"):
            result = self._spatial_memory.nearest_entity_by_name(px, py, ename)
            if result is not None:
                _, dist = result
                min_entity_dist = min(min_entity_dist, dist)

        # Near resources OR near own infrastructure = no penalty
        min_useful_dist = min(min_resource_dist, min_entity_dist)

        if min_useful_dist == float("inf"):
            return 0.0  # nothing remembered — no penalty

        if min_useful_dist <= 10:
            return self._near_resource_bonus

        if min_useful_dist > self._wander_distance_threshold:
            # Linear ramp: full penalty at 30 tiles excess (was 100)
            excess = min(min_useful_dist - self._wander_distance_threshold, 30)
            return self._wander_penalty_scale * (excess / 30.0)

        return 0.0

    def _economic_reward(self, curr: GameState, metrics=None) -> float:
        """Reward based on economic production value delta.

        Uses total_produced from metrics (cumulative production counts)
        and inventory value increase. Complex items score more than raw ores.
        """
        if self._economic_scorer is None:
            return 0.0

        r = 0.0

        # Production delta from metrics (cumulative production stats)
        if metrics is not None:
            curr_produced = getattr(metrics, "total_produced", {})
            if curr_produced and self._prev_produced:
                delta = self._economic_scorer.score_delta(
                    self._prev_produced, curr_produced
                )
                r += delta * self._economic_scale
            if curr_produced:
                self._prev_produced = dict(curr_produced)

        # Inventory value increase (small bonus — items in hand are useful)
        curr_inv_value = self._economic_scorer.score_inventory_value(curr.inventory)
        inv_delta = max(0.0, curr_inv_value - self._prev_inventory_value)
        r += inv_delta * self._economic_inventory_scale
        self._prev_inventory_value = curr_inv_value

        return r

    def _entity_placement_bonus(self, curr: GameState) -> float:
        """Bonus for each newly placed entity (identified by unit_number)."""
        bonus = 0.0
        for entity in curr.entities:
            uid = entity.unit_number
            if uid and uid not in self._seen_entity_ids:
                self._seen_entity_ids.add(uid)
                bonus += self._new_entity_bonus
        return bonus

    def _production_delta_bonus(self, prev: GameState, curr: GameState) -> float:
        """Bonus proportional to net increase in inventory item counts (capped)."""
        prev_inv = prev.inventory
        curr_inv = curr.inventory
        delta = 0.0
        for item, count in curr_inv.items():
            prev_count = prev_inv.get(item, 0)
            delta += max(0, count - prev_count)
        delta = min(delta, self._production_delta_cap)
        return delta * self._production_delta_scale
