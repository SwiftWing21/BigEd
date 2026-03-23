"""Cross-fleet task routing and capacity aggregation (v0.100.00b).

Routes tasks to peer fleets when the local queue exceeds the overflow
threshold.  Aggregates capacity data across all known federation peers
for cluster-wide visibility.

Routing decision logic:
  1. Local queue < overflow_threshold  -> run locally
  2. Local queue >= overflow_threshold AND peer available -> route to best peer
  3. No peers available               -> run locally (best effort)
  4. Priority >= local_priority_min    -> always run locally (critical tasks)
"""

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("federation_router")

FLEET_DIR = Path(__file__).parent

# ── In-memory routing stats (thread-safe) ────────────────────────────────────

_stats_lock = threading.Lock()
_routing_stats = {
    "routed_locally": 0,
    "routed_remotely": 0,
    "routing_failures": 0,
    "last_remote_route": None,      # ISO timestamp
    "last_remote_peer": None,       # peer URL
}


def _load_federation_config() -> dict:
    """Load [federation] section from fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("federation", {})
    except Exception:
        log.warning("federation_router: failed to load config", exc_info=True)
        return {}


def _load_fleet_config() -> dict:
    """Load full fleet.toml config."""
    try:
        from config import load_config
        return load_config()
    except Exception:
        return {}


def _get_local_capacity(config: dict | None = None) -> dict:
    """Return current local fleet capacity metrics.

    Returns dict with keys: max_workers, active_agents, pending_tasks,
    queue_ratio (pending / max_capacity).
    """
    if config is None:
        config = _load_fleet_config()
    max_workers = config.get("fleet", {}).get("max_workers", 10)
    # Approximate max queue depth as max_workers * 5 (same as supervisor)
    max_capacity = max_workers * 5

    pending = 0
    active = 0
    try:
        import db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='PENDING'"
            ).fetchone()
            pending = row[0] if row else 0
            row2 = conn.execute(
                "SELECT COUNT(*) FROM agents "
                "WHERE last_heartbeat >= datetime('now', '-60 seconds')"
            ).fetchone()
            active = row2[0] if row2 else 0
    except Exception:
        log.debug("federation_router: failed to query local capacity", exc_info=True)

    return {
        "max_workers": max_workers,
        "max_capacity": max_capacity,
        "active_agents": active,
        "pending_tasks": pending,
        "queue_ratio": pending / max(max_capacity, 1),
    }


def should_route_remotely(skill: str, priority: int = 5) -> bool:
    """Decide whether a task should be routed to a remote peer.

    Returns True when the local queue exceeds the overflow threshold
    AND the task is not pinned locally by priority.
    """
    fed_cfg = _load_federation_config()
    if not fed_cfg.get("enabled"):
        return False
    if not fed_cfg.get("routing_enabled", True):
        return False

    local_priority_min = int(fed_cfg.get("local_priority_min", 9))
    if priority >= local_priority_min:
        return False  # critical tasks always stay local

    overflow_threshold = float(fed_cfg.get("overflow_threshold", 0.85))
    local = _get_local_capacity()
    return local["queue_ratio"] >= overflow_threshold


def find_best_peer(skill: str) -> dict | None:
    """Pick the peer with lowest load that is online.

    Returns a dict {"url": str, "fleet_id": str, "agents": int, "pending": int}
    or None if no suitable peer is found.
    """
    fed_cfg = _load_federation_config()
    peers_urls = fed_cfg.get("peers", [])
    routing_timeout = int(fed_cfg.get("routing_timeout", 10))
    if not peers_urls:
        return None

    best = None
    best_load = float("inf")

    for url in peers_urls:
        try:
            resp = urllib.request.urlopen(
                f"{url}/api/federation/peers", timeout=routing_timeout
            )
            peer_data = json.loads(resp.read())
            # The peer endpoint returns per-fleet data; we also probe its own capacity
            try:
                cap_resp = urllib.request.urlopen(
                    f"{url}/api/federation/capacity", timeout=routing_timeout
                )
                cap = json.loads(cap_resp.read())
                load = cap.get("local", {}).get("queue_ratio", 1.0)
                agents = cap.get("local", {}).get("active_agents", 0)
                pending = cap.get("local", {}).get("pending_tasks", 0)
            except Exception:
                # Fallback: use heartbeat data; estimate load from pending/agents ratio
                # If capacity endpoint unavailable, use basic peer info
                load = 0.5  # unknown — treat as moderately loaded
                agents = 0
                pending = 0

            if load < best_load:
                best_load = load
                best = {
                    "url": url,
                    "fleet_id": url,
                    "agents": agents,
                    "pending": pending,
                    "load": load,
                }
        except Exception:
            log.debug("federation_router: peer %s unreachable", url)
            continue

    # Only route to peer if it is less loaded than local
    if best and best["load"] < 0.85:
        return best
    return None


def route_to_peer(peer: dict, task_dict: dict) -> dict:
    """POST a task to a peer's /api/trigger endpoint.

    Args:
        peer: dict from find_best_peer() with at least "url" key
        task_dict: dict with "type", "payload", "priority" keys

    Returns dict with "ok", "task_id", "peer" on success or "error" on failure.
    """
    fed_cfg = _load_federation_config()
    routing_timeout = int(fed_cfg.get("routing_timeout", 10))

    url = peer["url"]
    body = json.dumps({
        "type": task_dict.get("type", ""),
        "payload": task_dict.get("payload", {}),
        "priority": task_dict.get("priority", 5),
        "source": "federation",
    }).encode()

    try:
        req = urllib.request.Request(
            f"{url}/api/trigger",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=routing_timeout)
        result = json.loads(resp.read())

        with _stats_lock:
            _routing_stats["routed_remotely"] += 1
            _routing_stats["last_remote_route"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _routing_stats["last_remote_peer"] = url

        return {"ok": True, "task_id": result.get("task_id"), "peer": url}
    except Exception as exc:
        log.warning("federation_router: failed to route to %s: %s", url, exc)
        with _stats_lock:
            _routing_stats["routing_failures"] += 1
        return {"ok": False, "error": str(exc), "peer": url}


def record_local_route():
    """Increment the local routing counter (called by supervisor on local assignment)."""
    with _stats_lock:
        _routing_stats["routed_locally"] += 1


def get_routing_stats() -> dict:
    """Return a snapshot of routing statistics."""
    with _stats_lock:
        return dict(_routing_stats)


def get_aggregated_capacity() -> dict:
    """Collect capacity data from all known peers plus local fleet.

    Returns {
        "local": {...capacity dict...},
        "peers": {"<url>": {...}, ...},
        "cluster": {"total_agents": N, "total_pending": N, "total_max_capacity": N}
    }
    """
    config = _load_fleet_config()
    fed_cfg = config.get("federation", {})
    routing_timeout = int(fed_cfg.get("routing_timeout", 10))
    local = _get_local_capacity(config)

    total_agents = local["active_agents"]
    total_pending = local["pending_tasks"]
    total_max = local["max_capacity"]
    peer_caps = {}

    for url in fed_cfg.get("peers", []):
        try:
            resp = urllib.request.urlopen(
                f"{url}/api/federation/capacity", timeout=routing_timeout
            )
            data = json.loads(resp.read())
            peer_local = data.get("local", {})
            peer_caps[url] = peer_local
            total_agents += peer_local.get("active_agents", 0)
            total_pending += peer_local.get("pending_tasks", 0)
            total_max += peer_local.get("max_capacity", 0)
        except Exception:
            peer_caps[url] = {"error": "unreachable"}

    return {
        "local": local,
        "peers": peer_caps,
        "cluster": {
            "total_agents": total_agents,
            "total_pending": total_pending,
            "total_max_capacity": total_max,
            "cluster_queue_ratio": total_pending / max(total_max, 1),
        },
    }


def get_cluster_status() -> dict:
    """High-level cluster status for dashboard consumption.

    Returns per-peer breakdown plus cluster totals.
    """
    return get_aggregated_capacity()
