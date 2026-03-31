"""ActionPack / BlueprintStamp registry for the Factorio RL agent.

Provides typed action-pack and blueprint data models plus a slot-based
registry used by the bridge tick loop and the policy network's action mask.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Union

log = logging.getLogger(__name__)

MAX_PACK_SLOTS = 64


# ── Data Models ──────────────────────────────────────────────────


@dataclass
class ActionPack:
    """A reusable sequence of game actions."""

    name: str
    actions: list[dict]
    phase_required: int
    origin: str
    success_count: int = 0
    avg_reward: float = 0.0
    required_items: dict = field(default_factory=dict)
    total_invocations: int = 0

    def can_execute(self, inventory: dict) -> bool:
        """Return True if *inventory* contains all required_items."""
        for item, qty in self.required_items.items():
            if inventory.get(item, 0) < qty:
                return False
        return True

    def record_result(self, success: bool, reward: float) -> None:
        """Update running stats after an invocation."""
        self.total_invocations += 1
        if success:
            self.success_count += 1
        # Running average
        self.avg_reward += (reward - self.avg_reward) / self.total_invocations

    @property
    def success_rate(self) -> float:
        if self.total_invocations == 0:
            return 0.0
        return self.success_count / self.total_invocations


@dataclass
class BlueprintStamp:
    """A pre-built blueprint that can be stamped onto the map."""

    name: str
    blueprint_string: str
    footprint: tuple[int, int]
    phase_required: int
    required_items: dict = field(default_factory=dict)

    def can_execute(self, inventory: dict) -> bool:
        for item, qty in self.required_items.items():
            if inventory.get(item, 0) < qty:
                return False
        return True


# ── Helpers ──────────────────────────────────────────────────────


def _action_overlap(a: list[dict], b: list[dict]) -> float:
    """Jaccard similarity on JSON-serialised actions."""
    set_a = {json.dumps(act, sort_keys=True) for act in a}
    set_b = {json.dumps(act, sort_keys=True) for act in b}
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


# ── Registry ─────────────────────────────────────────────────────


class PackRegistry:
    """Slot-based registry for ActionPacks and BlueprintStamps.

    Uses copy-on-write lists so readers never see a half-mutated state.
    """

    def __init__(self) -> None:
        self._items: list[Union[ActionPack, BlueprintStamp]] = []
        self._stamp_flags: list[bool] = []

    # ── registration ─────────────────────────────────────────────

    def register_pack(self, pack: ActionPack) -> int:
        return self._register(pack, is_stamp=False)

    def register_stamp(self, stamp: BlueprintStamp) -> int:
        return self._register(stamp, is_stamp=True)

    def _register(self, item: Union[ActionPack, BlueprintStamp], *, is_stamp: bool) -> int:
        if len(self._items) >= MAX_PACK_SLOTS:
            raise ValueError(f"MAX_PACK_SLOTS ({MAX_PACK_SLOTS}) reached")
        slot_id = len(self._items)
        # Copy-on-write: create new lists then swap
        new_items = list(self._items)
        new_flags = list(self._stamp_flags)
        new_items.append(item)
        new_flags.append(is_stamp)
        self._items = new_items
        self._stamp_flags = new_flags
        return slot_id

    # ── lookups ──────────────────────────────────────────────────

    def get_by_id(self, slot_id: int) -> Union[ActionPack, BlueprintStamp]:
        return self._items[slot_id]

    def is_stamp(self, slot_id: int) -> bool:
        return self._stamp_flags[slot_id]

    def get_available(self, phase: int) -> list[Union[ActionPack, BlueprintStamp]]:
        return [item for item in self._items if item.phase_required <= phase]

    def get_pack_mask(self, phase: int, inventory: dict) -> list[int]:
        """Return a MAX_PACK_SLOTS-length binary mask.

        1 if the slot is filled, phase-available, and inventory-executable.
        """
        mask = [0] * MAX_PACK_SLOTS
        for i, item in enumerate(self._items):
            if item.phase_required <= phase and item.can_execute(inventory):
                mask[i] = 1
        return mask

    # ── learned-pack promotion ───────────────────────────────────

    def promote_learned(self, candidate: ActionPack) -> int | None:
        """Vet and register a candidate pack discovered by the agent."""
        n = len(candidate.actions)
        if n < 5 or n > 50:
            return None
        # Check overlap against every existing pack that has actions
        for item in self._items:
            if isinstance(item, ActionPack):
                if _action_overlap(candidate.actions, item.actions) > 0.80:
                    return None
        candidate.origin = "learned"
        return self.register_pack(candidate)

    # ── persistence ──────────────────────────────────────────────

    def save_learned(self, directory: str) -> None:
        """Serialize all learned packs to *directory* as JSON files."""
        os.makedirs(directory, exist_ok=True)
        for item in self._items:
            if isinstance(item, ActionPack) and item.origin == "learned":
                path = os.path.join(directory, f"{item.name}.json")
                data = {
                    "name": item.name,
                    "actions": item.actions,
                    "phase_required": item.phase_required,
                    "origin": item.origin,
                    "success_count": item.success_count,
                    "avg_reward": item.avg_reward,
                    "required_items": item.required_items,
                    "total_invocations": item.total_invocations,
                }
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)

    def load_packs(self, directory: str) -> int:
        """Load ActionPacks from JSON files in *directory*. Returns count."""
        count = 0
        if not os.path.isdir(directory):
            return 0
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(directory, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                pack = ActionPack(
                    name=data["name"],
                    actions=data["actions"],
                    phase_required=data["phase_required"],
                    origin=data.get("origin", "loaded"),
                    success_count=data.get("success_count", 0),
                    avg_reward=data.get("avg_reward", 0.0),
                    required_items=data.get("required_items", {}),
                    total_invocations=data.get("total_invocations", 0),
                )
                self.register_pack(pack)
                count += 1
            except Exception:
                log.warning("Failed to load pack from %s", path, exc_info=True)
        return count

    def load_stamps(self, directory: str) -> int:
        """Load BlueprintStamps from JSON files in *directory*. Returns count."""
        count = 0
        if not os.path.isdir(directory):
            return 0
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(directory, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                fp = data.get("footprint", (1, 1))
                stamp = BlueprintStamp(
                    name=data["name"],
                    blueprint_string=data["blueprint_string"],
                    footprint=tuple(fp),
                    phase_required=data["phase_required"],
                    required_items=data.get("required_items", {}),
                )
                self.register_stamp(stamp)
                count += 1
            except Exception:
                log.warning("Failed to load stamp from %s", path, exc_info=True)
        return count

    # ── pruning ──────────────────────────────────────────────────

    def prune_underperforming(self) -> list[str]:
        """Remove learned packs with <40% success over 20+ invocations.

        Returns names of pruned packs.
        """
        pruned: list[str] = []
        keep_items: list[Union[ActionPack, BlueprintStamp]] = []
        keep_flags: list[bool] = []
        for i, item in enumerate(self._items):
            should_prune = (
                isinstance(item, ActionPack)
                and item.origin == "learned"
                and item.total_invocations >= 20
                and item.success_rate < 0.40
            )
            if should_prune:
                pruned.append(item.name)
            else:
                keep_items.append(item)
                keep_flags.append(self._stamp_flags[i])
        if pruned:
            self._items = keep_items
            self._stamp_flags = keep_flags
        return pruned
