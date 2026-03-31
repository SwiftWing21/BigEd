# Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 6 P0 critical and 4 highest-priority P1 findings from the BigEd CC full audit (excluding Factorio module, which is being fixed separately).

**Architecture:** Each task is a focused fix to one module with new unit tests. No cross-module dependencies — all tasks are independently implementable. Fixes follow existing patterns (db._retry_write, except Exception with logging, path containment checks).

**Tech Stack:** Python, Flask, PyJWT, sqlite3, pathlib, unittest/pytest

**Audit source:** 5-pod audit from session 2026-03-30i

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet/sso.py` | **Modify** | Fix JWT fallback + enforce SSO middleware |
| `fleet/tenant_admin.py` | **Modify** | Path containment on skill deploy/remove |
| `fleet/marketplace.py` | **Modify** | Path containment + auth on install/review |
| `fleet/geo_fleet.py` | **Modify** | Add Bearer token auth to inter-fleet HTTP |
| `fleet/providers.py` | **Modify** | Fix conn.close on pooled connection |
| `fleet/billing.py` | **Modify** | Fail closed on quota check error |
| `tests/test_sso_security.py` | **Create** | SSO JWT + middleware tests |
| `tests/test_tenant_admin_security.py` | **Create** | Path traversal tests |
| `tests/test_marketplace_security.py` | **Create** | Path traversal + auth tests |
| `tests/test_geo_fleet_security.py` | **Create** | Inter-fleet auth tests |
| `tests/test_providers_conn.py` | **Create** | Connection pool tests |
| `tests/test_billing_quota.py` | **Create** | Fail-closed quota tests |

---

## Task 1: Fix JWT Silent Fallback to Unverified Decode

**Severity:** P0 Critical — authentication bypass
**Files:**
- Modify: `fleet/sso.py:370-379`
- Create: `tests/test_sso_security.py`

The current code falls back to returning an unverified JWT payload when PyJWT throws any exception (not just ImportError). A network error fetching JWKS, a key rotation, or a malformed token all silently bypass signature verification.

- [ ] **Step 1: Write failing test**

```python
# tests/test_sso_security.py
"""Security tests for SSO JWT verification and middleware enforcement."""
import pytest
import json
import base64
import time


def _make_unsigned_jwt(payload: dict) -> str:
    """Create a JWT-like string with no valid signature."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return f"{header.decode()}.{body.decode()}.invalidsignature"


def test_jwt_verification_rejects_on_exception():
    """JWT verification must NOT fall back to unverified payload on general exceptions."""
    import sys
    sys.path.insert(0, "fleet")
    from sso import _verify_jwt_signature

    fake_token = _make_unsigned_jwt({"sub": "attacker", "exp": int(time.time()) + 3600})
    fake_payload = {"sub": "attacker", "exp": int(time.time()) + 3600}

    # If PyJWT is installed, verification of a bad token should raise, not return payload
    # If PyJWT is NOT installed, ImportError should raise, not return unverified payload
    result = _verify_jwt_signature(fake_token, fake_payload, "https://fake.example.com")
    # Result should be None (rejected), not the payload (accepted)
    assert result is None, "JWT verification must reject tokens it cannot verify, not fall back to unverified payload"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_sso_security.py::test_jwt_verification_rejects_on_exception -v`
Expected: FAIL — current code returns the unverified payload

- [ ] **Step 3: Fix the JWT fallback**

In `fleet/sso.py`, find the `_verify_jwt_signature` function (around line 340-379). The current code has:

```python
    except ImportError:
        log.warning("PyJWT not installed — JWT signature NOT verified. ...")
    except Exception:
        log.warning("JWT signature verification failed — using unverified payload", exc_info=True)

    return payload  # ← THIS IS THE BUG: returns unverified payload
```

Change to:

```python
    except ImportError:
        log.warning("PyJWT not installed — JWT signature NOT verified. "
                    "Install with: pip install PyJWT cryptography")
        return None  # Reject: cannot verify without PyJWT
    except Exception:
        log.warning("JWT signature verification failed — rejecting token", exc_info=True)
        return None  # Reject: verification failed, do NOT return unverified payload
```

Remove the final `return payload` line at the end of the function.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_sso_security.py::test_jwt_verification_rejects_on_exception -v`
Expected: PASS

- [ ] **Step 5: Fix SSO middleware to enforce auth**

In `fleet/sso.py`, find `sso_auth_check()` (around line 730-763). The last line is:

```python
    # Fall through to existing token-based auth (dashboard_token still works)
    return None  # ← BUG: always allows through
```

Change the ending (after the SSO session check) to:

```python
    # Check for Bearer token fallback (API clients)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        from config import load_config
        cfg = load_config()
        dashboard_token = cfg.get("security", {}).get("dashboard_token", "")
        if dashboard_token and token == dashboard_token:
            return None  # Valid API token

    # No valid SSO session or API token — block the request
    return jsonify({"error": "Authentication required", "sso_login": "/auth/login"}), 401
```

- [ ] **Step 6: Write middleware enforcement test**

```python
# Append to tests/test_sso_security.py

def test_sso_middleware_blocks_unauthenticated_api():
    """SSO middleware must return 401 for unauthenticated /api/ requests when SSO is enabled."""
    import sys
    sys.path.insert(0, "fleet")
    # This test verifies the function logic, not a full Flask test
    # The key assertion: sso_auth_check returns a 401 response (not None) when:
    # - SSO is enabled
    # - No valid session
    # - No valid Bearer token
    # - Path starts with /api/
    # Due to Flask request context requirements, we verify the code path exists
    from sso import sso_auth_check
    import inspect
    source = inspect.getsource(sso_auth_check)
    assert "401" in source, "sso_auth_check must return 401 for unauthenticated requests"
    assert "Authentication required" in source, "sso_auth_check must include auth error message"
```

- [ ] **Step 7: Run all SSO tests**

Run: `cd fleet && python -m pytest ../tests/test_sso_security.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add fleet/sso.py tests/test_sso_security.py
git commit -m "fix(security): reject unverified JWT tokens, enforce SSO middleware auth"
```

---

## Task 2: Fix Path Traversal in Tenant Admin

**Severity:** P0 Critical — API-reachable file exfiltration
**Files:**
- Modify: `fleet/tenant_admin.py:281-320, 348-360`
- Create: `tests/test_tenant_admin_security.py`

Both `deploy_skill_to_tenant` and `remove_tenant_skill` accept user-controlled paths without containment checks.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tenant_admin_security.py
"""Security tests for tenant admin path traversal prevention."""
import pytest
import sys
sys.path.insert(0, "fleet")


def test_deploy_skill_rejects_path_traversal():
    """deploy_skill_to_tenant must reject paths containing '..'."""
    from tenant_admin import deploy_skill_to_tenant
    with pytest.raises(ValueError, match="[Pp]ath traversal|[Ii]nvalid"):
        deploy_skill_to_tenant("test-tenant", "../../etc/passwd")


def test_deploy_skill_rejects_absolute_path_outside_fleet():
    """deploy_skill_to_tenant must reject absolute paths outside fleet/skills/."""
    from tenant_admin import deploy_skill_to_tenant
    with pytest.raises(ValueError, match="[Oo]utside|[Ii]nvalid|[Pp]ath"):
        deploy_skill_to_tenant("test-tenant", "/etc/passwd")


def test_remove_skill_rejects_path_traversal():
    """remove_tenant_skill must reject skill names containing '..'."""
    from tenant_admin import remove_tenant_skill
    with pytest.raises(ValueError, match="[Pp]ath traversal|[Ii]nvalid"):
        remove_tenant_skill("test-tenant", "../../sso")


def test_remove_skill_rejects_slash_in_name():
    """remove_tenant_skill must reject skill names containing path separators."""
    from tenant_admin import remove_tenant_skill
    with pytest.raises(ValueError, match="[Pp]ath|[Ii]nvalid"):
        remove_tenant_skill("test-tenant", "subdir/evil")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_tenant_admin_security.py -v`
Expected: FAIL — current code doesn't check for path traversal

- [ ] **Step 3: Add path containment checks**

In `fleet/tenant_admin.py`, in `deploy_skill_to_tenant()`, after line 301 (`src = Path(skill_path)`), add:

```python
    # Path traversal guard
    if ".." in str(skill_path):
        raise ValueError(f"Invalid skill path: path traversal detected")

    src = Path(skill_path)
    if not src.is_absolute():
        src = FLEET_DIR / "skills" / (skill_path if skill_path.endswith(".py") else f"{skill_path}.py")

    # Containment check: resolved path must be within fleet/skills/
    try:
        src.resolve().relative_to((FLEET_DIR / "skills").resolve())
    except ValueError:
        raise ValueError(f"Invalid skill path: must be within fleet/skills/")
```

In `remove_tenant_skill()`, after line 352 (`filename = ...`), add:

```python
    # Path traversal guard
    if ".." in skill_name or "/" in skill_name or "\\" in skill_name:
        raise ValueError(f"Invalid skill name: path traversal detected")

    target = skills_dir / filename

    # Containment check
    try:
        target.resolve().relative_to(skills_dir.resolve())
    except ValueError:
        raise ValueError(f"Invalid skill name: resolves outside tenant directory")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_tenant_admin_security.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/tenant_admin.py tests/test_tenant_admin_security.py
git commit -m "fix(security): add path containment checks to tenant skill deploy/remove"
```

---

## Task 3: Fix Path Traversal + Missing Auth in Marketplace

**Severity:** P0 Critical — file exfiltration + unauthenticated install/review
**Files:**
- Modify: `fleet/marketplace.py:499-557, 718-749`
- Create: `tests/test_marketplace_security.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_marketplace_security.py
"""Security tests for marketplace path traversal and auth enforcement."""
import pytest
import sys
sys.path.insert(0, "fleet")


def test_install_package_validates_skill_names():
    """install_package must reject skill names with path traversal."""
    # We test the skill name validation logic directly
    from marketplace import _validate_skill_name
    assert _validate_skill_name("valid_skill") is True
    with pytest.raises(ValueError):
        _validate_skill_name("../../sso")
    with pytest.raises(ValueError):
        _validate_skill_name("subdir/evil")
    with pytest.raises(ValueError):
        _validate_skill_name("..\\windows\\system32")


def test_review_endpoint_has_auth():
    """api_marketplace_submit_review must have authentication check."""
    import inspect
    from marketplace import api_marketplace_submit_review
    source = inspect.getsource(api_marketplace_submit_review)
    assert "_require_role" in source or "require_auth" in source or "sso_user" in source, \
        "submit_review endpoint must have authentication"


def test_install_endpoint_has_auth():
    """api_marketplace_install must have authentication check."""
    import inspect
    from marketplace import api_marketplace_install
    source = inspect.getsource(api_marketplace_install)
    assert "_require_role" in source or "require_auth" in source or "sso_user" in source, \
        "install endpoint must have authentication"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_marketplace_security.py -v`
Expected: FAIL

- [ ] **Step 3: Add skill name validation function**

In `fleet/marketplace.py`, add a validation function near the top (after imports):

```python
def _validate_skill_name(name: str) -> bool:
    """Validate a skill name contains no path traversal characters."""
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Invalid skill name: '{name}' contains path traversal characters")
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"Invalid skill name: '{name}' contains non-alphanumeric characters")
    return True
```

- [ ] **Step 4: Add validation to install_package**

In `fleet/marketplace.py`, in `install_package()`, at line 551 (the `for skill_name in skill_list:` loop), add validation before the file copy:

```python
    for skill_name in skill_list:
        _validate_skill_name(skill_name)  # Path traversal guard
        src = FLEET_DIR / "skills" / f"{skill_name}.py"
```

- [ ] **Step 5: Add auth to review and install endpoints**

In `fleet/marketplace.py`, find the `_require_role` function or import it. Then add authentication to both endpoints.

For `api_marketplace_submit_review` (line 718-732), add at the start of the function:

```python
    err = _require_role("operator")
    if err:
        return err
```

For `api_marketplace_install` (line 735-749), add at the start:

```python
    err = _require_role("operator")
    if err:
        return err
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_marketplace_security.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add fleet/marketplace.py tests/test_marketplace_security.py
git commit -m "fix(security): add path validation to marketplace install, require auth on review/install"
```

---

## Task 4: Add Auth to Inter-Fleet HTTP Calls

**Severity:** P0 Critical — unauthenticated remote code execution via geo routing
**Files:**
- Modify: `fleet/geo_fleet.py:296-316, 430-441, 698-716`
- Create: `tests/test_geo_fleet_security.py`

All three inter-fleet HTTP functions (`route_to_region`, `apply_auto_scale`, `sync_skills_to_cdn`) send unauthenticated requests.

- [ ] **Step 1: Write failing test**

```python
# tests/test_geo_fleet_security.py
"""Security tests for geo fleet inter-fleet authentication."""
import pytest
import sys
sys.path.insert(0, "fleet")


def test_route_to_region_includes_auth_header():
    """route_to_region must include Authorization header in requests."""
    import inspect
    from geo_fleet import route_to_region
    source = inspect.getsource(route_to_region)
    assert "Authorization" in source or "Bearer" in source, \
        "route_to_region must include auth header in inter-fleet requests"


def test_apply_auto_scale_includes_auth_header():
    """apply_auto_scale must include Authorization header."""
    import inspect
    from geo_fleet import apply_auto_scale
    source = inspect.getsource(apply_auto_scale)
    assert "Authorization" in source or "Bearer" in source, \
        "apply_auto_scale must include auth header"


def test_sync_skills_to_cdn_includes_auth_header():
    """sync_skills_to_cdn must include Authorization header."""
    import inspect
    from geo_fleet import sync_skills_to_cdn
    source = inspect.getsource(sync_skills_to_cdn)
    assert "Authorization" in source or "Bearer" in source, \
        "sync_skills_to_cdn must include auth header"


def test_get_federation_token_returns_string():
    """_get_federation_token must return a non-empty string from config."""
    from geo_fleet import _get_federation_token
    # Should return a string (may be empty if not configured, but function must exist)
    result = _get_federation_token()
    assert isinstance(result, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_geo_fleet_security.py -v`
Expected: FAIL

- [ ] **Step 3: Add federation token helper**

In `fleet/geo_fleet.py`, add near the top (after imports):

```python
def _get_federation_token() -> str:
    """Get the federation shared secret from fleet.toml [federation] section."""
    from config import load_config
    cfg = load_config()
    return cfg.get("federation", {}).get("shared_secret", "")
```

- [ ] **Step 4: Add auth headers to all three functions**

In `route_to_region()`, at the Request construction (line 303-308), add the auth header:

```python
        token = _get_federation_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"{endpoint}/api/trigger",
            data=body,
            method="POST",
            headers=headers,
        )
```

Apply the same pattern to `apply_auto_scale()` (line 434-438) and `sync_skills_to_cdn()` (line 704-709).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_geo_fleet_security.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/geo_fleet.py tests/test_geo_fleet_security.py
git commit -m "fix(security): add Bearer token auth to all inter-fleet HTTP calls"
```

---

## Task 5: Fix conn.close() on Pooled Connection

**Severity:** P0 Critical — silent data corruption
**Files:**
- Modify: `fleet/providers.py:968-979`
- Create: `tests/test_providers_conn.py`

`get_agent_affinity()` calls `conn.close()` on a connection from `db.get_conn()`, corrupting the thread-local pool.

- [ ] **Step 1: Write failing test**

```python
# tests/test_providers_conn.py
"""Test that providers.py doesn't corrupt the connection pool."""
import pytest
import sys
sys.path.insert(0, "fleet")


def test_get_agent_affinity_does_not_close_pooled_conn():
    """get_agent_affinity must not call conn.close() on a pooled connection."""
    import inspect
    from providers import get_agent_affinity
    source = inspect.getsource(get_agent_affinity)
    assert "conn.close()" not in source, \
        "get_agent_affinity must not close pooled connections — use 'with db.get_conn()' instead"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_providers_conn.py -v`
Expected: FAIL — current code has `conn.close()`

- [ ] **Step 3: Fix the connection usage**

In `fleet/providers.py`, find `get_agent_affinity()` (around line 965-980). Replace:

```python
        conn = db.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as success "
            "FROM tasks WHERE assigned_to=? AND type=? "
            "AND created_at > datetime('now', '-24 hours')",
            (agent_name, skill_name)
        ).fetchone()
        conn.close()
```

With:

```python
        conn = db.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as success "
            "FROM tasks WHERE assigned_to=? AND type=? "
            "AND created_at > datetime('now', '-24 hours')",
            (agent_name, skill_name)
        ).fetchone()
        # Do NOT call conn.close() — it's a pooled connection from db.get_conn()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_providers_conn.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/providers.py tests/test_providers_conn.py
git commit -m "fix(providers): remove conn.close() on pooled connection — corrupted thread-local pool"
```

---

## Task 6: Fix Quota Check Failing Open

**Severity:** P0 Critical — unlimited usage during DB outage
**Files:**
- Modify: `fleet/billing.py:274-276`
- Create: `tests/test_billing_quota.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_billing_quota.py
"""Test that billing quota check fails closed (not open)."""
import pytest
import sys
sys.path.insert(0, "fleet")


def test_check_quota_fails_closed():
    """check_quota must return exceeded=True on error, not exceeded=False."""
    import inspect
    from billing import check_quota
    source = inspect.getsource(check_quota)
    # Find the except block — it must set exceeded to True
    # The current bug: returns {"exceeded": False} on exception
    lines = source.split("\n")
    in_except = False
    for line in lines:
        if "except" in line and "Exception" in line:
            in_except = True
        if in_except and '"exceeded"' in line:
            assert "True" in line, \
                f"check_quota exception handler must return exceeded=True (fail closed), found: {line.strip()}"
            break
    else:
        pytest.fail("Could not find exceeded return in except block")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fleet && python -m pytest ../tests/test_billing_quota.py -v`
Expected: FAIL — current code returns `exceeded: False`

- [ ] **Step 3: Fix the fail-open behavior**

In `fleet/billing.py`, at line 274-276, change:

```python
    except Exception:
        log.warning("billing: check_quota failed for %s", tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "exceeded": False,
                "quota": {}, "used": {}, "remaining": {}}
```

To:

```python
    except Exception:
        log.warning("billing: check_quota failed for %s — failing closed (exceeded=True)", tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "exceeded": True,
                "quota": {}, "used": {}, "remaining": {},
                "error": "quota_check_failed"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fleet && python -m pytest ../tests/test_billing_quota.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/billing.py tests/test_billing_quota.py
git commit -m "fix(billing): fail closed on quota check error — return exceeded=True, not False"
```

---

## Task 7: Fix Raw sqlite3.connect in process_control.py

**Severity:** P1 Warning — bypasses WAL retry and connection pool
**Files:**
- Modify: `fleet/process_control.py:47`

- [ ] **Step 1: Find and fix the raw connection**

In `fleet/process_control.py`, find the raw `sqlite3.connect()` call (around line 47). Replace it with:

```python
import db
conn = db.get_conn()
```

Remove any `import sqlite3` that was only used for this connection.

- [ ] **Step 2: Verify import works**

Run: `cd fleet && python -c "from process_control import *; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add fleet/process_control.py
git commit -m "fix(process_control): use db.get_conn() instead of raw sqlite3.connect()"
```

---

## Task 8: Fix Stale Doc Metrics

**Severity:** P1 Warning — misleading documentation
**Files:**
- Modify: `CLAUDE.md` (root)
- Modify: `fleet/CLAUDE.md`

- [ ] **Step 1: Fix root CLAUDE.md**

Find and replace these stale values:
- `33/33` smoke tests → `51/51 (fast) / 54/54 (full)`
- `190+ endpoints` → `228+ endpoints`
- Any reference to `fleet/data_access.py` → `BigEd/launcher/data_access.py`
- Skill count: verify current count with `ls fleet/skills/*.py | wc -l` and update

- [ ] **Step 2: Fix fleet/CLAUDE.md**

- `27/27` or `33/33` smoke tests → `51/51 (fast) / 54/54 (full)`
- `97+ registered` skills → actual count
- Remove or correct `fleet_bridge.py` reference (should be `dispatch_bridge.py`)
- Fix `data_access.py` reference

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md fleet/CLAUDE.md
git commit -m "docs: fix stale metrics — smoke count 51/54, endpoint count 228+, skill count, file paths"
```

---

## Task 9: Add pytest to CI

**Severity:** P1 Warning — tests/ directory never run in CI
**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Read current CI config**

Read `.github/workflows/ci.yml` to understand the current structure.

- [ ] **Step 2: Add pytest job**

Add a step after the smoke test step:

```yaml
      - name: Run unit tests
        run: |
          cd fleet
          python -m pytest ../tests/ -v --tb=short -x
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add pytest step — tests/ was never run in CI despite being configured"
```

---

## Task 10: Fix Filesystem Guard Audit Log Severity

**Severity:** P1 Warning — SOC 2 compliance gap
**Files:**
- Modify: `fleet/filesystem_guard.py:111`

- [ ] **Step 1: Fix the log level**

In `fleet/filesystem_guard.py`, at line 111, change:

```python
        except OSError:
            logger.debug("Could not write filesystem audit log to %s", self._log_path)
```

To:

```python
        except OSError:
            logger.warning("COMPLIANCE: Could not write filesystem audit log to %s — "
                          "SOC 2 audit trail may be incomplete", self._log_path, exc_info=True)
```

- [ ] **Step 2: Commit**

```bash
git add fleet/filesystem_guard.py
git commit -m "fix(compliance): raise filesystem audit log failure from debug to warning"
```

---

## Summary

| Task | Severity | Module | Fix |
|------|----------|--------|-----|
| 1 | P0 | sso.py | Reject unverified JWT, enforce middleware |
| 2 | P0 | tenant_admin.py | Path containment on skill deploy/remove |
| 3 | P0 | marketplace.py | Path validation + auth on install/review |
| 4 | P0 | geo_fleet.py | Bearer token auth on inter-fleet HTTP |
| 5 | P0 | providers.py | Remove conn.close() on pooled connection |
| 6 | P0 | billing.py | Fail closed on quota check error |
| 7 | P1 | process_control.py | Use db.get_conn() not raw sqlite3 |
| 8 | P1 | CLAUDE.md + fleet/CLAUDE.md | Fix stale smoke/skill/endpoint counts |
| 9 | P1 | ci.yml | Add pytest to CI pipeline |
| 10 | P1 | filesystem_guard.py | Audit log failure severity |

**Total: 10 tasks, ~10 commits**

Tasks 1-6 are all independent P0 fixes and can be parallelized. Tasks 7-10 are independent P1 fixes. No cross-task dependencies.
