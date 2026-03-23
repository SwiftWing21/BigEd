"""Unified multi-fleet dashboard data aggregation — cluster-wide views of agents, tasks, metrics.

Provides helpers that aggregate data across all federated peers for the
unified dashboard (Figma design).  Each function gracefully degrades if
peers are unreachable — local data is always returned.

v0.100.00b: Unified Dashboard Hooks
"""

import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

_log = logging.getLogger("federation_data")


def _get_federation_config() -> dict:
    """Load federation config with safe defaults."""
    try:
        from config import load_config
        cfg = load_config()
        fed = cfg.get("federation", {})
        return {
            "enabled": fed.get("enabled", False),
            "peers": fed.get("peers", []),
            "peer_timeout_secs": fed.get("peer_timeout_secs", 5),
        }
    except Exception:
        _log.warning("Failed to load federation config", exc_info=True)
        return {"enabled": False, "peers": [], "peer_timeout_secs": 5}


def _get_fleet_id() -> str:
    """Return this fleet's identifier."""
    try:
        from config import load_config
        cfg = load_config()
        name = cfg.get("naming", {}).get("device_name", "")
        if name:
            return name
    except Exception:
        pass
    import socket
    return socket.gethostname()


def _fetch_peer_json(peer_url: str, path: str, timeout: int = 5) -> dict | None:
    """GET JSON from a peer fleet. Returns None on failure."""
    try:
        url = f"{peer_url.rstrip('/')}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        _log.debug("Failed to fetch %s from peer %s", path, peer_url, exc_info=True)
        return None


def _fetch_all_peers(path: str) -> list[tuple[str, dict | None]]:
    """Fetch a JSON endpoint from all peers in parallel.

    Returns list of (peer_url, data) tuples. data is None for failed peers.
    """
    cfg = _get_federation_config()
    if not cfg["enabled"] or not cfg["peers"]:
        return []

    timeout = cfg["peer_timeout_secs"]
    results = []

    with ThreadPoolExecutor(max_workers=min(len(cfg["peers"]), 5)) as pool:
        futures = {
            pool.submit(_fetch_peer_json, peer, path, timeout): peer
            for peer in cfg["peers"]
        }
        for future in as_completed(futures, timeout=timeout + 2):
            peer = futures[future]
            try:
                data = future.result()
                results.append((peer, data))
            except Exception:
                results.append((peer, None))

    return results


def get_cluster_agents() -> list[dict]:
    """All agents across all peers (local + remote).

    Each agent dict includes a 'fleet' field indicating origin.
    """
    fleet_id = _get_fleet_id()

    # Local agents
    local_agents = []
    try:
        import db
        status = db.fleet_status()
        for a in status.get("agents", []):
            agent = dict(a) if hasattr(a, "keys") else a
            agent["fleet"] = fleet_id
            agent["fleet_url"] = "local"
            local_agents.append(agent)
    except Exception:
        _log.warning("Failed to get local agents", exc_info=True)

    # Remote agents
    remote_agents = []
    for peer_url, data in _fetch_all_peers("/api/agents"):
        if not data:
            continue
        agents_list = data if isinstance(data, list) else data.get("agents", [])
        for a in agents_list:
            a["fleet"] = peer_url
            a["fleet_url"] = peer_url
            remote_agents.append(a)

    return local_agents + remote_agents


def get_cluster_tasks(status: str | None = None) -> list[dict]:
    """All tasks across all peers, optionally filtered by status.

    Each task dict includes a 'fleet' field indicating origin.
    """
    fleet_id = _get_fleet_id()

    # Local tasks
    local_tasks = []
    try:
        import db
        conn = db.get_conn()
        if status:
            rows = conn.execute(
                "SELECT id, type, status, assigned_to, created_at FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT 100",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, type, status, assigned_to, created_at FROM tasks ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        for r in rows:
            task = dict(r)
            task["fleet"] = fleet_id
            task["fleet_url"] = "local"
            local_tasks.append(task)
    except Exception:
        _log.warning("Failed to get local tasks", exc_info=True)

    # Remote tasks
    remote_tasks = []
    path = "/api/tasks"
    if status:
        path += f"?status={status}"
    for peer_url, data in _fetch_all_peers(path):
        if not data:
            continue
        tasks_list = data if isinstance(data, list) else data.get("tasks", [])
        for t in tasks_list:
            t["fleet"] = peer_url
            t["fleet_url"] = peer_url
            remote_tasks.append(t)

    return local_tasks + remote_tasks


def get_cluster_metrics() -> dict:
    """Aggregated metrics across all peers.

    Returns:
        dict with total_agents, total_tasks, total_queue, per_fleet breakdown, reachable/unreachable counts
    """
    fleet_id = _get_fleet_id()

    # Local metrics
    local_agents = 0
    local_tasks = 0
    local_queue = 0
    try:
        import db
        status = db.fleet_status()
        local_agents = len(status.get("agents", []))
        task_counts = status.get("tasks", {})
        local_tasks = sum(task_counts.values()) if isinstance(task_counts, dict) else 0
        local_queue = task_counts.get("PENDING", 0) if isinstance(task_counts, dict) else 0
    except Exception:
        _log.warning("Failed to get local metrics", exc_info=True)

    fleets = [{
        "fleet_id": fleet_id,
        "url": "local",
        "agents": local_agents,
        "tasks": local_tasks,
        "queue": local_queue,
        "reachable": True,
    }]

    total_agents = local_agents
    total_tasks = local_tasks
    total_queue = local_queue
    reachable = 1
    unreachable = 0

    # Remote metrics
    for peer_url, data in _fetch_all_peers("/api/status"):
        if not data:
            unreachable += 1
            fleets.append({
                "fleet_id": peer_url,
                "url": peer_url,
                "agents": 0,
                "tasks": 0,
                "queue": 0,
                "reachable": False,
            })
            continue

        reachable += 1
        peer_agents = data.get("agents", 0)
        peer_queue = data.get("queue_depth", data.get("pending", 0))
        peer_tasks = data.get("total_tasks", peer_queue)

        total_agents += peer_agents
        total_tasks += peer_tasks
        total_queue += peer_queue

        fleets.append({
            "fleet_id": data.get("fleet_id", peer_url),
            "url": peer_url,
            "agents": peer_agents,
            "tasks": peer_tasks,
            "queue": peer_queue,
            "reachable": True,
        })

    return {
        "total_agents": total_agents,
        "total_tasks": total_tasks,
        "total_queue": total_queue,
        "reachable_peers": reachable,
        "unreachable_peers": unreachable,
        "fleets": fleets,
    }
