import tomllib
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "fleet.toml"

# Skills allowed in air-gap mode (deny-by-default whitelist)
AIR_GAP_SKILLS = {
    "code_review", "code_discuss", "code_index", "code_quality",
    "summarize", "discuss", "flashcard", "analyze_results",
    "rag_index", "rag_query", "benchmark", "ingest",
    "security_review", "security_audit",
}


def load_config():
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    # air_gap_mode implies offline_mode
    if cfg.get("fleet", {}).get("air_gap_mode", False):
        cfg.setdefault("fleet", {})["offline_mode"] = True
    return cfg


def is_offline(config: dict) -> bool:
    return config.get("fleet", {}).get("offline_mode", False)


def is_air_gap(config: dict) -> bool:
    return config.get("fleet", {}).get("air_gap_mode", False)
