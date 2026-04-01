# API Governance & Graph Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a central API gate that makes the fleet local-only by default, with explicit session-based API enablement, hard budget enforcement, and universe graph visibility for all data flows.

**Architecture:** New `api_gate.py` module owns all external API access decisions. `call_complex()` in `_models.py` and `providers.py` route through the gate. Dashboard and CLI provide session controls. Universe graph gets fixed to show all registered nodes and gains transient API call visualization.

**Tech Stack:** Python 3.11+, Flask (dashboard endpoints), Cytoscape.js (graph), threading.Lock (concurrency), collections.deque (ring buffer), tomlkit (config)

**Spec:** `docs/superpowers/specs/2026-03-25-api-governance-design.md`

---

## File Structure

| File | Responsibility | New/Modified |
|------|---------------|--------------|
| `fleet/api_gate.py` | Central gate: state, check(), enable(), disable(), ring buffer, SSE alerts | New |
| `fleet/providers.py` | Wire gate into `_call_*()` functions, configurable fallback chain, `purpose` param | Modified |
| `fleet/skills/_models.py` | Wire gate into `call_complex()`, add `purpose` param | Modified |
| `fleet/skills/_review.py` | Refactor to use `call_complex(purpose="review")`, fail-hold | Modified |
| `fleet/skills/marketing.py` | Replace direct `anthropic.Anthropic()` with `call_complex()` | Modified |
| `fleet/fleet.toml` | Add `[api_gate]` section, change `[review]` and `[budgets]` defaults | Modified |
| `fleet/views_blueprint.py` | Fix heartbeat filters, add API call nodes to universe graph | Modified |
| `fleet/views/fleet-overview.json` | Add all sources | Modified |
| `fleet/templates/dashboard.html` | Embed universe graph in Pipeline tab, add API gate controls, cost summary | Modified |
| `fleet/dashboard.py` | Add `/api/gate/*` endpoints | Modified |
| `fleet/lead_client.py` | Add `api` subcommand (enable/disable/status) | Modified |
| `fleet/smoke_test.py` | Add `test_no_direct_api_imports`, `test_api_gate` | Modified |

---

## Task 1: Core API Gate Module

**Files:**
- Create: `fleet/api_gate.py`

- [ ] **Step 1: Create `api_gate.py` with GateState, check(), enable(), disable()**

```python
"""
API Gate — central authority for all external API calls.

Safe by default: gate OFF, $0 budget, local-only.
Thread-safe: all state mutations under _gate_lock.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("api_gate")

_gate_lock = threading.Lock()


@dataclass
class APICallRecord:
    timestamp: float
    provider: str
    skill: str
    agent: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    purpose: str  # "task" | "review" | "evolution"
    fallback_from: Optional[str] = None


@dataclass
class GateState:
    enabled: bool = False
    session_budget_usd: float = 0.00
    session_spend_usd: float = 0.00
    drain_mode: str = "graceful"  # "graceful" | "hard"
    expires_at: Optional[float] = None  # epoch timestamp
    allowed_providers: set = field(default_factory=set)


_state = GateState()
_ring: collections.deque[APICallRecord] = collections.deque(maxlen=200)

# SSE subscribers for gate events (set by dashboard)
_event_subscribers: list = []


def _is_expired() -> bool:
    return _state.expires_at is not None and time.time() > _state.expires_at


def check(provider: str, purpose: str = "task", config: dict | None = None) -> tuple[bool, str]:
    """Check if an API call is allowed. Returns (allowed, reason).

    Must be called before every external API request.
    Falls back gracefully — gate bugs never block local operation.
    """
    try:
        if config:
            from config import is_offline, is_air_gap
            if is_offline(config) or is_air_gap(config):
                return False, "offline_mode or air_gap_mode active"

        with _gate_lock:
            if _is_expired():
                _state.enabled = False
                _push_event("gate_expired", "Session TTL expired — API disabled")
                return False, "session expired"

            if not _state.enabled:
                return False, "gate disabled"

            if provider not in _state.allowed_providers:
                return False, f"provider '{provider}' not enabled"

            if _state.session_spend_usd >= _state.session_budget_usd:
                if _state.drain_mode == "hard":
                    _push_event("budget_hit", f"Hard stop — ${_state.session_spend_usd:.2f} / ${_state.session_budget_usd:.2f}")
                    _state.enabled = False
                    return False, "budget exceeded (hard stop)"
                else:
                    _push_event("budget_hit", f"Graceful drain — ${_state.session_spend_usd:.2f} / ${_state.session_budget_usd:.2f}")
                    _state.enabled = False
                    return False, "budget exceeded (graceful drain)"

            # Budget 80% warning
            if (_state.session_budget_usd > 0 and
                    _state.session_spend_usd / _state.session_budget_usd >= 0.8):
                _push_event("budget_warning", f"80% budget used — ${_state.session_spend_usd:.2f} / ${_state.session_budget_usd:.2f}")

            return True, "allowed"
    except Exception:
        log.warning("api_gate.check() error — falling back to local", exc_info=True)
        return False, "gate error (safe fallback)"


def record_call(provider: str, skill: str, agent: str,
                input_tokens: int, output_tokens: int, cost_usd: float,
                latency_ms: int, purpose: str = "task",
                fallback_from: str | None = None):
    """Record a completed API call to the ring buffer and update spend."""
    record = APICallRecord(
        timestamp=time.time(), provider=provider, skill=skill, agent=agent,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost_usd, latency_ms=latency_ms, purpose=purpose,
        fallback_from=fallback_from,
    )
    with _gate_lock:
        _ring.append(record)
        _state.session_spend_usd += cost_usd

    # Fallback alert (unless purpose=review)
    if fallback_from and purpose != "review":
        _push_event("fallback", f"{fallback_from} -> {provider} (skill={skill})")


def enable(budget: float, providers: list[str],
           ttl_hours: float | None = None,
           drain_mode: str = "graceful") -> dict:
    """Enable the API gate for a session."""
    with _gate_lock:
        _state.enabled = True
        _state.session_budget_usd = budget
        _state.session_spend_usd = 0.0
        _state.drain_mode = drain_mode
        _state.allowed_providers = set(providers)
        _state.expires_at = (time.time() + ttl_hours * 3600) if ttl_hours else None
    _push_event("gate_enabled", f"Budget ${budget:.2f}, providers={providers}")
    return status()


def disable() -> dict:
    """Disable the API gate."""
    with _gate_lock:
        _state.enabled = False
    _push_event("gate_disabled", "API gate disabled")
    return status()


def set_drain_mode(mode: str) -> dict:
    """Set drain mode: 'graceful' or 'hard'."""
    with _gate_lock:
        _state.drain_mode = mode
    return status()


def status() -> dict:
    """Return current gate state."""
    with _gate_lock:
        return {
            "enabled": _state.enabled,
            "budget": _state.session_budget_usd,
            "spent": round(_state.session_spend_usd, 4),
            "remaining": round(max(0, _state.session_budget_usd - _state.session_spend_usd), 4),
            "drain_mode": _state.drain_mode,
            "providers": sorted(_state.allowed_providers),
            "expires_at": _state.expires_at,
            "ttl_remaining_s": round(_state.expires_at - time.time()) if _state.expires_at else None,
            "ring_size": len(_ring),
        }


def get_ring(limit: int = 200) -> list[dict]:
    """Return recent API calls from the ring buffer."""
    with _gate_lock:
        items = list(_ring)
    return [
        {
            "timestamp": r.timestamp, "provider": r.provider, "skill": r.skill,
            "agent": r.agent, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "cost_usd": r.cost_usd,
            "latency_ms": r.latency_ms, "purpose": r.purpose,
            "fallback_from": r.fallback_from,
        }
        for r in items[-limit:]
    ]


def _push_event(event_type: str, detail: str):
    """Push SSE event to dashboard subscribers."""
    log.info("api_gate: %s — %s", event_type, detail)
    for cb in _event_subscribers:
        try:
            cb(event_type, detail)
        except Exception:
            pass
```

- [ ] **Step 2: Verify module imports cleanly**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "import api_gate; print(api_gate.status())"`
Expected: `{'enabled': False, 'budget': 0.0, 'spent': 0, ...}`

- [ ] **Step 3: Commit**

```bash
git add fleet/api_gate.py
git commit -m "feat: add api_gate module — central API access control"
```

---

## Task 2: Wire Gate into providers.py

**Files:**
- Modify: `fleet/providers.py` (lines 510, 620-670, 671-722, 724-772)

- [ ] **Step 1: Replace hardcoded FALLBACK_CHAIN with config-driven**

At line 510, replace:
```python
FALLBACK_CHAIN = ["claude", "gemini", "local"]
```
With:
```python
def _get_fallback_chain(provider: str, config: dict) -> list[str]:
    """Get fallback chain for a provider from fleet.toml [api_gate]."""
    gate_cfg = config.get("api_gate", {})
    providers_cfg = gate_cfg.get("providers", {})
    provider_cfg = providers_cfg.get(provider, {})
    chain = provider_cfg.get("fallback", gate_cfg.get("fallback_chain", ["local"]))
    return chain
```

- [ ] **Step 2: Add gate check + ring buffer recording to `_call_claude()`**

At the top of `_call_claude()` (line 620), add gate check. After successful response, add `record_call()`. Add `purpose` parameter to signature:

```python
def _call_claude(system, user, models, max_tokens, cache_system=True,
                 skill_name="unknown", task_id=None, agent_name=None,
                 purpose="task", config=None) -> str:
    import api_gate
    allowed, reason = api_gate.check("claude", purpose, config)
    if not allowed:
        raise RuntimeError(f"API gate blocked claude: {reason}")
    # ... existing implementation ...
    # After resp = client.messages.create(...), before return:
    api_gate.record_call(
        provider="claude", skill=skill_name, agent=agent_name or "",
        input_tokens=getattr(resp.usage, 'input_tokens', 0),
        output_tokens=getattr(resp.usage, 'output_tokens', 0),
        cost_usd=calculate_cost(resp.usage, model),
        latency_ms=int((time.time() - _start) * 1000),
        purpose=purpose,
    )
```

- [ ] **Step 3: Same for `_call_gemini()` and `_call_minimax()`**

Add `purpose` param and gate check to both functions following the same pattern.

- [ ] **Step 4: Verify providers.py still imports cleanly**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "import providers; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add fleet/providers.py
git commit -m "feat: wire api_gate into provider call functions"
```

---

## Task 3: Wire Gate into _models.py call_complex()

**Files:**
- Modify: `fleet/skills/_models.py` (lines 63-188)

- [ ] **Step 1: Add `purpose` parameter to call_complex()**

Update signature at line 63:
```python
def call_complex(system: str, user: str, config: dict, max_tokens: int = 2048,
                 cache_system: bool = False, skill_name: str = "unknown",
                 task_id=None, agent_name=None, purpose: str = "task") -> str:
```

- [ ] **Step 2: Add gate check before provider dispatch**

After the offline_mode check (~line 70), add:
```python
    # API gate check — if gate disabled, force local
    if provider != "local":
        import api_gate
        allowed, reason = api_gate.check(provider, purpose, config)
        if not allowed:
            log.info("api_gate rejected %s for %s: %s — using local", provider, skill_name, reason)
            provider = "local"
```

- [ ] **Step 3: Pass `purpose` and `config` through to `_call_claude()`, `_call_gemini()`, `_call_minimax()`**

In the provider dispatch section (~lines 130-170), add `purpose=purpose, config=config` to each `_call_*()` invocation.

- [ ] **Step 4: Verify call_complex still works**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "from skills._models import call_complex; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add fleet/skills/_models.py
git commit -m "feat: wire api_gate into call_complex() routing"
```

---

## Task 4: Refactor _review.py to Use call_complex()

**Files:**
- Modify: `fleet/skills/_review.py` (lines 42-85, 118-141)

- [ ] **Step 1: Replace `_review_claude()` and `_review_gemini()` with call_complex()**

Replace lines 118-141:
```python
def _review_api(system, user, review_cfg, config):
    """Review via configured API provider, routed through call_complex()."""
    from skills._models import call_complex
    return call_complex(
        system=system, user=user, config=config,
        max_tokens=512, skill_name="_review",
        purpose="review",
    )
```

- [ ] **Step 2: Update the `run()` function to use `_review_api()` and change fail-open to fail-hold**

In the `run()` function (~line 42-85), replace the provider routing:
```python
    try:
        provider = review_cfg.get("provider", "local")
        if provider == "local":
            raw = _review_local(system, user, config)
        else:
            raw = _review_api(system, user, review_cfg, config)
    except Exception as e:
        log.warning("Review failed: %s — holding for human review", e)
        return {"verdict": "HOLD", "critique": f"Review error (held for human): {e}", "confidence": 0.0}
```

- [ ] **Step 3: Delete `_review_claude()` and `_review_gemini()` functions**

Remove lines 118-141 entirely (the two direct-API functions).

- [ ] **Step 4: Verify review module imports**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "from skills._review import run; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add fleet/skills/_review.py
git commit -m "feat: refactor _review.py to use call_complex(purpose='review'), fail-hold"
```

---

## Task 5: Refactor marketing.py Direct API Bypass

**Files:**
- Modify: `fleet/skills/marketing.py` (lines 45-74)

- [ ] **Step 1: Replace direct anthropic.Anthropic() with call_complex()**

Find the `_write_copy()` function (around line 45) and replace the direct client usage:

```python
# BEFORE (direct bypass):
# import anthropic
# client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
# resp = client.messages.create(model=model, ...)

# AFTER (through governance):
from skills._models import call_complex
result = call_complex(
    system=system_prompt,
    user=user_prompt,
    config=config,
    max_tokens=max_tokens,
    skill_name="marketing",
    purpose="task",
)
```

- [ ] **Step 2: Remove `import anthropic` from the file**

- [ ] **Step 3: Verify**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "from skills.marketing import run; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add fleet/skills/marketing.py
git commit -m "fix: route marketing.py through call_complex() instead of direct API"
```

---

## Task 6: Update fleet.toml Defaults

**Files:**
- Modify: `fleet/fleet.toml`

- [ ] **Step 1: Add `[api_gate]` section**

Add after the `[models]` section (~line 90):
```toml
# -- API Gate -----------------------------------------------------------------
# Central API access control. Safe by default: gate OFF, $0 budget, local-only.
# Enable via: lead_client.py api enable --budget 2.00 --providers claude
[api_gate]
enabled = false                # Master switch -- default OFF (local-only)
default_budget = 0.00          # Session budget in USD ($0 = must specify on enable)
drain_mode = "graceful"        # "graceful" = finish in-flight | "hard" = immediate stop
fallback_chain = ["local"]     # Default fallback: local only (no paid cascade)

[api_gate.providers.claude]
enabled = false
fallback = ["local"]

[api_gate.providers.gemini]
enabled = false
fallback = ["local"]

[api_gate.providers.minimax]
enabled = false
fallback = ["local"]
```

- [ ] **Step 2: Change review default to local**

At line 84, change:
```toml
provider = "local"             # "api" = call_complex | "local" = Ollama (CHANGED: was "api")
```

- [ ] **Step 3: Change budget enforcement default to block**

At line 255, change:
```toml
enforcement = "block"          # "warn" | "throttle" | "block" (CHANGED: was "warn")
```

- [ ] **Step 4: Add deprecation comments to superseded keys**

At `complex_provider` (line 67):
```toml
complex_provider = "local"    # DEPRECATED: superseded by [api_gate]. Kept for backwards compat.
```

At `api_keys_required` (line 12):
```toml
api_keys_required = false    # DEPRECATED: superseded by [api_gate]. Kept for backwards compat.
```

- [ ] **Step 5: Commit**

```bash
git add fleet/fleet.toml
git commit -m "feat: add [api_gate] config, change review/budget defaults to safe"
```

---

## Task 7: Dashboard API Gate Endpoints

**Files:**
- Modify: `fleet/dashboard.py`

- [ ] **Step 1: Add gate endpoints to dashboard.py**

Add near the existing `/api/usage/*` endpoints (~line 1450):

```python
# -- API Gate -----------------------------------------------------------------

@app.route("/api/gate/status")
def api_gate_status():
    import api_gate
    return jsonify(api_gate.status())

@app.route("/api/gate/enable", methods=["POST"])
def api_gate_enable():
    import api_gate
    data = request.get_json(silent=True) or {}
    budget = float(data.get("budget", 0))
    providers = data.get("providers", [])
    ttl = data.get("ttl_hours")
    drain = data.get("drain_mode", "graceful")
    if budget <= 0:
        return jsonify({"error": "budget must be > 0"}), 400
    if not providers:
        return jsonify({"error": "at least one provider required"}), 400
    # Check offline/air_gap override
    cfg = _load_config()
    from config import is_offline, is_air_gap
    if is_offline(cfg) or is_air_gap(cfg):
        return jsonify({"error": "Cannot enable API gate — offline_mode or air_gap_mode is active"}), 409
    result = api_gate.enable(budget, providers, ttl, drain)
    return jsonify(result)

@app.route("/api/gate/disable", methods=["POST"])
def api_gate_disable():
    import api_gate
    return jsonify(api_gate.disable())

@app.route("/api/gate/drain-mode", methods=["PUT"])
def api_gate_drain_mode():
    import api_gate
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "graceful")
    if mode not in ("graceful", "hard"):
        return jsonify({"error": "mode must be 'graceful' or 'hard'"}), 400
    return jsonify(api_gate.set_drain_mode(mode))

@app.route("/api/gate/ring")
def api_gate_ring():
    import api_gate
    limit = request.args.get("limit", 200, type=int)
    return jsonify(api_gate.get_ring(limit))
```

- [ ] **Step 2: Verify endpoints load**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "import dashboard; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add fleet/dashboard.py
git commit -m "feat: add /api/gate/* endpoints for API governance"
```

---

## Task 8: CLI Commands (lead_client.py)

**Files:**
- Modify: `fleet/lead_client.py`

- [ ] **Step 1: Add `api` subcommand group**

Find the argparse subparsers section and add:

```python
# -- API Gate -----------------------------------------------------------------
api_p = sub.add_parser("api", help="API gate controls")
api_sub = api_p.add_subparsers(dest="api_action")

api_enable = api_sub.add_parser("enable", help="Enable API access")
api_enable.add_argument("--budget", type=float, required=True, help="Session budget in USD")
api_enable.add_argument("--providers", nargs="+", default=["claude"], help="Providers to enable")
api_enable.add_argument("--ttl", type=str, default=None, help="Auto-expire (e.g., 4h)")
api_enable.add_argument("--drain", default="graceful", choices=["graceful", "hard"])

api_sub.add_parser("disable", help="Disable API access")
api_sub.add_parser("status", help="Show gate status")

api_drain = api_sub.add_parser("drain-mode", help="Set drain mode")
api_drain.add_argument("mode", choices=["graceful", "hard"])
```

- [ ] **Step 2: Add `cmd_api()` handler**

```python
def cmd_api(args):
    action = args.api_action
    if action == "enable":
        ttl_hours = None
        if args.ttl:
            val = args.ttl.rstrip("h")
            ttl_hours = float(val)
        data = {"budget": args.budget, "providers": args.providers,
                "ttl_hours": ttl_hours, "drain_mode": args.drain}
        resp = _post("/api/gate/enable", data)
    elif action == "disable":
        resp = _post("/api/gate/disable", {})
    elif action == "status":
        resp = _get("/api/gate/status")
    elif action == "drain-mode":
        resp = _put("/api/gate/drain-mode", {"mode": args.mode})
    else:
        print("Usage: lead_client.py api {enable|disable|status|drain-mode}")
        return
    _print_json(resp)
```

- [ ] **Step 3: Wire handler in main dispatch**

Add to the command dispatch section:
```python
elif args.command == "api":
    cmd_api(args)
```

- [ ] **Step 4: Verify**

Run: `cd /c/Users/max/Projects/Education/fleet && python lead_client.py api status`
Expected: JSON with `{"enabled": false, "budget": 0.0, ...}`

- [ ] **Step 5: Commit**

```bash
git add fleet/lead_client.py
git commit -m "feat: add 'lead_client.py api' CLI for gate control"
```

---

## Task 9: Fix Universe Graph -- Heartbeat Filters

**Files:**
- Modify: `fleet/views_blueprint.py` (lines ~258, ~1041)
- Modify: `fleet/views/fleet-overview.json`

- [ ] **Step 1: Fix `_graph_supervisor()` heartbeat filter**

Find the agent query (around line 258):
```sql
WHERE a.last_heartbeat >= datetime('now', '-120 seconds')
```
Replace with:
```sql
WHERE a.last_heartbeat IS NOT NULL
```

- [ ] **Step 2: Fix `_graph_universe()` heartbeat filter**

Find the agent query (around line 1041):
```sql
WHERE last_heartbeat >= datetime('now', '-300 seconds')
```
Replace with:
```sql
WHERE last_heartbeat IS NOT NULL
```

- [ ] **Step 3: Update fleet-overview.json sources**

Replace contents of `fleet/views/fleet-overview.json`:
```json
{
  "schema_version": 1,
  "name": "fleet-overview",
  "description": "Full fleet overview -- agents, skills, tasks, models, knowledge",
  "sources": ["supervisor", "rag", "reinforcement", "knowledge", "autoresearch", "universe"],
  "layout": "radial",
  "metrics_overlay": ["uptime_s", "worker_count", "task_queue_depth"],
  "animation": {
    "dispatches": "pulse",
    "heartbeat": "fade"
  }
}
```

- [ ] **Step 4: Add API call nodes to `_graph_universe()`**

At the end of `_graph_universe()`, before the final `return nodes, edges`, add:

```python
    # -- 9. API CALL NODES (from gate ring buffer) ----------------------------
    try:
        import api_gate
        for call in api_gate.get_ring(50):  # Last 50 calls for graph
            call_id = f"api_call:{call['provider']}:{int(call['timestamp'] * 1000)}"
            _add_node(call_id, type="api_call", source="universe",
                      label=f"{call['provider']} ({call['skill']})",
                      status="ACTIVE",
                      metrics={"tokens": call['input_tokens'] + call['output_tokens'],
                               "cost": call['cost_usd'],
                               "purpose": call['purpose']})
            # Edge: skill -> api_call
            skill_id = f"skill:{call['skill']}"
            _add_edge(skill_id, call_id, "api_call", 1)
            # Edge: api_call -> provider model node
            model_id = f"model:{call['provider']}"
            _add_node(model_id, type="model", source="universe",
                      label=call['provider'], status="ACTIVE")
            _add_edge(call_id, model_id, "routes_to", 1)
    except Exception:
        log.warning("universe: api_call ring failed", exc_info=True)
```

- [ ] **Step 5: Verify graph endpoint returns more nodes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -c "
import db, views_blueprint
nodes, edges = views_blueprint._graph_supervisor(db)
print(f'supervisor: {len(nodes)} nodes, {len(edges)} edges')
"`
Expected: More than 2 nodes

- [ ] **Step 6: Commit**

```bash
git add fleet/views_blueprint.py fleet/views/fleet-overview.json
git commit -m "fix: universe graph shows all agents + API call nodes"
```

---

## Task 10: Dashboard -- Embed Universe Graph + Gate Controls

**Files:**
- Modify: `fleet/templates/dashboard.html`

- [ ] **Step 1: Fix Cytoscape container height**

Find `#cytoscape-container` CSS (~line 875):
```css
#cytoscape-container {
  width: 100%;
  height: 400px;
```
Change to:
```css
#cytoscape-container {
  width: 100%;
  height: calc(100vh - 280px);
  min-height: 400px;
```

- [ ] **Step 2: Replace `loadNeuralGraph()` with universe graph fetch**

Replace the `loadNeuralGraph()` function (~line 2607) with a version that fetches from the ViewPort API instead of building a simple hub-spoke from `/api/status`. The new version:
- Fetches `/api/views/graph/fleet-overview`
- Maps node types to colors: agent=#10b981, skill=#3b82f6, task=#f59e0b, model=#a78bfa, folder=#4fc3f7, api_call=#ef4444
- Renders api_call nodes as small diamonds
- Adds directional arrows on edges
- Uses cose layout for organic clustering

Use safe DOM methods (createElement, textContent) for error states instead of setting content via string concatenation. Create a helper function `_setEmptyState(containerId, message)` that builds the empty state using DOM API.

- [ ] **Step 3: Add API Gate control panel to Settings section**

Find the Settings section (~line 1751) and add an API Gate card before the existing settings form. The card should include:
- Budget slider ($0-$20) with a `+` button that opens a prompt for custom values
- Provider checkboxes (Claude, Gemini, MiniMax)
- TTL dropdown (1h, 2h, 4h, 8h, No expiry)
- Drain mode selector (Graceful, Hard)
- Enable button (green) and Kill button (red)
- Live spend progress bar (hidden when gate disabled)

Build all UI elements using safe DOM methods (createElement, setAttribute, addEventListener). Do not use string-based HTML insertion.

- [ ] **Step 4: Add gate JavaScript functions**

Add `enableGate()`, `disableGate()`, `updateGateUI()` functions and a 10-second polling interval that updates the gate status when the settings tab is visible. Use `apiFetch()` for all API calls. Use `textContent` for text updates and `style` property for visual state changes.

- [ ] **Step 5: Commit**

```bash
git add fleet/templates/dashboard.html
git commit -m "feat: embed universe graph in Pipeline tab, add API gate controls"
```

---

## Task 11: Smoke Tests

**Files:**
- Modify: `fleet/smoke_test.py`

- [ ] **Step 1: Add `test_no_direct_api_imports`**

Add to the test list:

```python
def test_no_direct_api_imports():
    """Skills must route through call_complex(), not direct API clients."""
    BANNED = ["import anthropic", "import google.generativeai", "from anthropic import"]
    EXEMPT = {"_models.py", "providers.py", "_contract.py"}
    violations = []
    skills_dir = FLEET_DIR / "skills"
    for py in skills_dir.glob("*.py"):
        if py.name in EXEMPT:
            continue
        content = py.read_text(encoding="utf-8", errors="ignore")
        for pattern in BANNED:
            if pattern in content:
                violations.append(f"{py.name}: {pattern}")
    if violations:
        return False, f"Direct API imports found: {'; '.join(violations)}"
    return True, f"All skills route through call_complex()"
```

- [ ] **Step 2: Add `test_api_gate`**

```python
def test_api_gate():
    """API gate module loads and defaults to disabled."""
    try:
        import api_gate
        s = api_gate.status()
        if s["enabled"]:
            return False, "Gate should default to disabled"
        return True, f"Gate disabled, budget ${s['budget']}"
    except Exception as e:
        return False, str(e)
```

- [ ] **Step 3: Register both tests in the test list**

Add to the `ALL_TESTS` list (or equivalent):
```python
("api_gate", test_api_gate),
("no_direct_api", test_no_direct_api_imports),
```

- [ ] **Step 4: Run smoke tests**

Run: `cd /c/Users/max/Projects/Education/fleet && python smoke_test.py --fast`
Expected: All tests pass including the two new ones

- [ ] **Step 5: Commit**

```bash
git add fleet/smoke_test.py
git commit -m "test: add smoke tests for api_gate + direct API import ban"
```

---

## Task 12: Integration Test -- Full Flow

- [ ] **Step 1: Verify gate blocks API calls when disabled**

```bash
cd /c/Users/max/Projects/Education/fleet
python -c "
import api_gate
allowed, reason = api_gate.check('claude')
print(f'Allowed: {allowed}, Reason: {reason}')
assert not allowed
print('PASS: gate blocks when disabled')
"
```

- [ ] **Step 2: Verify gate allows after enable**

```bash
python -c "
import api_gate
api_gate.enable(budget=5.0, providers=['claude'], ttl_hours=1)
allowed, reason = api_gate.check('claude')
print(f'Allowed: {allowed}, Reason: {reason}')
assert allowed
# Check that gemini is still blocked
allowed2, reason2 = api_gate.check('gemini')
assert not allowed2
print('PASS: gate allows enabled providers only')
api_gate.disable()
"
```

- [ ] **Step 3: Verify budget enforcement**

```bash
python -c "
import api_gate
api_gate.enable(budget=0.01, providers=['claude'])
api_gate.record_call('claude', 'test', 'agent1', 100, 50, 0.02, 100)
allowed, reason = api_gate.check('claude')
print(f'Allowed: {allowed}, Reason: {reason}')
assert not allowed
assert 'budget' in reason
print('PASS: budget enforcement works')
"
```

- [ ] **Step 4: Verify universe graph has more nodes**

```bash
python -c "
import urllib.request, json
resp = urllib.request.urlopen('http://localhost:5555/api/views/graph/fleet-overview', timeout=10)
data = json.loads(resp.read())
nodes = data.get('nodes', [])
print(f'Nodes: {len(nodes)}')
assert len(nodes) > 5, f'Expected >5 nodes, got {len(nodes)}'
print('PASS: universe graph shows full fleet')
"
```

- [ ] **Step 5: Run full smoke test suite**

Run: `cd /c/Users/max/Projects/Education/fleet && python smoke_test.py`
Expected: All tests pass

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: API governance system — gate, controls, graph visibility"
```
