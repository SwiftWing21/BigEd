"""Analyze fleet API cost — per-skill/model breakdown with tier-downgrade recommendations.

Queries the usage table for the last N days, computes total cost, call count,
and average tokens per skill, identifies expensive skills (cost_per_call > $0.01
with 5+ calls), and returns recommendations for complexity tier downgrades.

Payload:
  days    int   lookback window in days (default: 7)

Returns:
  {"status": "ok", "period_days": N, "top_costs": [...], "recommendations": [...]}
"""
import logging

SKILL_NAME = "cost_analyze"
DESCRIPTION = "Analyze fleet API costs per skill/model with tier-downgrade recommendations"
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

log = logging.getLogger(__name__)

_EXPENSIVE_THRESHOLD = 0.01  # cost per call in USD
_MIN_CALLS = 5               # minimum calls to flag


def run(payload, config):
    try:
        import db

        days = max(1, int(payload.get("days", 7)))

        with db.get_conn() as conn:
            # Per-skill cost breakdown
            skill_rows = conn.execute(
                """SELECT skill, model,
                          COUNT(*) as call_count,
                          SUM(cost_usd) as total_cost,
                          SUM(input_tokens) as total_input,
                          SUM(output_tokens) as total_output
                   FROM usage
                   WHERE created_at >= datetime('now', ?)
                   GROUP BY skill, model
                   ORDER BY total_cost DESC""",
                (f"-{days} days",),
            ).fetchall()

        if not skill_rows:
            return {
                "status": "ok",
                "period_days": days,
                "total_cost": 0.0,
                "total_calls": 0,
                "top_costs": [],
                "recommendations": [],
                "message": "No usage data in the period",
            }

        # Build cost entries
        top_costs = []
        grand_total_cost = 0.0
        grand_total_calls = 0

        for row in skill_rows:
            call_count = row["call_count"]
            total_cost = row["total_cost"] or 0.0
            total_input = row["total_input"] or 0
            total_output = row["total_output"] or 0
            avg_tokens = round((total_input + total_output) / max(call_count, 1))
            cost_per_call = round(total_cost / max(call_count, 1), 6)

            grand_total_cost += total_cost
            grand_total_calls += call_count

            top_costs.append({
                "skill": row["skill"],
                "model": row["model"],
                "call_count": call_count,
                "total_cost": round(total_cost, 4),
                "cost_per_call": cost_per_call,
                "avg_tokens": avg_tokens,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
            })

        # Identify expensive skills and recommend downgrades
        recommendations = []
        for entry in top_costs:
            if (entry["cost_per_call"] > _EXPENSIVE_THRESHOLD
                    and entry["call_count"] >= _MIN_CALLS):
                model = entry["model"]
                # Suggest tier downgrade based on current model
                suggestion = _suggest_downgrade(model)
                if suggestion:
                    potential_savings = round(
                        entry["total_cost"] * suggestion["savings_pct"], 4
                    )
                    recommendations.append({
                        "skill": entry["skill"],
                        "current_model": model,
                        "suggested_model": suggestion["model"],
                        "reason": suggestion["reason"],
                        "cost_per_call": entry["cost_per_call"],
                        "call_count": entry["call_count"],
                        "potential_savings": potential_savings,
                    })

        recommendations.sort(key=lambda x: x["potential_savings"], reverse=True)

        return {
            "status": "ok",
            "period_days": days,
            "total_cost": round(grand_total_cost, 4),
            "total_calls": grand_total_calls,
            "top_costs": top_costs[:20],  # top 20 by cost
            "recommendations": recommendations,
        }

    except Exception:
        log.warning("cost_analyze failed", exc_info=True)
        return {"status": "error", "message": "cost analysis failed — see logs"}


def _suggest_downgrade(model: str) -> dict | None:
    """Suggest a cheaper model tier based on current model name."""
    model_lower = model.lower()

    # Opus -> Sonnet
    if "opus" in model_lower:
        return {
            "model": "claude-sonnet-4-6",
            "reason": "Opus is premium tier — Sonnet handles most tasks at lower cost",
            "savings_pct": 0.6,
        }

    # Sonnet -> Haiku
    if "sonnet" in model_lower:
        return {
            "model": "claude-haiku-4-5",
            "reason": "Sonnet may be over-provisioned for simple skills — try Haiku",
            "savings_pct": 0.7,
        }

    # Large local models -> smaller ones
    if "8b" in model_lower or "7b" in model_lower:
        return {
            "model": "qwen3:4b",
            "reason": "8B model may be overkill — 4B handles routine tasks",
            "savings_pct": 0.3,
        }

    return None
