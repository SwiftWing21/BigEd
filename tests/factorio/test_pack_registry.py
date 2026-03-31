"""TDD tests for PackRegistry, ActionPack, and BlueprintStamp."""
import json
import os
import tempfile

import pytest

# Will import from fleet.factorio.pack_registry once implemented
from factorio.pack_registry import (
    ActionPack,
    BlueprintStamp,
    MAX_PACK_SLOTS,
    PackRegistry,
    _action_overlap,
)


# ── ActionPack tests ──────────────────────────────────────────────


def test_action_pack_creation():
    pack = ActionPack(
        name="mine_iron",
        actions=[{"type": "MINE", "target": "iron-ore"}],
        phase_required=1,
        origin="builtin",
    )
    assert pack.name == "mine_iron"
    assert len(pack.actions) == 1
    assert pack.phase_required == 1
    assert pack.origin == "builtin"
    assert pack.success_count == 0
    assert pack.avg_reward == 0.0
    assert pack.required_items == {}
    assert pack.total_invocations == 0
    assert pack.success_rate == 0.0


def test_action_pack_can_execute_checks_inventory():
    pack = ActionPack(
        name="smelt",
        actions=[{"type": "INSERT", "target": "furnace"}],
        phase_required=1,
        origin="builtin",
        required_items={"iron-ore": 5, "stone-furnace": 1},
    )
    # Missing items
    assert pack.can_execute({}) is False
    assert pack.can_execute({"iron-ore": 10}) is False
    # Insufficient quantity
    assert pack.can_execute({"iron-ore": 3, "stone-furnace": 1}) is False
    # Sufficient
    assert pack.can_execute({"iron-ore": 5, "stone-furnace": 1}) is True
    assert pack.can_execute({"iron-ore": 100, "stone-furnace": 2}) is True


def test_action_pack_can_execute_no_requirements():
    pack = ActionPack(
        name="idle",
        actions=[{"type": "WAIT"}],
        phase_required=0,
        origin="builtin",
    )
    assert pack.can_execute({}) is True
    assert pack.can_execute({"anything": 99}) is True


def test_action_pack_record_result():
    pack = ActionPack(
        name="test",
        actions=[{"type": "MINE"}],
        phase_required=1,
        origin="builtin",
    )
    pack.record_result(True, 1.0)
    assert pack.success_count == 1
    assert pack.total_invocations == 1
    assert pack.avg_reward == 1.0

    pack.record_result(False, -0.5)
    assert pack.success_count == 1
    assert pack.total_invocations == 2
    assert pack.avg_reward == pytest.approx(0.25)  # (1.0 + -0.5) / 2


def test_action_pack_success_rate():
    pack = ActionPack(
        name="test",
        actions=[{"type": "MINE"}],
        phase_required=1,
        origin="builtin",
    )
    assert pack.success_rate == 0.0
    pack.record_result(True, 1.0)
    assert pack.success_rate == 1.0
    pack.record_result(False, 0.0)
    assert pack.success_rate == 0.5


# ── BlueprintStamp tests ─────────────────────────────────────────


def test_blueprint_stamp_creation():
    stamp = BlueprintStamp(
        name="smelter_line",
        blueprint_string="0eNr...",
        footprint=(3, 4),
        phase_required=2,
    )
    assert stamp.name == "smelter_line"
    assert stamp.blueprint_string == "0eNr..."
    assert stamp.footprint == (3, 4)
    assert stamp.phase_required == 2
    assert stamp.required_items == {}


def test_blueprint_stamp_can_execute_checks_inventory():
    stamp = BlueprintStamp(
        name="smelter_line",
        blueprint_string="0eNr...",
        footprint=(3, 4),
        phase_required=2,
        required_items={"stone-furnace": 4, "transport-belt": 10},
    )
    assert stamp.can_execute({}) is False
    assert stamp.can_execute({"stone-furnace": 4}) is False
    assert stamp.can_execute({"stone-furnace": 4, "transport-belt": 10}) is True
    assert stamp.can_execute({"stone-furnace": 10, "transport-belt": 20}) is True


# ── PackRegistry tests ───────────────────────────────────────────


def test_registry_register_pack():
    reg = PackRegistry()
    pack = ActionPack(
        name="mine_iron",
        actions=[{"type": "MINE", "target": "iron-ore"}],
        phase_required=1,
        origin="builtin",
    )
    slot_id = reg.register_pack(pack)
    assert slot_id == 0
    assert reg.get_by_id(0) is pack


def test_registry_register_stamp():
    reg = PackRegistry()
    stamp = BlueprintStamp(
        name="smelter",
        blueprint_string="abc",
        footprint=(2, 3),
        phase_required=2,
    )
    slot_id = reg.register_stamp(stamp)
    assert slot_id == 0
    assert reg.get_by_id(0) is stamp
    assert reg.is_stamp(0) is True


def test_registry_is_pack_vs_stamp():
    reg = PackRegistry()
    pack = ActionPack(
        name="mine",
        actions=[{"type": "MINE"}],
        phase_required=1,
        origin="builtin",
    )
    stamp = BlueprintStamp(
        name="bp",
        blueprint_string="x",
        footprint=(1, 1),
        phase_required=1,
    )
    pid = reg.register_pack(pack)
    sid = reg.register_stamp(stamp)
    assert reg.is_stamp(pid) is False
    assert reg.is_stamp(sid) is True


def test_registry_get_available_filters_by_phase():
    reg = PackRegistry()
    p1 = ActionPack(name="a", actions=[{"type": "X"}], phase_required=1, origin="builtin")
    p2 = ActionPack(name="b", actions=[{"type": "Y"}], phase_required=2, origin="builtin")
    p3 = ActionPack(name="c", actions=[{"type": "Z"}], phase_required=3, origin="builtin")
    reg.register_pack(p1)
    reg.register_pack(p2)
    reg.register_pack(p3)

    avail = reg.get_available(phase=2)
    assert len(avail) == 2
    assert p1 in avail
    assert p2 in avail
    assert p3 not in avail


def test_registry_max_slots_enforced():
    reg = PackRegistry()
    for i in range(MAX_PACK_SLOTS):
        reg.register_pack(
            ActionPack(name=f"p{i}", actions=[{"type": "X"}], phase_required=1, origin="builtin")
        )
    with pytest.raises(ValueError, match="MAX_PACK_SLOTS"):
        reg.register_pack(
            ActionPack(name="overflow", actions=[{"type": "X"}], phase_required=1, origin="builtin")
        )


def test_registry_get_pack_mask():
    reg = PackRegistry()
    p1 = ActionPack(
        name="a",
        actions=[{"type": "MINE"}],
        phase_required=1,
        origin="builtin",
        required_items={"iron-ore": 1},
    )
    p2 = ActionPack(
        name="b",
        actions=[{"type": "PLACE"}],
        phase_required=2,
        origin="builtin",
    )
    reg.register_pack(p1)
    reg.register_pack(p2)

    inventory = {"iron-ore": 5}
    mask = reg.get_pack_mask(phase=1, inventory=inventory)
    assert len(mask) == MAX_PACK_SLOTS
    assert mask[0] == 1  # phase 1, has iron-ore
    assert mask[1] == 0  # phase 2, not available at phase 1
    assert all(m == 0 for m in mask[2:])

    # Phase 2 with inventory
    mask2 = reg.get_pack_mask(phase=2, inventory=inventory)
    assert mask2[0] == 1
    assert mask2[1] == 1  # no required_items, so can_execute is True


def test_registry_get_pack_mask_inventory_blocks():
    reg = PackRegistry()
    p = ActionPack(
        name="need_coal",
        actions=[{"type": "INSERT"}],
        phase_required=1,
        origin="builtin",
        required_items={"coal": 10},
    )
    reg.register_pack(p)
    mask = reg.get_pack_mask(phase=1, inventory={})
    assert mask[0] == 0  # can't execute without coal


# ── promote_learned ──────────────────────────────────────────────


def test_promote_learned_accepts_valid():
    reg = PackRegistry()
    candidate = ActionPack(
        name="discovered",
        actions=[{"type": f"A{i}"} for i in range(10)],
        phase_required=1,
        origin="agent",
    )
    slot = reg.promote_learned(candidate)
    assert slot is not None
    assert reg.get_by_id(slot).origin == "learned"


def test_promote_learned_rejects_too_few_actions():
    reg = PackRegistry()
    candidate = ActionPack(
        name="tiny",
        actions=[{"type": "A"} for _ in range(4)],
        phase_required=1,
        origin="agent",
    )
    assert reg.promote_learned(candidate) is None


def test_promote_learned_rejects_too_many_actions():
    reg = PackRegistry()
    candidate = ActionPack(
        name="huge",
        actions=[{"type": f"A{i}"} for i in range(51)],
        phase_required=1,
        origin="agent",
    )
    assert reg.promote_learned(candidate) is None


def test_promote_learned_rejects_high_overlap():
    reg = PackRegistry()
    actions = [{"type": f"A{i}"} for i in range(10)]
    existing = ActionPack(
        name="existing",
        actions=actions,
        phase_required=1,
        origin="builtin",
    )
    reg.register_pack(existing)

    # Candidate with identical actions
    candidate = ActionPack(
        name="clone",
        actions=actions.copy(),
        phase_required=1,
        origin="agent",
    )
    assert reg.promote_learned(candidate) is None


# ── save/load ────────────────────────────────────────────────────


def test_save_and_load_learned():
    reg = PackRegistry()
    pack = ActionPack(
        name="learned_pack",
        actions=[{"type": f"A{i}"} for i in range(10)],
        phase_required=1,
        origin="learned",
        required_items={"iron-plate": 2},
    )
    pack.record_result(True, 1.0)
    reg.register_pack(pack)

    with tempfile.TemporaryDirectory() as tmpdir:
        reg.save_learned(tmpdir)
        # Verify file was created
        files = os.listdir(tmpdir)
        assert len(files) == 1
        assert files[0].endswith(".json")

        # Load into fresh registry
        reg2 = PackRegistry()
        count = reg2.load_packs(tmpdir)
        assert count == 1
        loaded = reg2.get_by_id(0)
        assert loaded.name == "learned_pack"
        assert loaded.origin == "learned"
        assert loaded.success_count == 1
        assert loaded.required_items == {"iron-plate": 2}


def test_load_stamps():
    with tempfile.TemporaryDirectory() as tmpdir:
        stamp_data = {
            "name": "test_stamp",
            "blueprint_string": "0eNr...",
            "footprint": [3, 4],
            "phase_required": 2,
            "required_items": {"stone-furnace": 4},
        }
        with open(os.path.join(tmpdir, "test_stamp.json"), "w") as f:
            json.dump(stamp_data, f)

        reg = PackRegistry()
        count = reg.load_stamps(tmpdir)
        assert count == 1
        s = reg.get_by_id(0)
        assert isinstance(s, BlueprintStamp)
        assert s.footprint == (3, 4)
        assert reg.is_stamp(0) is True


# ── prune_underperforming ────────────────────────────────────────


def test_prune_underperforming():
    reg = PackRegistry()
    bad = ActionPack(
        name="bad_pack",
        actions=[{"type": "X"}],
        phase_required=1,
        origin="learned",
    )
    # Record 20+ invocations with <40% success
    for _ in range(5):
        bad.record_result(True, 0.5)
    for _ in range(16):
        bad.record_result(False, -0.1)
    reg.register_pack(bad)

    good = ActionPack(
        name="good_pack",
        actions=[{"type": "Y"}],
        phase_required=1,
        origin="builtin",
    )
    for _ in range(20):
        good.record_result(True, 1.0)
    reg.register_pack(good)

    pruned = reg.prune_underperforming()
    assert "bad_pack" in pruned
    assert "good_pack" not in pruned


def test_prune_skips_low_invocation():
    reg = PackRegistry()
    new_pack = ActionPack(
        name="new_pack",
        actions=[{"type": "X"}],
        phase_required=1,
        origin="learned",
    )
    # Only 5 invocations, all failures -- should NOT be pruned (< 20 invocations)
    for _ in range(5):
        new_pack.record_result(False, -0.1)
    reg.register_pack(new_pack)

    pruned = reg.prune_underperforming()
    assert len(pruned) == 0


# ── _action_overlap ──────────────────────────────────────────────


def test_action_overlap_identical():
    a = [{"type": "MINE", "target": "iron"}]
    b = [{"type": "MINE", "target": "iron"}]
    assert _action_overlap(a, b) == 1.0


def test_action_overlap_disjoint():
    a = [{"type": "MINE", "target": "iron"}]
    b = [{"type": "PLACE", "target": "furnace"}]
    assert _action_overlap(a, b) == 0.0


def test_action_overlap_partial():
    a = [{"type": "MINE"}, {"type": "PLACE"}]
    b = [{"type": "MINE"}, {"type": "INSERT"}]
    assert _action_overlap(a, b) == pytest.approx(1 / 3)  # 1 common / 3 unique


# ── copy-on-write ────────────────────────────────────────────────


def test_registry_copy_on_write():
    """register_pack creates a new list (copy-on-write)."""
    reg = PackRegistry()
    old_list = reg._items
    reg.register_pack(
        ActionPack(name="a", actions=[{"type": "X"}], phase_required=1, origin="builtin")
    )
    assert reg._items is not old_list
