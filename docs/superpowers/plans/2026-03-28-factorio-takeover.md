# Factorio Human Takeover Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pause/resume, human directives (sticky + expiring), customizable preset buttons, and an action builder to the Factorio agent — accessible from both dashboard and CLI.

**Architecture:** All takeover state (paused, directives, presets) lives in AgentBrain with a threading.Lock for thread safety. Bridge API exposes 8 new endpoints. Dashboard gets control bar + directives panel + action builder. CLI gets a `factorio` subcommand group.

**Tech Stack:** Python 3.14, Flask, threading.Lock, HTML/CSS/JS (dashboard), argparse (CLI), pytest

**Spec:** `docs/superpowers/specs/2026-03-28-factorio-takeover-design.md`

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `fleet/factorio/agent_brain.py` | **Modify** | Directive/Preset dataclasses, threading.Lock, pause/resume, directive CRUD, preset CRUD, prompt injection, directive expiry, updated next_action/get_plan_status |
| `fleet/factorio/bridge_api.py` | **Modify** | 8 new endpoints (pause, resume, directive CRUD, preset CRUD), update status/plan responses |
| `fleet/templates/dashboard.html` | **Modify** | Control bar, directives panel, action builder in Factorio section |
| `fleet/dashboard.py` | **Modify** | Proxy endpoints for pause/resume through dashboard |
| `fleet/lead_client.py` | **Modify** | `factorio` subcommand group (pause, resume, directive, action shortcuts) |
| `tests/test_agent_brain.py` | **Modify** | Tests for pause, directives, presets, expiry, prompt injection, thread safety |
| `tests/test_bridge_api.py` | **Modify** | Tests for new endpoints |

---

### Task 1: Add threading.Lock + pause/resume to AgentBrain

**Files:**
- Modify: `fleet/factorio/agent_brain.py`
- Modify: `tests/test_agent_brain.py`

- [ ] **Step 1: Write failing tests for pause/resume**

Add to `tests/test_agent_brain.py`:

```python
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
    action = brain.next_action(GameState(tick=10), [])
    assert action is None  # paused — no action
    assert brain._plan == []  # plan cleared on pause


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
    brain._plan = [{"action": "wait", "ticks": 30}]
    brain._plan_index = 0
    action = brain.next_action(GameState(tick=10), [])
    assert action is not None


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
    brain.next_action(GameState(tick=10), events)
    assert brain._plan == []


def test_brain_has_lock():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain
    import threading

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    assert isinstance(brain._lock, threading.Lock)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_brain.py::test_pause_stops_actions tests/test_agent_brain.py::test_brain_has_lock -v`
Expected: FAIL — no `pause` method, no `_lock` attribute

- [ ] **Step 3: Implement threading.Lock + pause/resume**

In `fleet/factorio/agent_brain.py`:

1. Add `import threading` and `import uuid` at top (alongside existing imports)

2. In `__init__`, add after `self._plan_count = 0`:
```python
        self._lock = threading.Lock()
        self._paused: bool = False
        self._directives: list = []
        self._presets: list = []
```

3. Add methods after `__init__`:
```python
    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._plan = []
            self._plan_index = 0
        log.info("Brain paused by human")

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        log.info("Brain resumed by human")

    @property
    def is_paused(self) -> bool:
        return self._paused
```

4. In `next_action()`, add pause check AFTER the idle_assembler block (after `self._idle_assembler_count = 0`), BEFORE `# Drain current plan`:
```python
        # Check pause — after events, before plan drain
        if self._paused:
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: All 20 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py
git commit -m "feat(factorio): add threading.Lock + pause/resume to AgentBrain"
```

---

### Task 2: Add directive system to AgentBrain

**Files:**
- Modify: `fleet/factorio/agent_brain.py`
- Modify: `tests/test_agent_brain.py`

- [ ] **Step 1: Write failing tests for directives**

Add to `tests/test_agent_brain.py`:

```python
def test_add_directive():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    did = brain.add_directive("focus on power", sticky=True)
    assert len(did) == 8
    directives = brain.get_directives()
    assert len(directives) == 1
    assert directives[0]["text"] == "focus on power"
    assert directives[0]["sticky"] is True
    assert directives[0]["plans_remaining"] == -1


def test_add_directive_non_sticky():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("fix belt", sticky=False, plans=3)
    directives = brain.get_directives()
    assert directives[0]["plans_remaining"] == 3


def test_remove_directive():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    did = brain.add_directive("test")
    assert brain.remove_directive(did) is True
    assert brain.get_directives() == []
    assert brain.remove_directive("nonexistent") is False


def test_clear_directives():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("one")
    brain.add_directive("two")
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

    brain.add_directive("focus on power", sticky=True)
    brain.add_directive("fix belt at 5,10", plans=1)
    _, user = brain._build_prompt(GameState(tick=50))
    assert "Human Directives" in user
    assert "focus on power" in user
    assert "fix belt" in user
    assert "sticky" in user


def test_directive_expiry():
    from factorio.state_parser import GameState
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain
    from unittest.mock import patch, MagicMock
    import json

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")

    brain.add_directive("sticky one", sticky=True)
    brain.add_directive("expire me", sticky=False, plans=1)

    actions = '[{"action": "wait", "ticks": 60}]'
    body = json.dumps({"response": actions}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        brain._generate_plan(GameState(tick=10))

    directives = brain.get_directives()
    assert len(directives) == 1
    assert directives[0]["text"] == "sticky one"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_brain.py::test_add_directive -v`
Expected: FAIL — no `add_directive` method

- [ ] **Step 3: Implement Directive dataclass and CRUD methods**

In `fleet/factorio/agent_brain.py`:

1. Add `from dataclasses import dataclass` to imports. Add after imports, before `INVALIDATION_EVENTS`:
```python
@dataclass
class Directive:
    id: str
    text: str
    sticky: bool
    plans_remaining: int
    created_at: float
```

2. Add directive methods after `is_paused` property:
```python
    def add_directive(self, text: str, sticky: bool = False, plans: int = 1) -> str:
        directive = Directive(
            id=uuid.uuid4().hex[:8], text=text, sticky=sticky,
            plans_remaining=-1 if sticky else plans, created_at=time.monotonic(),
        )
        with self._lock:
            self._directives.append(directive)
        log.info("Directive added [%s]: %s (sticky=%s, plans=%s)", directive.id, text, sticky, plans)
        return directive.id

    def remove_directive(self, directive_id: str) -> bool:
        with self._lock:
            before = len(self._directives)
            self._directives = [d for d in self._directives if d.id != directive_id]
            return len(self._directives) < before

    def clear_directives(self) -> int:
        with self._lock:
            count = len(self._directives)
            self._directives = []
            return count

    def get_directives(self) -> list[dict]:
        with self._lock:
            return [{"id": d.id, "text": d.text, "sticky": d.sticky,
                     "plans_remaining": d.plans_remaining} for d in self._directives]
```

3. Modify `_build_prompt()` — replace the `lines = [...]` block so directives inject between state and objective. The lines list should be built as:
```python
        lines = [
            "# Current Factory State",
            state_md,
            "",
        ]
        if self._directives:
            lines.append("# Human Directives (PRIORITY \u2014 follow these)")
            for d in self._directives:
                tag = "sticky" if d.sticky else f"{d.plans_remaining} plan(s) left"
                lines.append(f"- [{tag}] {d.text}")
            lines.append("")
        lines.extend([
            "# Current Objective",
            f"Phase {objective.get('phase', '?')}: {objective.get('phase_name', '')}",
            # ... rest unchanged
        ])
```

4. Add directive expiry in `_generate_plan()` — after `self._plan_count += 1` line, before `return actions`:
```python
                    with self._lock:
                        expired = []
                        for d in self._directives:
                            if not d.sticky:
                                d.plans_remaining -= 1
                                if d.plans_remaining <= 0:
                                    expired.append(d.id)
                        if expired:
                            self._directives = [d for d in self._directives if d.id not in expired]
                            log.info("Directives expired: %s", expired)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: All 26 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py
git commit -m "feat(factorio): add directive system with sticky/expiring and prompt injection"
```

---

### Task 3: Add preset buttons + update get_plan_status

**Files:**
- Modify: `fleet/factorio/agent_brain.py`
- Modify: `tests/test_agent_brain.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_brain.py`:

```python
def test_default_presets():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    presets = brain.get_presets()
    assert len(presets) == 6
    assert "Focus Power" in [p["label"] for p in presets]


def test_add_remove_preset():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    pid = brain.add_preset("Custom", "do custom thing", sticky=True, plans=5)
    assert len(brain.get_presets()) == 7
    assert brain.remove_preset(pid) is True
    assert len(brain.get_presets()) == 6


def test_plan_status_includes_paused_and_directives():
    from factorio.bridge_config import BridgeConfig
    from factorio.world_model import WorldModel
    from factorio.agent_brain import AgentBrain

    cfg = BridgeConfig(current_phase=1)
    wm = WorldModel()
    brain = AgentBrain(cfg, wm, curricula_dir="tests/fixtures/curricula")
    brain.pause()
    brain.add_directive("test")
    status = brain.get_plan_status()
    assert status["paused"] is True
    assert len(status["directives"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_brain.py::test_default_presets -v`

- [ ] **Step 3: Implement Preset dataclass, defaults, CRUD, update get_plan_status**

1. Add Preset dataclass after Directive:
```python
@dataclass
class Preset:
    id: str
    label: str
    text: str
    sticky: bool
    plans: int
```

2. Add DEFAULT_PRESETS constant after SYSTEM_PROMPT:
```python
DEFAULT_PRESETS = [
    {"label": "Focus Power", "text": "Focus on building power infrastructure", "sticky": False, "plans": 3},
    {"label": "Expand Mining", "text": "Expand mining operations - more drills on ore patches", "sticky": False, "plans": 3},
    {"label": "Fix Bottlenecks", "text": "Identify and fix production bottlenecks", "sticky": False, "plans": 2},
    {"label": "Scale Smelting", "text": "Scale up smelting - more furnaces, better throughput", "sticky": False, "plans": 3},
    {"label": "Build Defenses", "text": "Build defensive walls and turrets", "sticky": False, "plans": 3},
    {"label": "Optimize Layout", "text": "Optimize factory layout for throughput", "sticky": False, "plans": 2},
]
```

3. In `__init__`, replace `self._presets: list = []` with:
```python
        self._presets: list[Preset] = [Preset(id=uuid.uuid4().hex[:8], **p) for p in DEFAULT_PRESETS]
```

4. Add preset methods after directive methods:
```python
    def get_presets(self) -> list[dict]:
        with self._lock:
            return [{"id": p.id, "label": p.label, "text": p.text,
                     "sticky": p.sticky, "plans": p.plans} for p in self._presets]

    def add_preset(self, label: str, text: str, sticky: bool = False, plans: int = 1) -> str:
        preset = Preset(id=uuid.uuid4().hex[:8], label=label, text=text, sticky=sticky, plans=plans)
        with self._lock:
            self._presets.append(preset)
        return preset.id

    def remove_preset(self, preset_id: str) -> bool:
        with self._lock:
            before = len(self._presets)
            self._presets = [p for p in self._presets if p.id != preset_id]
            return len(self._presets) < before
```

5. Update `get_plan_status()` to include paused + directives:
```python
    def get_plan_status(self) -> dict:
        with self._lock:
            return {
                "plan": list(self._plan), "plan_index": self._plan_index,
                "plan_count": self._plan_count, "planning": False,
                "paused": self._paused, "consecutive_failures": self._consecutive_failures,
                "directives": [{"id": d.id, "text": d.text, "sticky": d.sticky,
                                "plans_remaining": d.plans_remaining} for d in self._directives],
            }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_agent_brain.py -v`
Expected: All 29 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/agent_brain.py tests/test_agent_brain.py
git commit -m "feat(factorio): add preset buttons and update get_plan_status"
```

---

### Task 4: Add takeover API endpoints

**Files:**
- Modify: `fleet/factorio/bridge_api.py`
- Modify: `tests/test_bridge_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_bridge_api.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bridge_api.py::test_pause_endpoint -v`

- [ ] **Step 3: Implement endpoints**

In `fleet/factorio/bridge_api.py`, add all 8 new endpoints inside `create_api()` (after existing `api_plan`), and modify `api_status` to include paused. See spec for exact endpoint definitions:
- `POST /api/pause`, `POST /api/resume`
- `POST /api/directive`, `DELETE /api/directive/<id>`, `GET /api/directives`, `DELETE /api/directives`
- `GET /api/presets`, `POST /api/preset`, `DELETE /api/preset/<id>`
- Update `api_status` to return `{**_bridge_status, "paused": _brain.is_paused if _brain else False}`

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_bridge_api.py -v`
Expected: All 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/bridge_api.py tests/test_bridge_api.py
git commit -m "feat(factorio): add 8 takeover API endpoints"
```

---

### Task 5: Add Factorio CLI subcommands

**Files:**
- Modify: `fleet/lead_client.py`

- [ ] **Step 1: Read lead_client.py** to find the subparser section (~line 977) and command dispatch

- [ ] **Step 2: Add factorio subcommand group** after the last `add_parser` call. Add subparsers for: pause, resume, status, directive, directives, clear-directive, clear-directives, place, craft, research, move. See spec Section 5 for exact CLI syntax.

- [ ] **Step 3: Add `_handle_factorio(args)` handler** that reads bridge_port from fleet.toml and hits bridge API via `urllib.request`. See spec for all subcommand behaviors.

- [ ] **Step 4: Add dispatch** in main(): `elif args.command == "factorio": _handle_factorio(args)`

- [ ] **Step 5: Verify CLI parses**

Run: `cd fleet && python lead_client.py factorio --help`

- [ ] **Step 6: Commit**

```bash
git add fleet/lead_client.py
git commit -m "feat(factorio): add CLI subcommands for takeover controls"
```

---

### Task 6: Add dashboard UI

**Files:**
- Modify: `fleet/templates/dashboard.html`
- Modify: `fleet/dashboard.py`

- [ ] **Step 1: Add control bar** — Pause/Resume toggle button + brain status text + directive badge in the Factorio section header

- [ ] **Step 2: Add directives panel** — preset buttons row, text input with sticky/plans selector, active directives list with dismiss buttons

- [ ] **Step 3: Add action builder** — collapsible panel with action type dropdown, dynamic form fields, send button, result display. Use safe DOM manipulation methods (createElement/textContent) instead of innerHTML for any user-provided content.

- [ ] **Step 4: Add JS functions** — `toggleFactorioPause()`, `sendFactorioDirective()`, `removeFactorioDirective()`, `sendFactorioAction()`, `updateFactorioActionFields()`, `_loadFactorioDirectives()`, `_loadFactorioPresets()`. Extend `loadFactorio()` to fetch directives/presets/pause status.

- [ ] **Step 5: Add dashboard proxy endpoints** in `fleet/dashboard.py` for `/api/factorio/pause` and `/api/factorio/resume` (same pattern as existing bridge-status/bridge-state proxies)

- [ ] **Step 6: Commit**

```bash
git add fleet/templates/dashboard.html fleet/dashboard.py
git commit -m "feat(factorio): add takeover dashboard UI"
```

---

### Task 7: End-to-End Verification

- [ ] **Step 1: Run all Factorio tests**

Run: `python -m pytest tests/test_agent_brain.py tests/test_bridge_api.py tests/test_bridge_config.py tests/test_curriculum_manager.py -v`
Expected: All tests PASS

- [ ] **Step 2: Verify CLI**

Run: `cd fleet && python lead_client.py factorio --help`

- [ ] **Step 3: Verify brain loads with all features**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); from factorio.agent_brain import AgentBrain, Directive, Preset; from factorio.bridge_config import BridgeConfig; from factorio.world_model import WorldModel; b = AgentBrain(BridgeConfig(current_phase=1), WorldModel()); print(f'Presets: {len(b.get_presets())}, Paused: {b.is_paused}, Directives: {len(b.get_directives())}')"`
Expected: `Presets: 6, Paused: False, Directives: 0`

- [ ] **Step 4: Final commit if needed**
