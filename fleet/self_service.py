"""Self-Service Tenant Provisioning — sign-up to fleet-running flow.

v0.400.00b: Registration, plan management, API key lifecycle, onboarding
checklist.  Integrates with tenant_admin.py (CRUD), billing.py (quotas),
and sso.py (auth).

Dashboard endpoints registered as a Flask Blueprint (self_service_bp).
API keys are SHA-256 hashed before storage — plaintext is never persisted.
"""
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from security import require_role as _require_role_raw, safe_error as _safe_error

FLEET_DIR = Path(__file__).parent

log = logging.getLogger("self_service")

self_service_bp = Blueprint("self_service", __name__)


# ── Config helpers ────────────────────────────────────────────────────────

def _load_config():
    """Load fleet.toml config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    try:
        with open(FLEET_DIR / "fleet.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _require_role(role):
    return _require_role_raw(role, _load_config)


def _ss_config() -> dict:
    """Return [self_service] config with defaults."""
    cfg = _load_config()
    ss = cfg.get("self_service", {})
    return {
        "enabled": ss.get("enabled", False),
        "require_email_verification": ss.get("require_email_verification", True),
        "default_plan": ss.get("default_plan", "free"),
        "max_api_keys_per_tenant": ss.get("max_api_keys_per_tenant", 5),
    }


# ── DB helpers (lazy imports) ─────────────────────────────────────────────

def _get_conn():
    import db
    return db.get_conn()


def _retry_write(fn):
    import db
    return db._retry_write(fn)


# ── Schema ────────────────────────────────────────────────────────────────

SELF_SERVICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    created_at REAL NOT NULL,
    revoked_at REAL,
    scopes TEXT NOT NULL DEFAULT 'read,write'
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tid ON api_keys (tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys (key_prefix);

CREATE TABLE IF NOT EXISTS onboarding (
    tenant_id TEXT PRIMARY KEY,
    steps_json TEXT NOT NULL,
    started_at REAL NOT NULL,
    completed_at REAL
);
"""

_tables_ensured = False


def _ensure_tables():
    """Create self-service tables (idempotent)."""
    global _tables_ensured
    if _tables_ensured:
        return
    try:
        conn = _get_conn()
        conn.executescript(SELF_SERVICE_SCHEMA)
        _tables_ensured = True
    except Exception:
        log.warning("self_service: failed to create tables", exc_info=True)


# ── Plan definitions ──────────────────────────────────────────────────────

PLANS = {
    "free": {
        "name": "free",
        "display_name": "Free",
        "max_agents": 5,
        "max_tokens_day": 10_000,
        "max_tasks_day": 50,
        "price_monthly": 0,
        "description": "Get started — 5 agents, 10k tokens/day",
    },
    "starter": {
        "name": "starter",
        "display_name": "Starter",
        "max_agents": 10,
        "max_tokens_day": 100_000,
        "max_tasks_day": 500,
        "price_monthly": 29,
        "description": "Small teams — 10 agents, 100k tokens/day",
    },
    "pro": {
        "name": "pro",
        "display_name": "Pro",
        "max_agents": 25,
        "max_tokens_day": 1_000_000,
        "max_tasks_day": 5000,
        "price_monthly": 99,
        "description": "Scale up — 25 agents, 1M tokens/day",
    },
    "enterprise": {
        "name": "enterprise",
        "display_name": "Enterprise",
        "max_agents": 100,
        "max_tokens_day": 10_000_000,
        "max_tasks_day": 50_000,
        "price_monthly": -1,  # custom pricing
        "description": "Custom limits — contact sales",
    },
}

_DEFAULT_ONBOARDING_STEPS = {
    "account_created": False,
    "fleet_provisioned": False,
    "first_task_submitted": False,
    "billing_configured": False,
}


# ── Registration flow ────────────────────────────────────────────────────

def register_tenant(name: str, email: str, plan: str = "free") -> dict:
    """Create tenant, generate API key, set quota, return onboarding info.

    Raises ValueError if self-service is disabled or inputs are invalid.
    """
    import tenant_admin
    import billing

    ss = _ss_config()
    if not ss["enabled"]:
        raise ValueError("Self-service registration is disabled")

    name = name.strip()
    email = email.strip().lower()
    if not name:
        raise ValueError("Tenant name is required")
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")

    if plan not in PLANS:
        raise ValueError(f"Unknown plan: {plan}. Available: {', '.join(PLANS)}")

    plan_info = PLANS[plan]

    # Create tenant via tenant_admin
    config = {"email": email, "plan": plan}
    tenant_id = tenant_admin.create_tenant(name, config)

    # Set quota based on plan
    billing.set_quota(tenant_id, {
        "max_agents": plan_info["max_agents"],
        "max_tokens_day": plan_info["max_tokens_day"],
        "max_tasks_day": plan_info["max_tasks_day"],
    })

    # Generate initial API key
    _ensure_tables()
    api_key = _create_api_key(tenant_id, scopes="read,write")

    # Initialize onboarding
    _init_onboarding(tenant_id)
    complete_onboarding(tenant_id, "account_created")

    log.info("Self-service registration: tenant=%s email=%s plan=%s",
             tenant_id, email, plan)

    return {
        "tenant_id": tenant_id,
        "email": email,
        "plan": plan,
        "api_key": api_key,
        "onboarding": get_onboarding_status(tenant_id),
        "message": "Registration complete. Save your API key — it cannot be retrieved later.",
    }


def _init_onboarding(tenant_id: str):
    """Create onboarding record for a new tenant."""
    _ensure_tables()
    now = time.time()
    steps = json.dumps(_DEFAULT_ONBOARDING_STEPS)

    def _do():
        conn = _get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO onboarding (tenant_id, steps_json, started_at) "
            "VALUES (?, ?, ?)",
            (tenant_id, steps, now),
        )
        conn.commit()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("self_service: _init_onboarding failed for %s", tenant_id, exc_info=True)


def get_onboarding_status(tenant_id: str) -> dict:
    """Return onboarding checklist with completion status."""
    _ensure_tables()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM onboarding WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if not row:
            return {"tenant_id": tenant_id, "steps": _DEFAULT_ONBOARDING_STEPS,
                    "started_at": None, "completed_at": None, "pct_complete": 0}
        steps = json.loads(row["steps_json"])
        done = sum(1 for v in steps.values() if v)
        total = len(steps)
        pct = round(done / max(total, 1) * 100, 1)
        return {
            "tenant_id": tenant_id,
            "steps": steps,
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "pct_complete": pct,
        }
    except Exception:
        log.warning("self_service: get_onboarding_status failed for %s",
                     tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "steps": _DEFAULT_ONBOARDING_STEPS,
                "started_at": None, "completed_at": None, "pct_complete": 0}


def complete_onboarding(tenant_id: str, step: str):
    """Mark an onboarding step as complete."""
    _ensure_tables()

    def _do():
        conn = _get_conn()
        row = conn.execute(
            "SELECT steps_json FROM onboarding WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if not row:
            log.warning("self_service: no onboarding record for %s", tenant_id)
            return
        steps = json.loads(row["steps_json"])
        if step not in steps:
            log.warning("self_service: unknown onboarding step '%s'", step)
            return
        steps[step] = True
        all_done = all(steps.values())
        completed_at = time.time() if all_done else None
        conn.execute(
            "UPDATE onboarding SET steps_json = ?, completed_at = ? WHERE tenant_id = ?",
            (json.dumps(steps), completed_at, tenant_id),
        )
        conn.commit()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("self_service: complete_onboarding failed for %s/%s",
                     tenant_id, step, exc_info=True)


# ── Plan management ──────────────────────────────────────────────────────

def get_plans() -> list[dict]:
    """Return available plans with pricing info."""
    return list(PLANS.values())


def get_current_plan(tenant_id: str) -> dict:
    """Return the tenant's current plan with usage summary."""
    import tenant_admin
    import billing

    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    config = json.loads(tenant.get("config_json", "{}"))
    plan_name = config.get("plan", "free")
    plan_info = PLANS.get(plan_name, PLANS["free"]).copy()

    quota = billing.get_quota(tenant_id)
    usage = billing.get_quota_usage(tenant_id)

    plan_info["tenant_id"] = tenant_id
    plan_info["quota"] = quota
    plan_info["usage"] = usage
    return plan_info


def upgrade_plan(tenant_id: str, new_plan: str) -> dict:
    """Change tenant plan and adjust quotas.

    Returns updated plan info. Raises ValueError on invalid input.
    """
    import tenant_admin
    import billing

    if new_plan not in PLANS:
        raise ValueError(f"Unknown plan: {new_plan}. Available: {', '.join(PLANS)}")

    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    if tenant["status"] != "active":
        raise ValueError(f"Tenant {tenant_id} is {tenant['status']}")

    plan_info = PLANS[new_plan]

    # Update tenant config with new plan
    config = json.loads(tenant.get("config_json", "{}"))
    old_plan = config.get("plan", "free")
    config["plan"] = new_plan
    config["plan_changed_at"] = time.time()
    tenant_admin.update_tenant(tenant_id, {
        "config": config,
        "max_agents": plan_info["max_agents"],
    })

    # Update billing quotas
    billing.set_quota(tenant_id, {
        "max_agents": plan_info["max_agents"],
        "max_tokens_day": plan_info["max_tokens_day"],
        "max_tasks_day": plan_info["max_tasks_day"],
    })

    log.info("Plan upgrade: tenant=%s %s -> %s", tenant_id, old_plan, new_plan)

    return {
        "tenant_id": tenant_id,
        "old_plan": old_plan,
        "new_plan": new_plan,
        "effective_immediately": True,
        "plan_details": plan_info,
    }


# ── API key management ────────────────────────────────────────────────────

def _hash_key(key: str) -> str:
    """SHA-256 hash of an API key — never store plaintext."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _create_api_key(tenant_id: str, scopes: str = "read,write") -> str:
    """Generate a scoped API key. Returns the plaintext key (shown once)."""
    ss = _ss_config()
    _ensure_tables()

    # Check key limit
    conn = _get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM api_keys WHERE tenant_id = ? AND revoked_at IS NULL",
        (tenant_id,),
    ).fetchone()[0]
    max_keys = ss["max_api_keys_per_tenant"]
    if count >= max_keys:
        raise ValueError(f"API key limit reached ({max_keys}). Revoke an existing key first.")

    # Generate key: bk_<tenant_prefix>_<random>
    raw = secrets.token_urlsafe(32)
    prefix = f"bk_{tenant_id[:6]}"
    api_key = f"{prefix}_{raw}"
    key_hash = _hash_key(api_key)
    now = time.time()

    def _do():
        c = _get_conn()
        c.execute(
            "INSERT INTO api_keys (tenant_id, key_hash, key_prefix, created_at, scopes) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, key_hash, prefix, now, scopes),
        )
        c.commit()

    _retry_write(_do)
    log.info("API key generated: tenant=%s prefix=%s", tenant_id, prefix)
    return api_key


def generate_api_key(tenant_id: str) -> str:
    """Public interface — generate a scoped API key for a tenant."""
    import tenant_admin
    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    if tenant["status"] != "active":
        raise ValueError(f"Tenant {tenant_id} is {tenant['status']}")
    return _create_api_key(tenant_id)


def revoke_api_key(tenant_id: str, key_prefix: str):
    """Revoke an API key by its prefix. Raises ValueError if not found."""
    _ensure_tables()

    def _do():
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE api_keys SET revoked_at = ? "
            "WHERE tenant_id = ? AND key_prefix = ? AND revoked_at IS NULL",
            (time.time(), tenant_id, key_prefix),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"No active key with prefix '{key_prefix}' for tenant {tenant_id}")

    _retry_write(_do)
    log.info("API key revoked: tenant=%s prefix=%s", tenant_id, key_prefix)


def list_api_keys(tenant_id: str) -> list[dict]:
    """List active API keys for a tenant (masked — prefix only)."""
    _ensure_tables()
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT key_prefix, created_at, revoked_at, scopes "
            "FROM api_keys WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        return [
            {
                "prefix": r["key_prefix"],
                "created_at": r["created_at"],
                "revoked_at": r["revoked_at"],
                "scopes": r["scopes"],
                "active": r["revoked_at"] is None,
            }
            for r in rows
        ]
    except Exception:
        log.warning("self_service: list_api_keys failed for %s", tenant_id, exc_info=True)
        return []


# ── Dashboard Blueprint (/api/register, /api/onboarding, etc.) ───────────

@self_service_bp.route("/api/register", methods=["POST"])
def api_register():
    """POST /api/register — self-service registration {name, email, plan}."""
    try:
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        plan = data.get("plan", _ss_config()["default_plan"])
        if not name:
            return jsonify({"error": "name is required"}), 400
        if not email:
            return jsonify({"error": "email is required"}), 400
        result = register_tenant(name, email, plan)
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("api_register failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@self_service_bp.route("/api/onboarding/<tenant_id>", methods=["GET"])
def api_onboarding(tenant_id):
    """GET /api/onboarding/<tenant_id> — onboarding checklist."""
    try:
        status = get_onboarding_status(tenant_id)
        return jsonify(status)
    except Exception as e:
        log.warning("api_onboarding failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@self_service_bp.route("/api/plans", methods=["GET"])
def api_plans():
    """GET /api/plans — available plans with pricing."""
    try:
        return jsonify({"plans": get_plans()})
    except Exception as e:
        log.warning("api_plans failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@self_service_bp.route("/api/plans/upgrade", methods=["POST"])
def api_upgrade_plan():
    """POST /api/plans/upgrade — change plan {tenant_id, plan}."""
    try:
        data = request.get_json(silent=True) or {}
        tenant_id = data.get("tenant_id", "").strip()
        plan = data.get("plan", "").strip()
        if not tenant_id:
            return jsonify({"error": "tenant_id is required"}), 400
        if not plan:
            return jsonify({"error": "plan is required"}), 400
        result = upgrade_plan(tenant_id, plan)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("api_upgrade_plan failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@self_service_bp.route("/api/keys/generate", methods=["POST"])
def api_generate_key():
    """POST /api/keys/generate — generate API key {tenant_id}."""
    try:
        data = request.get_json(silent=True) or {}
        tenant_id = data.get("tenant_id", "").strip()
        if not tenant_id:
            return jsonify({"error": "tenant_id is required"}), 400
        key = generate_api_key(tenant_id)
        return jsonify({
            "api_key": key,
            "message": "Save this key now — it cannot be retrieved later.",
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("api_generate_key failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@self_service_bp.route("/api/keys/<prefix>", methods=["DELETE"])
def api_revoke_key(prefix):
    """DELETE /api/keys/<prefix> — revoke key by prefix."""
    try:
        data = request.get_json(silent=True) or {}
        tenant_id = data.get("tenant_id", "").strip()
        if not tenant_id:
            return jsonify({"error": "tenant_id is required"}), 400
        revoke_api_key(tenant_id, prefix)
        return jsonify({"status": "revoked", "prefix": prefix})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("api_revoke_key failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@self_service_bp.route("/api/keys", methods=["GET"])
def api_list_keys():
    """GET /api/keys — list keys (masked) for a tenant."""
    try:
        tenant_id = request.args.get("tenant_id", "").strip()
        if not tenant_id:
            return jsonify({"error": "tenant_id query param is required"}), 400
        keys = list_api_keys(tenant_id)
        return jsonify({"tenant_id": tenant_id, "keys": keys, "count": len(keys)})
    except Exception as e:
        log.warning("api_list_keys failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500
