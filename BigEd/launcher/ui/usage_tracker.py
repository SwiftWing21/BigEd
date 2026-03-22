"""Session and all-time usage tracking for Fleet Comm providers."""
from __future__ import annotations
import threading

PROVIDERS = ("local", "claude", "gemini")


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


class UsageTracker:
    """In-memory session counters + helpers for fleet.db all-time queries."""

    def __init__(self):
        self._lock = threading.Lock()
        self._session: dict[str, dict] = {}
        for p in PROVIDERS:
            self._session[p] = self._empty()

    @staticmethod
    def _empty() -> dict:
        return {"tokens": 0, "cost": 0.0, "calls": 0,
                "tok_per_sec": 0.0, "model": ""}

    def record(self, provider: str, model: str, *,
               tokens_in: int = 0, tokens_out: int = 0,
               cost: float = 0.0, tok_per_sec: float = 0.0) -> None:
        with self._lock:
            s = self._session.setdefault(provider, self._empty())
            s["tokens"] += tokens_in + tokens_out
            s["cost"] += cost
            s["calls"] += 1
            s["model"] = model
            if tok_per_sec > 0:
                s["tok_per_sec"] = tok_per_sec

    def session_stats(self, provider: str) -> dict:
        with self._lock:
            return dict(self._session.get(provider, self._empty()))

    def reset(self, provider: str) -> None:
        with self._lock:
            self._session[provider] = self._empty()

    def format_line(self, provider: str) -> str:
        s = self.session_stats(provider)
        if s["calls"] == 0:
            return ""
        tok = _fmt_tokens(s["tokens"])
        if provider == "local":
            tps = f"{s['tok_per_sec']:.0f} tok/s" if s["tok_per_sec"] else ""
            model = s["model"] or ""
            parts = [f"{tok} tok", tps, model]
            return " | ".join(p for p in parts if p)
        cost = f"${s['cost']:.2f}"
        calls = f"{s['calls']} call{'s' if s['calls'] != 1 else ''}"
        return f"{tok} tok | {cost} | {calls}"
