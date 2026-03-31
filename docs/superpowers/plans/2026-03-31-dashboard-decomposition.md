# Dashboard Decomposition — Implementation Plan

**Date:** 2026-03-31
**Spec:** `docs/superpowers/specs/2026-03-29-dashboard-decomposition-design.md`
**Goal:** Split 5,680-line `fleet/dashboard.py` (155 routes) into focused blueprints. No route URL changes. Pure internal refactor.

---

## Phase 1: Extract shared utilities (`dashboard_utils.py`)

**Why first:** Every subsequent blueprint imports from here. Also eliminates the duplicated `_load_config()` / `query()` / `_check_rate_limit()` in `process_control.py`.

### Task 1.1: Create `fleet/dashboard_utils.py`

Extract these functions/constants from `dashboard.py` into `fleet/dashboard_utils.py`:

```python
# fleet/dashboard_utils.py
"""Shared infrastructure for dashboard blueprints."""
import json
import logging
import re
import sqlite3
import time
import threading
from datetime import datetime
from pathlib import Path

from security import (
    get_request_role,
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

log = logging.getLogger("dashboard")

FLEET_DIR = Path(__file__).parent
DB_PATH = FLEET_DIR / "fleet.db"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
HW_STATE_JSON = FLEET_DIR / "hw_state.json"
VALID_AGENT = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

# ── Config loader ────────────────────────────────────────────────────────────

def _load_config():
    """Load fleet.toml for thermal/training/module config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    toml_path = FLEET_DIR / "fleet.toml"
    if not toml_path.exists():
        return {}
    return tomllib.loads(toml_path.read_text(encoding="utf-8"))


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_conn():
    """Get fleet.db connection via db module (WAL, retry, optional SQLCipher)."""
    import db as _db
    return _db.get_conn()


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Role helpers ─────────────────────────────────────────────────────────────

def _get_request_role(req=None):
    return get_request_role(_load_config, req)


def _require_role(role):
    return _require_role_raw(role, _load_config)


# ── Rate limiter ─────────────────────────────────────────────────────────────

_rate_limits = {}

def _check_rate_limit(endpoint, max_per_min=10):
    """Simple in-memory rate limit. Returns True if allowed."""
    now = time.time()
    if len(_rate_limits) > 500:
        stale = [k for k, v in _rate_limits.items() if now - v[0] >= 300]
        for k in stale:
            del _rate_limits[k]
    if endpoint not in _rate_limits:
        _rate_limits[endpoint] = (now, 1)
        return True
    last, count = _rate_limits[endpoint]
    if now - last > 60:
        _rate_limits[endpoint] = (now, 1)
        return True
    if count >= max_per_min:
        return False
    _rate_limits[endpoint] = (last, count + 1)
    return True


# ── Misc helpers ─────────────────────────────────────────────────────────────

def _is_recent(timestamp_str: str, seconds: int = 120) -> bool:
    try:
        ts = datetime.fromisoformat(timestamp_str)
        return (datetime.utcnow() - ts).total_seconds() < seconds
    except Exception:
        return False


def safe_error(e):
    return _safe_error(e)
```

**What changes in `dashboard.py`:**
- Remove definitions of: `_load_config`, `get_conn`, `query`, `_get_request_role`, `_require_role`, `_check_rate_limit`, `_is_recent`, `VALID_AGENT`, `FLEET_DIR`, `DB_PATH`, `KNOWLEDGE_DIR`, `HW_STATE_JSON`
- Add at top: `from dashboard_utils import (FLEET_DIR, DB_PATH, KNOWLEDGE_DIR, HW_STATE_JSON, VALID_AGENT, _load_config, get_conn, query, _get_request_role, _require_role, _check_rate_limit, _is_recent, safe_error)`
- Keep `_safe_error` reference from security import for existing callers (or alias via `safe_error`)

**What changes in `process_control.py`:**
- Remove local `_load_config`, `_get_conn`, `query`, `_require_role`, `_check_rate_limit`, `VALID_AGENT`, `FLEET_DIR`, `DB_PATH`, `HW_STATE_JSON`
- Add: `from dashboard_utils import (FLEET_DIR, DB_PATH, HW_STATE_JSON, VALID_AGENT, _load_config, get_conn, query, _require_role, _check_rate_limit)`
- Keep the `fleet_bp = Blueprint(...)` and all route handlers unchanged

### Task 1.2: Tests

```bash
# Verify imports resolve
python -c "from dashboard_utils import _load_config, query, get_conn, _check_rate_limit; print('OK')"

# Verify dashboard still loads
python -c "import sys; sys.path.insert(0,'fleet'); import dashboard; print(f'{len(dashboard.app.url_map._rules)} routes OK')"

# Verify process_control still loads
python -c "import sys; sys.path.insert(0,'fleet'); from process_control import fleet_bp; print(f'{len(fleet_bp.deferred_functions)} deferred OK')"

# Full smoke test
python fleet/smoke_test.py --fast
```

### Task 1.3: Commit

```
refactor(dashboard): extract shared utils to dashboard_utils.py

Moves _load_config, query, get_conn, _check_rate_limit, VALID_AGENT,
and role helpers into dashboard_utils.py. Both dashboard.py and
process_control.py now import from the shared module, eliminating
duplication.
```

---

## Phase 2: Extract Factorio proxy blueprint

**Why next:** 19 Factorio endpoints (lines 1542-1915) are fully self-contained — no cross-dependencies with other dashboard routes. Largest single-domain block.

### Task 2.1: Create `fleet/factorio_blueprint.py`

Move these routes from `dashboard.py`:

| Route | Method | Line | Notes |
|-------|--------|------|-------|
| `/api/factorio/bridge-status` | GET | 1542 | Proxy |
| `/api/factorio/bridge-state` | GET | 1555 | Proxy |
| `/api/factorio/spectator` | POST | 1568 | Launch client |
| `/api/factorio/fpm` | POST | 1594 | Launch FPM GUI |
| `/api/factorio/start` | POST | 1686 | Start server+bridge |
| `/api/factorio/stop` | POST | 1720 | Stop all |
| `/api/factorio/restart` | POST | 1728 | Full restart |
| `/api/factorio/restart-bridge` | POST | 1739 | Bridge-only restart |
| `/api/factorio/pause` | POST | 1783 | Proxy pause |
| `/api/factorio/resume` | POST | 1796 | Proxy resume |
| `/api/factorio/focus` | POST | 1809 | Focus toggle |
| `/api/factorio/focus` | GET | 1837 | Focus state |
| `/api/factorio/plans` | GET | 1848 | Proxy plan queue |
| `/api/factorio/plan-history` | GET | 1861 | Proxy plan history |
| `/api/factorio/training-status` | GET | 1874 | Proxy ML metrics |
| `/api/factorio/spatial-map` | GET | 1889 | Proxy spatial map |
| `/api/factorio/reward-history` | GET | 1904 | Proxy reward history |

Also move these helpers:
- `_factorio_procs` dict (line 1612)
- `_factorio_kill_all()` (line 1615)
- `_factorio_wait_for_rcon()` (line 1656)

**Blueprint structure:**

```python
# fleet/factorio_blueprint.py
"""Factorio sandbox proxy endpoints."""
import json, logging, os, sys, time, urllib.request
from pathlib import Path
from flask import Blueprint, jsonify, request

from dashboard_utils import _load_config, _broadcast_sse, FLEET_DIR

log = logging.getLogger("dashboard.factorio")
factorio_bp = Blueprint("factorio", __name__)

_QUEUE_PAUSE_FILE = FLEET_DIR / ".queue_paused"

def _proxy_bridge(path, method="GET", timeout=5):
    """Generic proxy to Factorio bridge API."""
    port = _load_config().get("factorio", {}).get("bridge_port", 27016)
    url = f"http://127.0.0.1:{port}{path}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), 200
    except Exception:
        return {"error": "Bridge unreachable"}, 502

# ... all @factorio_bp.route(...) handlers
```

**Key detail:** The `_proxy_bridge` helper deduplicates the repeated pattern of `_load_config().get("factorio", {}).get("bridge_port", 27016)` + `urlopen` + error handling (appears 8+ times).

**Note on `_broadcast_sse`:** The Factorio focus toggle and mode switch call `_broadcast_sse`. This function must be exported from `dashboard_utils.py` (or the SSE state moved there — see Phase 3). For Phase 2, add `_broadcast_sse` and the SSE client list/lock to `dashboard_utils.py` as a forward step.

### Task 2.2: Register in `dashboard.py`

After removing the Factorio routes from dashboard.py, add:

```python
from factorio_blueprint import factorio_bp
app.register_blueprint(factorio_bp)
```

### Task 2.3: Tests

```bash
# Verify blueprint loads and has expected routes
python -c "
import sys; sys.path.insert(0,'fleet')
from factorio_blueprint import factorio_bp
rules = [r.rule for r in factorio_bp.deferred_functions] if hasattr(factorio_bp, 'deferred_functions') else []
print(f'factorio_bp loaded')
"

# Full smoke test
python fleet/smoke_test.py --fast
```

### Task 2.4: Commit

```
refactor(dashboard): extract Factorio endpoints to factorio_blueprint.py

19 routes + 3 helpers moved. Generic _proxy_bridge() deduplicates
8 repeated bridge proxy patterns.
```

---

## Phase 3: Extract SSE + alerts

**Why now:** The SSE broadcaster (lines 3245-3412, 167 lines) contains duplicated query logic that mirrors REST endpoints. Alert monitor (lines 573-670, ~100 lines) is a standalone background thread. Both are infrastructure that other blueprints need.

### Task 3.1: Move SSE state to `dashboard_utils.py`

Add to `dashboard_utils.py`:

```python
import queue as _queue_mod

# ── SSE state ────────────────────────────────────────────────────────────────
_alerts = []
_alert_lock = threading.Lock()
_sse_clients = []
_sse_lock = threading.Lock()

def _add_alert(level, message, source="system"):
    """Add alert and broadcast via SSE. Deduplicates."""
    with _alert_lock:
        for existing in _alerts[-20:]:
            if existing["message"] == message and not existing["acknowledged"]:
                return
        alert = {
            "id": int(time.time() * 1000),
            "level": level,
            "message": message,
            "source": source,
            "time": datetime.utcnow().isoformat(),
            "acknowledged": False,
        }
        _alerts.append(alert)
        if len(_alerts) > 100:
            _alerts.pop(0)
    _broadcast_sse({"type": "alert", "data": alert})


def _broadcast_sse(data):
    """Send SSE event to all connected clients, reap stale ones."""
    msg = f"data: {json.dumps(data)}\n\n"
    now = time.time()
    dead = []
    with _sse_lock:
        for client in _sse_clients:
            try:
                client["queue"].put_nowait(msg)
                client["last_active"] = now
            except Exception:
                dead.append(client)
        for c in dead:
            _sse_clients.remove(c)
        _sse_clients[:] = [c for c in _sse_clients if now - c["last_active"] <= 120]
```

### Task 3.2: Create `fleet/sse_blueprint.py`

Move from `dashboard.py`:
- `/api/stream` route (lines 3212-3240)
- `_sse_broadcaster()` thread function (lines 3245-3412)

```python
# fleet/sse_blueprint.py
"""SSE streaming endpoint and adaptive broadcaster thread."""
from flask import Blueprint, Response
from dashboard_utils import (
    _sse_clients, _sse_lock, _broadcast_sse, _load_config,
    query, HW_STATE_JSON, _get_effective_mode, _get_modifier_states,
)

sse_bp = Blueprint("sse", __name__)

@sse_bp.route("/api/stream")
def api_stream():
    # ... (moved from dashboard.py)

def _sse_broadcaster():
    # ... (moved from dashboard.py, uses query/HW_STATE_JSON from utils)
```

**Dependency note:** `_sse_broadcaster` calls `_get_effective_mode()` and `_get_modifier_states()`. These mode helpers are Phase 4 extractions. For Phase 3, either:
- (a) Move mode helpers to `dashboard_utils.py` early (they're pure functions of config), or
- (b) Import them from dashboard.py temporarily (acceptable since dashboard.py still exists)

Recommend (a) — mode helpers (`_get_effective_mode`, `_get_active_mode`, `_get_mode_state`, `_get_mode_detail`, `_detect_available_modes`, `_get_modifier_states`, `_set_desired_mode`, `_persist_mode`, `_desired_mode` state) are ~170 lines of pure logic. Moving them to `dashboard_utils.py` keeps the SSE blueprint clean.

### Task 3.3: Create `fleet/alerts.py`

Move from `dashboard.py`:
- `_alert_monitor()` thread function (lines 573-670)
- `_monitor_start_time` (line 105)

```python
# fleet/alerts.py
"""Alert monitoring background thread."""
from dashboard_utils import (
    _load_config, query, _add_alert, HW_STATE_JSON, FLEET_DIR,
)

def _alert_monitor():
    # ... (moved from dashboard.py, all helpers come from dashboard_utils)
```

### Task 3.4: Wire into `dashboard.py`

Update the `__main__` block where threads are started:

```python
# Was:
threading.Thread(target=_alert_monitor, daemon=True).start()
threading.Thread(target=_sse_broadcaster, daemon=True).start()

# Becomes:
from alerts import _alert_monitor
from sse_blueprint import _sse_broadcaster
threading.Thread(target=_alert_monitor, daemon=True).start()
threading.Thread(target=_sse_broadcaster, daemon=True).start()
```

Register the blueprint:

```python
from sse_blueprint import sse_bp
app.register_blueprint(sse_bp)
```

### Task 3.5: Tests

```bash
python fleet/smoke_test.py --fast

# Verify SSE endpoint exists
python -c "
import sys; sys.path.insert(0,'fleet')
from sse_blueprint import sse_bp, _sse_broadcaster
print('sse_bp loaded')
"

# Verify alerts module
python -c "
import sys; sys.path.insert(0,'fleet')
from alerts import _alert_monitor
print('alerts loaded')
"
```

### Task 3.6: Commit

```
refactor(dashboard): extract SSE broadcaster + alert monitor

SSE state (_broadcast_sse, _add_alert, client list) moves to
dashboard_utils.py. /api/stream route + broadcaster thread move to
sse_blueprint.py. Alert monitor thread moves to alerts.py.
```

---

## Phase 4: Extract remaining domain blueprints

Each extraction follows the same pattern: create blueprint file, move routes, register in dashboard.py, run smoke tests.

### Task 4.1: `fleet/mode_blueprint.py` (~350 lines)

Move from `dashboard.py`:
- `/api/mode/status` (line 1919)
- `/api/mode/switch` (line 1945) — large handler (~120 lines)
- Mode helpers already in `dashboard_utils.py` from Phase 3

**Key complexity:** `api_mode_switch` calls `api_factorio_stop()` and `api_factorio_start()` internally. After Phase 2 these live in `factorio_blueprint.py`. Import them:

```python
from factorio_blueprint import _factorio_kill_all
```

Or better: `api_mode_switch` should call the Factorio stop/start via HTTP (localhost) rather than function import, keeping blueprints decoupled. This is a judgment call — direct import is simpler for a monolith.

### Task 4.2: `fleet/federation_blueprint.py` (~215 lines)

Move from `dashboard.py` (lines 3707-3920):
- `/api/federation/heartbeat` POST
- `/api/federation/peers` GET
- `/api/federation/discovered` GET
- `/api/federation/capacity` GET
- `/api/federation/hitl` GET
- `/api/federation/routing-stats` GET
- `/api/federation/route` POST
- `/api/federation/cert-status` GET
- `/api/federation/hitl/respond` POST
- `/api/federation/exchange-cert` POST
- `/api/federation/hitl/notify` POST
- `_federation_peers` dict (line 108)

### Task 4.3: `fleet/tasks_blueprint.py` (~310 lines)

Move from `dashboard.py`:
- `/api/tasks/waiting-human` (line 4523)
- `/api/tasks/<id>/respond` (line 4568)
- `/api/tasks/<id>/question` (line 4591)
- `/api/tasks/recent` (line 4633)
- `/api/tasks/queue` (line 4648)
- `/api/tasks/<id>/priority` (line 4678)
- `/api/tasks/<id>` DELETE (line 4713)
- `/api/tasks/<id>/requeue` (line 4739)
- `/api/tasks/dispatch` (line 4765)

### Task 4.4: `fleet/deploy_blueprint.py` (~150 lines)

Move from `dashboard.py` (lines 3921-4068):
- `/api/deploy/prepare` POST
- `/api/deploy/push` POST
- `/api/deploy/status/<id>` GET
- `/api/deploy/rollback/<id>` POST
- `/api/deploy/history` GET
- `/api/deploy/receive` POST
- `/api/deploy/pending` GET
- `/api/deploy/approve/<id>` POST
- `/api/deploy/reject/<id>` POST

### Task 4.5: `fleet/settings_blueprint.py` (~150 lines)

Move from `dashboard.py`:
- `/api/settings` GET (line 5172)
- `/api/settings/<section>` PUT (line 5197)
- `/api/settings/schema` GET (line 5281)
- `/api/settings/theme` GET (line 3435)
- `/api/settings/theme` POST (line 3448)
- `_VALID_THEMES` constant (line 3432)

### Task 4.6: Commit after each blueprint (or batch 2-3)

```
refactor(dashboard): extract mode/federation/tasks/deploy/settings blueprints

5 domain blueprints extracted. dashboard.py reduced by ~1,175 lines.
```

---

## Phase 5: Extract grouped blueprints (small domains by theme)

### Task 5.1: `fleet/monitoring_blueprint.py` (~440 lines)

Bundle these related domains:
- `/api/alerts/*` — alert list, acknowledge, dismiss
- `/api/logs/stream`, `/api/logs/recent`, `/api/logs/sources` (lines 5294-5435)
- `/api/thermal` (line 1349) — thermal + provider health
- `/api/health` (line 762) — fleet health check
- `/api/cluster/metrics` (line 4121)
- `/api/integrity`, `/api/integrity/refresh` (lines 3144-3210)
- `/api/sla` (line 4133)

### Task 5.2: `fleet/metering_blueprint.py` (~330 lines)

Bundle:
- `/api/billing/<tenant_id>/usage` (line 2536)
- `/api/billing/<tenant_id>/invoice` (line 2548)
- `/api/billing/<tenant_id>/quota` GET+PUT (lines 2566, 2577)
- `/api/billing/overview` (line 2593)
- `/api/billing/pricing` (line 2606)
- `/api/usage`, `/api/usage/delta`, `/api/usage/budgets`, `/api/usage/dashboard`, `/api/usage/regression` (lines 2313-2488)
- `/api/gate/*` — status, enable, disable, drain-mode, ring (lines 2489-2535)
- `/api/scaling/prediction`, `/api/scaling/retrain` (lines 2763-2786)

### Task 5.3: `fleet/ops_blueprint.py` (~280 lines)

Bundle:
- `/api/cache/stats`, `/api/cache/invalidate`, `/api/cache/invalidate/<name>` (lines 4174-4228)
- `/api/audit`, `/api/audit/export`, `/api/audit/purge` (lines 2983-3105)
- `/api/gdpr/erasure` (line 3108)
- `/api/filesystem/audit` (line 2109)
- `/api/trigger`, `/api/trigger/status` (lines 4229-4295)

### Task 5.4: `fleet/knowledge_blueprint.py` (~410 lines)

Bundle:
- `/api/rag` (line 1324)
- `/api/recommendations/<skill>`, `/api/recommendations/popular` (lines 5436-5477)
- `/api/knowledge` (line 1193)
- `/api/discussions` (line 1160)
- `/api/reviews` (line 1264)
- `/api/experiments`, `/api/experiments` POST, `/api/experiments/<id>/results`, `/api/experiments/<id>/promote` (lines 5478-5585)
- `/api/feedback` GET+POST, `/api/feedback/stats` (lines 4296-4522)

### Task 5.5: Commit

```
refactor(dashboard): extract monitoring/metering/ops/knowledge blueprints

4 grouped blueprints extracted. dashboard.py is now ~800 lines.
```

---

## Phase 6: App factory + cleanup

### Task 6.1: Move import-time side effects

Currently at module level in `dashboard.py`:
- `_cpu_sampler_thread` start (lines 63-67) — move into `__main__` or factory
- `_TEMPLATE_PATH.read_text()` (line 3419) — move into `index()` handler (already has fallback)
- `_register_security_hooks(app, ...)` (line 87) — move into factory
- `_restore_mode()` (line 5677) — already in `__main__`

### Task 6.2: Create `create_app()` factory

```python
def create_app(config=None):
    """Flask app factory — enables testing with test_client()."""
    app = Flask(__name__)
    cfg = config or _load_config()

    # Security hooks
    _register_security_hooks(app, lambda: cfg)

    # Register all blueprints
    from factorio_blueprint import factorio_bp
    from sse_blueprint import sse_bp
    from mode_blueprint import mode_bp
    from federation_blueprint import federation_bp
    from tasks_blueprint import tasks_bp
    from deploy_blueprint import deploy_bp
    from settings_blueprint import settings_bp
    from monitoring_blueprint import monitoring_bp
    from metering_blueprint import metering_bp
    from ops_blueprint import ops_bp
    from knowledge_blueprint import knowledge_bp
    # ... existing blueprints (fleet_bp, health_bp, etc.)

    for bp in [factorio_bp, sse_bp, mode_bp, ...]:
        app.register_blueprint(bp)

    # After-request hooks
    @app.after_request
    def _log_api_attribution(response):
        ...
    @app.after_request
    def _add_security_headers(response):
        ...

    return app
```

### Task 6.3: Update `__main__` block

```python
if __name__ == "__main__":
    app = create_app()
    # Start background threads
    # ... (existing logic)
    app.run(...)
```

### Task 6.4: Final line count verification

Target:
- `dashboard.py`: ~600-800 lines (factory + small routes + `__main__`)
- `dashboard_utils.py`: ~300-400 lines (shared infrastructure)
- 11 new blueprints: 150-440 lines each

### Task 6.5: Commit

```
refactor(dashboard): app factory pattern, remove import-time side effects

create_app() enables test_client() usage. Background threads start
only when run as __main__. dashboard.py is now ~700 lines.
```

---

## Execution Order Summary

| Phase | Tasks | Est. Lines Moved | Cumulative Reduction |
|-------|-------|-----------------|---------------------|
| 1: Utils | 3 | ~120 (dedup) | 5,560 |
| 2: Factorio | 4 | ~375 | 5,185 |
| 3: SSE + Alerts | 6 | ~270 | 4,915 |
| 4: Domain BPs | 6 | ~1,175 | 3,740 |
| 5: Grouped BPs | 5 | ~1,460 | 2,280 |
| 6: Factory | 5 | ~1,480 (restructure) | ~700 |

## Risk Mitigation

1. **Circular imports:** `dashboard_utils.py` imports only from `security` and `db` — no circular risk. Blueprints import from `dashboard_utils`, never from `dashboard.py`.

2. **SSE cross-cutting:** `_broadcast_sse` is used by routes in multiple blueprints (Factorio focus, mode switch, audit logging). Centralizing it in `dashboard_utils.py` (Phase 1/3) ensures all blueprints can call it.

3. **Mode switch coupling:** `api_mode_switch` calls Factorio start/stop directly. After extraction, use direct function import from `factorio_blueprint.py` (acceptable in a monolith). If this becomes a problem, refactor to use an event bus later.

4. **Test coverage:** Run `python fleet/smoke_test.py --fast` after every phase. The smoke test hits all 33 endpoint categories and will catch broken route registrations.

5. **Blueprint registration order:** Preserve the current order in `dashboard.py` to avoid route precedence surprises with Flask's URL matching.

## Validation Checklist

After each phase:
- [ ] `python fleet/smoke_test.py --fast` passes (33/33)
- [ ] `python -c "import sys; sys.path.insert(0,'fleet'); import dashboard; print(len(dashboard.app.url_map._rules))"` shows same route count
- [ ] No duplicate route warnings in Flask startup log
- [ ] Dashboard loads in browser at `http://localhost:5555`
- [ ] SSE stream connects and receives status updates
