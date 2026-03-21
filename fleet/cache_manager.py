"""Fleet-wide cache registry and invalidation.

Provides a central registry where any module can register its in-memory caches
(model lists, temperature readings, parsed status, federation peers, etc.)
and allows fleet-wide or targeted invalidation via API or internal calls.

Thread-safe: multiple workers may register/invalidate simultaneously.
Lazy imports: no fleet module imports at module level to avoid circular deps.

Usage:
    from cache_manager import register_cache, invalidate, invalidate_all

    # In your module's init or first-use path:
    register_cache("cpu_temp", lambda: _clear_temp_cache(), ttl_secs=5)

    # Invalidate from anywhere:
    invalidate("cpu_temp")       # single cache
    invalidate_all()             # nuclear option
    invalidate_stale()           # only past-TTL caches

Dashboard endpoints (added to dashboard.py):
    GET  /api/cache/stats              — list all caches with age/TTL
    POST /api/cache/invalidate         — invalidate all (or ?name=X for one)
    POST /api/cache/invalidate/<name>  — invalidate specific cache

Registered caches (documented, not yet wired into source modules):
    cpu_temp          — cpu_temp.py: _cache_temp / _cache_time (5s TTL)
    provider_health   — providers.py: _provider_health dict (60s TTL)
    circuit_state     — providers.py: _circuit_state per-provider breaker (300s TTL)
    federation_peers  — dashboard.py: _federation_peers dict (120s TTL)
    alerts_memory     — dashboard.py: _alerts in-memory list (no TTL, manual only)
    hw_state          — hw_supervisor.py: hw_state.json on-disk cache (5s TTL)
"""
import time
import threading
from typing import Callable

# ── Registry ──────────────────────────────────────────────────────────────────

_registry: dict[str, dict] = {}  # name -> {clear_fn, last_cleared, ttl, registered_at}
_lock = threading.Lock()


def register_cache(name: str, clear_fn: Callable[[], None], ttl_secs: int = 300) -> None:
    """Register a named cache with its clear function and TTL.

    Args:
        name: Unique cache identifier (e.g. "cpu_temp", "provider_health").
        clear_fn: Zero-arg callable that resets the cache to empty/fresh state.
        ttl_secs: Time-to-live in seconds. invalidate_stale() clears caches
                  that haven't been cleared within this window.

    Re-registering the same name overwrites the previous entry.
    """
    now = time.time()
    with _lock:
        _registry[name] = {
            "clear_fn": clear_fn,
            "ttl": ttl_secs,
            "last_cleared": now,
            "registered_at": now,
        }


def unregister_cache(name: str) -> bool:
    """Remove a cache from the registry. Returns True if it existed."""
    with _lock:
        return _registry.pop(name, None) is not None


def invalidate(name: str) -> bool:
    """Invalidate a specific cache by name.

    Calls the registered clear_fn and updates last_cleared timestamp.
    Returns True if the cache was found and cleared, False if unknown name.
    """
    with _lock:
        entry = _registry.get(name)
        if not entry:
            return False
        # Copy fn ref while holding lock, call outside to avoid deadlock
        fn = entry["clear_fn"]

    # Call clear_fn outside lock -- the fn itself may acquire other locks
    try:
        fn()
    except Exception:
        pass  # Cache clear must never crash the caller

    with _lock:
        entry = _registry.get(name)
        if entry:
            entry["last_cleared"] = time.time()
    return True


def invalidate_all() -> int:
    """Invalidate all registered caches. Returns count of caches cleared."""
    with _lock:
        names = list(_registry.keys())

    cleared = 0
    for name in names:
        if invalidate(name):
            cleared += 1
    return cleared


def invalidate_stale() -> int:
    """Invalidate caches that have exceeded their TTL. Returns count cleared.

    This is safe to call periodically (e.g. from a background thread) to
    keep caches fresh without requiring callers to know about TTLs.
    """
    now = time.time()
    stale_names = []
    with _lock:
        for name, entry in _registry.items():
            if entry["ttl"] <= 0:
                continue  # TTL=0 means manual-only, skip auto-stale
            age = now - entry["last_cleared"]
            if age > entry["ttl"]:
                stale_names.append(name)

    cleared = 0
    for name in stale_names:
        if invalidate(name):
            cleared += 1
    return cleared


def get_cache_stats() -> list[dict]:
    """Return stats for all registered caches.

    Each entry contains:
        name, ttl, age_secs, last_cleared (ISO), registered_at (ISO),
        is_stale (bool).
    """
    now = time.time()
    stats = []
    with _lock:
        for name, entry in sorted(_registry.items()):
            age = now - entry["last_cleared"]
            stats.append({
                "name": name,
                "ttl": entry["ttl"],
                "age_secs": round(age, 1),
                "is_stale": entry["ttl"] > 0 and age > entry["ttl"],
                "last_cleared": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(entry["last_cleared"])
                ),
                "registered_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(entry["registered_at"])
                ),
            })
    return stats


def get_cache_count() -> int:
    """Return number of registered caches."""
    with _lock:
        return len(_registry)


# ── Self-registration helpers ─────────────────────────────────────────────────
#
# These register known fleet caches on first import. Each uses lazy imports
# inside the clear_fn to avoid circular dependencies. The actual source
# modules are NOT modified -- these are "external" registrations that reach
# into the module's globals to reset them.
#
# Modules can also call register_cache() themselves for tighter ownership.


def _register_known_caches() -> None:
    """Register all known fleet caches. Safe to call multiple times."""

    # ── cpu_temp.py: _cache_temp / _cache_time ────────────────────────────
    def _clear_cpu_temp():
        try:
            import cpu_temp
            cpu_temp._cache_temp = 0
            cpu_temp._cache_time = 0.0
        except ImportError:
            pass

    register_cache("cpu_temp", _clear_cpu_temp, ttl_secs=5)

    # ── providers.py: _provider_health dict ───────────────────────────────
    def _clear_provider_health():
        try:
            import providers
            providers._provider_health.clear()
        except ImportError:
            pass

    register_cache("provider_health", _clear_provider_health, ttl_secs=60)

    # ── providers.py: _circuit_state per-provider breaker ─────────────────
    def _clear_circuit_state():
        try:
            import providers
            with providers._circuit_lock:
                providers._circuit_state.clear()
        except ImportError:
            pass

    register_cache("circuit_state", _clear_circuit_state, ttl_secs=300)

    # ── dashboard.py: _federation_peers dict ──────────────────────────────
    def _clear_federation_peers():
        try:
            import dashboard
            dashboard._federation_peers.clear()
        except ImportError:
            pass

    register_cache("federation_peers", _clear_federation_peers, ttl_secs=120)

    # ── dashboard.py: _alerts in-memory list ──────────────────────────────
    def _clear_alerts():
        try:
            import dashboard
            with dashboard._alert_lock:
                dashboard._alerts.clear()
        except ImportError:
            pass

    register_cache("alerts_memory", _clear_alerts, ttl_secs=0)  # manual only (TTL=0 means never auto-stale)

    # ── hw_supervisor.py: hw_state.json on-disk cache ─────────────────────
    # This is a file-based cache -- "clearing" means deleting the stale file
    # so the next write_state() call produces a fresh one.
    def _clear_hw_state():
        try:
            from pathlib import Path
            hw_path = Path(__file__).parent / "hw_state.json"
            if hw_path.exists():
                hw_path.unlink(missing_ok=True)
        except Exception:
            pass

    register_cache("hw_state", _clear_hw_state, ttl_secs=5)

    # ── dashboard.py: _rate_limits (rate limiter state) ───────────────────
    def _clear_rate_limits():
        try:
            import dashboard
            dashboard._rate_limits.clear()
        except ImportError:
            pass

    register_cache("rate_limits", _clear_rate_limits, ttl_secs=60)


# Auto-register on import
_register_known_caches()
