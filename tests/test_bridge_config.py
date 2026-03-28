"""Tests for Factorio bridge config loading."""
import pytest


def test_default_config():
    from factorio.bridge_config import BridgeConfig
    cfg = BridgeConfig()
    assert cfg.enabled is False
    assert cfg.role == "host"
    assert cfg.rcon_port == 27015
    assert cfg.cadence == "adaptive"
    assert cfg.sandbox_mode is True
    assert cfg.reserved_workers == 0
    assert cfg.max_actions_per_step == 20
    assert cfg.rcon_timeout_secs == 5
    assert cfg.rcon_max_retries == 3
    assert cfg.adaptive_boost_hold_secs == 30


def test_load_from_dict():
    from factorio.bridge_config import BridgeConfig
    raw = {
        "enabled": True,
        "rcon_port": 27020,
        "rcon_password": "test123",
        "cadence": "fast",
        "sandbox_mode": False,
        "reserved_workers": 2,
    }
    cfg = BridgeConfig.from_dict(raw)
    assert cfg.enabled is True
    assert cfg.rcon_port == 27020
    assert cfg.rcon_password == "test123"
    assert cfg.cadence == "fast"
    assert cfg.sandbox_mode is False
    assert cfg.reserved_workers == 2


def test_load_from_fleet_toml():
    from factorio.bridge_config import load_factorio_config
    cfg = load_factorio_config()
    assert isinstance(cfg.rcon_port, int)
    assert cfg.cadence in ("fast", "medium", "slow", "adaptive")


def test_ollama_config_defaults():
    from factorio.bridge_config import BridgeConfig
    cfg = BridgeConfig()
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.ollama_model == "qwen3:8b"
    assert cfg.ollama_timeout == 60
    assert cfg.plan_max_actions == 20
    assert cfg.plan_invalidation_failures == 3
    assert cfg.ollama_cooldown_secs == 30


def test_ollama_config_from_dict():
    from factorio.bridge_config import BridgeConfig
    raw = {
        "enabled": True,
        "ollama_model": "qwen3:4b",
        "ollama_timeout": 45,
        "plan_max_actions": 10,
    }
    cfg = BridgeConfig.from_dict(raw)
    assert cfg.ollama_model == "qwen3:4b"
    assert cfg.ollama_timeout == 45
    assert cfg.plan_max_actions == 10
    assert cfg.ollama_url == "http://localhost:11434"  # default kept


# --- New experiment config fields ---

def test_new_fields_have_defaults():
    from factorio.bridge_config import BridgeConfig
    cfg = BridgeConfig()
    assert cfg.prompt_template == "baseline"
    assert cfg.temperature is None
    assert cfg.top_p is None
    assert cfg.idle_assembler_replan == 3


def test_from_dict_picks_up_new_fields():
    from factorio.bridge_config import BridgeConfig
    d = {"prompt_template": "compact_v1", "temperature": 0.7, "top_p": 0.9, "idle_assembler_replan": 5}
    cfg = BridgeConfig.from_dict(d)
    assert cfg.prompt_template == "compact_v1"
    assert cfg.temperature == 0.7
    assert cfg.top_p == 0.9
    assert cfg.idle_assembler_replan == 5


def test_from_dict_ignores_unknown():
    from factorio.bridge_config import BridgeConfig
    d = {"prompt_template": "baseline", "bogus_field": 42}
    cfg = BridgeConfig.from_dict(d)
    assert cfg.prompt_template == "baseline"
