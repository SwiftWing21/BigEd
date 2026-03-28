# fleet/factorio/state_parser.py
"""Parse Factorio RCON JSON responses into typed dataclasses."""
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("biged.factorio.state")


@dataclass
class Entity:
    name: str = ""
    type: str = ""
    position: dict = field(default_factory=dict)
    direction: int = 0
    health: float = 0
    unit_number: int = 0
    recipe: str = ""
    crafting_progress: float = 0.0
    is_crafting: bool = False
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    belt_contents: dict = field(default_factory=dict)
    held_item: dict | None = None
    mining_target: str = ""
    energy: float = 0.0
    status: str = ""


@dataclass
class GameState:
    tick: int = 0
    time_of_day: float = 0.0
    player_position: dict = field(default_factory=dict)
    player_health: float = 0
    inventory: dict = field(default_factory=dict)
    entities: list[Entity] = field(default_factory=list)
    entity_count: int = 0
    resources: list[dict] = field(default_factory=list)
    research_name: str = ""
    research_progress: float = 0.0
    map_explored_chunks: int = 0


@dataclass
class GameMetrics:
    tick: int = 0
    total_produced: dict = field(default_factory=dict)
    total_consumed: dict = field(default_factory=dict)
    flow_per_minute: dict = field(default_factory=dict)
    electric_satisfaction: str = ""
    electric_capacity_mw: float = 0.0
    electric_entity_count: int = 0
    completed_research: list[str] = field(default_factory=list)
    current_research: str = ""
    current_research_progress: float = 0.0


def parse_state(raw_json: str) -> GameState:
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Failed to parse state JSON")
        return GameState()

    entities = []
    for e in data.get("entities", []):
        entities.append(Entity(
            name=e.get("name", ""), type=e.get("type", ""),
            position=e.get("position", {}), direction=e.get("direction", 0),
            health=e.get("health", 0), unit_number=e.get("unit_number", 0),
            recipe=e.get("recipe", ""), crafting_progress=e.get("crafting_progress", 0.0),
            is_crafting=e.get("is_crafting", False), input=e.get("input", {}),
            output=e.get("output", {}), belt_contents=e.get("belt_contents", {}),
            held_item=e.get("held_item"), mining_target=e.get("mining_target", ""),
            energy=e.get("energy", 0.0), status=e.get("status", ""),
        ))

    player = data.get("player", {})
    research = data.get("research") or {}

    return GameState(
        tick=data.get("tick", 0), time_of_day=data.get("time_of_day", 0.0),
        player_position=player.get("position", {}),
        player_health=player.get("health", 0),
        inventory=data.get("inventory", {}), entities=entities,
        entity_count=data.get("entity_count", 0),
        resources=data.get("resources", []),
        research_name=research.get("name", ""),
        research_progress=research.get("progress", 0.0),
        map_explored_chunks=data.get("map_explored_chunks", 0),
    )


def parse_metrics(raw_json: str) -> GameMetrics:
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Failed to parse metrics JSON")
        return GameMetrics()

    electric = data.get("electric") or {}
    research = data.get("research") or {}

    return GameMetrics(
        tick=data.get("tick", 0),
        total_produced=data.get("total_produced", {}),
        total_consumed=data.get("total_consumed", {}),
        flow_per_minute=data.get("flow_per_minute", {}),
        electric_satisfaction=electric.get("satisfaction", ""),
        electric_capacity_mw=electric.get("capacity_mw", 0.0),
        electric_entity_count=electric.get("entity_count", 0),
        completed_research=research.get("completed", []),
        current_research=research.get("current", ""),
        current_research_progress=research.get("progress", 0.0),
    )


def state_to_markdown(state: GameState, metrics: GameMetrics | None = None) -> str:
    lines = [f"# Factory State (tick {state.tick})\n"]
    pos = state.player_position
    lines.append(f"**Position:** ({pos.get('x', 0)}, {pos.get('y', 0)})  ")
    lines.append(f"**Health:** {state.player_health}\n")

    lines.append("## Inventory")
    if state.inventory:
        for item, count in sorted(state.inventory.items()):
            lines.append(f"- {item}: {count}")
    else:
        lines.append("- (empty)")
    lines.append("")

    if state.research_name:
        pct = int(state.research_progress * 100)
        lines.append(f"## Research\n- {state.research_name}: {pct}%\n")

    if state.resources:
        lines.append("## Resources Nearby")
        for r in state.resources:
            lines.append(f"- {r['name']}: {r['total_amount']:,} ({r['patches']} patches)")
        lines.append("")

    lines.append(f"## Entities ({state.entity_count} total)")
    by_type: dict[str, list] = {}
    for e in state.entities:
        by_type.setdefault(e.type, []).append(e)
    for etype, ents in sorted(by_type.items()):
        lines.append(f"\n### {etype} ({len(ents)})")
        for e in ents[:20]:
            pos_str = f"({e.position.get('x', 0)}, {e.position.get('y', 0)})"
            detail = f"- **{e.name}** at {pos_str}"
            if e.recipe:
                detail += f" recipe={e.recipe}"
            if e.is_crafting:
                detail += f" crafting={int(e.crafting_progress * 100)}%"
            if e.belt_contents:
                items = ", ".join(f"{k}:{v}" for k, v in e.belt_contents.items())
                detail += f" carrying=[{items}]"
            lines.append(detail)

    if metrics and metrics.flow_per_minute:
        lines.append("\n## Production Flow (items/min)")
        for item, rate in sorted(metrics.flow_per_minute.items(), key=lambda x: -x[1]):
            lines.append(f"- {item}: {rate}/min")

    return "\n".join(lines)
