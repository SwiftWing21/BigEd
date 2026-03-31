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


# ── Rate limiter ─────────────────────────────────────────────────────────────

_rate_limits = {}


def _check_rate_limit(endpoint, max_per_min=10):
    """Simple in-memory rate limit. Returns True if allowed."""
    now = time.time()
    # Evict stale entries when dict grows large
    if len(_rate_limits) > 500:
        stale = [k for k, v in _rate_limits.items() if now - v[0] >= 300]
        for k in stale:
            del _rate_limits[k]
    if endpoint not in _rate_limits:
        _rate_limits[endpoint] = (now, 1)
        return True
    last, count = _rate_limits[endpoint]
    if now - last > 60:
        _rate_limits[endpoint] = (now, 1)
        return True
    if count >= max_per_min:
        return False
    _rate_limits[endpoint] = (last, count + 1)
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
