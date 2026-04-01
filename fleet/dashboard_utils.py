"""Shared infrastructure for dashboard blueprints.

Centralises helpers that were duplicated between dashboard.py and
process_control.py: config loading, DB access, rate limiting, role
checks, and common constants.
"""
import json
import logging
import re
import time
import threading
from datetime import datetime
from pathlib import Path

from security import (
    get_request_role,
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

log = logging.getLogger("dashboard")

FLEET_DIR = Path(__file__).parent
DB_PATH = FLEET_DIR / "fleet.db"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"
VALID_AGENT = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

# ── Config loader ────────────────────────────────────────────────────────────


def _load_config():
    """Load fleet.toml for thermal/training/module config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    toml_path = FLEET_DIR / "fleet.toml"
    if not toml_path.exists():
        return {}
    return tomllib.loads(toml_path.read_text(encoding="utf-8"))


# ── DB helpers ───────────────────────────────────────────────────────────────


def get_conn():
    """Get fleet.db connection via db module (WAL, retry, optional SQLCipher)."""
    import db as _db
    return _db.get_conn()


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Role helpers ─────────────────────────────────────────────────────────────


def _get_request_role(req=None):
    return get_request_role(_load_config, req)


def _require_role(role):
    return _require_role_raw(role, _load_config)


# ── Adaptive Rate Limiter ────────────────────────────────────────────────────
#
# Normal use: no limits. Only triggers under abnormal load (potential DDoS).
# Tracks requests per source (IP) across a sliding window. If a single source
# exceeds the burst threshold in a short window, that source gets throttled.
# The threshold is configurable via fleet.toml [security] rate_limit_burst.
# Default: 500 requests per 10-second window per source = ~50 req/s sustained.
# This allows rapid human interaction while catching automated flooding.

_rate_windows = {}  # key: (source_ip, window_id) → count
_rate_locked = {}   # key: source_ip → unlock_time (epoch)

# Defaults — overridden by fleet.toml [security] if present
_RATE_WINDOW_SEC = 10
_RATE_BURST_THRESHOLD = 500  # requests per window before throttling
_RATE_LOCKOUT_SEC = 60       # how long to lock out a flooding source


def _get_rate_config():
    """Load adaptive rate limit config from fleet.toml (cached by load_config)."""
    try:
        cfg = _load_config()
        sec = cfg.get("security", {})
        return (
            sec.get("rate_limit_window", _RATE_WINDOW_SEC),
            sec.get("rate_limit_burst", _RATE_BURST_THRESHOLD),
            sec.get("rate_limit_lockout", _RATE_LOCKOUT_SEC),
        )
    except Exception:
        return _RATE_WINDOW_SEC, _RATE_BURST_THRESHOLD, _RATE_LOCKOUT_SEC


def _check_rate_limit(endpoint=None, max_per_min=None):
    """Adaptive rate limiter — only throttles under abnormal load.

    For normal human use (dozens of clicks/min), always returns True.
    Only triggers when a single source exceeds burst threshold (e.g. 500 req/10s).
    The endpoint and max_per_min params are kept for backward compat but ignored
    in favor of the adaptive per-source approach.
    """
    try:
        from flask import request as _req
        source = _req.remote_addr or "local"
    except Exception:
        return True  # outside request context — allow

    now = time.time()
    window_sec, burst, lockout = _get_rate_config()

    # Check if source is currently locked out
    if source in _rate_locked:
        if now < _rate_locked[source]:
            return False
        del _rate_locked[source]

    # Sliding window: bucket by (source, window_id)
    window_id = int(now / window_sec)
    key = (source, window_id)

    # Evict old windows periodically
    if len(_rate_windows) > 2000:
        cutoff = window_id - 3
        stale = [k for k in _rate_windows if k[1] < cutoff]
        for k in stale:
            del _rate_windows[k]

    count = _rate_windows.get(key, 0) + 1
    _rate_windows[key] = count

    if count > burst:
        # Lock out this source
        _rate_locked[source] = now + lockout
        log.warning("Rate limit triggered: source=%s count=%d/%ds — locked for %ds",
                     source, count, window_sec, lockout)
        return False

    return True


# ── Misc helpers ─────────────────────────────────────────────────────────────


def _is_recent(timestamp_str: str, seconds: int = 120) -> bool:
    try:
        ts = datetime.fromisoformat(timestamp_str)
        return (datetime.utcnow() - ts).total_seconds() < seconds
    except Exception:
        return False


def safe_error(e):
    return _safe_error(e)


# ── SSE state (shared across blueprints) ────────────────────────────────────

_alerts = []
_alert_lock = threading.Lock()
_sse_clients = []  # list[{"queue": Queue, "last_active": float}]
_sse_lock = threading.Lock()


def _add_alert(level: str, message: str, source: str = "system"):
    """Add an alert (info/warning/critical) and broadcast via SSE. Deduplicates."""
    with _alert_lock:
        # Deduplicate: skip if same message already exists and isn't acknowledged
        for existing in _alerts[-20:]:
            if existing["message"] == message and not existing["acknowledged"]:
                return
        alert = {
            "id": int(time.time() * 1000),
            "level": level,
            "message": message,
            "source": source,
            "time": datetime.utcnow().isoformat(),
            "acknowledged": False,
        }
        _alerts.append(alert)
        # Keep only last 100 alerts
        if len(_alerts) > 100:
            _alerts.pop(0)
    _broadcast_sse({"type": "alert", "data": alert})


def _broadcast_sse(data: dict):
    """Send SSE event to all connected clients, reap stale ones."""
    msg = f"data: {json.dumps(data)}\n\n"
    now = time.time()
    dead = []
    with _sse_lock:
        for client in _sse_clients:
            try:
                client["queue"].put_nowait(msg)
                client["last_active"] = now
            except Exception:
                dead.append(client)
        # Reap stale (>120s) and dead clients
        for c in dead:
            _sse_clients.remove(c)
        _sse_clients[:] = [c for c in _sse_clients if now - c["last_active"] <= 120]
