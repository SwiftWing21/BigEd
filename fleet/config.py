import tomllib
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "fleet.toml"

def load_config():
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)
