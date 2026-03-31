"""Unit tests for fleet/federation_router.py — cross-fleet task routing."""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest

import federation_router as fr


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_stats():
    """Reset routing stats between tests."""
    with fr._stats_lock:
        fr._routing_stats.update({
            "routed_locally": 0,
            "routed_remotely": 0,
            "routing_failures": 0,
            "last_remote_route": None,
            "last_remote_peer": None,
        })
    yield


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fed_cfg(enabled=True, routing_enabled=True, overflow_threshold=0.85,
             local_priority_min=9, peers=None):
    return {
        "enabled": enabled,
        "routing_enabled": routing_enabled,
        "overflow_threshold": overflow_threshold,
        "local_priority_min": local_priority_min,
        "routing_timeout": 5,
        "peers": peers or [],
    }


# ── 1. should_route_remotely — disabled federation returns False ──────────────

def test_should_route_remotely_federation_disabled():
    with mock.patch.object(fr, "_load_federation_config",
                           return_value=_fed_cfg(enabled=False)):
        assert fr.should_route_remotely("summarize", 5) is False


# ── 2. should_route_remotely — routing disabled returns False ─────────────────

def test_should_route_remotely_routing_disabled():
    with mock.patch.object(fr, "_load_federation_config",
                           return_value=_fed_cfg(routing_enabled=False)):
        assert fr.should_route_remotely("summarize", 5) is False


# ── 3. should_route_remotely — high priority tasks stay local ─────────────────

def test_should_route_remotely_high_priority_stays_local():
    """Tasks at or above local_priority_min must never be routed remotely."""
    cfg = _fed_cfg(enabled=True, routing_enabled=True,
                   overflow_threshold=0.0, local_priority_min=9)
    with mock.patch.object(fr, "_load_federation_config", return_value=cfg):
        # Priority >= 9 must stay local even at overflow
        assert fr.should_route_remotely("summarize", 9) is False
        assert fr.should_route_remotely("summarize", 10) is False


# ── 4. should_route_remotely — under threshold stays local ───────────────────

def test_should_route_remotely_under_threshold(tmp_db):
    """When queue_ratio is below threshold, keep work local."""
    cfg = _fed_cfg(enabled=True, overflow_threshold=0.85)
    local_cap = {
        "max_workers": 10, "max_capacity": 50,
        "active_agents": 2, "pending_tasks": 5,
        "queue_ratio": 0.10,
    }
    with mock.patch.object(fr, "_load_federation_config", return_value=cfg), \
         mock.patch.object(fr, "_get_local_capacity", return_value=local_cap):
        assert fr.should_route_remotely("summarize", 5) is False


# ── 5. should_route_remotely — over threshold routes remotely ────────────────

def test_should_route_remotely_over_threshold(tmp_db):
    """When queue_ratio >= threshold, route remotely."""
    cfg = _fed_cfg(enabled=True, overflow_threshold=0.85)
    local_cap = {
        "max_workers": 10, "max_capacity": 50,
        "active_agents": 10, "pending_tasks": 45,
        "queue_ratio": 0.90,
    }
    with mock.patch.object(fr, "_load_federation_config", return_value=cfg), \
         mock.patch.object(fr, "_get_local_capacity", return_value=local_cap):
        assert fr.should_route_remotely("summarize", 5) is True


# ── 6. find_best_peer — no peers returns None ────────────────────────────────

def test_find_best_peer_no_peers():
    with mock.patch.object(fr, "_load_federation_config",
                           return_value=_fed_cfg(peers=[])):
        result = fr.find_best_peer("summarize")
    assert result is None


# ── 7. find_best_peer — unreachable peer returns None ────────────────────────

def test_find_best_peer_unreachable():
    cfg = _fed_cfg(peers=["http://peer1:5555"])
    with mock.patch.object(fr, "_load_federation_config", return_value=cfg), \
         mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = fr.find_best_peer("summarize")
    assert result is None


# ── 8. find_best_peer — highly loaded peer returns None ───────────────────────

def test_find_best_peer_overloaded_peer():
    """Peer with queue_ratio >= 0.85 should not be selected."""
    cfg = _fed_cfg(peers=["http://peer1:5555"])
    peer_peers_resp = mock.MagicMock()
    peer_peers_resp.read.return_value = b'{"peers": []}'
    peer_cap_resp = mock.MagicMock()
    peer_cap_resp.read.return_value = (
        b'{"local": {"queue_ratio": 0.95, "active_agents": 10, "pending_tasks": 95}}'
    )

    responses = [peer_peers_resp, peer_cap_resp]

    with mock.patch.object(fr, "_load_federation_config", return_value=cfg), \
         mock.patch("urllib.request.urlopen", side_effect=responses):
        result = fr.find_best_peer("summarize")
    assert result is None


# ── 9. route_to_peer — success updates stats ─────────────────────────────────

def test_route_to_peer_success():
    peer = {"url": "http://peer1:5555", "fleet_id": "peer1"}
    task = {"type": "summarize", "payload": {"text": "hello"}, "priority": 5}

    resp = mock.MagicMock()
    resp.read.return_value = b'{"task_id": 42}'

    with mock.patch("urllib.request.urlopen", return_value=resp), \
         mock.patch.object(fr, "_load_federation_config", return_value=_fed_cfg()):
        result = fr.route_to_peer(peer, task)

    assert result["ok"] is True
    assert result["task_id"] == 42
    assert result["peer"] == "http://peer1:5555"

    stats = fr.get_routing_stats()
    assert stats["routed_remotely"] == 1


# ── 10. route_to_peer — failure updates failure stats ────────────────────────

def test_route_to_peer_failure():
    peer = {"url": "http://dead_peer:5555", "fleet_id": "dead"}
    task = {"type": "summarize", "payload": {}, "priority": 5}

    with mock.patch("urllib.request.urlopen", side_effect=OSError("network error")), \
         mock.patch.object(fr, "_load_federation_config", return_value=_fed_cfg()):
        result = fr.route_to_peer(peer, task)

    assert result["ok"] is False
    assert "error" in result

    stats = fr.get_routing_stats()
    assert stats["routing_failures"] == 1


# ── 11. record_local_route — increments local counter ────────────────────────

def test_record_local_route():
    fr.record_local_route()
    fr.record_local_route()
    stats = fr.get_routing_stats()
    assert stats["routed_locally"] == 2


# ── 12. get_routing_stats — returns expected keys ────────────────────────────

def test_get_routing_stats_structure():
    stats = fr.get_routing_stats()
    assert "routed_locally" in stats
    assert "routed_remotely" in stats
    assert "routing_failures" in stats
    assert "last_remote_route" in stats
    assert "last_remote_peer" in stats


# ── 13. get_aggregated_capacity — includes local metrics ─────────────────────

def test_get_aggregated_capacity_no_peers(tmp_db):
    """With no peers configured, cluster capacity equals local."""
    with mock.patch.object(fr, "_load_fleet_config", return_value={
        "fleet": {"max_workers": 8},
        "federation": {"peers": [], "routing_timeout": 5},
    }):
        result = fr.get_aggregated_capacity()
    assert "local" in result
    assert "peers" in result
    assert "cluster" in result
    assert result["cluster"]["total_agents"] >= 0


# ── 14. _get_local_capacity — returns expected keys ──────────────────────────

def test_get_local_capacity_structure(tmp_db):
    cap = fr._get_local_capacity({"fleet": {"max_workers": 10}})
    assert "max_workers" in cap
    assert "pending_tasks" in cap
    assert "active_agents" in cap
    assert "queue_ratio" in cap
    assert cap["max_workers"] == 10
    assert 0.0 <= cap["queue_ratio"] <= float("inf")
