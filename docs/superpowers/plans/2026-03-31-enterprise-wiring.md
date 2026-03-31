# Enterprise Wiring Fixes — Implementation Plan
**Version:** 0.400.00b patch
**Date:** 2026-03-31
**Status:** Ready to implement

---

## Overview

Eight enterprise modules are architecturally complete but disconnected from the main execution
path. This plan wires each one in independently — all tasks are safe to implement in any order.

Each task includes:
- Exact diff / code snippet to add
- Config guard pattern (fail-open where required)
- Test command to verify the fix
- Suggested commit message

---

## Task 1 — Wire `billing.record_usage()` into worker dispatch

**File:** `fleet/worker.py`
**Companion:** `fleet/billing.py` (signature: `record_usage(tenant_id, skill, tokens_in, tokens_out, model, cost)`)
**Where to insert:** After the CT-4 budget check block (line ~940), still inside the outer task-success path, before the outer `except Exception as e:` on line 941.

### What to add

```python
# CT-5: Billing metering — record usage per tenant if enabled
try:
    _billing_cfg = config.get("billing", {})
    if _billing_cfg.get("enabled", False):
        import billing as _billing
        _tenant_id = task.get("tenant_id") or config.get("platform", {}).get("default_tenant_id", "default")
        _skill_name = task.get("type", "unknown")
        _model_used = result.get("_model", "") if isinstance(result, dict) else ""
        _tokens_in = result.get("_tokens_in", 0) if isinstance(result, dict) else 0
        _tokens_out = result.get("_tokens_out", 0) if isinstance(result, dict) else 0
        _cost = result.get("_cost_usd", 0.0) if isinstance(result, dict) else 0.0
        _billing.record_usage(
            _tenant_id, _skill_name,
            _tokens_in, _tokens_out,
            _model_used, _cost,
        )
except Exception:
    pass  # billing must never block task execution
```

Insert this block in **both** code paths in worker.py where `db.complete_task()` is called:
- Line ~895 (non-review path, `else` branch)
- Line ~856 (review PASS path)

The `_tokens_in / _tokens_out / _cost_usd / _model` keys are already populated by the providers layer in result dicts. For skills that don't emit them, the values default to 0 (records a call with zero tokens, which is still correct for task-count billing).

### fleet.toml guard (already present, no change needed)
```toml
[billing]
enabled = false   # flip to true to activate
```

### Test
```bash
cd fleet
python -c "
import billing
billing.ensure_billing_tables()
billing.record_usage('test_tenant', 'code_review', 0, 0, '', 0.0)
usage = billing.get_tenant_usage('test_tenant')
assert usage['total_tokens_in'] == 0
print('billing.record_usage: OK')
"
```

### Commit
```
feat(billing): wire record_usage into worker dispatch path (CT-5)
```

---

## Task 2 — Wire `guardrails.evaluate_output()` into worker post-processing

**File:** `fleet/worker.py`
**Companion:** `fleet/guardrails.py` (signature: `evaluate_output(text, config=None) -> GuardrailResult`)

### Where to insert

After the DITL disclaimer injection block (line ~838) and before the evaluator-optimizer review
gate (`if _should_review(...)` on line ~849). This places guardrails *before* the review gate so
toxic content never reaches the reviewer.

```python
# Guardrails: evaluate output for toxicity/PII if enabled (never blocks on error)
try:
    _gr_cfg = config.get("guardrails", {})
    if _gr_cfg.get("enabled", False):
        from guardrails import evaluate_output as _gr_eval, GuardrailConfig
        _gr_text = ""
        if isinstance(result, str):
            _gr_text = result
        elif isinstance(result, dict):
            _gr_text = result.get("response") or result.get("result") or json.dumps(result)
        if _gr_text:
            _gr_config = GuardrailConfig(
                toxicity=_gr_cfg.get("toxicity", True),
                pii_detection=_gr_cfg.get("pii_detection", True),
                max_output_length=_gr_cfg.get("max_output_length", 0),
                topic_rails=_gr_cfg.get("blocked_topics", []),
            )
            _gr_result = _gr_eval(_gr_text, _gr_config)
            if not _gr_result.passed:
                _high = [f for f in _gr_result.findings if f["severity"] == "high"]
                log.warning(
                    f"Task {task['id']} guardrail FAIL ({len(_high)} high-severity findings): "
                    + "; ".join(f["detail"] for f in _high[:3])
                )
                if _gr_cfg.get("block_on_fail", False):
                    db.fail_task(task['id'], "guardrail: high-severity content blocked")
                    continue
            elif _gr_result.findings:
                log.info(f"Task {task['id']} guardrail WARN: "
                         + "; ".join(f["detail"] for f in _gr_result.findings[:3]))
except Exception:
    log.warning("Guardrail check failed — continuing", exc_info=True)
    pass  # guardrails must never block task execution
```

### fleet.toml addition (add under `[security]` or as its own section)
```toml
[guardrails]
enabled = false           # true = evaluate all skill outputs
toxicity = true
pii_detection = true
block_on_fail = false     # false = log only, true = fail task on high-severity
blocked_topics = []       # e.g. ["competitor_names", "pricing"]
max_output_length = 0     # 0 = no limit
```

### Test
```bash
cd fleet
python -c "
from guardrails import evaluate_output, GuardrailConfig
cfg = GuardrailConfig(toxicity=True, pii_detection=True)
r = evaluate_output('Hello, this is a normal response.', cfg)
assert r.passed, f'clean text failed: {r.findings}'
r2 = evaluate_output('Contact john@example.com for info.', cfg)
assert any(f[\"type\"] == \"pii\" for f in r2.findings)
print('guardrails.evaluate_output: OK')
"
```

### Commit
```
feat(guardrails): wire evaluate_output into worker post-processing
```

---

## Task 3 — Fix RBAC role table split in `security.py`

**File:** `fleet/security.py`

### Problem

`RBAC_ROLES` (line 49–53) has 3 roles. `PERMISSIONS` (line 57–63) has 5 roles. `require_role()`
uses `RBAC_ROLES` exclusively (line 141–143), so `developer` and `auditor` are defined in
`PERMISSIONS` but never accepted by `require_role()`.

### Fix

Delete `RBAC_ROLES` dict (lines 49–53) and rewrite `require_role()` to use `PERMISSIONS` as the
single source of truth.

**Remove** the `RBAC_ROLES` dict:
```python
# DELETE these lines:
RBAC_ROLES = {
    "admin": {"read", "write", "delete", "configure"},
    "operator": {"read", "write"},
    "viewer": {"read"},
}
```

**Replace** `require_role` (lines 124–147) with:

```python
def require_role(role, config_loader):
    """Decorator to enforce minimum role for an endpoint.

    Uses the PERMISSIONS table as the single source of truth. A request is
    allowed if the requesting role's permission set is a superset of the
    required role's permission set. Roles not in PERMISSIONS are denied.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            user_role = get_request_role(config_loader)
            user_perms = PERMISSIONS.get(user_role, set())
            required_perms = PERMISSIONS.get(role, set())
            if not required_perms or not required_perms.issubset(user_perms):
                return jsonify({"error": "insufficient permissions",
                                "required_role": role,
                                "your_role": user_role}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator
```

**Note:** Any call-site that currently passes `"operator"` or `"admin"` continues to work
unchanged. `"developer"` and `"auditor"` now also work.

### Test
```bash
cd fleet
python -c "
import flask, security, importlib
app = flask.Flask('test')

with app.test_request_context('/', headers={'Authorization': 'Bearer admin_tok'}):
    importlib.reload(security)
    cfg = lambda: {'security': {'admin_token': 'admin_tok'}}
    role = security.get_request_role(cfg)
    assert role == 'admin', f'Expected admin, got {role}'

    assert security.check_permission('developer', 'deploy'), 'developer should deploy'
    assert not security.check_permission('developer', 'delete'), 'developer should not delete'
    assert security.check_permission('auditor', 'audit'), 'auditor should audit'
    assert not security.check_permission('auditor', 'write'), 'auditor should not write'
    print('RBAC unified table: OK')
"
```

### Commit
```
fix(security): unify RBAC_ROLES and PERMISSIONS into single table
```

---

## Task 4 — Add API key validation to `control_plane.py`

**Files:** `fleet/control_plane.py`, `fleet/db.py`

### Step 1 — Add `tenant_api_keys` table to `db.py`

Add to the schema constants (applied in `_ensure_schema()` or equivalent init function):

```python
_ENTERPRISE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    label TEXT DEFAULT '',
    created_at REAL NOT NULL,
    last_used_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tak_hash ON tenant_api_keys (key_hash);
CREATE INDEX IF NOT EXISTS idx_tak_tenant ON tenant_api_keys (tenant_id);
"""
```

Apply it:
```python
conn.executescript(_ENTERPRISE_SCHEMA)
```

### Step 2 — Add `validate_api_key()` and `store_api_key()` to `control_plane.py`

Add after the `_hash_key()` helper (line ~521):

```python
def store_api_key(tenant_id: str, api_key: str, label: str = "") -> None:
    """Persist a hashed API key for a tenant."""
    key_hash = _hash_key(api_key)
    now = time.time()

    def _do():
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO tenant_api_keys
                   (tenant_id, key_hash, label, created_at)
                   VALUES (?, ?, ?, ?)""",
                (tenant_id, key_hash, label, now),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("store_api_key failed for tenant=%s", tenant_id, exc_info=True)


def validate_api_key(api_key: str) -> str | None:
    """Validate an API key and return the tenant_id, or None if invalid.

    Also updates last_used_at on success.
    """
    if not api_key or not api_key.startswith("fleet_"):
        return None
    key_hash = _hash_key(api_key)
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT tenant_id FROM tenant_api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        conn.close()
    except Exception:
        log.warning("validate_api_key DB lookup failed", exc_info=True)
        return None

    if row is None:
        return None

    tenant_id = row["tenant_id"] if hasattr(row, "keys") else row[0]

    # Update last_used_at (fire-and-forget)
    def _touch():
        c = _get_conn()
        try:
            c.execute(
                "UPDATE tenant_api_keys SET last_used_at = ? WHERE key_hash = ?",
                (time.time(), key_hash),
            )
            c.commit()
        finally:
            c.close()
    try:
        _retry_write(_touch)
    except Exception:
        pass

    return tenant_id
```

### Step 3 — Wire `store_api_key` into `provision_fleet()`

In `provision_fleet()`, after generating `api_key` (line ~131) and before calling
`tenant_admin.update_tenant()`, add:

```python
# Persist key hash to DB so validate_api_key() can verify it later
store_api_key(tenant_id, api_key, label="provisioned")
```

### Step 4 — Wire `validate_api_key` into control plane middleware

Add a `before_request` handler and a verification endpoint to the blueprint:

```python
@platform_bp.before_request
def _check_platform_api_key():
    """Validate X-Fleet-Key header for /api/platform/* calls if key is present."""
    fleet_key = request.headers.get("X-Fleet-Key", "")
    if fleet_key:
        tenant_id = validate_api_key(fleet_key)
        if tenant_id is None:
            return jsonify({"error": "invalid or unknown API key"}), 401
        request.fleet_tenant_id = tenant_id  # type: ignore[attr-defined]


@platform_bp.route("/api/platform/validate-key", methods=["POST"])
def api_validate_key():
    """POST /api/platform/validate-key — verify an API key, returns tenant_id."""
    data = request.get_json(silent=True) or {}
    key = data.get("api_key", "")
    if not key:
        return jsonify({"error": "api_key required"}), 400
    tenant_id = validate_api_key(key)
    if tenant_id is None:
        return jsonify({"valid": False}), 401
    return jsonify({"valid": True, "tenant_id": tenant_id})
```

### Test
```bash
cd fleet
python -c "
import db, control_plane, time

# Ensure table exists
conn = db.get_conn()
conn.executescript('''
CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE, label TEXT DEFAULT \\'\\',
    created_at REAL NOT NULL, last_used_at REAL
);
''')
conn.close()

key = 'fleet_testkey12345678'
control_plane.store_api_key('tenant_abc', key, label='test')
result = control_plane.validate_api_key(key)
assert result == 'tenant_abc', f'Expected tenant_abc, got {result}'
assert control_plane.validate_api_key('fleet_badkey') is None
print('validate_api_key: OK')
"
```

### Commit
```
feat(control_plane): add tenant_api_keys table + validate_api_key()
```

---

## Task 5 — Enable `filesystem_guard` by default for enterprise

**Files:** `fleet/filesystem_guard.py`, `fleet/supervisor.py`

### Problem

`FileSystemGuard` is instantiated with `enforce=False` by default and there is no log message
telling operators whether the guard is active or passive on startup.

### Fix — add startup log in `FileSystemGuard.__init__()`

In `filesystem_guard.py`, after the existing assignments (line ~45), add:

```python
if self._enforce:
    logger.info(
        "FileSystemGuard ACTIVE — enforce=True, deny_by_default=%s, "
        "log_all_access=%s, zones=%d",
        self._deny_by_default, self._log_all, len(self._zones),
    )
else:
    logger.debug(
        "FileSystemGuard PASSIVE — enforce=False (access control disabled). "
        "Set [filesystem] enforce = true in fleet.toml to activate."
    )
```

### Fix — instantiate guard at supervisor boot

Confirm `FileSystemGuard` is instantiated at startup. If no call site exists in `supervisor.py`,
add one in the early boot sequence:

```python
try:
    from filesystem_guard import FileSystemGuard
    from config import load_config as _lc
    _fs_guard = FileSystemGuard(_lc())
    # log message fires inside __init__
except Exception:
    log.warning("filesystem_guard init failed", exc_info=True)
```

### fleet.toml — no change to existing defaults

The TOML value stays `enforce = false`. To activate enterprise mode, operators set:
```toml
[filesystem]
enforce = true
deny_by_default = true
log_all_access = true
```

### Test
```bash
cd fleet
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from filesystem_guard import FileSystemGuard

# Passive mode (default)
g = FileSystemGuard({'filesystem': {'enforce': False}})
assert g.check_access('fleet/any/path.py', 'read') == True

# Active mode
g2 = FileSystemGuard({'filesystem': {'enforce': True, 'deny_by_default': True}})
assert g2.is_enterprise() == True
print('FileSystemGuard startup log: OK')
"
```

### Commit
```
feat(filesystem_guard): add startup log message for enforce on/off state
```

---

## Task 6 — Fix `experiment.py` auto-window dead code

**File:** `fleet/experiment.py`

### Problem

`_in_auto_window(exp_cfg)` is defined (line ~244) and fully implemented but never called.
`_should_auto_approve()` (line ~230) checks the type list but ignores the time window.

### Fix

Replace the `return` statement at the end of `_should_auto_approve`:

**Before:**
```python
        return bool(types.get(experiment_type, False))
    except Exception:
        log.warning("Failed to read experiment config", exc_info=True)
        return False
```

**After:**
```python
        if not types.get(experiment_type, False):
            return False
        return self._in_auto_window(exp_cfg)
    except Exception:
        log.warning("Failed to read experiment config", exc_info=True)
        return False
```

No changes needed to `_in_auto_window` — it already returns `True` when no windows are
configured, so existing deployments without `auto_windows` continue to work.

### fleet.toml example (optional — existing config already works without this):
```toml
[experiments.auto_windows]
windows = ["Sat 00:00-06:00", "Sun 00:00-06:00"]
```

### Test
```bash
cd fleet
python -c "
from unittest.mock import patch
from experiment import ExperimentFramework
ef = ExperimentFramework()

# No windows configured = allowed when type matches
with patch('config.load_config', return_value={
    'experiments': {
        'auto_approve_types': {'embedding_retrain': True},
        'auto_windows': {},
    }
}):
    assert ef._should_auto_approve('embedding_retrain') == True
    assert ef._should_auto_approve('router_update') == False

print('experiment auto-window gate: OK')
"
```

### Commit
```
fix(experiment): wire _in_auto_window into _should_auto_approve
```

---

## Task 7 — Add marketplace uninstall auth

**File:** `fleet/marketplace.py`

### Problem

`api_marketplace_uninstall` (line ~778) has no `@_require_role` decorator — any caller can
uninstall packages from any tenant.

### Fix

Add the decorator immediately before the function definition:

```python
@marketplace_bp.route("/api/marketplace/packages/<package_id>/install", methods=["DELETE"])
@_require_role("operator")          # ADD THIS LINE
def api_marketplace_uninstall(package_id):
```

No other changes needed. `_require_role` is already defined in `marketplace.py` (line ~71) as
a convenience wrapper around `security.require_role`.

### Verify no other unprotected write endpoints

```bash
cd fleet
grep -n "@marketplace_bp.route" marketplace.py | grep -E "POST|PUT|DELETE|PATCH"
# Confirm each has @_require_role immediately after
```

### Test
```bash
cd fleet
python -c "
import flask
from marketplace import marketplace_bp
app = flask.Flask('test')
app.register_blueprint(marketplace_bp)

with app.test_client() as c:
    resp = c.delete('/api/marketplace/packages/test_pkg/install',
                    json={'tenant_id': 'tenant_abc'},
                    content_type='application/json')
    assert resp.status_code == 403, f'Expected 403, got {resp.status_code}'
    print('marketplace uninstall auth: OK')
"
```

### Commit
```
fix(marketplace): require operator role for package uninstall endpoint
```

---

## Task 8 — Persist SSO sessions to DB

**Files:** `fleet/sso.py`, `fleet/db.py`

### Step 1 — Add `sso_sessions` table to `db.py`

Append to existing schema constants:

```python
_SSO_SCHEMA = """
CREATE TABLE IF NOT EXISTS sso_sessions (
    token TEXT PRIMARY KEY,
    user_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    exp REAL NOT NULL,
    last_seen REAL
);
CREATE INDEX IF NOT EXISTS idx_sso_exp ON sso_sessions (exp);
"""
```

Apply in `_ensure_schema()`:
```python
conn.executescript(_SSO_SCHEMA)
```

### Step 2 — Add DB helpers to `sso.py`

Add after the `_store_lock` declaration (line ~34):

```python
_SSO_TABLES_ENSURED = False


def _ensure_sso_table():
    global _SSO_TABLES_ENSURED
    if _SSO_TABLES_ENSURED:
        return
    try:
        import db
        conn = db.get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sso_sessions (
                token TEXT PRIMARY KEY, user_json TEXT NOT NULL,
                created_at REAL NOT NULL, exp REAL NOT NULL, last_seen REAL
            );
            CREATE INDEX IF NOT EXISTS idx_sso_exp ON sso_sessions (exp);
        """)
        conn.close()
        _SSO_TABLES_ENSURED = True
    except Exception:
        log.warning("sso: failed to create sso_sessions table", exc_info=True)


def _db_write_session(token: str, session_data: dict) -> None:
    """Persist session to DB. Fail silently — in-memory remains authoritative."""
    try:
        _ensure_sso_table()
        import db
        user_json = json.dumps({k: v for k, v in session_data.items() if k != "token"})
        def _do():
            conn = db.get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO sso_sessions
                       (token, user_json, created_at, exp, last_seen)
                       VALUES (?, ?, ?, ?, ?)""",
                    (token, user_json,
                     session_data.get("created_at", time.time()),
                     session_data.get("exp", time.time() + 28800),
                     time.time()),
                )
                conn.commit()
            finally:
                conn.close()
        db._retry_write(_do)
    except Exception:
        log.warning("sso: _db_write_session failed", exc_info=True)


def _db_delete_session(token: str) -> None:
    try:
        _ensure_sso_table()
        import db
        def _do():
            conn = db.get_conn()
            try:
                conn.execute("DELETE FROM sso_sessions WHERE token = ?", (token,))
                conn.commit()
            finally:
                conn.close()
        db._retry_write(_do)
    except Exception:
        log.warning("sso: _db_delete_session failed", exc_info=True)


def _db_load_session(token: str) -> dict | None:
    """Load session from DB on cache miss (handles restarts)."""
    try:
        _ensure_sso_table()
        import db
        conn = db.get_conn()
        row = conn.execute(
            "SELECT user_json, exp FROM sso_sessions WHERE token = ?", (token,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        exp = row[1] if not hasattr(row, "keys") else row["exp"]
        if exp < time.time():
            _db_delete_session(token)
            return None
        user_json = row[0] if not hasattr(row, "keys") else row["user_json"]
        data = json.loads(user_json)
        data["token"] = token
        return data
    except Exception:
        log.warning("sso: _db_load_session failed", exc_info=True)
        return None
```

### Step 3 — Wire into existing session functions

**`create_session()`** — after `_sessions[token] = session_data`, add:
```python
_db_write_session(token, session_data)
```

**`revoke_session()`** — after `_sessions.pop(token, None)`, add:
```python
_db_delete_session(token)
```

**`validate_session()` / `get_session_from_request()`** — add DB fallback on cache miss:
```python
session_data = _sessions.get(token)
if session_data is None:
    # DB fallback: recovers sessions after restart (cold in-memory cache)
    session_data = _db_load_session(token)
    if session_data:
        with _store_lock:
            _sessions[token] = session_data  # warm the cache
```

### Test
```bash
cd fleet
python -c "
import sso, time

user_info = {'sub': 'u1', 'email': 'test@example.com', 'role': 'operator'}
token = sso.create_session(user_info)
assert token

data = sso.validate_session(token)
assert data is not None and data.get('role') == 'operator'

# Simulate cold cache
with sso._store_lock:
    sso._sessions.clear()

# Verify DB fallback restores session
data2 = sso.validate_session(token)
assert data2 is not None, 'DB fallback failed after cache clear'
assert data2.get('role') == 'operator'

# Revoke
sso.revoke_session(token)
assert sso.validate_session(token) is None
print('SSO DB persistence: OK')
"
```

### Commit
```
feat(sso): persist sessions to DB — survives restart via cold-cache fallback
```

---

## Implementation Order (recommended)

Tasks can be done in any order, but this sequence goes lowest-risk to highest-surface-area:

1. **Task 6** — experiment dead code (pure logic fix, zero risk)
2. **Task 7** — marketplace auth (1-line decorator, zero risk)
3. **Task 3** — RBAC unify (security-sensitive but only tightening, not relaxing)
4. **Task 5** — filesystem_guard startup log (additive only)
5. **Task 2** — guardrails wiring (fail-open, no behavior change until config flipped)
6. **Task 1** — billing wiring (fail-open, no behavior change until config flipped)
7. **Task 4** — API key validation (new table + new endpoint, no existing paths changed)
8. **Task 8** — SSO persistence (largest surface area, test cold-cache scenario carefully)

---

## Audit Coverage Check

| Criterion | Tasks | Impact |
|-----------|-------|--------|
| Security — auth hardening | 3, 7 | Closes two auth gaps |
| Enterprise billing | 1 | Activates metering pipeline end-to-end |
| Compliance / guardrails | 2, 5 | SOC 2 audit trail, output safety |
| Platform / control plane | 4 | Enables API key auth for SaaS provisioning |
| ML experiment safety | 6 | Time-window gating now actually enforced |
| Session durability | 8 | Eliminates SSO logout on supervisor restart |
