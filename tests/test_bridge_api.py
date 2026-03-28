# tests/test_bridge_api.py
"""Tests for the localhost bridge API."""
import json
import queue
import pytest


@pytest.fixture
def client():
    from factorio.bridge_api import create_api
    from factorio.world_model import WorldModel
    from factorio.bridge_config import BridgeConfig
    from factorio.agent_brain import AgentBrain

    wm = WorldModel()
    cmd_q = queue.Queue()
    cfg = BridgeConfig(current_phase=1)
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    app = create_api(wm, cmd_q, brain)
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


def test_plan_endpoint(client):
    c, wm, q = client
    resp = c.get("/api/plan")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "plan" in data
    assert "progress" in data


def test_pause_endpoint(client):
    c, wm, q = client
    resp = c.post("/api/pause")
    assert resp.status_code == 200
    assert json.loads(resp.data)["paused"] is True


def test_resume_endpoint(client):
    c, wm, q = client
    c.post("/api/pause")
    resp = c.post("/api/resume")
    assert json.loads(resp.data)["paused"] is False


def test_directive_crud(client):
    c, wm, q = client
    resp = c.post("/api/directive", data=json.dumps({"text": "focus power", "sticky": True}), content_type="application/json")
    did = json.loads(resp.data)["id"]
    resp = c.get("/api/directives")
    assert len(json.loads(resp.data)) == 1
    c.delete(f"/api/directive/{did}")
    assert len(json.loads(c.get("/api/directives").data)) == 0


def test_clear_directives_endpoint(client):
    c, wm, q = client
    c.post("/api/directive", data=json.dumps({"text": "one"}), content_type="application/json")
    c.post("/api/directive", data=json.dumps({"text": "two"}), content_type="application/json")
    resp = c.delete("/api/directives")
    assert json.loads(resp.data)["cleared"] == 2


def test_preset_crud(client):
    c, wm, q = client
    assert len(json.loads(c.get("/api/presets").data)) == 6
    resp = c.post("/api/preset", data=json.dumps({"label": "Custom", "text": "custom"}), content_type="application/json")
    pid = json.loads(resp.data)["id"]
    assert len(json.loads(c.get("/api/presets").data)) == 7
    c.delete(f"/api/preset/{pid}")
    assert len(json.loads(c.get("/api/presets").data)) == 6


def test_status_includes_paused(client):
    c, wm, q = client
    data = json.loads(c.get("/api/status").data)
    assert "paused" in data
    assert data["paused"] is False
