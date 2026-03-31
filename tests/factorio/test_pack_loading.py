"""Tests for loading hardcoded packs and blueprint stamps from JSON."""
from pathlib import Path


def test_hardcoded_packs_load():
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    count = reg.load_packs(packs_dir / "hardcoded")
    assert count >= 21, f"Expected >= 21 packs, got {count}"


def test_blueprint_stamps_load():
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    count = reg.load_stamps(packs_dir / "blueprints")
    assert count >= 2, f"Expected >= 2 stamps, got {count}"


def test_loaded_packs_have_valid_actions():
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    reg.load_packs(packs_dir / "hardcoded")
    valid_actions = {
        "place",
        "craft",
        "research",
        "move",
        "set_recipe",
        "remove",
        "wait",
        "mine",
        "insert",
    }
    for i in range(len(reg._items)):
        if not reg.is_stamp(i):
            pack = reg.get_by_id(i)
            for act in pack.actions:
                assert act.get("action") in valid_actions, (
                    f"Invalid action '{act.get('action')}' in {pack.name}"
                )


def test_packs_have_required_items():
    """Every hardcoded pack must declare required_items."""
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    reg.load_packs(packs_dir / "hardcoded")
    for i in range(len(reg._items)):
        pack = reg.get_by_id(i)
        assert pack.required_items, f"Pack {pack.name} has empty required_items"


def test_stamps_have_footprint():
    """Every blueprint stamp must have a footprint tuple."""
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    reg.load_stamps(packs_dir / "blueprints")
    for i in range(len(reg._items)):
        stamp = reg.get_by_id(i)
        assert len(stamp.footprint) == 2, f"Stamp {stamp.name} has bad footprint"


def test_phase_ordering():
    """Checkpoint 0 packs should have phase_required=0, etc."""
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    reg.load_packs(packs_dir / "hardcoded")
    phases_seen = set()
    for i in range(len(reg._items)):
        pack = reg.get_by_id(i)
        phases_seen.add(pack.phase_required)
    assert 0 in phases_seen, "No phase-0 packs found"
    assert 1 in phases_seen, "No phase-1 packs found"
    assert 2 in phases_seen, "No phase-2 packs found"


def test_late_game_packs_exist():
    """Verify packs exist for checkpoints 3-7 (blue science through space)."""
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    reg.load_packs(packs_dir / "hardcoded")
    phases = set()
    for item in reg._items:
        phases.add(item.phase_required)
    assert 3 in phases, "No phase-3 (blue science) packs found"
    assert 4 in phases, "No phase-4 (purple science) packs found"
    assert 5 in phases, "No phase-5 (yellow science) packs found"
    assert 6 in phases, "No phase-6 (rocket) packs found"
    assert 7 in phases, "No phase-7 (space science) packs found"
