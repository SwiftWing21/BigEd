# tests/test_bridge_api.py
"""Tests for the localhost bridge API."""
import json
import queue
import pytest


@pytest.fixture
def client():
    from factorio.bridge_api import create_api
    from factorio.world_model import WorldModel
    wm = WorldModel()
    cmd_q = queue.Queue()
    app = create_api(wm, cmd_q)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, wm, cmd_q


def test_status_endpoint(client):
    c, wm, q = client
    resp = c.get("/api/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "running" in data


def test_state_returns_snapshot(client):
    from factorio.state_parser import GameState, Entity
    c, wm, q = client
    wm.update(GameState(tick=500, inventory={"iron-plate": 42}))
    resp = c.get("/api/state")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["tick"] == 500
    assert data["inventory"]["iron-plate"] == 42


def test_command_queues_actions(client):
    c, wm, q = client
    resp = c.post("/api/command",
                  data=json.dumps({"actions": [{"action": "wait", "ticks": 60}]}),
                  content_type="application/json")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["queued"] is True
    assert not q.empty()


def test_command_rejects_missing_actions(client):
    c, wm, q = client
    resp = c.post("/api/command",
                  data=json.dumps({"foo": "bar"}),
                  content_type="application/json")
    assert resp.status_code == 400


def test_state_503_when_no_world_model():
    from factorio.bridge_api import create_api
    app = create_api(None, queue.Queue())
    app.config["TESTING"] = True
    with app.test_client() as c:
        resp = c.get("/api/state")
        assert resp.status_code == 503
