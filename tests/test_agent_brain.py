"""Tests for AgentBrain — state flattening, prompt building, plan management."""
import json
import pytest
from unittest.mock import patch, MagicMock


# --- Task 4: flatten_state and prompt building ---

def test_flatten_state():
    from factorio.state_parser import GameState, Entity
    from factorio.agent_brain import flatten_state

    state = GameState(
        tick=100,
        inventory={"iron-plate": 42, "coal": 10},
        entities=[
            Entity(name="stone-furnace", type="furnace"),
            Entity(name="stone-furnace", type="furnace"),
            Entity(name="inserter", type="inserter"),
        ],
        research_name="automation",
        research_progress=0.5,
    )
    flat = flatten_state(state)
    assert flat["inventory"]["iron-plate"] == 42
    assert flat["entities"]["stone-furnace"] == 2
    assert flat["entities"]["inserter"] == 1
    assert flat["research"]["name"] == "automation"
    assert flat["research"]["progress"] == 0.5


def test_flatten_state_empty():
    from factorio.state_parser import GameState
    from factorio.agent_brain import flatten_state

    state = GameState()
    flat = flatten_state(state)
    assert flat["inventory"] == {}
    assert flat["entities"] == {}


def test_build_prompt_includes_objective():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    state = GameState(tick=50, inventory={"iron-plate": 10})
    system, user = brain._build_prompt(state)
    assert "JSON array" in system
    assert "action" in system
    assert "Current Objective" in user or "Objective" in user


def test_build_prompt_includes_previous_results():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._last_results = [
        {"action": "place", "success": True, "description": "Place stone-furnace"},
        {"action": "craft", "success": False, "error": "missing item"},
    ]
    state = GameState(tick=100)
    _, user = brain._build_prompt(state)
    assert "Previous Plan" in user or "previous" in user.lower()


# --- Task 5: Ollama HTTP + response parsing ---

def _mock_ollama_response(actions_json: str):
    """Create a mock urllib response returning an Ollama-formatted response."""
    body = json.dumps({"response": actions_json}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_generate_plan_parses_json():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    actions = '[{"action": "craft", "recipe": "iron-gear-wheel", "count": 5}]'
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response(actions)):
        plan = brain._generate_plan(GameState(tick=10))

    assert len(plan) == 1
    assert plan[0]["action"] == "craft"


def test_generate_plan_strips_markdown_fences():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    actions = '```json\n[{"action": "wait", "ticks": 60}]\n```'
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response(actions)):
        plan = brain._generate_plan(GameState(tick=10))

    assert len(plan) == 1
    assert plan[0]["action"] == "wait"


def test_generate_plan_caps_at_max_actions():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1, plan_max_actions=3)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    actions = json.dumps([{"action": "wait", "ticks": 60}] * 10)
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response(actions)):
        plan = brain._generate_plan(GameState(tick=10))

    assert len(plan) == 3


def test_generate_plan_filters_invalid_actions():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    actions = '[{"action": "craft", "recipe": "gear", "count": 1}, {"action": "dance"}, {"action": "place", "entity": "furnace", "position": {"x":0,"y":0}}]'
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response(actions)):
        plan = brain._generate_plan(GameState(tick=10))

    assert len(plan) == 2
    assert plan[0]["action"] == "craft"
    assert plan[1]["action"] == "place"


def test_generate_plan_cooldown_on_connection_error():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1, ollama_cooldown_secs=10)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
        plan = brain._generate_plan(GameState(tick=10))

    assert plan == []
    assert brain._ollama_cooldown_until > 0


# --- Task 6: next_action, report_result, plan invalidation ---

def test_next_action_returns_translated_action():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "craft", "recipe": "iron-gear-wheel", "count": 5}]
    brain._plan_index = 0

    action = brain.next_action(GameState(tick=10), [])
    assert action is not None
    assert action.action_type == "craft"
    assert brain._plan_index == 1


def test_next_action_drains_plan():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [
        {"action": "craft", "recipe": "gear", "count": 1},
        {"action": "wait", "ticks": 30},
    ]
    brain._plan_index = 0

    a1 = brain.next_action(GameState(tick=10), [])
    assert a1.action_type == "craft"
    a2 = brain.next_action(GameState(tick=11), [])
    assert a2.action_type == "wait"
    assert brain._plan_index == 2


def test_next_action_invalidates_on_entity_destroyed():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel, GameEvent
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "wait", "ticks": 60}]
    brain._plan_index = 0

    events = [GameEvent(event_type="entity_destroyed", tick=10)]
    brain._ollama_cooldown_until = float("inf")  # force cooldown
    action = brain.next_action(GameState(tick=10), events)
    assert action is None
    assert brain._plan == []


def test_report_result_tracks_failures():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.action_translator import TranslatedAction
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1, plan_invalidation_failures=3)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "wait", "ticks": 60}] * 5
    brain._plan_index = 0

    action = TranslatedAction("place", "/biged-cmd {}", "Place test")
    brain.report_result(action, {"success": False, "error": "cannot place"})
    brain.report_result(action, {"success": False, "error": "cannot place"})
    assert brain._plan != []
    brain.report_result(action, {"success": False, "error": "cannot place"})
    assert brain._plan == []


def test_report_result_resets_on_success():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.action_translator import TranslatedAction
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1, plan_invalidation_failures=3)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "wait", "ticks": 60}] * 5

    action = TranslatedAction("craft", None, "Craft test")
    brain.report_result(action, {"success": False})
    brain.report_result(action, {"success": False})
    brain.report_result(action, {"success": True})
    assert brain._consecutive_failures == 0
    assert brain._plan != []


def test_check_progress_delegates_to_curriculum():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    state = GameState(tick=50, inventory={"iron-plate": 15})
    result = brain.check_progress(state)
    assert result["lesson_passed"] is True


def test_get_plan_status():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "craft"}, {"action": "place"}]
    brain._plan_index = 1

    status = brain.get_plan_status()
    assert len(status["plan"]) == 2
    assert status["plan_index"] == 1
    assert status["planning"] is False
