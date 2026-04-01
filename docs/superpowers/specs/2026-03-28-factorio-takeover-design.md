# Factorio Human Takeover Controls — Design Spec

**Date:** 2026-03-28
**Status:** Draft
**Scope:** Pause/resume, directive system (sticky + expiring), customizable preset buttons, action builder UI, dashboard + CLI integration
**Depends on:** Factorio Agent Loop (2026-03-28-factorio-agent-loop-design.md) — must be implemented first
**Out of scope:** Blueprint library, blueprint creation, factorioprints.com scraping (separate spec)

## Summary

Add human takeover controls to the autonomous Factorio agent. Two modes: (1) pause the brain and send actions directly ("grab the wheel"), (2) send directives that influence the LLM's next plan without pausing ("nudge the copilot"). Accessible from both the dashboard Factorio tab and CLI.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State location | AgentBrain | Brain owns reasoning state; pause/directives are reasoning concerns |
| Interaction surface | Dashboard + CLI | Dashboard for visual monitoring, CLI for scripting |
| Directive persistence | Sticky + expiring | Quick nudges auto-expire, strategic directives persist until cleared |
| Action builder backend | Existing `/api/command` | No new backend needed, UI convenience over existing endpoint |
| Presets storage | In-memory on AgentBrain | Not config-level, resets on bridge restart. Good enough for now. |
| Thread safety | `threading.Lock` on AgentBrain | API thread and bridge async loop both access brain state concurrently |

## Thread Safety

AgentBrain is accessed from two threads:
1. **Flask API thread** — reads/writes `_paused`, `_directives`, `_presets`, reads `_plan`
2. **Bridge async loop** (via `asyncio.to_thread`) — reads `_paused`, reads/mutates `_directives` (expiry), mutates `_plan`/`_plan_index`

**Solution:** Add `self._lock = threading.Lock()` to AgentBrain. All public methods that read or write shared state must acquire it. Internal methods called while the lock is held should not re-acquire (use `_lock` only at the public API boundary).

Methods requiring lock: `pause()`, `resume()`, `is_paused`, `add_directive()`, `remove_directive()`, `clear_directives()`, `get_directives()`, `get_presets()`, `add_preset()`, `remove_preset()`, `get_plan_status()`, `next_action()`, `report_result()`, `check_progress()`.

Note: `_generate_plan()` calls Ollama (blocking up to 60s). The lock must be released before the HTTP call and re-acquired after, to avoid blocking the API thread during LLM inference. Pattern:
```python
def next_action(self, state, events):
    with self._lock:
        if self._paused:
            return None
        # process events, check plan...
        if need_new_plan:
            # release lock for Ollama call
            pass
    # Outside lock: call Ollama
    plan = self._generate_plan(state)
    with self._lock:
        self._plan = plan
        # ... continue
```

## Components

### 1. Pause/Resume (AgentBrain)

**New state:**
```python
self._paused: bool = False
```

**New methods:**
```python
def pause(self) -> None:
    """Pause autonomous planning. Clears current plan so resume gets fresh state."""
    self._paused = True
    self._plan = []
    self._plan_index = 0
    log.info("Brain paused by human")

def resume(self) -> None:
    """Resume autonomous planning."""
    self._paused = False
    log.info("Brain resumed by human")

@property
def is_paused(self) -> bool:
    return self._paused
```

**Modified `next_action()`:**
```python
def next_action(self, state, events):
    # 1. Always process events (even when paused) to keep invalidation state fresh
    # ... existing event processing (invalidation, idle_assembler counting) ...

    # 2. THEN check pause — after events, before plan drain/generation
    if self._paused:
        return None

    # 3. Plan drain and generation (existing logic unchanged)
    # ...
```

Event processing (plan invalidation, idle assembler counting) runs regardless of pause state. This ensures that when the user resumes, the brain has up-to-date event context and won't use a stale plan.

**Bridge behavior when paused:**
- Tick loop continues running (state perception, world model updates, event detection, curriculum checks)
- Events are processed (plan invalidation fires normally — stale plans cleared even while paused)
- `next_action()` returns `None` after events — no autonomous actions execute
- Human command queue (`/api/command`) still drains and executes — this is "grab the wheel" mode
- Cadence still applies (bridge keeps ticking at configured rate)

### 2. Directive System (AgentBrain)

**Directive model:**
```python
@dataclass
class Directive:
    id: str              # first 8 chars of uuid4().hex
    text: str            # natural language directive
    sticky: bool         # True = persists until cleared
    plans_remaining: int # for non-sticky: countdown per plan cycle. -1 for sticky.
    created_at: float    # time.monotonic()
```

**New state:**
```python
self._directives: list[Directive] = []
```

**New methods:**
```python
def add_directive(self, text: str, sticky: bool = False, plans: int = 1) -> str:
    """Add a human directive. Returns directive ID."""
    directive = Directive(
        id=uuid.uuid4().hex[:8],
        text=text,
        sticky=sticky,
        plans_remaining=-1 if sticky else plans,
        created_at=time.monotonic(),
    )
    self._directives.append(directive)
    log.info("Directive added [%s]: %s (sticky=%s, plans=%s)", directive.id, text, sticky, plans)
    return directive.id

def remove_directive(self, directive_id: str) -> bool:
    """Remove a directive by ID. Returns True if found."""
    before = len(self._directives)
    self._directives = [d for d in self._directives if d.id != directive_id]
    return len(self._directives) < before

def clear_directives(self) -> int:
    """Remove all directives. Returns count cleared."""
    count = len(self._directives)
    self._directives = []
    return count

def get_directives(self) -> list[dict]:
    """Return active directives for API/dashboard."""
    return [
        {"id": d.id, "text": d.text, "sticky": d.sticky,
         "plans_remaining": d.plans_remaining}
        for d in self._directives
    ]
```

**Directive expiry — in `_generate_plan()`:**
After a plan is successfully generated, decrement non-sticky directive counters and remove expired ones:
```python
# After plan generated successfully:
expired = []
for d in self._directives:
    if not d.sticky:
        d.plans_remaining -= 1
        if d.plans_remaining <= 0:
            expired.append(d.id)
self._directives = [d for d in self._directives if d.id not in expired]
if expired:
    log.info("Directives expired: %s", expired)
```

**Prompt injection — in `_build_prompt()`:**
If directives are active, inject a section between state and objective:
```python
if self._directives:
    lines.append("# Human Directives (PRIORITY — follow these)")
    for d in self._directives:
        tag = "sticky" if d.sticky else f"{d.plans_remaining} plan(s) left"
        lines.append(f"- [{tag}] {d.text}")
    lines.append("")
```

This block is inserted into the `lines` list in `_build_prompt()` after the state markdown section and before the "# Current Objective" line (approximately after the empty-line separator following state_md).

The "PRIORITY" label ensures the LLM weighs directives over its own reasoning when they conflict.

**Directives added while paused:** Non-sticky directives with low `plans` counts will fire on the first plan after resume, even if added long ago. This is intentional — the user chose to queue a directive while paused. If the directive is no longer relevant, the user should clear it before resuming.

### 3. Preset Buttons (AgentBrain)

**Preset model:**
```python
@dataclass
class Preset:
    id: str        # first 8 chars of uuid4().hex
    label: str     # button text: "Focus Power"
    text: str      # directive text: "Focus on building power infrastructure"
    sticky: bool   # default sticky setting when clicked
    plans: int     # default plans count when clicked
```

**Default presets** (loaded in `__init__`):
```python
DEFAULT_PRESETS = [
    {"label": "Focus Power", "text": "Focus on building power infrastructure — boilers, steam engines, electric poles", "sticky": False, "plans": 3},
    {"label": "Expand Mining", "text": "Expand mining operations — more drills on ore patches", "sticky": False, "plans": 3},
    {"label": "Fix Bottlenecks", "text": "Identify and fix production bottlenecks — check idle assemblers and full outputs", "sticky": False, "plans": 2},
    {"label": "Scale Smelting", "text": "Scale up smelting — more furnaces, better belt throughput", "sticky": False, "plans": 3},
    {"label": "Build Defenses", "text": "Build defensive structures — walls and turrets around the perimeter", "sticky": False, "plans": 3},
    {"label": "Optimize Layout", "text": "Optimize factory layout — reduce belt length, improve throughput", "sticky": False, "plans": 2},
]
```

**New state:**
```python
self._presets: list[Preset] = [Preset(id=uuid.uuid4().hex[:8], **p) for p in DEFAULT_PRESETS]
```

**New methods:**
```python
def get_presets(self) -> list[dict]:
    """Return preset buttons for dashboard."""
    return [{"id": p.id, "label": p.label, "text": p.text,
             "sticky": p.sticky, "plans": p.plans} for p in self._presets]

def add_preset(self, label: str, text: str, sticky: bool = False, plans: int = 1) -> str:
    """Add a custom preset button. Returns preset ID."""
    preset = Preset(id=uuid.uuid4().hex[:8], label=label, text=text, sticky=sticky, plans=plans)
    self._presets.append(preset)
    return preset.id

def remove_preset(self, preset_id: str) -> bool:
    """Remove a preset by ID. Returns True if found."""
    before = len(self._presets)
    self._presets = [p for p in self._presets if p.id != preset_id]
    return len(self._presets) < before
```

Clicking a preset in the dashboard calls `add_directive(preset.text, preset.sticky, preset.plans)` — presets are just convenience wrappers over the directive system.

### 4. Bridge API Endpoints (bridge_api.py)

**New endpoints** (all require `_brain` to be set, return 503 if not):

```
POST /api/pause          → brain.pause()              → {"paused": true}
POST /api/resume         → brain.resume()             → {"paused": false}

POST /api/directive       → {text, sticky?, plans?}    → {"id": "abc12345"}
DELETE /api/directive/<id> → brain.remove_directive(id) → {"removed": true|false}
GET /api/directives       → brain.get_directives()     → [{id, text, sticky, plans_remaining}, ...]
DELETE /api/directives     → brain.clear_directives()   → {"cleared": N}

GET /api/presets         → brain.get_presets()         → [{id, label, text, sticky, plans}, ...]
POST /api/preset         → {label, text, sticky?, plans?} → {"id": "def67890"}
DELETE /api/preset/<id>  → brain.remove_preset(id)     → {"removed": true|false}
```

**Modified existing endpoints:**
- `GET /api/status` — add `paused` field to response dict (read `_brain.is_paused` inline, do NOT mutate `_bridge_status`): `{**_bridge_status, "paused": _brain.is_paused if _brain else False}`
- `GET /api/plan` — add `directives` field: `brain.get_directives()`

### 5. CLI (lead_client.py)

New `factorio` subcommand group:

```
lead_client.py factorio pause
lead_client.py factorio resume
lead_client.py factorio status                          # shows paused, plan, directives, phase

lead_client.py factorio directive "focus on power"      # non-sticky, 1 plan
lead_client.py factorio directive --sticky "focus on power"
lead_client.py factorio directive --plans 5 "fix belts"
lead_client.py factorio directives                      # list active
lead_client.py factorio clear-directive <id>
lead_client.py factorio clear-directives                # clear all

lead_client.py factorio place stone-furnace 5 10        # action builder shortcuts
lead_client.py factorio place stone-furnace 5 10 --dir south
lead_client.py factorio craft iron-gear-wheel 10
lead_client.py factorio research automation
lead_client.py factorio move 5 10
```

CLI commands hit the bridge API (same endpoints as dashboard). Bridge port read from `fleet.toml [factorio] bridge_port`.

### 6. Dashboard UI (dashboard.html)

All changes within the existing `section-factorio` div.

**Control bar** (top, next to Refresh button):
- Pause/Resume toggle: `<button onclick="toggleFactorioPause()">` — green "Running" / yellow "Paused"
- Brain status text: "Executing step 3/12" / "Planning..." / "Paused" / "Cooldown"
- Active directives badge: "(2 directives)"

**Directives panel** (new div, below existing stat cards):
- Text input + sticky checkbox + plans dropdown (1/3/5/sticky) + Send button
- Preset button row: styled as pill buttons, each calls `sendFactorioDirective(preset.text, preset.sticky, preset.plans)`
- Small "+" to add custom preset (modal or inline form: label + text + sticky + plans)
- Active directives list: each shows text + tag (sticky/N plans left) + X dismiss button

**Action builder** (collapsible div, below directives):
- Action type `<select>`: place, craft, research, move, remove, set_recipe, connect
- Dynamic form fields that change based on selected action type
- Send button → POSTs to `/api/command`
- Result display: shows last action result (success/error)

**JS functions:**
```
toggleFactorioPause()        — POST /api/pause or /api/resume based on current state
sendFactorioDirective(text, sticky, plans) — POST /api/directive
removeFactorioDirective(id)  — DELETE /api/directive/<id>
sendFactorioAction()         — reads form, POSTs to /api/command
addFactorioPreset()          — POST /api/preset
removeFactorioPreset(id)     — DELETE /api/preset/<id>
```

Existing `loadFactorio()` function extended to also fetch `/api/directives`, `/api/presets`, and `paused` status, updating the UI accordingly.

## Modified `get_plan_status()`

Update to include paused state and directives:
```python
def get_plan_status(self) -> dict:
    return {
        "plan": list(self._plan),
        "plan_index": self._plan_index,
        "plan_count": self._plan_count,
        "planning": False,
        "paused": self._paused,
        "consecutive_failures": self._consecutive_failures,
        "directives": self.get_directives(),
    }
```

## File Summary

| File | Status | Change |
|------|--------|--------|
| `fleet/factorio/agent_brain.py` | **Modify** | Directive/Preset dataclasses, pause/resume, directive CRUD, prompt injection, expiry logic |
| `fleet/factorio/bridge_api.py` | **Modify** | 8 new endpoints, update status/plan responses |
| `fleet/templates/dashboard.html` | **Modify** | Control bar, directives panel, action builder in Factorio section |
| `fleet/lead_client.py` | **Modify** | `factorio` subcommand group (pause, resume, directive, action shortcuts) |
| `tests/test_agent_brain.py` | **Modify** | Tests for pause, directives, presets, expiry, prompt injection |
| `tests/test_bridge_api.py` | **Modify** | Tests for new endpoints |

## Out of Scope (Future Specs)

- **Blueprint library** — scrape factorioprints.com (most recent, most favorited, categories), store in knowledge, deploy via RCON
- **Blueprint creation** — agent creates reusable blueprints from entity placements, compares against library
- **SSE push** — real-time dashboard updates (currently uses polling via loadFactorio)
