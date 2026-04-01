# Factorio Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Factorio bridge to a local Ollama LLM that autonomously reasons about game state, produces action plans, and advances through a 4-phase curriculum.

**Architecture:** Hybrid plan-and-drain loop — the LLM generates a multi-step plan (5-20 actions), the bridge drains it one action per tick, re-planning when exhausted or world events invalidate the plan. CurriculumManager tracks lessons within 4 training phases and auto-advances.

**Tech Stack:** Python 3.14, Ollama (qwen3:8b), urllib.request, asyncio, TOML (tomllib), pytest

**Spec:** `docs/superpowers/specs/2026-03-28-factorio-agent-loop-design.md`

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `fleet/factorio/curriculum_manager.py` | **Create** | Phase lifecycle, TOML loading, lesson tracking, criteria checking |
| `fleet/factorio/agent_brain.py` | **Create** | Plan-and-drain loop, Ollama HTTP calls, prompt assembly, state flattening |
| `fleet/factorio/curricula/phase1_bootstrap.toml` | **Create** | Phase 1 lessons: hand-craft, furnaces, smelting |
| `fleet/factorio/curricula/phase2_automate.toml` | **Create** | Phase 2 lessons: power, miners, belt-fed smelting |
| `fleet/factorio/curricula/phase3_science.toml` | **Create** | Phase 3 lessons: assemblers, red science, labs |
| `fleet/factorio/curricula/phase4_expand.toml` | **Create** | Phase 4 lessons: circuits, green science, scaling |
| `fleet/factorio/bridge_config.py` | **Modify** | Add ollama_url, ollama_model, ollama_timeout, plan_max_actions, plan_invalidation_failures, ollama_cooldown_secs |
| `fleet/factorio/bridge_api.py` | **Modify** | Add brain param to create_api(), add /api/plan endpoint |
| `fleet/factorio/bridge.py` | **Modify** | Instantiate AgentBrain, restructure tick step 5, asyncio.to_thread for brain calls |
| `tests/test_curriculum_manager.py` | **Create** | Tests for CurriculumManager |
| `tests/test_agent_brain.py` | **Create** | Tests for AgentBrain (mocked Ollama) |
| `tests/test_bridge_config.py` | **Modify** | Add tests for new config fields |
| `tests/test_bridge_api.py` | **Modify** | Update create_api() calls, test /api/plan |

---

### Task 1: Add Ollama Config Fields to BridgeConfig

**Files:**
- Modify: `fleet/factorio/bridge_config.py`
- Modify: `tests/test_bridge_config.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_bridge_config.py`, add:

```python
def test_ollama_config_defaults():
    from factorio.bridge_config import BridgeConfig
    cfg = BridgeConfig()
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.ollama_model == "qwen3:8b"
    assert cfg.ollama_timeout == 60
    assert cfg.plan_max_actions == 20
    assert cfg.plan_invalidation_failures == 3
    assert cfg.ollama_cooldown_secs == 30


def test_ollama_config_from_dict():
    from factorio.bridge_config import BridgeConfig
    raw = {
        "enabled": True,
        "ollama_model": "qwen3:4b",
        "ollama_timeout": 45,
        "plan_max_actions": 10,
    }
    cfg = BridgeConfig.from_dict(raw)
    assert cfg.ollama_model == "qwen3:4b"
    assert cfg.ollama_timeout == 45
    assert cfg.plan_max_actions == 10
    assert cfg.ollama_url == "http://localhost:11434"  # default kept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bridge_config.py::test_ollama_config_defaults tests/test_bridge_config.py::test_ollama_config_from_dict -v`
Expected: FAIL — `BridgeConfig` has no `ollama_url` attribute

- [ ] **Step 3: Add new fields to BridgeConfig**

In `fleet/factorio/bridge_config.py`, add these fields to the `BridgeConfig` dataclass after `ollama_cooldown_secs`:

```python
ollama_url: str = "http://localhost:11434"
ollama_model: str = "qwen3:8b"
ollama_timeout: int = 60
plan_max_actions: int = 20
plan_invalidation_failures: int = 3
ollama_cooldown_secs: int = 30
```

Add them after the existing `curriculum_dir` field (at the end of the dataclass).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bridge_config.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge_config.py tests/test_bridge_config.py
git commit -m "feat(factorio): add Ollama config fields to BridgeConfig"
```

---

### Task 2: Create CurriculumManager

**Files:**
- Create: `fleet/factorio/curriculum_manager.py`
- Create: `tests/test_curriculum_manager.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_curriculum_manager.py`:

```python
"""Tests for CurriculumManager — TOML loading, lesson tracking, phase advancement."""
import pytest
import os
import tempfile
from pathlib import Path


PHASE1_TOML = b"""
[meta]
phase = 1
name = "Bootstrap"
description = "Hand-craft basics"

[[lessons]]
name = "Craft gears"
description = "Craft 10 iron gear wheels"
criteria = "inventory.iron-gear-wheel >= 10"
hint = "craft iron-gear-wheel count=10"
max_attempts = 20

[[lessons]]
name = "Place furnaces"
description = "Place 3 stone furnaces"
criteria = "entities.stone-furnace >= 3"
hint = "place stone-furnace near ore"
max_attempts = 30
"""

PHASE2_TOML = b"""
[meta]
phase = 2
name = "Automate"
description = "Build power and automation"

[[lessons]]
name = "Build power"
description = "Place boiler + steam engine"
criteria = "entities.boiler >= 1 AND entities.steam-engine >= 1"
hint = "offshore-pump -> boiler -> steam-engine"
max_attempts = 30
"""


@pytest.fixture
def curricula_dir(tmp_path):
    \"\"\"Create a temp dir with phase TOML files.\"\"\"
    (tmp_path / "phase1_bootstrap.toml").write_bytes(PHASE1_TOML)
    (tmp_path / "phase2_automate.toml").write_bytes(PHASE2_TOML)
    return str(tmp_path)


def test_load_phase(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    obj = cm.get_current_objective()
    assert obj["phase"] == 1
    assert obj["lesson_name"] == "Craft gears"
    assert "iron-gear-wheel" in obj["criteria"]


def test_check_progress_lesson_not_passed(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    state = {"inventory": {"iron-gear-wheel": 5}, "entities": {}}
    result = cm.check_progress(state)
    assert result["lesson_passed"] is False
    assert result["phase_complete"] is False


def test_check_progress_lesson_passed(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    state = {"inventory": {"iron-gear-wheel": 15}, "entities": {}}
    result = cm.check_progress(state)
    assert result["lesson_passed"] is True
    assert result["phase_complete"] is False
    # Now on lesson 2
    obj = cm.get_current_objective()
    assert obj["lesson_name"] == "Place furnaces"


def test_check_progress_phase_complete(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    # Pass lesson 1
    cm.check_progress({"inventory": {"iron-gear-wheel": 15}, "entities": {}})
    # Pass lesson 2
    result = cm.check_progress({"inventory": {}, "entities": {"stone-furnace": 5}})
    assert result["lesson_passed"] is True
    assert result["phase_complete"] is True


def test_advance_phase(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    # Pass all phase 1 lessons
    cm.check_progress({"inventory": {"iron-gear-wheel": 15}, "entities": {}})
    cm.check_progress({"inventory": {}, "entities": {"stone-furnace": 5}})
    # Advance
    ok = cm.advance_phase()
    assert ok is True
    obj = cm.get_current_objective()
    assert obj["phase"] == 2
    assert obj["lesson_name"] == "Build power"


def test_advance_phase_at_max(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=2, curricula_dir=curricula_dir)
    # Pass phase 2 lessons
    cm.check_progress({"inventory": {}, "entities": {"boiler": 1, "steam-engine": 1}})
    # Try to advance — no phase 3 TOML
    ok = cm.advance_phase()
    assert ok is False


def test_get_progress(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    p = cm.get_progress()
    assert p["phase"] == 1
    assert p["total_lessons"] == 2
    assert p["completed"] == 0
    assert p["current_lesson"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_curriculum_manager.py -v`
Expected: FAIL — `No module named 'factorio.curriculum_manager'`

- [ ] **Step 3: Implement CurriculumManager**

Create `fleet/factorio/curriculum_manager.py`:

```python
"""Curriculum manager — phase lifecycle, TOML loading, lesson evaluation."""
import logging
from pathlib import Path

from factorio.curriculum import evaluate_criteria, LessonTracker

log = logging.getLogger("biged.factorio.curriculum_mgr")


class CurriculumManager:
    """Manages curriculum phases and lesson progression."""

    def __init__(self, current_phase: int = 1, curricula_dir: str = "fleet/factorio/curricula"):
        self._phase = current_phase
        self._curricula_dir = Path(curricula_dir)
        self._meta: dict = {}
        self._lessons: list[dict] = []
        self._tracker: LessonTracker | None = None
        self._completed_phases: list[int] = []
        self._load_phase(current_phase)

    def _load_phase(self, phase: int) -> bool:
        """Load a phase TOML by scanning for phase{N}_*.toml."""
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                log.warning("No TOML library available")
                return False

        pattern = f"phase{phase}_*.toml"
        matches = list(self._curricula_dir.glob(pattern))
        if not matches:
            log.warning("No curriculum found for phase %d in %s", phase, self._curricula_dir)
            return False

        path = matches[0]
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            log.warning("Failed to load %s", path, exc_info=True)
            return False

        self._meta = data.get("meta", {})
        self._lessons = data.get("lessons", [])
        self._tracker = LessonTracker(total_lessons=len(self._lessons))
        self._phase = phase
        log.info("Loaded phase %d: %s (%d lessons)", phase, self._meta.get("name", "?"), len(self._lessons))
        return True

    def get_current_objective(self) -> dict:
        """Return the current lesson objective for the LLM prompt."""
        if not self._tracker or not self._lessons:
            return {"phase": self._phase, "lesson_name": "No curriculum loaded",
                    "criteria": "", "description": "", "hint": ""}
        idx = self._tracker.current_index
        if idx >= len(self._lessons):
            return {"phase": self._phase, "lesson_name": "Phase complete",
                    "criteria": "", "description": "", "hint": ""}
        lesson = self._lessons[idx]
        return {
            "phase": self._phase,
            "phase_name": self._meta.get("name", f"Phase {self._phase}"),
            "lesson_name": lesson["name"],
            "criteria": lesson["criteria"],
            "description": lesson.get("description", ""),
            "hint": lesson.get("hint", ""),
        }

    def check_progress(self, state_dict: dict) -> dict:
        """Evaluate current lesson criteria against flattened state dict."""
        if not self._tracker or not self._lessons:
            return {"lesson_passed": False, "phase_complete": False, "progress": self.get_progress()}

        idx = self._tracker.current_index
        if idx >= len(self._lessons):
            return {"lesson_passed": False, "phase_complete": True, "progress": self.get_progress()}

        lesson = self._lessons[idx]
        self._tracker.mark_attempt(idx)

        if evaluate_criteria(lesson["criteria"], state_dict):
            self._tracker.mark_passed(idx)
            log.info("Lesson %d passed: %s", idx, lesson["name"])
            phase_complete = self._tracker.all_passed
            return {
                "lesson_passed": True,
                "lesson_name": lesson["name"],
                "phase_complete": phase_complete,
                "phase": self._phase,
                "progress": self.get_progress(),
            }

        return {"lesson_passed": False, "phase_complete": False, "progress": self.get_progress()}

    def advance_phase(self) -> bool:
        """Advance to next phase. Returns False if no next phase exists."""
        if self._phase not in self._completed_phases:
            self._completed_phases.append(self._phase)
        next_phase = self._phase + 1
        if self._load_phase(next_phase):
            log.info("Advanced to phase %d", next_phase)
            return True
        log.info("No phase %d found — curriculum complete", next_phase)
        return False

    def get_progress(self) -> dict:
        """Full progress snapshot for dashboard/logging."""
        tracker_progress = self._tracker.get_progress() if self._tracker else {}
        return {
            "phase": self._phase,
            "phase_name": self._meta.get("name", ""),
            "total_lessons": tracker_progress.get("total", 0),
            "completed": tracker_progress.get("completed", 0),
            "current_lesson": tracker_progress.get("current", 0),
            "attempts": tracker_progress.get("attempts", []),
            "completed_phases": list(self._completed_phases),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_curriculum_manager.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/curriculum_manager.py tests/test_curriculum_manager.py
git commit -m "feat(factorio): add CurriculumManager with phase lifecycle"
```

---

### Task 3: Create Curriculum TOML Files

**Files:**
- Create: `fleet/factorio/curricula/phase1_bootstrap.toml`
- Create: `fleet/factorio/curricula/phase2_automate.toml`
- Create: `fleet/factorio/curricula/phase3_science.toml`
- Create: `fleet/factorio/curricula/phase4_expand.toml`

- [ ] **Step 1: Create `fleet/factorio/curricula/` directory and phase1**

```toml
# fleet/factorio/curricula/phase1_bootstrap.toml
[meta]
phase = 1
name = "Bootstrap"
description = "Hand-craft basics, place first furnaces, establish smelting"

[[lessons]]
name = "Craft iron gear wheels"
description = "Craft 10 iron gear wheels from starting inventory"
criteria = "inventory.iron-gear-wheel >= 10"
hint = "Use craft action: recipe=iron-gear-wheel, count=10. Requires 20 iron plates."
max_attempts = 20

[[lessons]]
name = "Place stone furnaces"
description = "Place at least 3 stone furnaces"
criteria = "entities.stone-furnace >= 3"
hint = "Place stone-furnace at integer positions near iron ore patches. Craft more if needed."
max_attempts = 30

[[lessons]]
name = "Smelt iron plates"
description = "Accumulate 50 iron plates in inventory"
criteria = "inventory.iron-plate >= 50"
hint = "Furnaces smelt ore into plates. Feed iron-ore into furnaces manually or wait for output."
max_attempts = 50
```

- [ ] **Step 2: Create phase2**

```toml
# fleet/factorio/curricula/phase2_automate.toml
[meta]
phase = 2
name = "Automate Smelting"
description = "Build power chain, electric miners, belt-fed smelting line"

[[lessons]]
name = "Build power chain"
description = "Place offshore-pump, boiler, and steam-engine"
criteria = "entities.offshore-pump >= 1 AND entities.boiler >= 1 AND entities.steam-engine >= 1"
hint = "offshore-pump produces water -> pipe to boiler (needs coal) -> steam-engine generates power. Connect with small-electric-pole."
max_attempts = 40

[[lessons]]
name = "Place electric mining drills"
description = "Place at least 2 electric mining drills on ore"
criteria = "entities.electric-mining-drill >= 2"
hint = "Place electric-mining-drill on top of iron-ore or copper-ore patches. Needs power."
max_attempts = 30

[[lessons]]
name = "Build belt infrastructure"
description = "Place at least 10 transport belts"
criteria = "entities.transport-belt >= 10"
hint = "Use connect action to lay belts from miners to furnaces. Belt direction matters."
max_attempts = 40

[[lessons]]
name = "Automate with inserters"
description = "Place at least 4 inserters for loading/unloading"
criteria = "entities.inserter >= 4"
hint = "Inserters pick from BEHIND, drop in FRONT. Place between belt and furnace."
max_attempts = 40
```

- [ ] **Step 3: Create phase3**

```toml
# fleet/factorio/curricula/phase3_science.toml
[meta]
phase = 3
name = "First Science"
description = "Build assemblers, produce red science packs, start research"

[[lessons]]
name = "Place assembling machines"
description = "Place at least 2 assembling-machine-1"
criteria = "entities.assembling-machine-1 >= 2"
hint = "assembling-machine-1 is 3x3. Place with room for inserters. Set recipe after placing."
max_attempts = 30

[[lessons]]
name = "Produce iron gear wheels automatically"
description = "Accumulate 20 iron gear wheels via assembler production"
criteria = "inventory.iron-gear-wheel >= 20"
hint = "Set recipe=iron-gear-wheel on an assembler, feed iron plates via inserter. Wait for output."
max_attempts = 40

[[lessons]]
name = "Produce automation science packs"
description = "Accumulate 10 automation science packs"
criteria = "inventory.automation-science-pack >= 10"
hint = "automation-science-pack needs iron-gear-wheel + copper-plate. Set recipe on an assembler, feed ingredients via inserters."
max_attempts = 60

[[lessons]]
name = "Start research"
description = "Place a lab and begin researching automation"
criteria = "entities.lab >= 1"
hint = "Place a lab, feed science packs via inserter, then research technology=automation."
max_attempts = 40
```

- [ ] **Step 4: Create phase4**

```toml
# fleet/factorio/curricula/phase4_expand.toml
[meta]
phase = 4
name = "Expand"
description = "Electronic circuits, green science, scale production"

[[lessons]]
name = "Produce electronic circuits"
description = "Accumulate 20 electronic circuits"
criteria = "inventory.electronic-circuit >= 20"
hint = "electronic-circuit needs copper-wire (from copper-plate) + iron-plate. Set up copper-wire assembler first."
max_attempts = 60

[[lessons]]
name = "Scale smelting"
description = "Have at least 6 furnaces operational"
criteria = "entities.stone-furnace >= 6"
hint = "More furnaces = more throughput. Place in lines with belt/inserter automation."
max_attempts = 40

[[lessons]]
name = "Produce logistic science packs"
description = "Accumulate 10 logistic science packs"
criteria = "inventory.logistic-science-pack >= 10"
hint = "logistic-science-pack needs inserter + transport-belt. Set recipe on an assembler."
max_attempts = 80

[[lessons]]
name = "Research logistic automation"
description = "Have at least 5 completed research technologies"
criteria = "research.progress >= 0"
hint = "Keep labs fed with both red and green science. Queue up research."
max_attempts = 100
```

- [ ] **Step 5: Verify CurriculumManager loads the real TOMLs**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); from factorio.curriculum_manager import CurriculumManager; cm = CurriculumManager(1, 'fleet/factorio/curricula'); print(cm.get_current_objective())"`
Expected: prints dict with phase=1, lesson_name="Craft iron gear wheels"

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/curricula/
git commit -m "feat(factorio): add 4-phase curriculum TOML files"
```

---

### Task 4: Create AgentBrain — flatten_state and prompt building

**Files:**
- Create: `fleet/factorio/agent_brain.py`
- Create: `tests/test_agent_brain.py`

- [ ] **Step 1: Write failing tests for flatten_state and prompt building**

Create `tests/test_agent_brain.py`:

```python
"""Tests for AgentBrain — state flattening, prompt building, plan management."""
import pytest


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
```

- [ ] **Step 2: Create test fixture curricula**

Create `tests/fixtures/curricula/phase1_test.toml`:

```toml
[meta]
phase = 1
name = "Test Bootstrap"
description = "Test phase"

[[lessons]]
name = "Test lesson"
description = "Test description"
criteria = "inventory.iron-plate >= 10"
hint = "Test hint"
max_attempts = 5
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: FAIL — `No module named 'factorio.agent_brain'`

- [ ] **Step 4: Implement flatten_state and prompt building**

Create `fleet/factorio/agent_brain.py`:

```python
"""Agent brain — plan-and-drain loop with Ollama LLM reasoning."""
import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path

from factorio.bridge_config import BridgeConfig
from factorio.world_model import WorldModel, GameEvent
from factorio.state_parser import GameState, state_to_markdown
from factorio.action_translator import translate_action, TranslatedAction, KNOWN_ACTIONS
from factorio.curriculum_manager import CurriculumManager

log = logging.getLogger("biged.factorio.brain")

INVALIDATION_EVENTS = {"entity_destroyed", "power_outage", "resource_depleted", "research_complete"}

SYSTEM_PROMPT = """You are a Factorio automation agent controlling a factory through commands.
Respond with ONLY a valid JSON array of action objects. No markdown, no explanation, no text.

Available actions:
- {"action": "place", "entity": "<name>", "position": {"x": N, "y": N}, "direction": "north|east|south|west"}
- {"action": "craft", "recipe": "<name>", "count": N}
- {"action": "research", "technology": "<name>"}
- {"action": "move", "position": {"x": N, "y": N}}
- {"action": "set_recipe", "unit_number": N, "recipe": "<name>"}
- {"action": "connect", "entity": "transport-belt", "from": {"x": N, "y": N}, "to": {"x": N, "y": N}}
- {"action": "remove", "unit_number": N}
- {"action": "wait", "ticks": N}

Decision priority:
1. Fix bottlenecks (idle assemblers, full outputs)
2. Maintain power (build power if none or low)
3. Advance toward current objective
4. Optimize layout

Rules:
- Inserters pick from BEHIND, drop in FRONT (direction matters!)
- Always set_recipe on assemblers after placing
- Check inventory before placing — you can't place what you don't have
- Keep builds compact to minimize belt length
- Electric miners/assemblers need power to work"""


def flatten_state(state: GameState) -> dict:
    """Convert GameState to flat dict for curriculum criteria evaluation."""
    entity_counts: dict[str, int] = {}
    for e in state.entities:
        entity_counts[e.name] = entity_counts.get(e.name, 0) + 1
    return {
        "inventory": dict(state.inventory),
        "entities": entity_counts,
        "research": {"name": state.research_name, "progress": state.research_progress},
    }


class AgentBrain:
    """Plan-and-drain reasoning loop powered by local Ollama."""

    def __init__(self, config: BridgeConfig, world_model: WorldModel,
                 curricula_dir: str | None = None):
        self.config = config
        self.world_model = world_model
        self.curriculum = CurriculumManager(
            current_phase=config.current_phase,
            curricula_dir=curricula_dir or "fleet/factorio/curricula",
        )
        self._plan: list[dict] = []
        self._plan_index: int = 0
        self._consecutive_failures: int = 0
        self._idle_assembler_count: int = 0
        self._last_results: list[dict] = []
        self._ollama_cooldown_until: float = 0.0
        self._plan_count: int = 0

    def _build_prompt(self, state: GameState) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for Ollama."""
        objective = self.curriculum.get_current_objective()
        state_md = state_to_markdown(state)

        lines = [
            "# Current Factory State",
            state_md,
            "",
            "# Current Objective",
            f"Phase {objective.get('phase', '?')}: {objective.get('phase_name', '')}",
            f"Lesson: {objective.get('lesson_name', '?')} — {objective.get('description', '')}",
            f"Success criteria: {objective.get('criteria', '?')}",
            f"Hint: {objective.get('hint', '')}",
            "",
            "# Previous Plan Results",
        ]

        if self._last_results:
            for r in self._last_results:
                status = "OK" if r.get("success") else "FAIL"
                desc = r.get("description", r.get("action", "?"))
                err = f" — {r.get('error', '')}" if r.get("error") else ""
                lines.append(f"- [{status}] {desc}{err}")
        else:
            lines.append("First plan — no previous results.")

        lines.append("")
        lines.append("Generate 5-20 actions to work toward the objective.")

        return SYSTEM_PROMPT, "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py tests/fixtures/curricula/
git commit -m "feat(factorio): add AgentBrain with flatten_state and prompt building"
```

---

### Task 5: AgentBrain — Ollama HTTP + response parsing

**Files:**
- Modify: `fleet/factorio/agent_brain.py`
- Modify: `tests/test_agent_brain.py`

- [ ] **Step 1: Write failing tests for plan generation**

Add to `tests/test_agent_brain.py`:

```python
import json
from unittest.mock import patch, MagicMock


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
    import urllib.error

    cfg = BridgeConfig(current_phase=1, ollama_cooldown_secs=10)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
        plan = brain._generate_plan(GameState(tick=10))

    assert plan == []
    assert brain._ollama_cooldown_until > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_brain.py::test_generate_plan_parses_json -v`
Expected: FAIL — `AgentBrain has no attribute '_generate_plan'`

- [ ] **Step 3: Implement _generate_plan and _parse_response**

Add to `fleet/factorio/agent_brain.py` inside the `AgentBrain` class:

```python
    def _generate_plan(self, state: GameState) -> list[dict]:
        """Call Ollama to generate an action plan."""
        if time.monotonic() < self._ollama_cooldown_until:
            log.info("Ollama in cooldown, skipping plan generation")
            return []

        system_prompt, user_prompt = self._build_prompt(state)

        body = json.dumps({
            "model": self.config.ollama_model,
            "prompt": user_prompt,
            "system": system_prompt,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.config.ollama_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.config.ollama_timeout) as resp:
                    data = json.loads(resp.read())
                raw_text = data.get("response", "")
                actions = self._parse_response(raw_text)
                if actions:
                    self._plan_count += 1
                    log.info("Plan #%d generated: %d actions", self._plan_count, len(actions))
                    return actions
                if attempt == 0:
                    log.warning("Parse failed, retrying with shorter prompt")
                    user_prompt = "Respond with ONLY a JSON array of Factorio actions."
                    body = json.dumps({
                        "model": self.config.ollama_model,
                        "prompt": user_prompt,
                        "system": system_prompt,
                        "stream": False,
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        f"{self.config.ollama_url}/api/generate",
                        data=body,
                        headers={"Content-Type": "application/json"},
                    )
            except (ConnectionRefusedError, urllib.error.URLError, OSError) as e:
                log.warning("Ollama connection failed: %s", e)
                self._ollama_cooldown_until = time.monotonic() + self.config.ollama_cooldown_secs
                return []
            except Exception as e:
                log.warning("Ollama call failed: %s", e)
                return []

        return []

    def _parse_response(self, text: str) -> list[dict]:
        """Parse JSON action array from LLM response text."""
        text = text.strip()
        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Failed to parse LLM response as JSON")
            return []

        if not isinstance(parsed, list):
            log.warning("LLM response is not a list")
            return []

        # Filter to known actions and cap
        valid = [a for a in parsed if isinstance(a, dict) and a.get("action") in KNOWN_ACTIONS]
        return valid[:self.config.plan_max_actions]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py
git commit -m "feat(factorio): add Ollama HTTP calls and response parsing to AgentBrain"
```

---

### Task 6: AgentBrain — next_action, report_result, plan invalidation

**Files:**
- Modify: `fleet/factorio/agent_brain.py`
- Modify: `tests/test_agent_brain.py`

- [ ] **Step 1: Write failing tests for next_action and plan management**

Add to `tests/test_agent_brain.py`:

```python
def test_next_action_returns_translated_action():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    # Pre-load a plan
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
    # Plan should be cleared, next_action tries to generate new plan
    # With no Ollama running, returns None
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
    # 2 failures — plan stays
    brain.report_result(action, {"success": False, "error": "cannot place"})
    brain.report_result(action, {"success": False, "error": "cannot place"})
    assert brain._plan != []
    # 3rd failure — plan invalidated
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
    brain.report_result(action, {"success": True})  # resets counter
    assert brain._consecutive_failures == 0
    assert brain._plan != []  # plan still valid


def test_check_progress_delegates_to_curriculum():
    from factorio.state_parser import GameState, Entity
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    # Test fixture lesson: inventory.iron-plate >= 10
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_brain.py::test_next_action_returns_translated_action -v`
Expected: FAIL — `AgentBrain has no attribute 'next_action'`

- [ ] **Step 3: Implement next_action, report_result, check_progress, get_plan_status**

Add to `AgentBrain` class in `fleet/factorio/agent_brain.py`:

```python
    def next_action(self, state: GameState, events: list[GameEvent]) -> TranslatedAction | None:
        """Get the next action to execute. May call Ollama if plan is empty."""
        # Check for invalidation events
        has_idle = False
        for event in events:
            if event.event_type in INVALIDATION_EVENTS:
                log.info("Plan invalidated by event: %s", event.event_type)
                self._plan = []
                self._plan_index = 0
                break
            if event.event_type == "idle_assemblers":
                has_idle = True

        # Soft re-plan: idle_assemblers counter tracked across ticks, not per-event
        if has_idle:
            self._idle_assembler_count += 1
            if self._idle_assembler_count >= 3:
                log.info("Soft re-plan: %d consecutive idle_assemblers ticks", self._idle_assembler_count)
                self._plan = []
                self._plan_index = 0
                self._idle_assembler_count = 0
        else:
            self._idle_assembler_count = 0

        # Drain current plan
        if self._plan_index < len(self._plan):
            raw = self._plan[self._plan_index]
            self._plan_index += 1
            return translate_action(raw)

        # Plan exhausted — generate new one
        self._plan = self._generate_plan(state)
        self._plan_index = 0
        self._last_results = []

        if not self._plan:
            return None

        raw = self._plan[self._plan_index]
        self._plan_index += 1
        return translate_action(raw)

    def report_result(self, action: TranslatedAction, result: dict) -> None:
        """Track action result. Invalidate plan on consecutive failures."""
        result_record = {
            "action": action.action_type,
            "description": action.description,
            "success": result.get("success", False),
        }
        if result.get("error"):
            result_record["error"] = result["error"]
        self._last_results.append(result_record)

        if result.get("success"):
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.plan_invalidation_failures:
                log.warning("Plan invalidated: %d consecutive failures", self._consecutive_failures)
                self._plan = []
                self._plan_index = 0
                self._consecutive_failures = 0

    def check_progress(self, state: GameState) -> dict:
        """Check curriculum progress against current game state."""
        flat = flatten_state(state)
        return self.curriculum.check_progress(flat)

    def get_plan_status(self) -> dict:
        """Return current plan state for the API."""
        return {
            "plan": list(self._plan),
            "plan_index": self._plan_index,
            "plan_count": self._plan_count,
            "planning": False,  # True only during _generate_plan (sync, so always False when called)
            "consecutive_failures": self._consecutive_failures,
        }
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: All 17 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py
git commit -m "feat(factorio): add next_action, report_result, plan invalidation to AgentBrain"
```

---

### Task 7: Update bridge_api.py — add brain param and /api/plan

**Files:**
- Modify: `fleet/factorio/bridge_api.py`
- Modify: `tests/test_bridge_api.py`

- [ ] **Step 1: Write failing test for /api/plan**

Add to `tests/test_bridge_api.py`:

```python
def test_plan_endpoint(client):
    c, wm, q = client
    resp = c.get("/api/plan")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "plan" in data
    assert "progress" in data
```

Update the `client` fixture to pass a brain:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bridge_api.py::test_plan_endpoint -v`
Expected: FAIL — `create_api() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Update create_api and add /api/plan**

In `fleet/factorio/bridge_api.py`, change the `create_api` signature and add the endpoint:

```python
def create_api(world_model, command_queue, brain=None) -> Flask:
    global _world_model, _command_queue, _brain
    _world_model = world_model
    _command_queue = command_queue
    _brain = brain
```

Add at module level:
```python
_brain = None
```

Add the endpoint inside `create_api`:
```python
    @app.route("/api/plan")
    def api_plan():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        plan_status = _brain.get_plan_status()
        progress = _brain.curriculum.get_progress()
        return jsonify({**plan_status, "progress": progress})
```

- [ ] **Step 4: Run all bridge_api tests to verify they pass**

Run: `python -m pytest tests/test_bridge_api.py -v`
Expected: All 6 tests PASS (5 existing + 1 new)

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge_api.py tests/test_bridge_api.py
git commit -m "feat(factorio): add /api/plan endpoint and brain param to bridge API"
```

---

### Task 8: Integrate AgentBrain into bridge.py

**Files:**
- Modify: `fleet/factorio/bridge.py`

- [ ] **Step 1: Add import and brain instantiation**

In `fleet/factorio/bridge.py`, add import at top:

```python
from factorio.agent_brain import AgentBrain
```

In `FactorioBridge.__init__`, after `self.command_queue`:

```python
        self.brain = AgentBrain(config, self.world_model)
```

- [ ] **Step 2: Restructure tick() step 5 — human commands first, then brain**

Replace the existing step 5 block (lines ~111-139, the `while not self.command_queue.empty()` section) with:

```python
        # 5a. Drain human command queue first (priority)
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                actions = cmd.get("actions", [])
                translated = translate_batch(actions)
                results = []
                for ta in translated:
                    if ta.action_type == "wait":
                        ticks = 60
                        await asyncio.sleep(ticks / 60.0)
                        results.append({"action": "wait", "success": True})
                        continue
                    if not ta.rcon_command:
                        continue
                    try:
                        cmd_json = ta.rcon_command.split(" ", 1)[1] if " " in ta.rcon_command else "{}"
                        resp = await self.rcon.remote_call("exec_cmd", cmd_json)
                        result = json.loads(resp)
                    except json.JSONDecodeError:
                        result = {"raw": resp}
                    except Exception as e:
                        result = {"error": str(e), "success": False}
                    result["description"] = ta.description
                    results.append(result)
                store_result(cmd["id"], {"results": results})
            except queue.Empty:
                break

        # 5b. Ask brain for next autonomous action
        if self.command_queue.empty():
            action = await asyncio.to_thread(self.brain.next_action, state, events)
            if action and action.rcon_command:
                try:
                    cmd_json = action.rcon_command.split(" ", 1)[1] if " " in action.rcon_command else "{}"
                    resp = await self.rcon.remote_call("exec_cmd", cmd_json)
                    try:
                        result = json.loads(resp)
                    except json.JSONDecodeError:
                        result = {"raw": resp}
                except Exception as e:
                    result = {"error": str(e), "success": False}
                self.brain.report_result(action, result)
                log.info("Brain action: %s — %s",
                         action.description,
                         "OK" if result.get("success") else result.get("error", "unknown"))

        # 5c. Check curriculum progress
        progress = self.brain.check_progress(state)
        if progress.get("lesson_passed"):
            log.info("Lesson passed: %s", progress.get("lesson_name"))
        if progress.get("phase_complete"):
            log.info("Phase %d complete!", progress.get("phase"))
            if self.config.auto_advance:
                self.brain.curriculum.advance_phase()
```

- [ ] **Step 3: Update create_api call to pass brain**

In the `main()` function, change:

```python
    api_app = create_api(bridge.world_model, bridge.command_queue)
```

to:

```python
    api_app = create_api(bridge.world_model, bridge.command_queue, bridge.brain)
```

- [ ] **Step 4: Verify bridge imports and syntax**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); from factorio.bridge import FactorioBridge; print('OK')"`
Expected: prints `OK`

- [ ] **Step 5: Run all Factorio tests**

Run: `python -m pytest tests/test_bridge_config.py tests/test_bridge_api.py tests/test_agent_brain.py tests/test_curriculum_manager.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "feat(factorio): integrate AgentBrain into bridge tick loop"
```

---

### Task 9: End-to-End Smoke Test

**Files:**
- No new files — integration verification

- [ ] **Step 1: Verify all Factorio tests pass together**

Run: `python -m pytest tests/test_bridge_config.py tests/test_bridge_api.py tests/test_agent_brain.py tests/test_curriculum_manager.py tests/test_curriculum.py tests/test_action_translator.py tests/test_state_parser.py tests/test_world_model.py tests/test_cadence.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 2: Verify bridge loads with brain**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); from factorio.bridge import FactorioBridge; from factorio.bridge_config import BridgeConfig; b = FactorioBridge(BridgeConfig(current_phase=1)); print(f'Brain loaded, phase={b.brain.curriculum.get_progress()[\"phase\"]}, lessons={b.brain.curriculum.get_progress()[\"total_lessons\"]}')"`
Expected: prints `Brain loaded, phase=1, lessons=3`

- [ ] **Step 3: Verify bridge API serves /api/plan**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); from factorio.bridge_api import create_api; from factorio.world_model import WorldModel; from factorio.bridge_config import BridgeConfig; from factorio.agent_brain import AgentBrain; wm=WorldModel(); brain=AgentBrain(BridgeConfig(current_phase=1),wm); app=create_api(wm,None,brain); c=app.test_client(); r=c.get('/api/plan'); import json; d=json.loads(r.data); print(f'plan_count={d[\"plan_count\"]}, phase={d[\"progress\"][\"phase\"]}')"`
Expected: prints `plan_count=0, phase=1`

- [ ] **Step 4: Final commit with all files**

Run: `git status` — verify no unstaged changes related to this feature.

If any stragglers:
```bash
git add -A fleet/factorio/ tests/
git commit -m "chore(factorio): agent loop integration complete — all tests passing"
```
