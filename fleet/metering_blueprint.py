"""Billing, usage, API gate, and scaling endpoints.

Extracted from dashboard.py (Phase 5 of dashboard decomposition).
"""
import logging
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request

from dashboard_utils import (
    FLEET_DIR, _load_config, get_conn, query,
    _get_request_role, _require_role,
    _check_rate_limit, _safe_error,
)

log = logging.getLogger("dashboard.metering")

metering_bp = Blueprint("metering", __name__)


# ── CT-2: Cost Intelligence ─────────────────────────────────────────────────

@metering_bp.route("/api/usage")
def api_usage():
    """CT-2: Token usage aggregates by skill/model/agent."""
    try:
        import db
        period = request.args.get("period", "week")
        group = request.args.get("group", "skill")
        return jsonify(db.get_usage_summary(period, group))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/usage/delta")
def api_usage_delta():
    """CT-2: Compare usage between two date ranges."""
    try:
        import db
        from_start = request.args.get("from_start", "")
        from_end = request.args.get("from_end", "")
        to_start = request.args.get("to_start", "")
        to_end = request.args.get("to_end", "")
        if not all([from_start, from_end, to_start, to_end]):
            return jsonify({"error": "Required params: from_start, from_end, to_start, to_end"}), 400
        return jsonify(db.get_usage_delta(from_start, from_end, to_start, to_end))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/usage/budgets")
def api_usage_budgets():
    """CT-4: Token budget status — daily spend vs configured limits."""
    try:
        config = _load_config()
        budgets = config.get("budgets", {})
        if not budgets:
            return jsonify({"budgets": [], "message": "No budgets configured"})

        import db
        summary = db.get_usage_summary(period="day", group_by="skill")
        spent_map = {r["skill"]: r.get("total_cost", 0) or 0 for r in summary}

        # Filter out non-budget config keys (period, enforcement, etc.)
        enforcement = budgets.get("enforcement", "block")
        period = budgets.get("period", "day")
        result = []
        for skill, limit_usd in sorted(budgets.items()):
            if not isinstance(limit_usd, (int, float)):
                continue
            spent = spent_map.get(skill, 0)
            result.append({
                "skill": skill,
                "budget_usd": limit_usd,
                "spent_usd": round(spent, 6),
                "remaining_usd": round(max(0, limit_usd - spent), 6),
                "exceeded": spent >= limit_usd,
                "pct_used": round(spent / limit_usd * 100, 1) if limit_usd > 0 else 0,
            })
        return jsonify({"budgets": result, "enforcement": enforcement, "period": period})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/usage/dashboard")
def api_usage_dashboard():
    """Cost intelligence dashboard — live spend, projections, per-provider breakdown."""
    try:
        conn = get_conn()
        result = {"providers": {}, "today": {}, "week": {}, "month": {}, "projection": {}}

        # Per-provider totals (today)
        _allowed_intervals = {"-1 day", "-7 days", "-30 days"}
        for period, label, interval in [
            ("today", "Today", "-1 day"),
            ("week", "7 days", "-7 days"),
            ("month", "30 days", "-30 days"),
        ]:
            if interval not in _allowed_intervals:
                raise ValueError(f"Invalid interval: {interval!r}")
            rows = conn.execute("""
                SELECT provider,
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cost_usd), 0) as cost_usd,
                       COUNT(*) as calls
                FROM usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY provider
            """, (interval,)).fetchall()
            period_data = {}
            total_cost = 0
            total_tokens = 0
            for r in rows:
                p = r["provider"] or "local"
                period_data[p] = {
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                    "cost_usd": round(r["cost_usd"], 4),
                    "calls": r["calls"],
                }
                total_cost += r["cost_usd"]
                total_tokens += r["input_tokens"] + r["output_tokens"]
            period_data["_total"] = {
                "cost_usd": round(total_cost, 4),
                "tokens": total_tokens,
                "calls": sum(d["calls"] for d in period_data.values() if isinstance(d, dict) and "calls" in d),
            }
            result[period] = period_data

        # Top skills by cost (last 7 days)
        top_skills = conn.execute("""
            SELECT skill, provider,
                   COALESCE(SUM(cost_usd), 0) as cost_usd,
                   COALESCE(SUM(input_tokens + output_tokens), 0) as tokens,
                   COUNT(*) as calls
            FROM usage
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY skill, provider
            ORDER BY cost_usd DESC
            LIMIT 20
        """).fetchall()
        result["top_skills"] = [dict(r) for r in top_skills]

        # Daily cost trend (last 14 days)
        daily = conn.execute("""
            SELECT DATE(created_at) as day,
                   COALESCE(SUM(cost_usd), 0) as cost_usd,
                   COALESCE(SUM(input_tokens + output_tokens), 0) as tokens
            FROM usage
            WHERE created_at >= datetime('now', '-14 days')
            GROUP BY DATE(created_at)
            ORDER BY day
        """).fetchall()
        result["daily_trend"] = [dict(r) for r in daily]

        # Projection: based on 7-day average
        if result["week"].get("_total", {}).get("cost_usd", 0) > 0:
            weekly_cost = result["week"]["_total"]["cost_usd"]
            result["projection"] = {
                "monthly_usd": round(weekly_cost * 4.3, 2),
                "yearly_usd": round(weekly_cost * 52, 2),
                "daily_avg_usd": round(weekly_cost / 7, 4),
            }

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/usage/regression")
def api_usage_regression():
    """CT-3: Flag skills with >20% token increase vs previous period."""
    try:
        import db

        now = datetime.now()
        # Compare last 7 days vs previous 7 days
        to_end = now.strftime("%Y-%m-%d %H:%M:%S")
        to_start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        from_end = to_start
        from_start = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

        deltas = db.get_usage_delta(from_start, from_end, to_start, to_end)
        regressions = [d for d in deltas if d.get("delta_pct", 0) > 20]
        return jsonify({
            "period": {"from": f"{from_start} to {from_end}", "to": f"{to_start} to {to_end}"},
            "regressions": regressions,
            "total_skills_checked": len(deltas),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── API Gate ────────────────────────────────────────────────────────────────

@metering_bp.route("/api/gate/status")
def api_gate_status():
    import api_gate
    return jsonify(api_gate.status())


@metering_bp.route("/api/gate/enable", methods=["POST"])
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
    cfg = _load_config()
    from config import is_offline, is_air_gap
    if is_offline(cfg) or is_air_gap(cfg):
        return jsonify({"error": "Cannot enable API gate — offline_mode or air_gap_mode is active"}), 409
    result = api_gate.enable(budget, providers, ttl, drain)
    return jsonify(result)


@metering_bp.route("/api/gate/disable", methods=["POST"])
def api_gate_disable():
    import api_gate
    return jsonify(api_gate.disable())


@metering_bp.route("/api/gate/drain-mode", methods=["PUT"])
def api_gate_drain_mode():
    import api_gate
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "graceful")
    if mode not in ("graceful", "hard"):
        return jsonify({"error": "mode must be 'graceful' or 'hard'"}), 400
    return jsonify(api_gate.set_drain_mode(mode))


@metering_bp.route("/api/gate/ring")
def api_gate_ring():
    import api_gate
    limit = request.args.get("limit", 200, type=int)
    return jsonify(api_gate.get_ring(limit))


# ── Billing / Metering per Tenant ───────────────────────────────────────────

@metering_bp.route("/api/billing/<tenant_id>/usage")
def api_billing_usage(tenant_id):
    """Per-tenant usage summary for a billing period."""
    try:
        from billing import get_tenant_usage
        period = request.args.get("period", "month")
        return jsonify(get_tenant_usage(tenant_id, period))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/billing/<tenant_id>/invoice")
def api_billing_invoice(tenant_id):
    """Itemized invoice for a tenant."""
    try:
        from billing import calculate_invoice, export_invoice_csv
        period = request.args.get("period", "month")
        fmt = request.args.get("format", "json")
        if fmt == "csv":
            csv_data = export_invoice_csv(tenant_id, period)
            return Response(csv_data, mimetype="text/csv",
                            headers={"Content-Disposition":
                                     f"attachment; filename=invoice_{tenant_id}_{period}.csv"})
        return jsonify(calculate_invoice(tenant_id, period))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/billing/<tenant_id>/quota")
def api_billing_quota(tenant_id):
    """Quota status — current usage vs limits."""
    try:
        from billing import get_quota_usage
        return jsonify(get_quota_usage(tenant_id))
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/billing/<tenant_id>/quota", methods=["PUT"])
def api_billing_quota_update(tenant_id):
    """Update quota limits for a tenant (admin only)."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        from billing import set_quota, get_quota
        set_quota(tenant_id, data)
        return jsonify({"status": "updated", "quota": get_quota(tenant_id)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/billing/overview")
def api_billing_overview():
    """Admin view — usage across all tenants."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from billing import get_all_tenant_usage
        period = request.args.get("period", "month")
        return jsonify({"period": period, "tenants": get_all_tenant_usage(period)})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/billing/pricing")
def api_billing_pricing():
    """Current pricing tiers from config."""
    try:
        from billing import get_pricing
        return jsonify(get_pricing())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Predictive Scaling ──────────────────────────────────────────────────────

@metering_bp.route("/api/scaling/prediction")
def api_scaling_prediction():
    """Current ML prediction vs actual agent count."""
    try:
        from predictive_scaler import get_prediction_summary
        return jsonify(get_prediction_summary())
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@metering_bp.route("/api/scaling/retrain", methods=["POST"])
def api_scaling_retrain():
    """Trigger scaler model retrain from historical data."""
    deny = _require_role("admin")
    if deny:
        return deny
    try:
        from predictive_scaler import train_scaler_model
        result = train_scaler_model()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500
