"""Prompt template loader — TOML-based swappable prompts for AgentBrain."""
import logging
from pathlib import Path

log = logging.getLogger("biged.factorio.prompt_loader")

_DEFAULT_PROMPTS_DIR = "fleet/factorio/prompts"

# Project root is two levels above this file: fleet/factorio/prompt_loader.py -> fleet/ -> project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def load_prompt_template(name: str, prompts_dir: str = _DEFAULT_PROMPTS_DIR) -> dict:
    """Load a prompt template TOML by name. Returns dict with system_template and user_template."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    raw = Path(prompts_dir)
    path = (raw if raw.is_absolute() else _PROJECT_ROOT / raw) / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    templates = data.get("templates", {})
    if "system_template" not in templates:
        raise ValueError(f"Prompt template {name} missing [templates] system_template")
    if "user_template" not in templates:
        raise ValueError(f"Prompt template {name} missing [templates] user_template")

    return {
        "name": data.get("meta", {}).get("name", name),
        "system_template": templates["system_template"],
        "user_template": templates["user_template"],
    }


def render_prompt(template: dict, state: str, objective: str, previous_results: str) -> tuple[str, str]:
    """Render a prompt template with substituted placeholders."""
    system = template["system_template"]
    user = template["user_template"].format(
        state=state,
        objective=objective,
        previous_results=previous_results,
    )
    return system, user
