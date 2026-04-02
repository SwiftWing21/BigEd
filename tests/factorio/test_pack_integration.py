# tests/factorio/test_pack_integration.py
"""Integration test: pack registry → executor → reward → policy pipeline."""
import pytest
torch = pytest.importorskip("torch")


def test_pack_roundtrip():
    """Verify pack creation → registration → mask → executor → reward flow."""
    from factorio.pack_registry import PackRegistry, ActionPack, MAX_PACK_SLOTS
    from factorio.pack_executor import PackExecutor

    # 1. Create and register a pack
    reg = PackRegistry()
    pack = ActionPack(
        name="test_smelt",
        actions=[
            {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
            {"action": "insert", "item": "coal", "count": 5, "position": {"x": 0, "y": 0}},
        ],
        phase_required=0,
        origin="hardcoded",
        required_items={"stone-furnace": 1},
    )
    slot = reg.register_pack(pack)
    assert slot == 0

    # 2. Check mask
    mask = reg.get_pack_mask(phase=0, inventory={"stone-furnace": 5})
    assert mask[0] == 1
    assert sum(mask) == 1

    # 3. Execute through PackExecutor
    exe = PackExecutor()
    first = exe.start(pack, offset=(10, 20))
    assert first["position"]["x"] == 10
    assert first["position"]["y"] == 20

    exe.accumulate_reward(0.5)
    second = exe.next_step({"success": True})
    assert second is not None
    assert second["action"] == "insert"
    assert second["position"]["x"] == 10  # offset applied

    exe.accumulate_reward(0.3)
    done = exe.next_step({"success": True})
    assert done is None
    assert exe.completed
    assert exe.cumulative_reward == pytest.approx(0.8)


def test_policy_accepts_new_action_types():
    """Verify policy network works with PACK and STAMP action types."""
    from factorio.ml_policy import FactorioPolicy
    from factorio.action_space import ActionType

    # ActionType should have PACK and STAMP
    assert hasattr(ActionType, 'PACK')
    assert hasattr(ActionType, 'STAMP')
    num_types = len(ActionType)
    assert num_types >= 12  # 10 original + PACK + STAMP

    policy = FactorioPolicy(
        grid_channels=5, grid_size=64, feature_dim=69,
        num_action_types=num_types,
        num_entities=37, num_recipes=69, num_techs=20,
    )
    grid = torch.randn(1, 5, 64, 64)
    feat = torch.randn(1, 69)
    action_logits, value = policy.forward(grid, feat)
    assert action_logits.shape[1] == num_types

    # Pack heads produce correct shapes
    shared = policy._shared_forward(grid, feat)
    params = policy.get_action_params(shared, ActionType.PACK.value)
    assert params["pack_logits"].shape == (1, 64)
    assert params["offset_dx_logits"].shape == (1, 11)


def test_recorder_to_registry_flow():
    """Verify learned pack discovery: record → extract → promote."""
    from factorio.pack_recorder import PackRecorder
    from factorio.pack_registry import PackRegistry

    recorder = PackRecorder(max_length=100)
    registry = PackRegistry()

    # Record enough actions for a valid candidate
    for i in range(10):
        recorder.record({"action": "place", "entity": f"e{i}", "position": {"x": i, "y": 0}})

    # Extract candidate
    candidate = recorder.on_checkpoint_complete(checkpoint_id=1)
    assert candidate is not None
    assert len(candidate.actions) == 10

    # Promote to registry
    slot = registry.promote_learned(candidate)
    assert slot is not None
    assert slot == 0
    assert registry.get_by_id(slot).origin == "learned"


def test_reward_pack_signals():
    """Verify reward compute accepts pack_completed/pack_aborted params."""
    from factorio.reward import RewardComputer
    from factorio.state_parser import GameState
    rc = RewardComputer(phase=1)

    s1 = GameState(tick=100, player_position={"x": 0, "y": 0})
    s2 = GameState(tick=110, player_position={"x": 0, "y": 0})

    # Pack completion should add bonus
    r_pack = rc.compute(s1, s2, True, False, False, None, 10, [],
                        pack_completed=True)
    # Reset normalizer so the second call starts fresh
    rc2 = RewardComputer(phase=1)
    r_none = rc2.compute(s1, s2, True, False, False, None, 10, [])
    # Pack completed reward should be higher
    assert r_pack > r_none


def test_state_encoder_pack_progress():
    """Verify state encoder includes pack_progress in features."""
    from factorio.state_encoder import StateEncoder, _BASE_FEATURE_DIM
    assert _BASE_FEATURE_DIM == 73

    enc = StateEncoder(phase=1, grid_size=64)

    from factorio.state_parser import GameState
    state = GameState(
        tick=100,
        player_position={"x": 0, "y": 0},
        player_alive=True,
    )

    grid, world, features = enc.encode(state, None, pack_progress=0.75)
    assert features[72] == pytest.approx(0.75)


def test_curriculum_checkpoints():
    """Verify curriculum has 8-checkpoint support."""
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1)
    assert cm.checkpoint == 0
    assert cm.checkpoint_bonus == 10.0

    cm4 = CurriculumManager(current_phase=4)
    assert cm4.checkpoint == 3
    assert cm4.checkpoint_bonus == 40.0


def test_hardcoded_packs_valid():
    """Verify all hardcoded packs and stamps load successfully."""
    from pathlib import Path
    from factorio.pack_registry import PackRegistry

    reg = PackRegistry()
    packs_dir = Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs"
    packs_loaded = reg.load_packs(packs_dir / "hardcoded")
    stamps_loaded = reg.load_stamps(packs_dir / "blueprints")
    assert packs_loaded >= 7
    assert stamps_loaded >= 2
    # Verify mask works with loaded packs
    mask = reg.get_pack_mask(phase=0, inventory={})
    assert len(mask) == 64
