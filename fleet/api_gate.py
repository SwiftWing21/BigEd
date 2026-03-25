"""Central API access control gate for external provider calls.

Local-only by default. Must be explicitly enabled (with budget + provider
whitelist) before any external API call is allowed through.  Gate bugs
never block local operation — all checks are wrapped in try/except.
"""
from __future__ import annotations

import collections
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("api_gate")

# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class GateState:
    """Runtime state of the API gate."""

    enabled: bool = False
    session_budget_usd: float = 0.00
    session_spend_usd: float = 0.00
    drain_mode: str = "graceful"  # "graceful" | "immediate" | "none"
    expires_at: Optional[float] = None  # epoch seconds, None = no expiry
    allowed_providers: set[str] = field(default_factory=set)


@dataclass
class APICallRecord:
    """Single recorded API call."""

    timestamp: float
    provider: str
    skill: str
    agent: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    purpose: str = ""
    fallback_from: Optional[str] = None


# ── Module-level state ───────────────────────────────────────────────────────

_gate_lock = threading.Lock()
_state = GateState()
_ring: collections.deque[APICallRecord] = collections.deque(maxlen=200)
_event_subscribers: list = []

# Track whether budget warnings have already fired (reset on enable)
_budget_80_warned = False


# ── Internal helpers ─────────────────────────────────────────────────────────


def _push_event(event_type: str, detail: str) -> None:
    """Log the event and notify any SSE subscribers."""
    log.info("api_gate event: %s — %s", event_type, detail)
    payload = {"type": event_type, "detail": detail, "ts": time.time()}
    dead: list[int] = []
    for i, sub in enumerate(_event_subscribers):
        try:
            sub(payload)
        except Exception:
            dead.append(i)
    # Remove dead subscribers in reverse order so indices stay valid
    for i in reversed(dead):
        try:
            _event_subscribers.pop(i)
        except IndexError:
            pass


# ── Public API ───────────────────────────────────────────────────────────────


def check(
    provider: str,
    purpose: str = "",
    config: Optional[dict] = None,
) -> tuple[bool, str]:
    """Decide whether an external API call is allowed.

    Returns (True, "allowed") or (False, "<reason>").
    Gate bugs never block local operation — failures return (True, ...).
    """
    global _budget_80_warned
    try:
        # Hard overrides from fleet config
        if config is not None:
            from config import is_offline, is_air_gap

            if is_air_gap(config):
                return False, "air_gap_mode is active — all external calls blocked"
            if is_offline(config):
                return False, "offline_mode is active — external calls blocked"

        with _gate_lock:
            # Lazy TTL expiry check
            if _state.expires_at is not None and time.time() >= _state.expires_at:
                _state.enabled = False
                _push_event("gate_expired", "API gate TTL expired — auto-disabled")
                return False, "gate expired (TTL reached)"

            # Gate must be explicitly enabled
            if not _state.enabled:
                return False, "gate disabled (local-only mode)"

            # Provider whitelist
            if _state.allowed_providers and provider not in _state.allowed_providers:
                return (
                    False,
                    f"provider '{provider}' not in allowed set {_state.allowed_providers}",
                )

            # Budget enforcement
            if _state.session_budget_usd > 0:
                ratio = (
                    _state.session_spend_usd / _state.session_budget_usd
                    if _state.session_budget_usd
                    else 0.0
                )

                # 80% warning (fire once per enable cycle)
                if ratio >= 0.8 and not _budget_80_warned:
                    _budget_80_warned = True
                    _push_event(
                        "budget_warning",
                        f"80% of session budget consumed "
                        f"(${_state.session_spend_usd:.4f} / ${_state.session_budget_usd:.2f})",
                    )

                # Hard cap
                if _state.session_spend_usd >= _state.session_budget_usd:
                    _push_event(
                        "budget_hit",
                        f"Session budget exhausted "
                        f"(${_state.session_spend_usd:.4f} >= ${_state.session_budget_usd:.2f})",
                    )
                    return False, "session budget exhausted"

        return True, "allowed"

    except Exception:
        # Gate bugs must never block local operation — fall back to local
        log.warning("api_gate.check() error — falling back to local", exc_info=True)
        return False, "gate error (safe fallback to local)"


def record_call(
    provider: str,
    skill: str = "",
    agent: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    purpose: str = "",
    fallback_from: Optional[str] = None,
) -> None:
    """Record an API call in the ring buffer and update spend."""
    try:
        rec = APICallRecord(
            timestamp=time.time(),
            provider=provider,
            skill=skill,
            agent=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            purpose=purpose,
            fallback_from=fallback_from,
        )
        with _gate_lock:
            _ring.append(rec)
            _state.session_spend_usd += cost_usd

        # Alert on fallback (skip noise for review-purpose calls)
        if fallback_from and purpose != "review":
            _push_event(
                "fallback_alert",
                f"{fallback_from} → {provider} (skill={skill}, agent={agent})",
            )
    except Exception:
        log.warning("api_gate.record_call() error", exc_info=True)


def enable(
    budget: float = 0.0,
    providers: Optional[list[str]] = None,
    ttl_hours: Optional[float] = None,
    drain_mode: str = "graceful",
) -> dict:
    """Enable the API gate with a budget and provider whitelist.

    Args:
        budget: Session budget in USD (0 = unlimited).
        providers: List of allowed provider names (empty = all).
        ttl_hours: Auto-disable after this many hours (None = no expiry).
        drain_mode: "graceful" | "immediate" | "none".

    Returns:
        Gate status dict after enabling.
    """
    global _budget_80_warned
    with _gate_lock:
        _state.enabled = True
        _state.session_budget_usd = budget
        _state.session_spend_usd = 0.0
        _state.drain_mode = drain_mode
        _state.allowed_providers = set(providers) if providers else set()
        _state.expires_at = (time.time() + ttl_hours * 3600) if ttl_hours else None
        _budget_80_warned = False

    _push_event(
        "gate_enabled",
        f"budget=${budget:.2f}, providers={providers or 'all'}, "
        f"ttl={ttl_hours}h, drain={drain_mode}",
    )
    return status()


def disable() -> dict:
    """Disable the API gate (return to local-only mode)."""
    with _gate_lock:
        _state.enabled = False
    _push_event("gate_disabled", "API gate disabled — local-only mode")
    return status()


def set_drain_mode(mode: str) -> dict:
    """Set drain mode: 'graceful', 'immediate', or 'none'."""
    with _gate_lock:
        _state.drain_mode = mode
    _push_event("drain_mode_changed", f"drain mode → {mode}")
    return status()


def status() -> dict:
    """Return a full snapshot of the gate state."""
    with _gate_lock:
        return {
            "enabled": _state.enabled,
            "session_budget_usd": _state.session_budget_usd,
            "session_spend_usd": round(_state.session_spend_usd, 6),
            "drain_mode": _state.drain_mode,
            "expires_at": _state.expires_at,
            "allowed_providers": sorted(_state.allowed_providers),
            "ring_size": len(_ring),
            "ring_capacity": _ring.maxlen,
        }


def get_ring(limit: int = 50) -> list[dict]:
    """Return the most recent API call records (newest first)."""
    with _gate_lock:
        items = list(_ring)
    # Newest first, capped at limit
    items = items[-limit:] if limit < len(items) else items
    items.reverse()
    return [dataclasses.asdict(r) for r in items]
