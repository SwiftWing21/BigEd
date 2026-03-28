# fleet/factorio/world_model.py
"""Persistent in-memory world state with diffing and event detection."""
import logging
import threading
from dataclasses import dataclass, field

from factorio.state_parser import GameState, GameMetrics

log = logging.getLogger("biged.factorio.world")

RESOURCE_DEPLETION_THRESHOLD = 0.3


@dataclass
class GameEvent:
    event_type: str
    tick: int = 0
    detail: str = ""


class WorldModel:
    """Thread-safe persistent game state with diff-based event detection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: GameState | None = None
        self._prev_state: GameState | None = None
        self._metrics: GameMetrics | None = None
        self._entity_ids: set[int] = set()
        self._resource_baselines: dict[str, int] = {}

    @property
    def entity_count(self) -> int:
        with self._lock:
            if not self._state:
                return 0
            # Use len(entities) so unit tests that omit entity_count field work correctly.
            # entity_count on GameState is a snapshot hint from the game; len(entities)
            # reflects what we actually received and is authoritative here.
            return len(self._state.entities)

    def update(self, state: GameState, metrics: GameMetrics | None = None) -> list[GameEvent]:
        with self._lock:
            self._prev_state = self._state
            self._state = state
            if metrics:
                self._metrics = metrics

            if self._prev_state is None:
                self._entity_ids = {e.unit_number for e in state.entities if e.unit_number}
                for r in state.resources:
                    self._resource_baselines[r["name"]] = r["total_amount"]
                return []

            events = []
            events.extend(self._detect_entity_events(state))
            events.extend(self._detect_research_events(state))
            events.extend(self._detect_resource_events(state))
            events.extend(self._detect_power_events(state, self._metrics))

            self._entity_ids = {e.unit_number for e in state.entities if e.unit_number}

            return events

    def _detect_entity_events(self, state: GameState) -> list[GameEvent]:
        events = []
        new_ids = {e.unit_number for e in state.entities if e.unit_number}
        destroyed = self._entity_ids - new_ids
        if destroyed:
            events.append(GameEvent(
                event_type="entity_destroyed",
                tick=state.tick,
                detail=f"{len(destroyed)} entities destroyed",
            ))
        idle_count = sum(
            1 for e in state.entities
            if e.type in ("assembling-machine", "furnace") and not e.is_crafting
        )
        if idle_count > 0:
            events.append(GameEvent(
                event_type="idle_assemblers",
                tick=state.tick,
                detail=f"{idle_count} idle",
            ))
        return events

    def _detect_research_events(self, state: GameState) -> list[GameEvent]:
        if (self._prev_state.research_name
                and state.research_name != self._prev_state.research_name):
            return [GameEvent(
                event_type="research_complete",
                tick=state.tick,
                detail=f"Completed: {self._prev_state.research_name}",
            )]
        return []

    def _detect_power_events(self, state: GameState, metrics: GameMetrics | None) -> list[GameEvent]:
        if not metrics:
            return []
        if metrics.electric_satisfaction and metrics.electric_satisfaction != "ok":
            return [GameEvent(
                event_type="power_outage",
                tick=state.tick,
                detail=f"Electric satisfaction: {metrics.electric_satisfaction}",
            )]
        return []

    def _detect_resource_events(self, state: GameState) -> list[GameEvent]:
        events = []
        for r in state.resources:
            name = r["name"]
            amount = r["total_amount"]
            baseline = self._resource_baselines.get(name)
            if baseline and amount < baseline * RESOURCE_DEPLETION_THRESHOLD:
                events.append(GameEvent(
                    event_type="resource_depleted",
                    tick=state.tick,
                    detail=f"{name}: {amount}/{baseline}",
                ))
            if name not in self._resource_baselines:
                self._resource_baselines[name] = amount
        return events

    def get_snapshot(self) -> dict:
        with self._lock:
            if not self._state:
                return {"tick": 0, "entities": [], "inventory": {}, "resources": []}
            return {
                "tick": self._state.tick,
                "player_position": self._state.player_position,
                "inventory": dict(self._state.inventory),
                "entity_count": self._state.entity_count,
                "entities": [
                    {"name": e.name, "type": e.type, "position": e.position,
                     "unit_number": e.unit_number, "recipe": e.recipe,
                     "is_crafting": e.is_crafting}
                    for e in self._state.entities
                ],
                "resources": list(self._state.resources),
                "research_name": self._state.research_name,
                "research_progress": self._state.research_progress,
            }
