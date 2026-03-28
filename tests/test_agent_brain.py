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


# --- Task 1: pause/resume ---

def test_pause_stops_actions():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "craft", "recipe": "gear", "count": 1}]
    brain._plan_index = 0

    brain.pause()
    assert brain.is_paused is True
    assert brain._plan == []
    action = brain.next_action(GameState(tick=10), [])
    assert action is None


def test_resume_allows_actions():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.pause()
    brain.resume()
    assert brain.is_paused is False

    brain._plan = [{"action": "wait", "ticks": 10}]
    brain._plan_index = 0
    action = brain.next_action(GameState(tick=10), [])
    assert action is not None
    assert action.action_type == "wait"


def test_pause_still_processes_events():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel, GameEvent
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain._plan = [{"action": "wait", "ticks": 60}]
    brain._plan_index = 0

    brain.pause()
    events = [GameEvent(event_type="entity_destroyed", tick=10)]
    brain._ollama_cooldown_until = float("inf")
    action = brain.next_action(GameState(tick=10), events)
    # Plan should be cleared by the invalidation event, action is None (paused)
    assert action is None
    assert brain._plan == []


def test_brain_has_lock():
    import threading
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    assert isinstance(brain._lock, type(threading.Lock()))


# --- Task 2: directive system ---

def test_add_directive():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    directive_id = brain.add_directive("Build more miners", sticky=True)
    assert len(directive_id) == 8
    directives = brain.get_directives()
    assert len(directives) == 1
    assert directives[0]["text"] == "Build more miners"
    assert directives[0]["sticky"] is True
    assert directives[0]["id"] == directive_id


def test_add_directive_non_sticky():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("Temporary instruction", sticky=False, plans=3)
    directives = brain.get_directives()
    assert directives[0]["plans_remaining"] == 3


def test_remove_directive():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    directive_id = brain.add_directive("Remove me", sticky=False)
    removed = brain.remove_directive(directive_id)
    assert removed is True
    assert brain.get_directives() == []


def test_clear_directives():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("First", sticky=False)
    brain.add_directive("Second", sticky=True)
    count = brain.clear_directives()
    assert count == 2
    assert brain.get_directives() == []


def test_directive_in_prompt():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("Prioritize iron mining", sticky=True)
    _, user = brain._build_prompt(GameState(tick=10))
    assert "Human Directives" in user
    assert "Prioritize iron mining" in user


def test_directive_expiry():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("Sticky forever", sticky=True)
    brain.add_directive("Expire soon", sticky=False, plans=1)

    actions = '[{"action": "wait", "ticks": 10}]'
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response(actions)):
        brain._generate_plan(GameState(tick=10))

    directives = brain.get_directives()
    assert len(directives) == 1
    assert directives[0]["text"] == "Sticky forever"


# --- Task 3: presets + get_plan_status update ---

def test_default_presets():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    presets = brain.get_presets()
    assert len(presets) == 6
    labels = [p["label"] for p in presets]
    assert "Focus Power" in labels


def test_add_remove_preset():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    preset_id = brain.add_preset("Custom", "Do custom stuff", sticky=False, plans=2)
    assert len(brain.get_presets()) == 7

    removed = brain.remove_preset(preset_id)
    assert removed is True
    assert len(brain.get_presets()) == 6


def test_plan_status_includes_paused_and_directives():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.pause()
    brain.add_directive("Focus on power", sticky=True)

    status = brain.get_plan_status()
    assert status["paused"] is True
    assert len(status["directives"]) == 1
    assert status["directives"][0]["text"] == "Focus on power"


# --- Task: Experiment support (counters, template prompts, Ollama options) ---

def test_brain_cumulative_counters():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.action_translator import TranslatedAction
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    action = TranslatedAction("craft", "/biged-cmd {}", "Craft test")
    brain.report_result(action, {"success": True})
    brain.report_result(action, {"success": True})
    brain.report_result(action, {"success": False, "error": "fail"})

    assert brain.total_actions == 3
    assert brain.total_successes == 2
    assert brain.total_failures == 1


def test_brain_reset_counters():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.action_translator import TranslatedAction
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    action = TranslatedAction("craft", "/biged-cmd {}", "Craft test")
    brain.report_result(action, {"success": True})
    brain.reset_counters()
    assert brain.total_actions == 0
    assert brain.total_successes == 0
    assert brain.total_failures == 0


def test_brain_uses_prompt_template(tmp_path):
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.state_parser import GameState
    from factorio.agent_brain import AgentBrain

    # Create a custom prompt template
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "custom.toml").write_text('''
[meta]
name = "Custom"
description = "Test"

[templates]
system_template = "CUSTOM SYSTEM"
user_template = """State: {state}
Objective: {objective}
Results: {previous_results}"""
''', encoding="utf-8")

    cfg = BridgeConfig(current_phase=1, prompt_template="custom")
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula",
                       prompts_dir=str(prompts_dir))
    state = GameState(tick=10)
    system, user = brain._build_prompt(state)
    assert system == "CUSTOM SYSTEM"
    assert "State:" in user


def test_generate_plan_passes_temperature():
    import json
    from unittest.mock import patch, MagicMock
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1, temperature=0.3, top_p=0.8)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    actions = '[{"action": "wait", "ticks": 60}]'
    captured_body = {}

    def capture_urlopen(req, timeout=None):
        captured_body["data"] = json.loads(req.data)
        body = json.dumps({"response": actions}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=capture_urlopen):
        brain._generate_plan(GameState(tick=10))

    assert captured_body["data"]["options"]["temperature"] == 0.3
    assert captured_body["data"]["options"]["top_p"] == 0.8
