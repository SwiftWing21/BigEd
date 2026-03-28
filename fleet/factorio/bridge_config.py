"""Bridge configuration — loads from fleet.toml [factorio] section."""
import logging
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
    save_file: str = "sandbox.zip"
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
        return BridgeConfig.from_dict(section)
    except Exception:
        log.warning("Could not load [factorio] from fleet.toml, using defaults")
        return BridgeConfig()
