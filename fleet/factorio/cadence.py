# fleet/factorio/cadence.py
"""Cadence controller — manages tick interval with 4 modes."""
import time
import logging

log = logging.getLogger("biged.factorio.cadence")

VALID_MODES = {"fast", "medium", "slow", "adaptive"}
DEFAULT_ADAPTIVE_EVENTS = [
    "resource_depleted", "entity_destroyed", "research_complete",
    "power_outage", "idle_assemblers",
]


class CadenceController:
    def __init__(self, fast_ms: int = 1000, medium_ms: int = 5000,
                 slow_ms: int = 30000, boost_ms: int = 1500,
                 boost_hold_secs: int = 30,
                 adaptive_events: list[str] | None = None):
        self._fast = fast_ms / 1000.0
        self._medium = medium_ms / 1000.0
        self._slow = slow_ms / 1000.0
        self._boost = boost_ms / 1000.0
        self._boost_hold = boost_hold_secs
        self._adaptive_events = set(adaptive_events or DEFAULT_ADAPTIVE_EVENTS)
        self._mode = "adaptive"
        self._boost_until: float = 0.0
        self._decay_until: float = 0.0

    def set_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid cadence mode: {mode}. Must be one of {VALID_MODES}"
            )
        self._mode = mode
        log.info("Cadence mode set to: %s", mode)

    def get_interval_secs(self) -> float:
        if self._mode == "fast":
            return self._fast
        if self._mode == "medium":
            return self._medium
        if self._mode == "slow":
            return self._slow
        # adaptive
        now = time.monotonic()
        if now < self._boost_until:
            return self._boost
        if now < self._decay_until:
            return self._medium
        return self._slow

    def on_event(self, event_type: str) -> None:
        if self._mode != "adaptive":
            return
        if event_type not in self._adaptive_events:
            return
        now = time.monotonic()
        self._boost_until = now + self._boost_hold
        self._decay_until = self._boost_until + self._boost_hold
        log.info("Adaptive boost triggered by %s", event_type)

    @property
    def mode(self) -> str:
        return self._mode
