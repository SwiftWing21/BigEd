"""
Token Optimizer — Analyzes and optimizes fleet token usage for cost efficiency.

Strategies applied (in order of impact):
  1. Prompt caching     — cache_control ephemeral on stable content (90% input reduction)
  2. Model routing      — Haiku for simple, Sonnet for standard, Opus only for complex
  3. Context pruning    — trim history, use references not repetition
  4. Batch consolidation — Message Batches API for non-real-time (50% cost reduction)
  5. Local-first        — route to Ollama when quality bar is met (100% cost reduction)

Usage:
    lead_client.py task '{"type": "token_optimizer"}'
    lead_client.py task '{"type": "token_optimizer", "payload": {"action": "audit"}}'
    lead_client.py task '{"type": "token_optimizer", "payload": {"action": "recommend"}}'
"""
import json
import os
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "token_optimizer"
DESCRIPTION = "Analyze fleet token usage and recommend cost optimizations."
REQUIRES_NETWORK = False


# ── Cost efficiency rules ────────────────────────────────────────────────────

EFFICIENCY_RULES = [
    {
        "id": "cache_stable_system",
        "name": "Cache stable system prompts",
        "description": "Add cache_control: {type: 'ephemeral'} to system prompts and tool definitions. "
                       "Reduces input cost by up to 90% for repeated calls.",
        "impact": "high",
        "savings_pct": 90,
        "applies_to": ["claude"],
    },
    {
        "id": "route_simple_to_haiku",
        "name": "Route simple tasks to Haiku",
        "description": "Tasks like skill_test, benchmark, flashcard can use claude-haiku-4-5 "
                       "($0.80/M input) instead of Sonnet ($3.00/M). 73% input cost reduction.",
        "impact": "high",
        "savings_pct": 73,
        "applies_to": ["claude"],
    },
    {
        "id": "local_first",
        "name": "Use local Ollama for non-critical tasks",
        "description": "Route code_quality, summarize, flashcard, rag_query to local qwen3:8b. "
                       "100% API cost reduction. Quality is sufficient for these tasks.",
        "impact": "high",
        "savings_pct": 100,
        "applies_to": ["claude", "gemini"],
    },
    {
        "id": "batch_api",
        "name": "Use Message Batches API for bulk operations",
        "description": "Non-real-time tasks (code_review batches, security_audit, evolution_coordinator) "
                       "can use Anthropic's Batches API for 50% cost reduction.",
        "impact": "medium",
        "savings_pct": 50,
        "applies_to": ["claude"],
    },
    {
        "id": "trim_context_history",
        "name": "Trim conversation history",
        "description": "Keep only last 3-5 messages in context instead of full history. "
                       "Reduces input tokens by 30-60% on multi-turn interactions.",
        "impact": "medium",
        "savings_pct": 40,
        "applies_to": ["claude", "gemini"],
    },
    {
        "id": "compress_tool_defs",
        "name": "Compress tool definitions",
        "description": "Minimize tool/function descriptions. Each tool definition costs ~100-500 tokens. "
                       "With 10+ tools, this adds up on every call.",
        "impact": "low",
        "savings_pct": 15,
        "applies_to": ["claude", "gemini"],
    },
    {
        "id": "avoid_repetition",
        "name": "Reference don't repeat",
        "description": "Use 'as described above' or 'per the system prompt' instead of repeating "
                       "large blocks of context. LLMs handle references well.",
        "impact": "low",
        "savings_pct": 20,
        "applies_to": ["claude", "gemini"],
    },
]

# Skills that should ALWAYS use local (zero API cost)
LOCAL_ONLY_SKILLS = {
    "skill_test", "skill_evolve", "benchmark", "flashcard",
    "rag_index", "rag_query", "code_index", "code_quality",
    "ingest", "marathon_log", "model_recommend",
}

# Skills that can use Haiku instead of Sonnet
HAIKU_ELIGIBLE_SKILLS = {
    "summarize", "flashcard", "lead_research", "marketing",
    "generate_asset", "account_review", "discuss",
}


def run(payload: dict, config: dict, log) -> dict:
    """Run token optimization analysis."""
    action = payload.get("action", "audit")

    if action == "audit":
        return _audit_usage(config, log)
    elif action == "recommend":
        return _recommend_optimizations(config, log)
    elif action == "apply":
        return _apply_optimizations(config, log)
    else:
        return {"error": f"Unknown action: {action}. Use: audit, recommend, apply"}


def _audit_usage(config, log) -> dict:
    """Analyze recent token usage patterns and identify waste."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    conn = db.get_conn()
    result = {"period": "7 days", "findings": [], "summary": {}}

    # Total spend by provider
    providers = conn.execute("""
        SELECT provider,
               COALESCE(SUM(cost_usd), 0) as cost,
               COALESCE(SUM(input_tokens), 0) as input_tok,
               COALESCE(SUM(output_tokens), 0) as output_tok,
               COUNT(*) as calls
        FROM usage WHERE created_at >= datetime('now', '-7 days')
        GROUP BY provider
    """).fetchall()

    total_cost = 0
    for p in providers:
        total_cost += p["cost"]
        result["summary"][p["provider"] or "local"] = {
            "cost_usd": round(p["cost"], 4),
            "input_tokens": p["input_tok"],
            "output_tokens": p["output_tok"],
            "calls": p["calls"],
        }

    # Skills using API that should be local
    api_waste = conn.execute("""
        SELECT skill, provider, COUNT(*) as calls,
               COALESCE(SUM(cost_usd), 0) as cost
        FROM usage
        WHERE created_at >= datetime('now', '-7 days')
          AND provider IN ('claude', 'gemini')
          AND skill IN ({})
        GROUP BY skill, provider
        ORDER BY cost DESC
    """.format(",".join(f"'{s}'" for s in LOCAL_ONLY_SKILLS))).fetchall()

    if api_waste:
        wasted = sum(r["cost"] for r in api_waste)
        result["findings"].append({
            "rule": "local_first",
            "severity": "high",
            "message": f"${wasted:.4f} spent on API for local-eligible skills",
            "details": [dict(r) for r in api_waste],
            "fix": "Route these skills to local Ollama (zero cost)",
        })

    # Skills using Sonnet that could use Haiku
    sonnet_waste = conn.execute("""
        SELECT skill, COUNT(*) as calls,
               COALESCE(SUM(cost_usd), 0) as cost,
               COALESCE(SUM(input_tokens), 0) as tokens
        FROM usage
        WHERE created_at >= datetime('now', '-7 days')
          AND model LIKE 'claude-sonnet%'
          AND skill IN ({})
        GROUP BY skill
        ORDER BY cost DESC
    """.format(",".join(f"'{s}'" for s in HAIKU_ELIGIBLE_SKILLS))).fetchall()

    if sonnet_waste:
        potential_savings = sum(r["cost"] * 0.73 for r in sonnet_waste)
        result["findings"].append({
            "rule": "route_simple_to_haiku",
            "severity": "medium",
            "message": f"${potential_savings:.4f} potential savings by routing to Haiku",
            "details": [dict(r) for r in sonnet_waste],
            "fix": "Add these skills to SKILL_COMPLEXITY['simple'] in providers.py",
        })

    # High-frequency callers (>100 calls/day to API)
    freq = conn.execute("""
        SELECT skill, provider, COUNT(*) as calls,
               COALESCE(SUM(cost_usd), 0) as cost
        FROM usage
        WHERE created_at >= datetime('now', '-1 day')
          AND provider IN ('claude', 'gemini')
        GROUP BY skill, provider
        HAVING calls > 100
        ORDER BY calls DESC
    """).fetchall()

    if freq:
        result["findings"].append({
            "rule": "batch_api",
            "severity": "medium",
            "message": f"{sum(r['calls'] for r in freq)} high-frequency API calls in 24h",
            "details": [dict(r) for r in freq],
            "fix": "Consider Message Batches API (50% reduction) for bulk operations",
        })

    # Cache utilization check
    cache_stats = conn.execute("""
        SELECT COALESCE(SUM(input_tokens), 0) as total_input,
               COALESCE(SUM(CASE WHEN model LIKE 'claude%' THEN input_tokens ELSE 0 END), 0) as claude_input
        FROM usage WHERE created_at >= datetime('now', '-7 days')
    """).fetchone()

    # Note: we don't track cache_read_tokens separately in usage table yet
    if cache_stats["claude_input"] > 100000:
        result["findings"].append({
            "rule": "cache_stable_system",
            "severity": "high",
            "message": f"{cache_stats['claude_input']:,} Claude input tokens — ensure prompt caching is active",
            "fix": "Add cache_control: {type: 'ephemeral'} to system prompts in _call_claude()",
        })

    result["total_cost_7d"] = round(total_cost, 4)
    result["optimization_potential"] = round(
        sum(f.get("details", [{}])[0].get("cost", 0) if f.get("details") else 0
            for f in result["findings"]), 4)

    conn.close()
    log.info(f"Token audit: ${total_cost:.4f} spent, {len(result['findings'])} findings")
    return result


def _recommend_optimizations(config, log) -> dict:
    """Return applicable optimization rules with estimated savings."""
    audit = _audit_usage(config, log)

    recommendations = []
    for rule in EFFICIENCY_RULES:
        matching = [f for f in audit["findings"] if f["rule"] == rule["id"]]
        recommendations.append({
            **rule,
            "active_findings": len(matching),
            "findings": matching,
        })

    # Sort by impact
    impact_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: (impact_order.get(r["impact"], 3), -r.get("active_findings", 0)))

    return {
        "recommendations": recommendations,
        "total_cost_7d": audit["total_cost_7d"],
        "finding_count": len(audit["findings"]),
    }


def _apply_optimizations(config, log) -> dict:
    """Apply automatic optimizations where safe (local-first routing)."""
    applied = []

    # Check if LOCAL_COMPLEXITY_ROUTING in providers.py has all local-eligible skills
    try:
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        from providers import SKILL_COMPLEXITY

        missing_local = LOCAL_ONLY_SKILLS - set(SKILL_COMPLEXITY.get("simple", []))
        if missing_local:
            applied.append({
                "rule": "local_first",
                "action": f"Add {len(missing_local)} skills to simple tier: {', '.join(sorted(missing_local))}",
                "status": "manual — edit providers.py SKILL_COMPLEXITY",
            })
    except Exception:
        pass

    return {
        "applied": applied,
        "message": "Review recommendations and apply manually where needed. "
                   "Automatic application limited to safe, reversible changes.",
    }
