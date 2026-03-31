"""Bridge configuration — loads from fleet.toml [factorio] section."""
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("biged.factorio.config")


@dataclass
class BridgeConfig:
    """Typed config for the Factorio bridge."""
    enabled: bool = False
    role: str = "host"
    host_fleet_id: str = ""
    bridge_port: int = 27016
    rcon_host: str = "localhost"
    rcon_port: int = 27015
    rcon_password: str = ""
    server_mode: str = "headless"
    factorio_path: str = ""
    headless_path: str = ""
    save_file: str = "biged-sandbox.zip"
    spectator_enabled: bool = True
    cadence: str = "adaptive"
    cadence_fast_ms: int = 1000
    cadence_medium_ms: int = 5000
    cadence_slow_ms: int = 30000
    adaptive_boost_ms: int = 1500
    adaptive_boost_hold_secs: int = 30
    adaptive_events: list = field(default_factory=lambda: [
        "resource_depleted", "entity_destroyed", "research_complete",
        "power_outage", "idle_assemblers",
    ])
    rcon_timeout_secs: int = 5
    rcon_max_retries: int = 3
    rcon_circuit_breaker_secs: int = 30
    max_actions_per_step: int = 20
    sandbox_mode: bool = True
    biters: bool = False  # Enable enemies in training saves (default off)
    reserved_workers: int = 0
    current_phase: int = 1
    auto_advance: bool = True
    lua_install_mode: str = "manual"
    state_file: str = "fleet/factorio/factory-state.md"
    log_dir: str = "fleet/factorio/logs"
    curriculum_dir: str = "fleet/idle_curricula"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout: int = 60
    plan_max_actions: int = 20
    plan_invalidation_failures: int = 3
    ollama_cooldown_secs: int = 30
    prompt_template: str = "baseline"
    temperature: float | None = None
    top_p: float | None = None
    idle_assembler_replan: int = 3
    focus_workers_default: int = 2
    analyze_interval_ticks: int = 50

    # Mode: "ml" (RL policy) or "llm" (existing agent brain)
    mode: str = "ml"
    game_speed: int = 10  # Factorio game.speed multiplier for training
    ml_tick_delay_ms: int = 0  # delay between ML ticks (0 = no sleep, max throughput)
    num_agents: int = 4  # number of standalone agent characters in same world

    # ML training hyperparameters
    ml_learning_rate: float = 3e-4
    ml_batch_size: int = 64
    ml_update_every: int = 512
    ml_checkpoint_every: int = 20
    ml_max_episode_steps: int = 10000
    ml_gamma: float = 0.99
    ml_gae_lambda: float = 0.95
    ml_clip_ratio: float = 0.2
    ml_entropy_coeff: float = 0.05
    ml_value_coeff: float = 0.5
    ml_checkpoint_dir: str = "fleet/factorio/checkpoints"

    @classmethod
    def from_dict(cls, d: dict) -> "BridgeConfig":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


def load_factorio_config() -> BridgeConfig:
    try:
        from config import load_config
        cfg = load_config()
        section = cfg.get("factorio", {})
        bc = BridgeConfig.from_dict(section)
    except Exception:
        log.warning("Could not load [factorio] from fleet.toml, using defaults")
        bc = BridgeConfig()

    # Environment variable override — avoids filesystem buffering race
    env_password = os.environ.get("BIGED_RCON_PASSWORD")
    if env_password:
        bc.rcon_password = env_password

    return bc
