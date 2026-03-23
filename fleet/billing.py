"""Per-tenant billing & metering — usage tracking, invoicing, resource quotas.

v0.300.00b: Billing/Metering per Tenant
- record_usage / get_tenant_usage / get_all_tenant_usage / check_quota
- calculate_invoice / get_pricing / export_invoice_csv
- get_quota / set_quota / enforce_quota / get_quota_usage
"""
import csv
import io
import logging
import time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — applied via ensure_billing_tables()
# ---------------------------------------------------------------------------

BILLING_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenant_usage (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_usage_tid
    ON tenant_usage (tenant_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_tenant_usage_skill
    ON tenant_usage (tenant_id, skill);

CREATE TABLE IF NOT EXISTS tenant_quotas (
    tenant_id TEXT PRIMARY KEY,
    max_tasks_day INTEGER DEFAULT 1000,
    max_tokens_day INTEGER DEFAULT 1000000,
    max_agents INTEGER DEFAULT 10,
    max_vram_gb REAL DEFAULT 8.0
);
"""

_tables_ensured = False


def _get_conn():
    """Lazy import db to avoid circular imports."""
    import db
    return db.get_conn()


def _retry_write(fn):
    """Lazy proxy to db._retry_write."""
    import db
    return db._retry_write(fn)


def _load_billing_config():
    """Load [billing] section from fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("billing", {})
    except Exception:
        log.warning("billing: could not load fleet.toml [billing]", exc_info=True)
        return {}


def ensure_billing_tables():
    """Create billing tables if they don't exist (idempotent)."""
    global _tables_ensured
    if _tables_ensured:
        return
    try:
        conn = _get_conn()
        conn.executescript(BILLING_SCHEMA)
        conn.close()
        _tables_ensured = True
    except Exception:
        log.warning("billing: failed to create tables", exc_info=True)


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def _period_start(period="month"):
    """Return epoch timestamp for the start of the given period."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - _dt.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:  # month
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------

def record_usage(tenant_id, skill, tokens_in, tokens_out, model, cost):
    """Record a single usage event for a tenant."""
    ensure_billing_tables()
    now = time.time()

    def _do():
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO tenant_usage
                   (tenant_id, skill, model, tokens_in, tokens_out, cost_usd, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tenant_id, skill, model, int(tokens_in), int(tokens_out),
                 float(cost), now),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("billing: record_usage failed for tenant=%s", tenant_id, exc_info=True)


def get_tenant_usage(tenant_id, period="month"):
    """Aggregated usage for one tenant, grouped by skill and model.

    Returns:
        {
            "tenant_id": str,
            "period": str,
            "total_tokens_in": int,
            "total_tokens_out": int,
            "total_cost_usd": float,
            "by_skill": [{skill, tokens_in, tokens_out, cost_usd, calls}],
            "by_model": [{model, tokens_in, tokens_out, cost_usd, calls}],
        }
    """
    ensure_billing_tables()
    since = _period_start(period)
    try:
        conn = _get_conn()
        # Totals
        row = conn.execute(
            """SELECT COALESCE(SUM(tokens_in), 0) AS ti,
                      COALESCE(SUM(tokens_out), 0) AS to_,
                      COALESCE(SUM(cost_usd), 0.0) AS cost
               FROM tenant_usage
               WHERE tenant_id = ? AND recorded_at >= ?""",
            (tenant_id, since),
        ).fetchone()
        total_in = row["ti"] if row else 0
        total_out = row["to_"] if row else 0
        total_cost = row["cost"] if row else 0.0

        # By skill
        by_skill = [
            dict(r) for r in conn.execute(
                """SELECT skill,
                          SUM(tokens_in) AS tokens_in,
                          SUM(tokens_out) AS tokens_out,
                          SUM(cost_usd) AS cost_usd,
                          COUNT(*) AS calls
                   FROM tenant_usage
                   WHERE tenant_id = ? AND recorded_at >= ?
                   GROUP BY skill ORDER BY cost_usd DESC""",
                (tenant_id, since),
            ).fetchall()
        ]

        # By model
        by_model = [
            dict(r) for r in conn.execute(
                """SELECT model,
                          SUM(tokens_in) AS tokens_in,
                          SUM(tokens_out) AS tokens_out,
                          SUM(cost_usd) AS cost_usd,
                          COUNT(*) AS calls
                   FROM tenant_usage
                   WHERE tenant_id = ? AND recorded_at >= ?
                   GROUP BY model ORDER BY cost_usd DESC""",
                (tenant_id, since),
            ).fetchall()
        ]
        conn.close()

        return {
            "tenant_id": tenant_id,
            "period": period,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "total_cost_usd": round(total_cost, 6),
            "by_skill": by_skill,
            "by_model": by_model,
        }
    except Exception:
        log.warning("billing: get_tenant_usage failed for %s", tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "period": period,
                "total_tokens_in": 0, "total_tokens_out": 0,
                "total_cost_usd": 0.0, "by_skill": [], "by_model": []}


def get_all_tenant_usage(period="month"):
    """Admin view — usage across all tenants.

    Returns list of per-tenant summaries.
    """
    ensure_billing_tables()
    since = _period_start(period)
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT tenant_id,
                      SUM(tokens_in) AS tokens_in,
                      SUM(tokens_out) AS tokens_out,
                      SUM(cost_usd) AS cost_usd,
                      COUNT(*) AS calls
               FROM tenant_usage
               WHERE recorded_at >= ?
               GROUP BY tenant_id ORDER BY cost_usd DESC""",
            (since,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("billing: get_all_tenant_usage failed", exc_info=True)
        return []


def check_quota(tenant_id):
    """Check remaining budget vs limit for a tenant.

    Returns:
        {
            "tenant_id": str,
            "quota": {max_tasks_day, max_tokens_day, max_agents, max_vram_gb},
            "used": {tasks_today, tokens_today},
            "remaining": {tasks, tokens},
            "exceeded": bool,
        }
    """
    ensure_billing_tables()
    try:
        conn = _get_conn()
        quota = _get_quota_row(conn, tenant_id)
        day_start = _period_start("day")

        row = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(tokens_in + tokens_out), 0) AS total_tok
               FROM tenant_usage
               WHERE tenant_id = ? AND recorded_at >= ?""",
            (tenant_id, day_start),
        ).fetchone()
        conn.close()

        tasks_today = row["cnt"] if row else 0
        tokens_today = row["total_tok"] if row else 0

        remaining_tasks = max(0, quota["max_tasks_day"] - tasks_today)
        remaining_tokens = max(0, quota["max_tokens_day"] - tokens_today)
        exceeded = remaining_tasks == 0 or remaining_tokens == 0

        return {
            "tenant_id": tenant_id,
            "quota": quota,
            "used": {"tasks_today": tasks_today, "tokens_today": tokens_today},
            "remaining": {"tasks": remaining_tasks, "tokens": remaining_tokens},
            "exceeded": exceeded,
        }
    except Exception:
        log.warning("billing: check_quota failed for %s", tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "exceeded": False,
                "quota": {}, "used": {}, "remaining": {}}


# ---------------------------------------------------------------------------
# Billing / Invoicing
# ---------------------------------------------------------------------------

def get_pricing():
    """Current pricing tiers from fleet.toml [billing.pricing].

    Returns dict with tokens_per_dollar, base_monthly, overage_rate, currency.
    """
    cfg = _load_billing_config()
    pricing = cfg.get("pricing", {})
    return {
        "currency": cfg.get("currency", "USD"),
        "tokens_per_dollar": pricing.get("tokens_per_dollar", 1_000_000),
        "base_monthly": pricing.get("base_monthly", 0),
        "overage_rate": pricing.get("overage_rate", 0.001),
    }


def calculate_invoice(tenant_id, period="month"):
    """Itemized bill for a tenant.

    Returns:
        {
            "tenant_id": str, "period": str, "currency": str,
            "line_items": [{skill, model, tokens_in, tokens_out, cost_usd, calls}],
            "subtotal": float, "base_monthly": float, "total": float,
        }
    """
    usage = get_tenant_usage(tenant_id, period)
    pricing = get_pricing()

    # Build line items from skill+model combos
    ensure_billing_tables()
    since = _period_start(period)
    line_items = []
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT skill, model,
                      SUM(tokens_in) AS tokens_in,
                      SUM(tokens_out) AS tokens_out,
                      SUM(cost_usd) AS cost_usd,
                      COUNT(*) AS calls
               FROM tenant_usage
               WHERE tenant_id = ? AND recorded_at >= ?
               GROUP BY skill, model ORDER BY cost_usd DESC""",
            (tenant_id, since),
        ).fetchall()
        conn.close()
        line_items = [dict(r) for r in rows]
    except Exception:
        log.warning("billing: calculate_invoice line items failed", exc_info=True)

    subtotal = usage.get("total_cost_usd", 0.0)
    base = pricing.get("base_monthly", 0)
    total = round(subtotal + base, 6)

    return {
        "tenant_id": tenant_id,
        "period": period,
        "currency": pricing.get("currency", "USD"),
        "line_items": line_items,
        "subtotal": round(subtotal, 6),
        "base_monthly": base,
        "total": total,
    }


def export_invoice_csv(tenant_id, period="month"):
    """Export an itemized invoice as CSV string."""
    invoice = calculate_invoice(tenant_id, period)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Skill", "Model", "Tokens In", "Tokens Out",
                     "Cost (USD)", "Calls"])
    for item in invoice.get("line_items", []):
        writer.writerow([
            item.get("skill", ""),
            item.get("model", ""),
            item.get("tokens_in", 0),
            item.get("tokens_out", 0),
            round(item.get("cost_usd", 0.0), 6),
            item.get("calls", 0),
        ])
    writer.writerow([])
    writer.writerow(["", "", "", "", "Subtotal", round(invoice["subtotal"], 6)])
    writer.writerow(["", "", "", "", "Base Monthly", invoice["base_monthly"]])
    writer.writerow(["", "", "", "", "Total", round(invoice["total"], 6)])
    return output.getvalue()


# ---------------------------------------------------------------------------
# Resource quotas
# ---------------------------------------------------------------------------

def _get_quota_row(conn, tenant_id):
    """Fetch or return defaults for a tenant's quota row."""
    row = conn.execute(
        "SELECT * FROM tenant_quotas WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if row:
        return dict(row)
    return {
        "tenant_id": tenant_id,
        "max_tasks_day": 1000,
        "max_tokens_day": 1_000_000,
        "max_agents": 10,
        "max_vram_gb": 8.0,
    }


def get_quota(tenant_id):
    """Return quota limits for a tenant."""
    ensure_billing_tables()
    try:
        conn = _get_conn()
        result = _get_quota_row(conn, tenant_id)
        conn.close()
        return result
    except Exception:
        log.warning("billing: get_quota failed for %s", tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "max_tasks_day": 1000,
                "max_tokens_day": 1_000_000, "max_agents": 10, "max_vram_gb": 8.0}


def set_quota(tenant_id, limits):
    """Update quota limits for a tenant (upsert).

    Args:
        limits: dict with any of max_tasks_day, max_tokens_day, max_agents, max_vram_gb
    """
    ensure_billing_tables()
    allowed = {"max_tasks_day", "max_tokens_day", "max_agents", "max_vram_gb"}
    safe = {k: v for k, v in limits.items() if k in allowed}
    if not safe:
        return

    def _do():
        conn = _get_conn()
        try:
            existing = conn.execute(
                "SELECT tenant_id FROM tenant_quotas WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if existing:
                sets = ", ".join(f"{k} = ?" for k in safe)
                conn.execute(
                    f"UPDATE tenant_quotas SET {sets} WHERE tenant_id = ?",
                    (*safe.values(), tenant_id),
                )
            else:
                defaults = {
                    "max_tasks_day": 1000, "max_tokens_day": 1_000_000,
                    "max_agents": 10, "max_vram_gb": 8.0,
                }
                defaults.update(safe)
                conn.execute(
                    """INSERT INTO tenant_quotas
                       (tenant_id, max_tasks_day, max_tokens_day, max_agents, max_vram_gb)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tenant_id, defaults["max_tasks_day"], defaults["max_tokens_day"],
                     defaults["max_agents"], defaults["max_vram_gb"]),
                )
            conn.commit()
        finally:
            conn.close()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("billing: set_quota failed for %s", tenant_id, exc_info=True)


def enforce_quota(tenant_id, resource="tokens"):
    """Check if the tenant is within limits for the given resource.

    Args:
        resource: "tokens" or "tasks"

    Returns True if within limits, False if quota exceeded.
    """
    status = check_quota(tenant_id)
    remaining = status.get("remaining", {})
    if resource == "tasks":
        return remaining.get("tasks", 1) > 0
    return remaining.get("tokens", 1) > 0


def get_quota_usage(tenant_id):
    """Current usage vs limits for a tenant.

    Returns:
        {
            "tenant_id": str,
            "tasks": {"used": int, "limit": int, "pct": float},
            "tokens": {"used": int, "limit": int, "pct": float},
            "agents": {"limit": int},
            "vram_gb": {"limit": float},
        }
    """
    status = check_quota(tenant_id)
    quota = status.get("quota", {})
    used = status.get("used", {})

    tasks_limit = quota.get("max_tasks_day", 1000)
    tokens_limit = quota.get("max_tokens_day", 1_000_000)
    tasks_used = used.get("tasks_today", 0)
    tokens_used = used.get("tokens_today", 0)

    return {
        "tenant_id": tenant_id,
        "tasks": {
            "used": tasks_used,
            "limit": tasks_limit,
            "pct": round(tasks_used / max(tasks_limit, 1) * 100, 1),
        },
        "tokens": {
            "used": tokens_used,
            "limit": tokens_limit,
            "pct": round(tokens_used / max(tokens_limit, 1) * 100, 1),
        },
        "agents": {"limit": quota.get("max_agents", 10)},
        "vram_gb": {"limit": quota.get("max_vram_gb", 8.0)},
    }
