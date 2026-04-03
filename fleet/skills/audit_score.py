"""Thin skill wrapper for the ScoreRift audit system."""
import logging

SKILL_NAME = "audit_score"
DESCRIPTION = "Run ScoreRift audit scoring and return system health assessment."
VERSION = "1.0.0"
COMPLEXITY = "low"
REQUIRES_NETWORK = False
SUITE = "ops"
TAGS = ["audit", "health", "scoring"]

log = logging.getLogger(SKILL_NAME)


def run(payload: dict, config: dict) -> dict:
    """Execute audit scoring or return status."""
    import audit_scorer

    action = payload.get("action", "run")

    if action == "status":
        scores = audit_scorer.get_latest_scores()
        divergences = audit_scorer.get_active_divergences()
        return {"status": "ok", "scores": scores, "divergences": divergences}

    tier = payload.get("tier", "medium")
    if tier not in audit_scorer.TIER_ORDER:
        return {"status": "error", "error": f"Invalid tier: {tier}"}

    try:
        result = audit_scorer.run_and_store(tier)
        return {"status": "ok", **result}
    except Exception as e:
        log.warning("Audit scoring failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
