# Factorio Autoresearch Experiment Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autoresearch-style experiment loop that optimizes Factorio AgentBrain prompts and parameters via fixed-budget runs with keep/discard logic.

**Architecture:** Independent experiment runner wraps existing `FactorioBridge`, injects candidate configs (prompt template + brain params), runs 10-min budget windows, scores with phase-gated metrics, and logs replay data for future specialist model training.

**Tech Stack:** Python 3.10+, asyncio, tomllib, JSONL, existing fleet/factorio modules

**Spec:** `docs/superpowers/specs/2026-03-28-factorio-autoresearch-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `fleet/factorio/bridge_config.py` | **Modify** — add `prompt_template`, `temperature`, `top_p`, `idle_assembler_replan` fields |
| `fleet/factorio/agent_brain.py` | **Modify** — template-based prompts, Ollama options, cumulative counters, `reset_counters()` |
| `fleet/factorio/prompt_loader.py` | **Create** — load prompt TOML templates, resolve placeholders |
| `fleet/factorio/prompts/baseline.toml` | **Create** — extract current hardcoded prompt |
| `fleet/factorio/experiment_scorer.py` | **Create** — phase-gated metric computation |
| `fleet/factorio/experiment_runner.py` | **Create** — main experiment orchestrator loop |
| `fleet/factorio/candidates/` | **Create** — directory for candidate experiment TOMLs (separate from prompt templates) |
| `tests/test_prompt_loader.py` | **Create** — tests for prompt loading |
| `tests/test_experiment_scorer.py` | **Create** — tests for scoring logic |
| `tests/test_experiment_runner.py` | **Create** — tests for runner orchestration |

---

### Task 1: Add New Config Fields to BridgeConfig

**Files:**
- Modify: `fleet/factorio/bridge_config.py:8-50`
- Test: `tests/test_agent_brain.py` (existing tests must still pass)

- [ ] **Step 1: Write test for new config fields**

Create `tests/test_bridge_config.py`:

```python
"""Tests for BridgeConfig — new experiment fields."""
from factorio.bridge_config import BridgeConfig


def test_new_fields_have_defaults():
    cfg = BridgeConfig()
    assert cfg.prompt_template == "baseline"
    assert cfg.temperature is None
    assert cfg.top_p is None
    assert cfg.idle_assembler_replan == 3


def test_from_dict_picks_up_new_fields():
    d = {"prompt_template": "compact_v1", "temperature": 0.7, "top_p": 0.9, "idle_assembler_replan": 5}
    cfg = BridgeConfig.from_dict(d)
    assert cfg.prompt_template == "compact_v1"
    assert cfg.temperature == 0.7
    assert cfg.top_p == 0.9
    assert cfg.idle_assembler_replan == 5


def test_from_dict_ignores_unknown():
    d = {"prompt_template": "baseline", "bogus_field": 42}
    cfg = BridgeConfig.from_dict(d)
    assert cfg.prompt_template == "baseline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_bridge_config.py -v`
Expected: FAIL — `BridgeConfig` has no `prompt_template`, `temperature`, `top_p`, or `idle_assembler_replan` fields

- [ ] **Step 3: Add fields to BridgeConfig**

In `fleet/factorio/bridge_config.py`, add after line 50 (`ollama_cooldown_secs`):

```python
    prompt_template: str = "baseline"
    temperature: float | None = None
    top_p: float | None = None
    idle_assembler_replan: int = 3
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_bridge_config.py ../tests/test_agent_brain.py -v`
Expected: ALL PASS (new tests + existing brain tests unbroken)

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge_config.py tests/test_bridge_config.py
git commit -m "feat(factorio): add experiment config fields to BridgeConfig"
```

---

### Task 2: Create Prompt Loader and Baseline Template

**Files:**
- Create: `fleet/factorio/prompt_loader.py`
- Create: `fleet/factorio/prompts/baseline.toml`
- Create: `tests/test_prompt_loader.py`
- Create: `tests/fixtures/prompts/test_prompt.toml`

- [ ] **Step 1: Write tests for prompt loader**

Create `tests/test_prompt_loader.py`:

```python
"""Tests for prompt template loading and rendering."""
import os
import pytest


def test_load_template_returns_system_and_user():
    from factorio.prompt_loader import load_prompt_template
    tmpl = load_prompt_template("test_prompt", prompts_dir="tests/fixtures/prompts")
    assert "system_template" in tmpl
    assert "user_template" in tmpl


def test_load_template_missing_raises():
    from factorio.prompt_loader import load_prompt_template
    with pytest.raises(FileNotFoundError):
        load_prompt_template("nonexistent", prompts_dir="tests/fixtures/prompts")


def test_render_prompt_substitutes_placeholders():
    from factorio.prompt_loader import load_prompt_template, render_prompt
    tmpl = load_prompt_template("test_prompt", prompts_dir="tests/fixtures/prompts")
    system, user = render_prompt(tmpl, state="iron=42", objective="craft gears", previous_results="none")
    assert "iron=42" in user
    assert "craft gears" in user
```

- [ ] **Step 2: Create test fixture prompt**

Create `tests/fixtures/prompts/test_prompt.toml`:

```toml
[meta]
name = "Test Prompt"
description = "Fixture for unit tests"

[templates]
system_template = "You are a test agent. Respond with JSON actions."
user_template = """# State
{state}

# Objective
{objective}

# Previous
{previous_results}

Generate actions."""
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_prompt_loader.py -v`
Expected: FAIL — `factorio.prompt_loader` does not exist

- [ ] **Step 4: Implement prompt_loader.py**

Create `fleet/factorio/prompt_loader.py`:

```python
"""Prompt template loader — TOML-based swappable prompts for AgentBrain."""
import logging
from pathlib import Path

log = logging.getLogger("biged.factorio.prompt_loader")

_DEFAULT_PROMPTS_DIR = "fleet/factorio/prompts"


def load_prompt_template(name: str, prompts_dir: str = _DEFAULT_PROMPTS_DIR) -> dict:
    """Load a prompt template TOML by name. Returns dict with system_template and user_template."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    path = Path(prompts_dir) / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    templates = data.get("templates", {})
    if "system_template" not in templates:
        raise ValueError(f"Prompt template {name} missing [templates] system_template")
    if "user_template" not in templates:
        raise ValueError(f"Prompt template {name} missing [templates] user_template")

    return {
        "name": data.get("meta", {}).get("name", name),
        "system_template": templates["system_template"],
        "user_template": templates["user_template"],
    }


def render_prompt(template: dict, state: str, objective: str, previous_results: str) -> tuple[str, str]:
    """Render a prompt template with substituted placeholders."""
    system = template["system_template"]
    user = template["user_template"].format(
        state=state,
        objective=objective,
        previous_results=previous_results,
    )
    return system, user
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_prompt_loader.py -v`
Expected: ALL PASS

- [ ] **Step 6: Create baseline.toml — extract current hardcoded prompt**

Create `fleet/factorio/prompts/baseline.toml`. The system template is the current `SYSTEM_PROMPT` from `agent_brain.py` (lines 19-43). The user template is the current `_build_prompt` format:

```toml
[meta]
name = "Baseline"
description = "Original hardcoded prompt, extracted as-is from agent_brain.py"

[templates]
system_template = """You are a Factorio automation agent controlling a factory through commands.
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

user_template = """# Current Factory State
{state}

# Current Objective
{objective}

# Previous Plan Results
{previous_results}

Generate 5-20 actions to work toward the objective."""
```

**Note:** The system template uses single braces (it is NOT passed through `.format()`). Only the user template has `{state}`, `{objective}`, `{previous_results}` placeholders that get substituted by `render_prompt()`.

- [ ] **Step 7: Commit**

```bash
git add fleet/factorio/prompt_loader.py fleet/factorio/prompts/baseline.toml tests/test_prompt_loader.py tests/fixtures/prompts/test_prompt.toml
git commit -m "feat(factorio): add prompt template loader and baseline template"
```

---

### Task 3: Wire AgentBrain to Use Prompt Templates + Add Counters

**Files:**
- Modify: `fleet/factorio/agent_brain.py:1-263`
- Modify: `tests/test_agent_brain.py` (add new tests, existing tests must still pass)

- [ ] **Step 1: Write tests for new brain features**

Append to `tests/test_agent_brain.py`:

```python
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
    import tomllib
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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd fleet && python -m pytest ../tests/test_agent_brain.py -v -k "counter or reset or template or temperature"`
Expected: FAIL — no `total_actions`, `reset_counters`, `prompts_dir` param, or `options` in Ollama body

- [ ] **Step 3: Implement changes to agent_brain.py**

Modify `fleet/factorio/agent_brain.py`:

**3a.** Add import at top (after line 7):
```python
from factorio.prompt_loader import load_prompt_template, render_prompt
```

**3b.** Update `__init__` (lines 61-75) to accept `prompts_dir`, load template, init counters:
```python
    def __init__(self, config: BridgeConfig, world_model: WorldModel,
                 curricula_dir: str | None = None, prompts_dir: str | None = None):
        self.config = config
        self.world_model = world_model
        self.curriculum = CurriculumManager(
            current_phase=config.current_phase,
            curricula_dir=curricula_dir or "fleet/factorio/curricula",
        )
        self._prompts_dir = prompts_dir or "fleet/factorio/prompts"
        try:
            self._prompt_template = load_prompt_template(
                config.prompt_template, prompts_dir=self._prompts_dir)
        except (FileNotFoundError, ValueError):
            log.warning("Prompt template '%s' not found, using hardcoded fallback",
                        config.prompt_template)
            self._prompt_template = None
        self._plan: list[dict] = []
        self._plan_index: int = 0
        self._consecutive_failures: int = 0
        self._idle_assembler_count: int = 0
        self._last_results: list[dict] = []
        self._ollama_cooldown_until: float = 0.0
        self._plan_count: int = 0
        # Cumulative counters for experiment scoring
        self.total_actions: int = 0
        self.total_successes: int = 0
        self.total_failures: int = 0
```

**3c.** Update `_build_prompt` (lines 77-107) to use template when available:
```python
    def _build_prompt(self, state: GameState) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for Ollama."""
        objective = self.curriculum.get_current_objective()
        state_md = state_to_markdown(state)

        # Build objective text
        obj_text = (
            f"Phase {objective.get('phase', '?')}: {objective.get('phase_name', '')}\n"
            f"Lesson: {objective.get('lesson_name', '?')} — {objective.get('description', '')}\n"
            f"Success criteria: {objective.get('criteria', '?')}\n"
            f"Hint: {objective.get('hint', '')}"
        )

        # Build previous results text
        if self._last_results:
            result_lines = []
            for r in self._last_results:
                status = "OK" if r.get("success") else "FAIL"
                desc = r.get("description", r.get("action", "?"))
                err = f" — {r.get('error', '')}" if r.get("error") else ""
                result_lines.append(f"- [{status}] {desc}{err}")
            results_text = "\n".join(result_lines)
        else:
            results_text = "First plan — no previous results."

        # Use template if available, else fall back to hardcoded
        if self._prompt_template:
            return render_prompt(self._prompt_template,
                                state=state_md, objective=obj_text,
                                previous_results=results_text)

        # Fallback: original hardcoded format
        lines = [
            "# Current Factory State", state_md, "",
            "# Current Objective", obj_text, "",
            "# Previous Plan Results", results_text, "",
            "Generate 5-20 actions to work toward the objective.",
        ]
        return SYSTEM_PROMPT, "\n".join(lines)
```

**3d.** Update `_generate_plan` (lines 117-122) to include Ollama options:
```python
        body_dict = {
            "model": self.config.ollama_model,
            "prompt": user_prompt,
            "system": system_prompt,
            "stream": False,
        }
        # Add temperature/top_p if configured
        options = {}
        if self.config.temperature is not None:
            options["temperature"] = self.config.temperature
        if self.config.top_p is not None:
            options["top_p"] = self.config.top_p
        if options:
            body_dict["options"] = options

        body = json.dumps(body_dict).encode("utf-8")
```

**3e.** Update `report_result` (lines 228-247) to increment counters:
```python
    def report_result(self, action: TranslatedAction, result: dict) -> None:
        """Track action result. Invalidate plan on consecutive failures."""
        self.total_actions += 1
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
            self.total_successes += 1
        else:
            self._consecutive_failures += 1
            self.total_failures += 1
            if self._consecutive_failures >= self.config.plan_invalidation_failures:
                log.warning("Plan invalidated: %d consecutive failures", self._consecutive_failures)
                self._plan = []
                self._plan_index = 0
                self._consecutive_failures = 0
```

**3f.** Add `reset_counters` method (after `get_plan_status`):
```python
    def reset_counters(self) -> None:
        """Reset cumulative counters for experiment runner."""
        self.total_actions = 0
        self.total_successes = 0
        self.total_failures = 0
```

**3g.** Update idle assembler replan to use config (line 202):
Change `if self._idle_assembler_count >= 3:` to `if self._idle_assembler_count >= self.config.idle_assembler_replan:`

- [ ] **Step 4: Run ALL agent_brain tests**

Run: `cd fleet && python -m pytest ../tests/test_agent_brain.py -v`
Expected: ALL PASS (old and new)

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py
git commit -m "feat(factorio): wire prompt templates, Ollama options, and counters into AgentBrain"
```

---

### Task 4: Build Experiment Scorer

**Files:**
- Create: `fleet/factorio/experiment_scorer.py`
- Create: `tests/test_experiment_scorer.py`

- [ ] **Step 1: Write tests for scoring logic**

Create `tests/test_experiment_scorer.py`:

```python
"""Tests for phase-gated experiment scoring."""
from factorio.experiment_scorer import compute_score


def test_phase1_pure_lessons():
    score = compute_score(phase=1, lessons_passed=3, total_actions=100,
                          total_failures=5, throughput=0.0)
    assert score == 3.0


def test_phase1_zero_lessons():
    score = compute_score(phase=1, lessons_passed=0, total_actions=50,
                          total_failures=10, throughput=0.0)
    assert score == 0.0


def test_phase2_includes_efficiency():
    score = compute_score(phase=2, lessons_passed=4, total_actions=20,
                          total_failures=0, throughput=0.0)
    # 4 + (1/20) = 4.05
    assert abs(score - 4.05) < 0.001


def test_phase3_penalizes_failures():
    score = compute_score(phase=3, lessons_passed=4, total_actions=20,
                          total_failures=4, throughput=0.0)
    # 4 + (1/20) - 0.1*(4/20) = 4 + 0.05 - 0.02 = 4.03
    assert abs(score - 4.03) < 0.001


def test_phase4_adds_throughput():
    score = compute_score(phase=4, lessons_passed=4, total_actions=20,
                          total_failures=2, throughput=0.5)
    # 4 + (1/20) - 0.1*(2/20) + 0.5 = 4 + 0.05 - 0.01 + 0.5 = 4.54
    assert abs(score - 4.54) < 0.001


def test_zero_actions_no_division_error():
    score = compute_score(phase=2, lessons_passed=1, total_actions=0,
                          total_failures=0, throughput=0.0)
    assert score == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_experiment_scorer.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement experiment_scorer.py**

Create `fleet/factorio/experiment_scorer.py`:

```python
"""Phase-gated experiment scoring for Factorio autoresearch."""
import logging

log = logging.getLogger("biged.factorio.scorer")


def compute_score(phase: int, lessons_passed: int,
                  total_actions: int, total_failures: int,
                  throughput: float) -> float:
    """Compute phase-gated experiment score.

    Phase 1: lessons_passed only
    Phase 2: + action efficiency
    Phase 3: + failure penalty
    Phase 4: + throughput bonus

    All metrics are per-budget-window (aggregated across all plans in run).
    """
    score = float(lessons_passed)

    if phase >= 2 and total_actions > 0:
        score += 1.0 / total_actions

    if phase >= 3 and total_actions > 0:
        failure_rate = total_failures / total_actions
        score -= 0.1 * failure_rate

    if phase >= 4:
        score += throughput

    return score
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_experiment_scorer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/experiment_scorer.py tests/test_experiment_scorer.py
git commit -m "feat(factorio): add phase-gated experiment scorer"
```

---

### Task 5: Build Experiment Runner

**Files:**
- Create: `fleet/factorio/experiment_runner.py`
- Create: `tests/test_experiment_runner.py`

This is the largest task. The runner orchestrates the full loop: load candidate, create bridge, run budget, score, keep/discard, log.

- [ ] **Step 1: Write tests for runner components**

Create `tests/test_experiment_runner.py`:

```python
"""Tests for experiment runner — candidate loading, results logging, keep/discard."""
import json
import os
import pytest
from pathlib import Path


def test_load_candidate_from_toml(tmp_path):
    from factorio.experiment_runner import load_candidate

    (tmp_path / "test_candidate.toml").write_text('''
prompt = "compact_v1"
load_save = "my_save"
phase_override = 2

[params]
plan_size = 10
temperature = 0.5
failure_threshold = 2
''', encoding="utf-8")

    candidate = load_candidate(str(tmp_path / "test_candidate.toml"))
    assert candidate["prompt"] == "compact_v1"
    assert candidate["load_save"] == "my_save"
    assert candidate["phase_override"] == 2
    assert candidate["params"]["plan_size"] == 10
    assert candidate["params"]["temperature"] == 0.5


def test_load_candidate_defaults(tmp_path):
    from factorio.experiment_runner import load_candidate

    (tmp_path / "minimal.toml").write_text('prompt = "baseline"\n', encoding="utf-8")
    candidate = load_candidate(str(tmp_path / "minimal.toml"))
    assert candidate["prompt"] == "baseline"
    assert candidate.get("load_save") is None
    assert candidate.get("phase_override") is None
    assert candidate.get("params", {}) == {}


def test_append_result_tsv(tmp_path):
    from factorio.experiment_runner import append_result

    tsv_path = str(tmp_path / "results.tsv")
    append_result(tsv_path, experiment_id="exp_0001", phase=1, load_save=None,
                  prompt="baseline", metric=2.0, baseline=None, delta=None,
                  status="keep", description="initial baseline")

    lines = Path(tsv_path).read_text().strip().split("\n")
    assert len(lines) == 2  # header + 1 row
    assert "exp_0001" in lines[1]
    assert "keep" in lines[1]

    # Append another
    append_result(tsv_path, experiment_id="exp_0002", phase=1, load_save=None,
                  prompt="compact", metric=3.0, baseline=2.0, delta=1.0,
                  status="keep", description="better prompt")

    lines = Path(tsv_path).read_text().strip().split("\n")
    assert len(lines) == 3


def test_append_replay(tmp_path):
    from factorio.experiment_runner import append_replay

    jsonl_path = str(tmp_path / "replay.jsonl")
    append_replay(jsonl_path, experiment_id="exp_0001", phase=1,
                  lesson="Craft gears", state={"inventory": {"iron-plate": 5}},
                  plan=[{"action": "craft"}], actions_taken=1,
                  actions_succeeded=1, lesson_passed=True)

    lines = Path(jsonl_path).read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["experiment_id"] == "exp_0001"
    assert entry["lesson_passed"] is True


def test_load_baseline_from_tsv(tmp_path):
    from factorio.experiment_runner import append_result, load_baseline

    tsv_path = str(tmp_path / "results.tsv")
    append_result(tsv_path, "exp_0001", 1, None, "baseline", 2.0, None, None, "keep", "first")
    append_result(tsv_path, "exp_0002", 1, None, "compact", 1.5, 2.0, -0.5, "discard", "worse")
    append_result(tsv_path, "exp_0003", 1, None, "cot", 3.0, 2.0, 1.0, "keep", "better")

    best = load_baseline(tsv_path, phase=1)
    assert best == 3.0


def test_load_baseline_empty(tmp_path):
    from factorio.experiment_runner import load_baseline

    tsv_path = str(tmp_path / "results.tsv")
    best = load_baseline(tsv_path, phase=1)
    assert best is None


def test_build_experiment_config():
    from factorio.bridge_config import BridgeConfig
    from factorio.experiment_runner import build_experiment_config

    base = BridgeConfig(plan_max_actions=20, ollama_cooldown_secs=30)
    candidate = {
        "prompt": "compact_v1",
        "params": {
            "plan_size": 10,
            "temperature": 0.5,
            "cooldown_after_failure": 15,
            "failure_threshold": 2,
        },
    }
    cfg = build_experiment_config(base, candidate)
    assert cfg.prompt_template == "compact_v1"
    assert cfg.plan_max_actions == 10
    assert cfg.temperature == 0.5
    assert cfg.ollama_cooldown_secs == 15
    assert cfg.plan_invalidation_failures == 2
    # Base unchanged
    assert base.plan_max_actions == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_experiment_runner.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement experiment_runner.py**

Create `fleet/factorio/experiment_runner.py`:

```python
"""Experiment runner — autoresearch-style loop for Factorio AgentBrain optimization."""
import copy
import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from factorio.bridge_config import BridgeConfig

log = logging.getLogger("biged.factorio.experiment")

_DEFAULT_RESULTS_FILE = "fleet/factorio/experiment_results.tsv"
_DEFAULT_REPLAY_FILE = "fleet/factorio/replay_log.jsonl"

_TSV_FIELDS = [
    "experiment_id", "timestamp", "phase", "load_save", "prompt",
    "metric", "baseline", "delta", "status", "description",
]


def load_candidate(path: str) -> dict:
    """Load a candidate config TOML file."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    return {
        "prompt": data.get("prompt", "baseline"),
        "load_save": data.get("load_save"),
        "phase_override": data.get("phase_override"),
        "start_lesson": data.get("start_lesson", 0),
        "params": data.get("params", {}),
    }


def build_experiment_config(base_config: BridgeConfig, candidate: dict) -> BridgeConfig:
    """Merge candidate overrides into a copy of the base config."""
    cfg = copy.deepcopy(base_config)
    cfg.prompt_template = candidate.get("prompt", "baseline")

    params = candidate.get("params", {})
    if "plan_size" in params:
        cfg.plan_max_actions = params["plan_size"]
    if "ollama_timeout" in params:
        cfg.ollama_timeout = params["ollama_timeout"]
    if "cooldown_after_failure" in params:
        cfg.ollama_cooldown_secs = params["cooldown_after_failure"]
    if "failure_threshold" in params:
        cfg.plan_invalidation_failures = params["failure_threshold"]
    if "idle_assembler_replan" in params:
        cfg.idle_assembler_replan = params["idle_assembler_replan"]
    if "temperature" in params:
        cfg.temperature = params["temperature"]
    if "top_p" in params:
        cfg.top_p = params["top_p"]

    return cfg


def append_result(tsv_path: str, experiment_id: str, phase: int,
                  load_save: str | None, prompt: str, metric: float,
                  baseline: float | None, delta: float | None,
                  status: str, description: str) -> None:
    """Append one row to experiment_results.tsv. Creates file with header if needed."""
    path = Path(tsv_path)
    write_header = not path.exists()

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        if write_header:
            writer.writerow(_TSV_FIELDS)
        writer.writerow([
            experiment_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            phase,
            load_save or "-",
            prompt,
            f"{metric:.4f}",
            f"{baseline:.4f}" if baseline is not None else "-",
            f"{delta:+.4f}" if delta is not None else "-",
            status,
            description,
        ])


def append_replay(jsonl_path: str, experiment_id: str, phase: int,
                  lesson: str, state: dict, plan: list,
                  actions_taken: int, actions_succeeded: int,
                  lesson_passed: bool) -> None:
    """Append one replay entry to replay_log.jsonl."""
    entry = {
        "ts": int(time.time()),
        "experiment_id": experiment_id,
        "phase": phase,
        "lesson": lesson,
        "state": state,
        "plan": plan,
        "actions_taken": actions_taken,
        "actions_succeeded": actions_succeeded,
        "lesson_passed": lesson_passed,
    }
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def load_baseline(tsv_path: str, phase: int) -> float | None:
    """Load the best 'keep' score for a phase from results.tsv."""
    path = Path(tsv_path)
    if not path.exists():
        return None

    best = None
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("status") != "keep":
                continue
            try:
                row_phase = int(row.get("phase", 0))
            except ValueError:
                continue
            if row_phase != phase:
                continue
            try:
                metric = float(row["metric"])
            except (ValueError, KeyError):
                continue
            if best is None or metric > best:
                best = metric

    return best


def generate_experiment_id(results_path: str) -> str:
    """Generate next experiment ID (exp_0001, exp_0002, ...)."""
    path = Path(results_path)
    if not path.exists():
        return "exp_0001"

    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("exp_"):
                count += 1

    return f"exp_{count + 1:04d}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_experiment_runner.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/experiment_runner.py tests/test_experiment_runner.py
git commit -m "feat(factorio): add experiment runner with candidate loading, scoring, and logging"
```

---

### Task 6: Add run_experiment Orchestration Method

**Files:**
- Modify: `fleet/factorio/experiment_runner.py`
- Modify: `tests/test_experiment_runner.py`

This adds the async `run_experiment()` function that wires everything together: create bridge, run budget, collect metrics, score, decide keep/discard.

- [ ] **Step 1: Write integration-style test**

Append to `tests/test_experiment_runner.py`:

```python
def test_run_experiment_keep_discard_flow(tmp_path):
    """Test the full keep/discard decision flow (mocked bridge)."""
    from factorio.experiment_runner import (
        append_result, load_baseline, generate_experiment_id,
        build_experiment_config,
    )
    from factorio.experiment_scorer import compute_score
    from factorio.bridge_config import BridgeConfig

    results_path = str(tmp_path / "results.tsv")

    # Simulate experiment 1: baseline
    exp_id = generate_experiment_id(results_path)
    assert exp_id == "exp_0001"
    score = compute_score(phase=1, lessons_passed=2, total_actions=15,
                          total_failures=3, throughput=0.0)
    baseline = load_baseline(results_path, phase=1)
    assert baseline is None
    append_result(results_path, exp_id, 1, None, "baseline", score,
                  baseline, None, "keep", "initial")

    # Simulate experiment 2: better
    exp_id = generate_experiment_id(results_path)
    assert exp_id == "exp_0002"
    score2 = compute_score(phase=1, lessons_passed=3, total_actions=10,
                           total_failures=1, throughput=0.0)
    baseline = load_baseline(results_path, phase=1)
    assert baseline == 2.0
    delta = score2 - baseline
    status = "keep" if score2 > baseline else "discard"
    assert status == "keep"
    append_result(results_path, exp_id, 1, None, "compact", score2,
                  baseline, delta, status, "better prompt")

    # Simulate experiment 3: worse
    exp_id = generate_experiment_id(results_path)
    score3 = compute_score(phase=1, lessons_passed=1, total_actions=20,
                           total_failures=10, throughput=0.0)
    baseline = load_baseline(results_path, phase=1)
    assert baseline == 3.0  # from exp_0002
    status = "keep" if score3 > baseline else "discard"
    assert status == "discard"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_experiment_runner.py::test_run_experiment_keep_discard_flow -v`
Expected: PASS (uses already-implemented functions)

- [ ] **Step 3: Add run_loop function to experiment_runner.py**

Append to `fleet/factorio/experiment_runner.py`:

```python
import asyncio
import glob as glob_module

from factorio.experiment_scorer import compute_score
from factorio.agent_brain import AgentBrain, flatten_state
from factorio.bridge_config import load_factorio_config


async def run_single_experiment(
    candidate_path: str,
    base_config: BridgeConfig,
    budget_seconds: int = 600,
    results_file: str = _DEFAULT_RESULTS_FILE,
    replay_file: str = _DEFAULT_REPLAY_FILE,
    prompts_dir: str = "fleet/factorio/prompts",
) -> dict:
    """Run a single experiment: load candidate, create bridge, run budget, score.

    Returns dict with experiment_id, score, status, and baseline.
    """
    from factorio.bridge import FactorioBridge

    candidate = load_candidate(candidate_path)
    exp_config = build_experiment_config(base_config, candidate)
    phase = candidate.get("phase_override") or exp_config.current_phase
    exp_id = generate_experiment_id(results_file)

    log.info("=== Experiment %s: prompt=%s, phase=%d ===",
             exp_id, candidate["prompt"], phase)

    # Create bridge with experiment config
    bridge = FactorioBridge(exp_config)
    brain = bridge.brain

    # Load save if specified
    load_save = candidate.get("load_save")
    if load_save:
        log.info("Loading save: %s", load_save)
        try:
            await bridge.rcon.connect()
            await bridge.rcon.command(f"/load {load_save}")
            await asyncio.sleep(3)  # wait for save to load
        except Exception as e:
            log.warning("Failed to load save '%s': %s", load_save, e)
            append_result(results_file, exp_id, phase, load_save,
                          candidate["prompt"], 0.0, None, None, "error",
                          f"save load failed: {e}")
            return {"experiment_id": exp_id, "score": 0.0, "status": "error"}

    # Reset counters
    brain.reset_counters()

    # Run bridge with budget timeout
    try:
        bridge._running = True
        if not load_save:
            if not await bridge.connect_with_retry():
                raise ConnectionError("RCON connect failed")

        bridge_task = asyncio.create_task(
            _run_bridge_ticks(bridge, budget_seconds)
        )
        await asyncio.wait_for(bridge_task, timeout=budget_seconds + 30)
    except asyncio.TimeoutError:
        log.info("Budget expired for %s", exp_id)
    except Exception as e:
        log.warning("Experiment %s error: %s", exp_id, e)
        append_result(results_file, exp_id, phase, load_save,
                      candidate["prompt"], 0.0, None, None, "error", str(e))
        return {"experiment_id": exp_id, "score": 0.0, "status": "error"}
    finally:
        bridge.stop()

    # Score
    progress = brain.curriculum.get_progress()
    lessons_passed = progress.get("completed", 0)
    score = compute_score(
        phase=phase,
        lessons_passed=lessons_passed,
        total_actions=brain.total_actions,
        total_failures=brain.total_failures,
        throughput=0.0,  # TODO: extract from metrics when available
    )

    # Compare to baseline
    baseline = load_baseline(results_file, phase)
    if baseline is None:
        status = "keep"
        delta = None
    elif score > baseline:
        status = "keep"
        delta = score - baseline
    else:
        status = "discard"
        delta = score - baseline

    # Log results
    append_result(results_file, exp_id, phase, load_save,
                  candidate["prompt"], score, baseline, delta, status,
                  f"lessons={lessons_passed} actions={brain.total_actions} "
                  f"failures={brain.total_failures}")

    log.info("Experiment %s: score=%.4f baseline=%s status=%s",
             exp_id, score, baseline, status)

    return {
        "experiment_id": exp_id,
        "score": score,
        "baseline": baseline,
        "status": status,
        "lessons_passed": lessons_passed,
    }


async def _run_bridge_ticks(bridge, budget_seconds: int) -> None:
    """Run bridge tick loop for a fixed budget duration."""
    start = time.monotonic()
    while bridge._running and (time.monotonic() - start) < budget_seconds:
        try:
            await bridge.tick()
        except Exception as e:
            log.warning("Tick error: %s", e)
        interval = bridge.cadence.get_interval_secs()
        await asyncio.sleep(interval)


async def run_loop(
    candidates_dir: str = "fleet/factorio/candidates",
    base_config: BridgeConfig | None = None,
    budget_seconds: int = 600,
    max_experiments: int = 0,
    max_total_hours: float = 0,
    results_file: str = _DEFAULT_RESULTS_FILE,
    replay_file: str = _DEFAULT_REPLAY_FILE,
) -> None:
    """Main experiment loop — run candidates until stopped."""
    if base_config is None:
        base_config = load_factorio_config()

    log.info("Experiment loop starting — budget=%ds, candidates from %s",
             budget_seconds, candidates_dir)

    experiment_count = 0
    start_time = time.monotonic()

    # Find candidate TOML files
    candidate_files = sorted(glob_module.glob(f"{candidates_dir}/*.toml"))
    if not candidate_files:
        log.warning("No candidate files found in %s", candidates_dir)
        return

    candidate_idx = 0

    while True:
        # Check stop conditions
        if max_experiments > 0 and experiment_count >= max_experiments:
            log.info("Max experiments (%d) reached", max_experiments)
            break
        if max_total_hours > 0:
            elapsed_hours = (time.monotonic() - start_time) / 3600
            if elapsed_hours >= max_total_hours:
                log.info("Max total hours (%.1f) reached", max_total_hours)
                break

        # Cycle through candidates
        candidate_path = candidate_files[candidate_idx % len(candidate_files)]
        candidate_idx += 1

        try:
            result = await run_single_experiment(
                candidate_path=candidate_path,
                base_config=base_config,
                budget_seconds=budget_seconds,
                results_file=results_file,
                replay_file=replay_file,
            )
            experiment_count += 1
            log.info("Experiment %d complete: %s", experiment_count, result)
        except KeyboardInterrupt:
            log.info("Experiment loop interrupted")
            break
        except Exception as e:
            log.warning("Experiment failed: %s", e, exc_info=True)
            experiment_count += 1


def main():
    """CLI entry point for running experiments."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Factorio AgentBrain experiment runner")
    parser.add_argument("--candidates-dir", default="fleet/factorio/candidates",
                        help="Directory containing candidate TOML files")
    parser.add_argument("--budget", type=int, default=600,
                        help="Budget per experiment in seconds (default: 600)")
    parser.add_argument("--max-experiments", type=int, default=0,
                        help="Max experiments to run (0=unlimited)")
    parser.add_argument("--max-hours", type=float, default=0,
                        help="Max total hours to run (0=unlimited)")
    parser.add_argument("--single", type=str, default=None,
                        help="Run a single candidate TOML file and exit")
    args = parser.parse_args()

    if args.single:
        base_config = load_factorio_config()
        result = asyncio.run(run_single_experiment(
            candidate_path=args.single,
            base_config=base_config,
            budget_seconds=args.budget,
        ))
        print(json.dumps(result, indent=2))
    else:
        asyncio.run(run_loop(
            candidates_dir=args.candidates_dir,
            budget_seconds=args.budget,
            max_experiments=args.max_experiments,
            max_total_hours=args.max_hours,
        ))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

Run: `cd fleet && python -m pytest ../tests/test_experiment_runner.py ../tests/test_experiment_scorer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/experiment_runner.py tests/test_experiment_runner.py
git commit -m "feat(factorio): add run_single_experiment and run_loop orchestration"
```

---

### Task 7: Add .gitignore Entry and fleet.toml Config Section

**Files:**
- Modify: `.gitignore`
- Modify: `fleet/fleet.toml` (add `[factorio.experiments]` section)

- [ ] **Step 1: Add replay log to .gitignore**

Append to `.gitignore`:
```
# Factorio experiment replay data (large, machine-generated)
fleet/factorio/replay_log.jsonl
```

- [ ] **Step 2: Add experiment config to fleet.toml**

Read `fleet/fleet.toml`, find the `[factorio]` section, and add after the existing factorio settings:

```toml
[factorio.experiments]
budget_minutes = 10
max_experiments = 0
max_total_hours = 0
training_data_threshold = 500
results_file = "fleet/factorio/experiment_results.tsv"
replay_file = "fleet/factorio/replay_log.jsonl"
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore fleet/fleet.toml
git commit -m "chore(factorio): add experiment config section and gitignore replay log"
```

---

### Task 8: Run Full Test Suite

**Files:** All test files from Tasks 1-6

- [ ] **Step 1: Run all factorio-related tests**

Run: `cd fleet && python -m pytest ../tests/test_bridge_config.py ../tests/test_agent_brain.py ../tests/test_prompt_loader.py ../tests/test_experiment_scorer.py ../tests/test_experiment_runner.py ../tests/test_curriculum.py ../tests/test_curriculum_manager.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: 33/33 pass (no regressions)

- [ ] **Step 3: Commit any fixes if needed**

Only if tests uncovered issues. If all pass, skip this step.

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Config fields | `bridge_config.py`, `test_bridge_config.py` |
| 2 | Prompt loader + baseline template | `prompt_loader.py`, `prompts/baseline.toml`, `test_prompt_loader.py` |
| 3 | Wire brain to templates + counters | `agent_brain.py`, `test_agent_brain.py` |
| 4 | Experiment scorer | `experiment_scorer.py`, `test_experiment_scorer.py` |
| 5 | Experiment runner core | `experiment_runner.py`, `test_experiment_runner.py` |
| 6 | Run loop orchestration | `experiment_runner.py`, `test_experiment_runner.py` |
| 7 | Config + gitignore | `.gitignore`, `fleet.toml` |
| 8 | Full test suite validation | All test files |
