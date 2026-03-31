from factorio.bridge_config import BridgeConfig


def test_ml_config_defaults():
    cfg = BridgeConfig()
    assert cfg.mode == "ml"
    assert cfg.game_speed == 10
    assert cfg.ml_learning_rate == 3e-4
    assert cfg.ml_batch_size == 64
    assert cfg.ml_update_every == 512
    assert cfg.ml_checkpoint_every == 20
    assert cfg.ml_max_episode_steps == 10000
    assert cfg.ml_gamma == 0.99
    assert cfg.ml_gae_lambda == 0.95
    assert cfg.ml_clip_ratio == 0.2
    assert cfg.ml_entropy_coeff == 0.05
    assert cfg.ml_value_coeff == 0.5
    assert cfg.ml_checkpoint_dir == "fleet/factorio/checkpoints"


def test_mode_toggle():
    cfg = BridgeConfig(mode="llm")
    assert cfg.mode == "llm"
    cfg2 = BridgeConfig(mode="ml")
    assert cfg2.mode == "ml"
