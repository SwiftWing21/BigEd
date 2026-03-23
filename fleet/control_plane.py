"""SaaS Control Plane — unified fleet provisioning, orchestration, and platform health.

v0.400.00b: Multi-fleet management for SaaS deployments.
- provision_fleet / deprovision_fleet / get_fleet_status — tenant lifecycle
- list_managed_fleets / scale_fleet / migrate_fleet — orchestration
- get_platform_health / get_platform_metrics — aggregate observability
- Flask Blueprint with /api/platform/* endpoints (admin role required)

Integrates with tenant_admin, billing, sso, and compliance modules.
Config lives in fleet.toml [platform].
"""
import json
import logging
import secrets
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from security import (
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

FLEET_DIR = Path(__file__).parent

log = logging.getLogger("control_plane")

platform_bp = Blueprint("control_plane", __name__)


# ── Config loader (local — avoids circular import with dashboard) ──────────

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
    """Convenience wrapper for role-based access."""
    return _require_role_raw(role, _load_config)


def _platform_config():
    """Return [platform] config with defaults."""
    cfg = _load_config()
    pcfg = cfg.get("platform", {})
    return {
        "enabled": pcfg.get("enabled", False),
        "control_plane_mode": pcfg.get("control_plane_mode", False),
        "max_managed_fleets": pcfg.get("max_managed_fleets", 100),
        "default_agent_count": pcfg.get("default_agent_count", 4),
    }


# ── Lazy DB helpers ────────────────────────────────────────────────────────

def _get_conn():
    """Get a connection to the main fleet DB."""
    import db
    return db.get_conn()


def _retry_write(fn):
    """Lazy proxy to db._retry_write."""
    import db
    return db._retry_write(fn)


# ── Fleet Provisioning ────────────────────────────────────────────────────

def provision_fleet(tenant_id: str | None, config: dict | None = None) -> dict:
    """Provision a new managed fleet for a tenant.

    Creates the tenant (via tenant_admin), generates API keys, sets quota,
    and returns connection info.

    Args:
        tenant_id: Optional pre-assigned tenant ID.  If None a new tenant is
                   created via tenant_admin.create_tenant().
        config: Optional dict with name, max_agents, max_skills, quota overrides.

    Returns:
        dict with tenant_id, api_key, status, connection_info, quota.
    """
    import tenant_admin
    import billing

    pcfg = _platform_config()
    if not pcfg["enabled"]:
        raise ValueError("Platform control plane is disabled — "
                         "set [platform] enabled = true in fleet.toml")

    config = config or {}
    name = config.get("name", f"fleet-{uuid.uuid4().hex[:8]}")
    max_agents = config.get("max_agents", pcfg["default_agent_count"])

    # Check fleet limit
    existing = tenant_admin.list_tenants()
    if len(existing) >= pcfg["max_managed_fleets"]:
        raise ValueError(
            f"Max managed fleets ({pcfg['max_managed_fleets']}) reached"
        )

    # Create tenant if no ID provided
    if tenant_id is None:
        tenant_id = tenant_admin.create_tenant(name, {
            "max_agents": max_agents,
            "max_skills": config.get("max_skills", 50),
            "provisioned_by": "control_plane",
        })
    else:
        # Verify tenant exists
        tenant = tenant_admin.get_tenant(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

    # Generate API key for this fleet
    api_key = f"fleet_{secrets.token_urlsafe(32)}"

    # Store API key in tenant config
    tenant_admin.update_tenant(tenant_id, {
        "config": {
            "api_key_hash": _hash_key(api_key),
            "provisioned_at": time.time(),
            "provisioned_by": "control_plane",
            "region": config.get("region", "local"),
        },
    })

    # Set up billing quota
    quota_overrides = {}
    if "max_tasks_day" in config:
        quota_overrides["max_tasks_day"] = config["max_tasks_day"]
    if "max_tokens_day" in config:
        quota_overrides["max_tokens_day"] = config["max_tokens_day"]
    if max_agents:
        quota_overrides["max_agents"] = max_agents
    if quota_overrides:
        billing.set_quota(tenant_id, quota_overrides)

    quota = billing.get_quota(tenant_id)

    log.info("Provisioned fleet for tenant %s (%s)", tenant_id, name)

    return {
        "tenant_id": tenant_id,
        "name": name,
        "api_key": api_key,
        "status": "active",
        "connection_info": {
            "dashboard_port": _load_config().get("dashboard", {}).get("port", 5555),
            "region": config.get("region", "local"),
        },
        "quota": quota,
    }


def deprovision_fleet(tenant_id: str) -> dict:
    """Deprovision a managed fleet — suspend tenant, archive data, release resources.

    Args:
        tenant_id: The tenant to deprovision.

    Returns:
        dict with tenant_id, status, archived_at.
    """
    import tenant_admin
    import billing

    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    if tenant["status"] == "deleted":
        raise ValueError(f"Tenant {tenant_id} already deleted")

    # Suspend first to prevent new tasks
    if tenant["status"] == "active":
        tenant_admin.suspend_tenant(tenant_id)

    # Archive metadata
    archived_at = time.time()
    tenant_admin.update_tenant(tenant_id, {
        "config": {
            "archived_at": archived_at,
            "deprovisioned_by": "control_plane",
        },
    })

    # Soft-delete the tenant
    tenant_admin.delete_tenant(tenant_id)

    log.info("Deprovisioned fleet for tenant %s", tenant_id)

    return {
        "tenant_id": tenant_id,
        "status": "deprovisioned",
        "archived_at": archived_at,
    }


def get_fleet_status(tenant_id: str) -> dict:
    """Get comprehensive status for a managed fleet.

    Returns health, usage, quota, agents, and billing summary.
    """
    import tenant_admin
    import billing

    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    # Billing and quota
    usage = billing.get_tenant_usage(tenant_id, period="month")
    quota_usage = billing.get_quota_usage(tenant_id)
    invoice = billing.calculate_invoice(tenant_id, period="month")

    # Parse tenant config
    try:
        tenant_config = json.loads(tenant.get("config_json", "{}") or "{}")
    except Exception:
        tenant_config = {}

    return {
        "tenant_id": tenant_id,
        "name": tenant.get("name", ""),
        "status": tenant.get("status", "unknown"),
        "created_at": tenant.get("created_at"),
        "region": tenant_config.get("region", "local"),
        "health": _compute_fleet_health(tenant, quota_usage),
        "agents": {
            "max": tenant.get("max_agents", 5),
            "active": 0,  # populated by live supervisor query when available
        },
        "usage": {
            "period": usage.get("period", "month"),
            "tokens_in": usage.get("total_tokens_in", 0),
            "tokens_out": usage.get("total_tokens_out", 0),
            "cost_usd": usage.get("total_cost_usd", 0.0),
        },
        "quota": quota_usage,
        "billing": {
            "current_month_total": invoice.get("total", 0.0),
            "currency": invoice.get("currency", "USD"),
        },
        "skills": tenant.get("skill_count", len(
            tenant_admin.get_tenant_skills(tenant_id)
        )),
    }


# ── Multi-Fleet Orchestration ─────────────────────────────────────────────

def list_managed_fleets() -> list[dict]:
    """List all managed tenant fleets with status summary.

    Returns a list of fleet summaries (tenant_id, name, status, agents, usage).
    """
    import tenant_admin
    import billing

    tenants = tenant_admin.list_tenants()
    fleets = []
    for t in tenants:
        tid = t["id"]
        try:
            usage = billing.get_tenant_usage(tid, period="month")
            quota = billing.get_quota(tid)
        except Exception:
            log.warning("Failed to get usage/quota for tenant %s", tid)
            usage = {"total_tokens_in": 0, "total_tokens_out": 0,
                     "total_cost_usd": 0.0}
            quota = {}

        fleets.append({
            "tenant_id": tid,
            "name": t.get("name", ""),
            "status": t.get("status", "unknown"),
            "created_at": t.get("created_at"),
            "max_agents": t.get("max_agents", 5),
            "tokens_used": (usage.get("total_tokens_in", 0)
                            + usage.get("total_tokens_out", 0)),
            "cost_usd": usage.get("total_cost_usd", 0.0),
            "quota_agents": quota.get("max_agents", 10),
        })

    return fleets


def scale_fleet(tenant_id: str, agents: int) -> dict:
    """Adjust agent count for a tenant fleet.

    Updates both the tenant record and the billing quota.

    Args:
        tenant_id: Target tenant.
        agents: New desired agent count.

    Returns:
        dict with tenant_id, previous_agents, new_agents, status.
    """
    import tenant_admin
    import billing

    if agents < 1:
        raise ValueError("Agent count must be at least 1")

    pcfg = _platform_config()
    max_allowed = pcfg.get("max_managed_fleets", 100) * pcfg["default_agent_count"]
    if agents > max_allowed:
        raise ValueError(f"Agent count {agents} exceeds platform limit")

    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    if tenant["status"] != "active":
        raise ValueError(f"Cannot scale {tenant['status']} tenant")

    previous = tenant.get("max_agents", 5)

    # Update tenant record
    tenant_admin.update_tenant(tenant_id, {"max_agents": agents})

    # Update billing quota
    billing.set_quota(tenant_id, {"max_agents": agents})

    log.info("Scaled tenant %s: %d -> %d agents", tenant_id, previous, agents)

    return {
        "tenant_id": tenant_id,
        "previous_agents": previous,
        "new_agents": agents,
        "status": "scaled",
    }


def migrate_fleet(tenant_id: str, target_region: str) -> dict:
    """Prepare a fleet migration to a target region (metadata only).

    Actual data migration is handled by the operator via federation deploy.
    This records the migration intent and validates the tenant.

    Args:
        tenant_id: Tenant to migrate.
        target_region: Target region identifier.

    Returns:
        dict with migration_id, tenant_id, source_region, target_region, status.
    """
    import tenant_admin

    tenant = tenant_admin.get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    if tenant["status"] != "active":
        raise ValueError(f"Cannot migrate {tenant['status']} tenant — activate first")

    try:
        tenant_config = json.loads(tenant.get("config_json", "{}") or "{}")
    except Exception:
        tenant_config = {}

    source_region = tenant_config.get("region", "local")
    if source_region == target_region:
        raise ValueError(f"Tenant already in region {target_region}")

    migration_id = f"mig_{uuid.uuid4().hex[:12]}"

    # Record migration intent in tenant config
    tenant_config["migration"] = {
        "id": migration_id,
        "source": source_region,
        "target": target_region,
        "initiated_at": time.time(),
        "status": "pending",
    }
    tenant_admin.update_tenant(tenant_id, {
        "config": tenant_config,
    })

    log.info("Migration %s: tenant %s from %s -> %s",
             migration_id, tenant_id, source_region, target_region)

    return {
        "migration_id": migration_id,
        "tenant_id": tenant_id,
        "source_region": source_region,
        "target_region": target_region,
        "status": "pending",
    }


# ── Control Plane Health ──────────────────────────────────────────────────

def get_platform_health() -> dict:
    """Aggregate health across all managed fleets.

    Returns overall platform status, per-fleet health summary, and alerts.
    """
    import tenant_admin
    import billing

    tenants = tenant_admin.list_tenants()
    total = len(tenants)
    healthy = 0
    degraded = 0
    critical = 0
    alerts = []

    for t in tenants:
        tid = t["id"]
        status = t.get("status", "unknown")
        if status == "active":
            try:
                quota_usage = billing.get_quota_usage(tid)
                tasks_pct = quota_usage.get("tasks", {}).get("pct", 0)
                tokens_pct = quota_usage.get("tokens", {}).get("pct", 0)
                if tasks_pct > 90 or tokens_pct > 90:
                    degraded += 1
                    alerts.append({
                        "tenant_id": tid,
                        "severity": "warning",
                        "message": f"Quota usage high: tasks={tasks_pct}%, tokens={tokens_pct}%",
                    })
                else:
                    healthy += 1
            except Exception:
                degraded += 1
                log.warning("Health check failed for tenant %s", tid)
        elif status == "suspended":
            critical += 1
            alerts.append({
                "tenant_id": tid,
                "severity": "critical",
                "message": "Fleet suspended",
            })
        else:
            degraded += 1

    if critical > 0:
        overall = "critical"
    elif degraded > 0:
        overall = "degraded"
    elif total == 0:
        overall = "no_fleets"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "timestamp": time.time(),
        "fleets": {
            "total": total,
            "healthy": healthy,
            "degraded": degraded,
            "critical": critical,
        },
        "alerts": alerts[:50],  # cap alerts to prevent payload bloat
    }


def get_platform_metrics() -> dict:
    """Platform-wide metrics: total tenants, agents, tasks, revenue.

    Aggregates billing data across all tenants for the current month.
    """
    import tenant_admin
    import billing

    tenants = tenant_admin.list_tenants()
    all_usage = billing.get_all_tenant_usage(period="month")

    total_agents = sum(t.get("max_agents", 0) for t in tenants)
    active_tenants = sum(1 for t in tenants if t.get("status") == "active")
    total_tokens = sum(
        u.get("tokens_in", 0) + u.get("tokens_out", 0) for u in all_usage
    )
    total_revenue = sum(u.get("cost_usd", 0.0) for u in all_usage)
    total_tasks = sum(u.get("calls", 0) for u in all_usage)

    return {
        "timestamp": time.time(),
        "tenants": {
            "total": len(tenants),
            "active": active_tenants,
        },
        "agents": {
            "total_allocated": total_agents,
        },
        "tasks": {
            "total_this_month": total_tasks,
        },
        "tokens": {
            "total_this_month": total_tokens,
        },
        "revenue": {
            "total_this_month": round(total_revenue, 6),
            "currency": billing.get_pricing().get("currency", "USD"),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────

def _hash_key(key: str) -> str:
    """Hash an API key for storage (SHA-256)."""
    import hashlib
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _compute_fleet_health(tenant: dict, quota_usage: dict) -> dict:
    """Compute a health summary for a single fleet."""
    status = tenant.get("status", "unknown")
    if status != "active":
        return {"status": status, "score": 0}

    tasks_pct = quota_usage.get("tasks", {}).get("pct", 0)
    tokens_pct = quota_usage.get("tokens", {}).get("pct", 0)
    max_pct = max(tasks_pct, tokens_pct)

    if max_pct > 95:
        health_status = "critical"
        score = 20
    elif max_pct > 80:
        health_status = "warning"
        score = 60
    elif max_pct > 50:
        health_status = "good"
        score = 80
    else:
        health_status = "healthy"
        score = 100

    return {
        "status": health_status,
        "score": score,
        "quota_tasks_pct": tasks_pct,
        "quota_tokens_pct": tokens_pct,
    }


# ── Dashboard Blueprint (Flask) ──────────────────────────────────────────

@platform_bp.route("/api/platform/provision", methods=["POST"])
@_require_role("admin")
def api_provision_fleet():
    """POST /api/platform/provision — provision a new managed fleet."""
    try:
        data = request.get_json(silent=True) or {}
        tenant_id = data.get("tenant_id")
        config = data.get("config", {})
        if not config.get("name") and not tenant_id:
            return jsonify({"error": "config.name is required for new fleets"}), 400
        result = provision_fleet(tenant_id, config)
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("provision_fleet failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@platform_bp.route("/api/platform/<tenant_id>", methods=["DELETE"])
@_require_role("admin")
def api_deprovision_fleet(tenant_id):
    """DELETE /api/platform/<tenant_id> — deprovision a managed fleet."""
    try:
        result = deprovision_fleet(tenant_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("deprovision_fleet failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@platform_bp.route("/api/platform/fleets", methods=["GET"])
@_require_role("admin")
def api_list_fleets():
    """GET /api/platform/fleets — list all managed fleets."""
    try:
        fleets = list_managed_fleets()
        return jsonify({"fleets": fleets, "count": len(fleets)})
    except Exception as e:
        log.warning("list_managed_fleets failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@platform_bp.route("/api/platform/<tenant_id>/status", methods=["GET"])
@_require_role("admin")
def api_fleet_status(tenant_id):
    """GET /api/platform/<tenant_id>/status — fleet status."""
    try:
        result = get_fleet_status(tenant_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log.warning("get_fleet_status failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@platform_bp.route("/api/platform/<tenant_id>/scale", methods=["POST"])
@_require_role("admin")
def api_scale_fleet(tenant_id):
    """POST /api/platform/<tenant_id>/scale — scale fleet agent count."""
    try:
        data = request.get_json(silent=True) or {}
        agents = data.get("agents")
        if agents is None:
            return jsonify({"error": "agents count is required"}), 400
        if not isinstance(agents, int) or agents < 1:
            return jsonify({"error": "agents must be a positive integer"}), 400
        result = scale_fleet(tenant_id, agents)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("scale_fleet failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@platform_bp.route("/api/platform/health", methods=["GET"])
@_require_role("admin")
def api_platform_health():
    """GET /api/platform/health — aggregate platform health."""
    try:
        result = get_platform_health()
        return jsonify(result)
    except Exception as e:
        log.warning("get_platform_health failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@platform_bp.route("/api/platform/metrics", methods=["GET"])
@_require_role("admin")
def api_platform_metrics():
    """GET /api/platform/metrics — platform-wide metrics."""
    try:
        result = get_platform_metrics()
        return jsonify(result)
    except Exception as e:
        log.warning("get_platform_metrics failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500
