# Queue ↔ Factorio Integration — Design Spec

**Date:** 2026-03-28
**Status:** Draft
**Approach:** Brain Upgrade — extend AgentBrain to accept prioritized external plans from fleet workers

## Problem

The fleet task queue and Factorio bridge are fully independent systems. Workers can execute Factorio skills (`factorio_observe`, `factorio_act`, `factorio_plan`, `factorio_train`) but there is no coordination between worker-generated plans and the brain's autonomous planning. No mechanism exists to dedicate workers to Factorio analysis or to merge external plans with the brain's own plan queue. Transitioning between "fleet doing normal tasks" and "fleet helping Factorio" requires manual task creation with no priority integration.

## Design Decisions

- **Hybrid plan/directive model**: Fleet workers can push full action plans (high confidence) or softer directives (lower confidence) to the brain. The brain interprets directives when generating its next plan.
- **Priority-based merge with brain veto**: Plans compete on a priority heap. The brain can reject conflicting external plans, but human commands bypass veto.
- **Switchable worker reservation**: Default is opportunistic (Factorio tasks in normal queue). A Focus toggle reserves N workers with Factorio skill affinity and auto-generates analysis tasks.

## Architecture

### Priority Plan Queue

Replace `AgentBrain._plan` (flat list) with a priority-based plan queue that accepts submissions from multiple sources.

**Sources:**
| Source | Default Priority | Notes |
|--------|-----------------|-------|
| Human (dashboard/CLI) | CRITICAL (100) | Bypasses brain veto |
| Fleet worker (plan) | HIGH (75) | Subject to brain veto |
| Fleet worker (directive) | HIGH (75) | Injected into planning prompt, not plan queue |
| Local Ollama (brain) | NORMAL (50) | Brain's own generated plans |
| Speculative/low-confidence | LOW (25) | Queue behind everything |

**Priority bands:** CRITICAL (100), HIGH (75), NORMAL (50), LOW (25). Custom values allowed within 0-100.

### Conflict Resolution

Three rules govern how incoming plans interact with the current plan:

1. **Preemption**: Incoming plan with priority ≥ current + 25 preempts. Current plan is shelved (not deleted) and can resume after the preempting plan completes.
2. **Queuing**: Plans within ±25 priority of current plan queue behind it. FIFO within same priority band. Current plan continues undisturbed.
3. **Brain veto**: Brain can reject external plans that conflict with active curriculum objectives or directives. Rejected plans are logged with reason. Human commands (CRITICAL) bypass veto.

### Plan Submission Schema

```python
@dataclass
class PlanSubmission:
    actions: list[dict]        # [{action: "craft", item: "stone-furnace", count: 4}, ...]
    priority: int              # 0-100
    source: str                # "worker-2", "brain", "human"
    source_type: str           # "worker" | "brain" | "human"
    rationale: str             # why this plan was generated
    confidence: float          # 0.0-1.0, used for logging/display
    # Note: preemption is determined automatically by priority rules (incoming >= current + 25)
```

### Directive Schema

Extends the existing `Directive` dataclass in `agent_brain.py` (which has `id`, `text`, `sticky`, `plans_remaining`, `created_at`) with two new fields:

```python
@dataclass
class Directive:
    id: str
    text: str
    sticky: bool
    plans_remaining: int | None
    created_at: float
    priority: int = 50         # NEW — affects ordering in planning prompt
    source: str = "human"      # NEW — "human", "worker-2", etc.
```

The existing `add_directive(text, sticky, plans)` method is extended to accept optional `priority` and `source` parameters. `_build_prompt()` is updated to sort directives by priority (descending) before injecting into the planning prompt.

Directives do not enter the plan queue. They are injected into the brain's planning prompt context, ordered by priority. The brain interprets them when generating its next plan.

## Focus Toggle

### Modes

- **Opportunistic (default)**: Factorio tasks enter the normal fleet queue with standard priority. Workers pick them up like any other skill. No workers are reserved.
- **Focus ON**: Configurable number of workers (default: 2) are reserved for Factorio tasks. Supervisor auto-generates recurring `factorio_analyze` tasks at HIGH priority. Reserved workers set `affinity_skills=["factorio_*"]` in their claim loop but still fall back to normal tasks if no Factorio work is pending.

### Activation

- **Dashboard**: Toggle switch on Factorio page top bar
- **CLI**: `lead_client.py factorio focus on [--workers N]` / `lead_client.py factorio focus off`
- **API**: `POST /api/focus {on: bool, workers: int}`
- **Storage**: Flag file `.factorio_focus.json` with `{on: true, workers: ["coder_1", "coder_2"]}` — consistent with existing `.queue_paused` pattern. Workers and supervisor read this on each loop iteration.

### Task Generation (Focus ON)

When Focus is enabled, the supervisor generates:
- `factorio_analyze` — periodic state assessment (every N bridge ticks, configurable). Fetches game state, calls Claude/Gemini with curriculum context, then makes a confidence-gate decision: high confidence → `submit_plan()`, lower confidence → `submit_directive()`.
- `factorio_plan` — triggered on significant game events (entity destroyed, research complete, resource depleted). Strategic replanning with full state context.

Both task types are created at HIGH priority (75) and tagged `auto_generated: true`.

### Worker Reservation

Reserved workers modify their claim loop:
```python
# In worker.py claim loop
# Read focus state from .factorio_focus.json (cached, re-read every 5s)
focus = _read_focus_state()  # returns {on: bool, workers: ["coder_1", ...]} or None
if focus and focus["on"] and worker_name in focus["workers"]:
    task = db.claim_task(role, affinity_skills=["factorio_observe", "factorio_analyze",
                                                  "factorio_plan", "factorio_act", "factorio_train"])
    if not task:
        task = db.claim_task(role)  # fallback to normal queue — no idle waste
```

**Communication mechanism:** Supervisor writes `.factorio_focus.json` when Focus is toggled. Workers read it on each claim loop iteration (cached with 5s TTL to avoid filesystem thrash). When Focus is toggled off, reserved workers finish their current task normally — affinity simply stops applying on the next claim cycle.

**Worker selection:** Supervisor picks the N least-busy workers by checking `agents` table heartbeats. Named workers are written to the JSON file. If a reserved worker goes offline, supervisor auto-selects a replacement on next health check.

Non-reserved workers can still pick up Factorio tasks opportunistically.

## New Skill: `factorio_analyze`

```python
SKILL_NAME = "factorio_analyze"
DESCRIPTION = "Analyze Factorio game state and submit plans or directives to the brain"
REQUIRES_NETWORK = True  # calls Claude/Gemini for analysis

def run(task: dict, context: dict) -> dict:
    # 1. Fetch game state from bridge API
    # 2. Fetch brain's current plan + objectives + curriculum phase
    # 3. Call Claude/Gemini with state + curriculum context
    # 4. Confidence gate:
    #    - High confidence (>0.7): submit_plan() with full action sequence
    #    - Lower confidence: submit_directive() with high-level guidance
    # 5. Return analysis result for logging
```

## API Extensions

### Bridge API (port 27016) — plan/directive management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/plan/submit` | Submit prioritized plan from fleet worker |
| `GET` | `/api/plan/queue` | View pending plan queue + current active plan |
| `GET` | `/api/plan/history` | Completed/rejected plans with reasons |
| `POST` | `/api/directive/submit` | Submit prioritized directive from fleet worker |
| `GET` | `/api/focus` | Current Focus state (read-only mirror) |

### Dashboard API (port 5555) — focus control + proxies

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/factorio/focus` | Toggle Focus mode `{on: bool, workers: int}` — supervisor acts on this |
| `GET` | `/api/factorio/focus` | Current Focus state |
| `GET` | `/api/factorio/plans` | Proxy to bridge `/api/plan/queue` |
| `GET` | `/api/factorio/plan-history` | Proxy to bridge `/api/plan/history` |

The Focus toggle lives on the dashboard because it affects the supervisor (worker reservation, task generation), not the bridge. The bridge exposes a read-only `/api/focus` for display.

### `POST /api/plan/submit` Request

```json
{
  "actions": [{"action": "craft", "item": "stone-furnace", "count": 4}],
  "priority": 75,
  "source": "worker-2",
  "source_type": "worker",
  "rationale": "Iron smelting capacity needed for automation research",
  "confidence": 0.85
}
```

### `POST /api/plan/submit` Response

```json
{
  "status": "accepted",        // "accepted" | "queued" | "rejected" | "preempted_current"
  "plan_id": "p-1234",
  "position": 0,               // queue position (0 = will execute next)
  "reason": null                // rejection reason if rejected
}
```

## Dashboard UX

### Factorio Page

- **Focus toggle**: Top bar, next to bridge status. Shows reserved worker count when active.
- **Plan Queue panel**: New section showing active plan (with step progress), queued plans (with source/priority), and recently rejected plans (with rejection reason). Replaces or augments existing Game State section.

### Queue Page

- **Focus status badge**: Top-right, shows "🎮 Focus: ON (2 workers)" when active.
- **Factorio task highlighting**: 🎮 icon prefix on Factorio skill tasks. "auto-generated" label on Focus-mode tasks.

### Fleet Page

- **Affinity column**: New column in Agent Roster table. Shows "🎮 focus" badge on reserved workers.

### CLI Enhancements

- `lead_client.py factorio focus on [--workers N]` — enable Focus mode
- `lead_client.py factorio focus off` — disable Focus mode
- `lead_client.py factorio plans` — show plan queue (active + queued + recent history)
- `lead_client.py factorio status` — enhanced to include Focus state and plan queue summary

## AgentBrain Changes

### New Methods

```python
def submit_plan(self, submission: PlanSubmission) -> dict:
    """Accept or reject an external plan based on priority and conflict rules."""

def submit_directive(self, directive: DirectiveSubmission) -> None:
    """Add a directive to the planning prompt context."""

def _check_conflict(self, incoming: PlanSubmission) -> tuple[bool, str]:
    """Check if incoming plan conflicts with active objectives. Returns (conflicts, reason).

    V1 heuristic: always returns (False, '') — accept all plans. Priority rules handle ordering.
    Future: reject plans that target resources consumed by active plan or destroy curriculum-required entities.
    """

def _shelve_current_plan(self) -> None:
    """Save current plan for potential resumption after preempting plan completes."""

def _restore_shelved_plan(self) -> None:
    """Restore a previously shelved plan if still valid."""
```

### Modified Methods

- `next_action(state, events)` — drain from priority queue instead of flat list. Check for shelved plan restoration when current plan completes.
- `_build_prompt(state, events)` — inject active directives (ordered by priority) into planning prompt context.

### State Additions

```python
self._plan_queue: list[tuple] = []              # heapq min-heap, entries are (-priority, seq, PlanSubmission)
self._shelved_plan: PlanSubmission | None = None
self._active_directives: list[DirectiveSubmission] = []
self._plan_history: deque[dict] = deque(maxlen=50)  # completed/rejected log
self._plan_seq: int = 0                            # monotonic counter for FIFO tiebreaking
MAX_PLAN_QUEUE_DEPTH: int = 20                     # reject lowest-priority plans when full
```

## Known Limitations (V1)

- **Bridge restart loses queued plans.** All in-memory state (`_plan_queue`, `_shelved_plan`, `_active_directives`) is lost on bridge restart. Workers that submitted plans get no notification. Future improvement: persist plan queue to a JSON file (similar to `hw_state.json`).
- **Conflict detection is a no-op in V1.** `_check_conflict()` always accepts. Priority rules and brain veto provide ordering, not semantic conflict detection.
- **No curriculum-aware analysis.** `factorio_analyze` uses a generic analysis prompt. Curriculum-specific prompts are deferred.

## Implementation Conventions

- All HTTP calls in skills must include `timeout=` (10-30s typical)
- All `subprocess.Popen` calls must include `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)`
- Task insertion in supervisor must use `db._retry_write()`
- Skills use lazy imports (`import db` inside functions)
- Directive submission endpoint: `POST /api/directive/submit` on bridge, with `priority` and `source` fields

## Files to Modify

| File | Changes |
|------|---------|
| `fleet/factorio/agent_brain.py` | Priority plan queue, submit_plan/directive, conflict detection, shelving |
| `fleet/factorio/bridge_api.py` | 5 new endpoints (plan/submit, plan/queue, plan/history, focus, focus GET) |
| `fleet/factorio/bridge.py` | Wire focus state to supervisor, pass to brain |
| `fleet/factorio/bridge_config.py` | Add `factorio_focus`, `focus_workers` config fields |
| `fleet/skills/factorio_analyze.py` | New skill — state analysis + plan/directive submission |
| `fleet/skills/factorio_plan.py` | Update to use submit_plan() instead of direct bridge command |
| `fleet/skills/factorio_act.py` | Update to use submit_plan() for action sequences |
| `fleet/worker.py` | Affinity-based claiming for reserved workers |
| `fleet/supervisor.py` | Focus toggle handling, auto-generate factorio_analyze tasks |
| `fleet/lead_client.py` | New CLI commands (focus, plans) |
| `fleet/dashboard.py` | Focus toggle endpoint (`/api/factorio/focus`), plan queue proxies, UI badge updates |
| `fleet/fleet.toml` | New config keys: `focus_workers_default`, `analyze_interval` |

## Out of Scope

- Dashboard frontend HTML/JS changes for the Factorio page plan queue panel (separate task, will be part of the C-phase UX unification)
- Multi-agent Factorio (multiple brains competing) — single brain, multiple advisors for now
- Bridge-to-fleet task creation (bridge requesting fleet workers to do non-Factorio work)
- Curriculum-aware plan generation in `factorio_analyze` (deferred — uses generic analysis prompt initially)
