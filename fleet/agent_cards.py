"""Agent Card metadata — JSON capability descriptors for self-documentation and future A2A compatibility."""
import json
from pathlib import Path

FLEET_DIR = Path(__file__).parent


def generate_agent_card(role: str, config: dict) -> dict:
    """Generate an Agent Card for a worker role."""
    affinity = config.get("affinity", {}).get(role, [])
    # Strip coder_N suffix for affinity lookup
    base_role = role.split("_")[0] if role.startswith("coder_") else role
    if not affinity:
        affinity = config.get("affinity", {}).get(base_role, [])

    return {
        "name": role,
        "role": base_role,
        "version": "1.0",
        "capabilities": {
            "skills": affinity,
            "max_concurrent": 1,
            "supports_review": True,
            "supports_hitl": True,
        },
        "endpoints": {
            "status": "/api/fleet/workers",
            "inbox": f"db.get_messages('{role}')",
        },
        "metadata": {
            "framework": "BigEd CC",
            "protocol": "internal-sqlite",
        }
    }


def generate_all_cards(config: dict) -> list:
    """Generate Agent Cards for all configured roles."""
    if not config:
        from config import load_config
        config = load_config()
    roles = list(config.get("affinity", {}).keys())
    return [generate_agent_card(r, config) for r in roles]


def save_cards(config: dict = None):
    """Save all Agent Cards to knowledge/agent_cards.json."""
    if not config:
        from config import load_config
        config = load_config()
    cards = generate_all_cards(config)
    out = FLEET_DIR / "knowledge" / "agent_cards.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cards, indent=2), encoding="utf-8")
    return out
