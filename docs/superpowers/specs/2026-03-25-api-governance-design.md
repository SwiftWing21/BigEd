# API Governance & Graph Visibility Design

**Date:** 2026-03-25
**Status:** Draft
**Scope:** Fleet-wide API usage controls, provider management, budget enforcement, universe graph fix

---

## Problem Statement

The fleet has 8 API control points but enforcement is mostly advisory. Two incidents exposed the gaps:
- **$4 Claude spike:** Enabling `ANTHROPIC_API_KEY` caused the fleet to route all queued tasks through Claude immediately, with review system and idle evolution compounding costs.
- **3.1K Gemini 404s:** HA fallback chain silently cascaded to Gemini with a bad endpoint, burning free-tier quota on 404 errors.

Additionally, the universe graph (the primary visibility tool for data flows) is nearly empty due to a 120-second heartbeat filter and single-source view config.

### Root Causes
1. Budget enforcement defaults to `"warn"` — skills execute even when over budget
2. No global spend cap — only per-skill daily limits
3. Fallback chain fires silently between paid providers
4. Review system fails open (auto-PASS on error)
5. At least 1 skill (`marketing.py`) calls APIs directly, bypassing all controls
6. Fleet-overview graph only queries "supervisor" source with a 120s heartbeat window

---

## Design

### Layer 1: Global API Gate (`fleet/api_gate.py` — new file)

Central authority for all external API calls. Every call routes through here.

#### Precedence
The gate operates **below** existing hard overrides:
1. `air_gap_mode = true` → gate forced disabled, `api enable` CLI refuses with error
2. `offline_mode = true` → gate forced disabled, `api enable` CLI refuses with error
3. Gate disabled → all API calls rejected (use local)
4. Gate enabled → check provider/budget/drain as below

The gate **supersedes** `complex_provider` and `api_keys_required` config keys. After migration, those keys are ignored — the gate is the single source of truth for API access. Both keys are preserved in fleet.toml with a deprecation comment for backwards compatibility.

#### Thread Safety
All gate state is protected by `_gate_lock = threading.Lock()`. The `check()` method atomically reads `enabled`, `session_spend`, and `session_budget` under the lock. Spend updates after call completion also hold the lock. This matches the pattern used by the circuit breaker in `providers.py`.

#### State
```python
_gate_lock = threading.Lock()
_call_ring: collections.deque[APICallRecord] = collections.deque(maxlen=200)

@dataclass
class GateState:
    enabled: bool = False              # Master switch (default: OFF = local-only)
    session_budget_usd: float = 0.00   # Set when enabling
    session_spend_usd: float = 0.00    # Running total
    drain_mode: str = "graceful"       # "graceful" | "hard"
    expires_at: datetime | None = None # Auto-disable after TTL
    allowed_providers: set = field(default_factory=set)  # Explicitly enabled providers
```

**Process restart:** Gate state resets to disabled (safe default). The ring buffer is in-memory only — historical cost data lives in the `usage` DB table. The ring buffer is for real-time graph visualization only.

#### Config Loading
All `[api_gate]` fields read via defensive `.get()` pattern:
```python
gate_cfg = config.get("api_gate", {})
enabled = gate_cfg.get("enabled", False)
drain_mode = gate_cfg.get("drain_mode", "graceful")
```
Missing `[api_gate]` section (e.g., older fleet.toml) falls back to safe defaults. No KeyError possible.

#### Gate Check (called before every API request)
```
1. if offline_mode or air_gap_mode → reject (hard override)
2. if not enabled → reject (use local)
3. if expires_at and now > expires_at → auto-disable, reject
4. if provider not in allowed_providers → reject
5. if session_spend >= session_budget → trigger drain/hard stop
6. → allow, log call to ring buffer, update spend
```

TTL expiry is checked lazily on each `gate.check()` call (no background timer needed).

#### Drain Behavior (user-configurable, default: graceful)
- **Graceful (B):** Finish in-flight API calls, reject new ones, notify, switch to local
- **Hard (A):** Immediately reject all API calls, return error, notify

#### Ring Buffer
In-memory buffer of last 200 API calls for real-time graph visualization:
```python
@dataclass
class APICallRecord:
    timestamp: datetime
    provider: str        # "claude" | "gemini" | "minimax"
    skill: str
    agent: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    purpose: str         # "task" | "review" | "evolution"
    fallback_from: str | None  # If this was a fallback, who failed?
```

#### Config (`fleet.toml`)
```toml
[api_gate]
enabled = false                # Master switch — default OFF
default_budget = 0.00          # $0 = local-only until explicitly enabled
drain_mode = "graceful"        # "graceful" | "hard"
fallback_chain = ["local"]     # Default: local only, no paid cascade

[api_gate.providers.claude]
enabled = false
fallback = ["local"]           # Per-provider fallback chain

[api_gate.providers.gemini]
enabled = false
fallback = ["local"]

[api_gate.providers.minimax]
enabled = false
fallback = ["local"]
```

### Layer 2: Session Controls

#### CLI
```bash
lead_client.py api enable --budget 2.00 --ttl 4h --providers claude
lead_client.py api status
lead_client.py api disable
lead_client.py api drain-mode hard
```

#### Dashboard UI (Settings section or dedicated API panel)
- Toggle switch: "Enable External APIs"
- Budget: slider ($0–$20) + `+` button for manual entry of any amount
- Provider checkboxes: Claude / Gemini / MiniMax (independently toggleable)
- TTL dropdown: 1h / 2h / 4h / 8h / No expiry
- Drain mode toggle: Graceful / Hard
- Live spend bar: `$0.47 / $2.00 ████████░░░░ 23%`
- Kill switch button (red, always visible when enabled)

#### REST Endpoints
```
POST /api/gate/enable    {budget, ttl, providers, drain_mode}
POST /api/gate/disable
GET  /api/gate/status
PUT  /api/gate/drain-mode {mode}
```

#### Notifications (SSE events)
- Budget 80% → amber alert in dashboard
- Budget 100% → drain/stop triggered, red alert
- Provider fallback → alert (unless `purpose="review"`)
- Session TTL expired → auto-disable notification

### Layer 3: Provider Integration

#### Fallback Chain
Replace hardcoded `FALLBACK_CHAIN = ["claude", "gemini", "local"]` with per-provider configurable chains from `fleet.toml`. Default: `["local"]` only.

#### Fallback Alerts
When a fallback triggers, push SSE event + log warning — **unless** the call is tagged `purpose="review"` (intentional multi-provider "second set of eyes" review).

#### providers.py Changes
- `call_complex()` gains a `purpose: str = "task"` parameter (values: `"task"`, `"review"`, `"evolution"`)
- `call_complex()` calls `api_gate.check(provider, purpose)` before attempting any non-local provider
- `_call_claude()`, `_call_gemini()`, `_call_minimax()` all record to gate ring buffer on completion, passing `purpose` through
- Circuit breaker remains but respects gate state (if gate disabled, don't even attempt)
- The `purpose` parameter flows: `call_complex(purpose=)` → `api_gate.check(purpose=)` → `APICallRecord(purpose=)` → ring buffer → graph node metadata

### Layer 4: Direct API Bypass Audit

#### Problem
Skills like `marketing.py` call `anthropic.Anthropic()` directly, bypassing budget/logging/circuit-breaker.

#### Fix
- Audit all `fleet/skills/*.py` for direct API client imports
- Refactor each to use `call_complex()` from `skills/_models.py`
- Add smoke test to catch future violations:
  ```python
  def test_no_direct_api_imports():
      BANNED = ["import anthropic", "import google.generativeai", "from anthropic"]
      EXEMPT = {"_models.py", "providers.py", "_contract.py"}
      # _review.py is NOT exempt — it must use call_complex() after refactor
      # Fail if any non-exempt skill has banned imports
  ```

### Layer 5: Review System Fix

- **Refactor `_review.py`** to route through `call_complex(purpose="review")` instead of calling `anthropic.Anthropic()` and `genai.configure()` directly. This ensures review calls go through the gate, get logged to the ring buffer, and respect budgets. Remove `_review.py` from the smoke test EXEMPT list.
- Change `[review] provider` default: `"api"` → `"local"`
- When review deliberately uses a second provider, the `purpose="review"` tag on `call_complex()` suppresses fallback alerts
- Change failure mode: **fail-open** (auto-PASS) → **fail-hold** (task → `WAITING_HUMAN`)
- Skills importing `api_gate` must use lazy import (`import api_gate` inside functions, not at module level)

### Layer 6: Budget Enforcement Default

- Change `[budgets] enforcement` default: `"warn"` → `"block"`
- Per-skill budgets become hard limits
- Global gate budget is the outer envelope; per-skill budgets are inner limits

### Layer 7: Universe Graph Fix

#### Problem 1: Heartbeat Filters Too Strict
**File:** `fleet/views_blueprint.py`

Both `_graph_supervisor()` (120s window) and `_graph_universe()` (300s window) filter agents by heartbeat recency. Change both from time-window queries to:
```sql
WHERE a.last_heartbeat IS NOT NULL
```
Use the `status` column to distinguish active vs idle (not heartbeat recency). Nodes render with status-based colors — ACTIVE agents are green, IDLE are dimmed.

#### Problem 2: Fleet-Overview Single Source
**File:** `fleet/views/fleet-overview.json`

Change sources from:
```json
"sources": ["supervisor"]
```
To:
```json
"sources": ["supervisor", "rag", "reinforcement", "knowledge", "autoresearch", "universe"]
```

#### Problem 3: Graph Should Be a Dashboard Tab
Instead of requiring a separate `/view/graph/` page, embed the full universe graph as a tab in the main dashboard Pipeline section. Replace the current `loadNeuralGraph()` (which only fetches `/api/status` and builds a simple hub-spoke) with a call to `/api/views/graph/fleet-overview` using the full ViewPort engine.

The existing `#cytoscape-container` in the Pipeline → Neural Graph tab becomes the universe graph container. Size it to fill the available panel area (not a fixed 400px height).

#### API Call Nodes in Universe Graph
Each API call from the ring buffer creates a transient node:
- **Type:** `api_call`
- **Color:** provider-coded (Claude=#10b981, Gemini=#3b82f6, MiniMax=#a78bfa)
- **Size:** proportional to token count
- **Edges:** `skill → api_call → provider` showing data flow
- **Lifespan:** Nodes fade after 60s (configurable)
**Data source:** `_graph_universe()` in `views_blueprint.py` reads the `api_gate` ring buffer to generate transient API call nodes and edges alongside the existing agent/skill/task/model/folder/message/config nodes.

### Layer 8: Summary Dashboard

The existing dashboard API/Analytics section gains a cost summary card:
- **Live spend:** `$0.47 / $2.00` with progress bar
- **Tokens used:** input/output breakdown by provider
- **Top skills by cost:** last 24h
- **Provider health:** circuit breaker state per provider
- **Gate status:** enabled/disabled, TTL remaining, drain mode

This uses existing `/api/usage/summary` and new `/api/gate/status` endpoints.

---

## Architecture Summary

```
Global API Gate (master switch + session budget)
  └── Provider Controls (enable/disable each, custom fallback chains)
       └── Per-Skill Budgets (enforcement="block")
            └── call_complex() (the only path to external APIs)
                 ├── Cost Tracking → DB + Ring Buffer
                 ├── Universe Graph → transient api_call nodes
                 └── Summary Dashboard → live spend/tokens
```

**Safe by default:** Fleet starts with API disabled, $0 budget, local-only fallback. You explicitly enable when you want it.

---

## Files Changed

| File | Change | New/Modified |
|------|--------|--------------|
| `fleet/api_gate.py` | Central gate, state, ring buffer, check/enable/disable | New |
| `fleet/providers.py` | Wire gate check into call_complex(), configurable fallback chains | Modified |
| `fleet/fleet.toml` | Add `[api_gate]` section, change budget/review defaults | Modified |
| `fleet/skills/_models.py` | Gate check before API dispatch | Modified |
| `fleet/skills/_review.py` | Refactor to use call_complex(purpose="review"), default local, fail-hold | Modified |
| `fleet/skills/marketing.py` | Refactor to use call_complex() | Modified |
| `fleet/views_blueprint.py` | Fix heartbeat filter, add api_call nodes to universe | Modified |
| `fleet/views/fleet-overview.json` | Add all sources | Modified |
| `fleet/templates/dashboard.html` | Embed universe graph in Pipeline tab, add cost summary card, add API gate controls | Modified |
| `fleet/dashboard.py` | Add `/api/gate/*` endpoints | Modified |
| `fleet/lead_client.py` | Add `api enable/disable/status` CLI commands | Modified |
| `fleet/smoke_test.py` | Add test_no_direct_api_imports | Modified |

---

## Migration

1. Existing fleets with `complex_provider = "local"` are unaffected (already local-only)
2. Fleets with `complex_provider = "claude"` or `"gemini"` will need to `lead_client.py api enable --budget X` after upgrade
3. `[review] provider` changes from `"api"` to `"local"` — existing review passes continue with Ollama
4. `[budgets] enforcement` changes from `"warn"` to `"block"` — existing budget configs become hard limits
5. `FALLBACK_CHAIN` moves from hardcoded to `fleet.toml [api_gate]` — default `["local"]`

---

## Error Handling

- Gate check must use `except Exception:` (never bare `except:`) so a gate bug never prevents local-only operation
- If `api_gate.check()` raises, fall back to local silently with a `log.warning()`
- Gate must never crash the worker — it is advisory-then-enforced, not a hard wall that could kill the fleet

## Known Limitations

- Gate state is in-memory — process restart resets to disabled (safe default). If a 4h TTL session is interrupted by restart, the budget resets. This is acceptable (safe > convenient).
- Ring buffer is in-memory only (last 200 calls). Historical cost data for the "Top skills by cost: last 24h" summary card comes from the `usage` DB table, not the ring buffer.
- Single-operator design. Concurrent mutations from multiple CLI/dashboard sessions follow last-write-wins semantics.

## Non-Goals

- Per-tenant API quotas (enterprise billing feature, separate scope)
- Automatic API key rotation (handled by `secret_rotate` skill)
- Pricing table auto-refresh from provider APIs
- WebSocket transport (HTTP polling + SSE is sufficient for graph updates)
