"""Cross-fleet HITL aggregation — collect and route human-in-the-loop tasks across federated peers.

Provides:
- get_all_hitl_tasks()          — local + remote WAITING_HUMAN tasks
- respond_to_remote_hitl()      — forward operator response to originating peer
- forward_hitl_notification()   — notify peers about new HITL task
- get_federation_hitl_config()  — config from [federation.hitl] in fleet.toml

v0.100.00b: Cross-Fleet HITL
"""

import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

_log = logging.getLogger("federation_hitl")


def get_federation_hitl_config() -> dict:
    """Load [federation.hitl] config with safe defaults."""
    try:
        from config import load_config
        cfg = load_config()
        fed = cfg.get("federation", {})
        hitl_cfg = fed.get("hitl", {})
        return {
            "enabled": fed.get("enabled", False),
            "peers": fed.get("peers", []),
            "peer_timeout_secs": fed.get("peer_timeout_secs", 5),
            "aggregate_remote": hitl_cfg.get("aggregate_remote", True),
            "forward_notifications": hitl_cfg.get("forward_notifications", True),
            "remote_response_timeout": hitl_cfg.get("remote_response_timeout", 30),
        }
    except Exception:
        _log.warning("Failed to load federation HITL config, using defaults", exc_info=True)
        return {
            "enabled": False,
            "peers": [],
            "peer_timeout_secs": 5,
            "aggregate_remote": True,
            "forward_notifications": True,
            "remote_response_timeout": 30,
        }


def _get_fleet_id() -> str:
    """Return this fleet's identifier (device_name or hostname)."""
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


def _fetch_peer_hitl(peer_url: str, timeout: int = 5) -> list[dict]:
    """Fetch WAITING_HUMAN tasks from a single peer. Returns [] on failure."""
    try:
        url = f"{peer_url.rstrip('/')}/api/tasks/waiting-human"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tasks = json.loads(resp.read().decode())
        # Annotate each task with its source fleet
        for t in tasks:
            t["source_fleet"] = peer_url
            t["source"] = f"peer:{peer_url}"
        return tasks
    except Exception:
        _log.debug("Failed to fetch HITL tasks from peer %s", peer_url, exc_info=True)
        return []


def get_all_hitl_tasks() -> list[dict]:
    """Collect WAITING_HUMAN tasks from local DB and all known peers.

    Returns a list of task dicts, each with a 'source_fleet' field:
    - Local tasks: source_fleet = local fleet_id, source = "local"
    - Remote tasks: source_fleet = peer URL, source = "peer:<url>"
    """
    cfg = get_federation_hitl_config()
    fleet_id = _get_fleet_id()

    # Always include local tasks
    local_tasks = []
    try:
        import db
        raw = db.get_waiting_human_tasks()
        for t in raw:
            task_dict = dict(t) if hasattr(t, "keys") else t
            task_dict["source_fleet"] = fleet_id
            task_dict["source"] = "local"
            local_tasks.append(task_dict)
    except Exception:
        _log.warning("Failed to get local HITL tasks", exc_info=True)

    # If federation disabled or no peers, return local only
    if not cfg["enabled"] or not cfg["aggregate_remote"] or not cfg["peers"]:
        return local_tasks

    # Fetch remote tasks in parallel
    remote_tasks = []
    timeout = cfg["peer_timeout_secs"]
    with ThreadPoolExecutor(max_workers=min(len(cfg["peers"]), 5)) as pool:
        futures = {
            pool.submit(_fetch_peer_hitl, peer, timeout): peer
            for peer in cfg["peers"]
        }
        for future in as_completed(futures, timeout=timeout + 2):
            peer = futures[future]
            try:
                tasks = future.result()
                remote_tasks.extend(tasks)
            except Exception:
                _log.debug("Timeout fetching HITL from peer %s", peer)

    return local_tasks + remote_tasks


def respond_to_remote_hitl(peer_url: str, task_id: int, response: str) -> dict:
    """Forward an operator's HITL response to the originating peer fleet.

    Args:
        peer_url: The peer fleet's base URL (e.g. "http://192.168.1.50:5555")
        task_id: The task ID on the remote peer
        response: The operator's response text

    Returns:
        dict with 'ok' key on success, 'error' on failure
    """
    cfg = get_federation_hitl_config()
    timeout = cfg["remote_response_timeout"]

    try:
        url = f"{peer_url.rstrip('/')}/api/tasks/{task_id}/respond"
        body = json.dumps({"response": response}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
        return result
    except Exception as e:
        _log.warning("Failed to forward HITL response to %s task %d: %s",
                      peer_url, task_id, e, exc_info=True)
        return {"error": f"Failed to reach peer {peer_url}: {e}"}


def forward_hitl_notification(peer_urls: list[str], task_info: dict) -> dict:
    """Notify peers about a new HITL task so operator sees it on any connected dashboard.

    Args:
        peer_urls: List of peer fleet base URLs to notify
        task_info: Dict with task details (task_id, type, question, agent, etc.)

    Returns:
        dict with counts of successful and failed notifications
    """
    cfg = get_federation_hitl_config()
    if not cfg["forward_notifications"]:
        return {"notified": 0, "failed": 0, "skipped": True}

    fleet_id = _get_fleet_id()
    payload = {
        **task_info,
        "_source_fleet": fleet_id,
    }
    body = json.dumps(payload).encode()
    timeout = cfg["peer_timeout_secs"]

    notified = 0
    failed = 0

    def _notify_peer(peer_url):
        nonlocal notified, failed
        try:
            url = f"{peer_url.rstrip('/')}/api/federation/hitl/notify"
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=timeout)
            return True
        except Exception:
            _log.debug("Failed to notify peer %s about HITL task", peer_url, exc_info=True)
            return False

    with ThreadPoolExecutor(max_workers=min(len(peer_urls), 5)) as pool:
        futures = {pool.submit(_notify_peer, url): url for url in peer_urls}
        for future in as_completed(futures, timeout=timeout + 2):
            try:
                if future.result():
                    notified += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

    return {"notified": notified, "failed": failed}
