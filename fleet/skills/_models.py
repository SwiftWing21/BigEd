"""
Shared model routing for fleet skills.
All complex-inference calls go through call_complex() — provider is determined
by config['models']['complex_provider'] in fleet.toml.

Providers:
  claude  — Anthropic API (ANTHROPIC_API_KEY)
  gemini  — Google Gemini API (GEMINI_API_KEY)
  local   — Ollama local model (same as config['models']['local'])
"""
import os

from providers import (
    PRICING, FALLBACK_CHAIN, calculate_cost,
    _call_claude, _call_gemini, _call_local,
)


def check_budget(skill_name: str, config: dict) -> dict | None:
    """Check if a skill has a token budget and current usage. Returns budget info with enforcement mode."""
    budgets = config.get("budgets", {})
    enforcement = budgets.get("enforcement", "warn")  # warn | throttle | block
    if not budgets or skill_name not in budgets:
        return None
    budget_usd = budgets[skill_name]
    try:
        import db
        # Get this skill's usage for the current day
        summary = db.get_usage_summary(period="day", group_by="skill")
        current = next((r for r in summary if r.get("skill") == skill_name), None)
        spent = current["total_cost"] if current else 0.0
        return {
            "skill": skill_name,
            "budget_usd": budget_usd,
            "spent_usd": round(spent, 6),
            "remaining_usd": round(budget_usd - spent, 6),
            "exceeded": spent >= budget_usd,
            "enforcement": enforcement,
        }
    except Exception:
        return None


def call_complex(system: str, user: str, config: dict, max_tokens: int = 2048, cache_system: bool = False,
                 skill_name: str = "unknown", task_id=None, agent_name=None) -> str:
    """Route a complex inference call with HA fallback cascade."""
    models = config.get("models", {})
    provider = models.get("complex_provider", "claude")

    # Offline mode: force local provider (no external API calls)
    if config.get("fleet", {}).get("offline_mode", False):
        provider = "local"

    # CT-4: Budget check with configurable enforcement
    try:
        budget = check_budget(skill_name, config)
        if budget and budget["exceeded"]:
            mode = budget.get("enforcement", "warn")
            import sys
            print(f"[BUDGET] {mode.upper()}: {skill_name} exceeded daily budget "
                  f"(${budget['spent_usd']:.4f} / ${budget['budget_usd']:.4f})",
                  file=sys.stderr)
            if mode == "block":
                return f"[BUDGET BLOCKED] {skill_name} exceeded daily budget. Try again tomorrow."
            elif mode == "throttle":
                import time
                time.sleep(5)  # 5-second delay as soft throttle
    except Exception:
        pass  # budget checking must never break skill execution

    # v0.45: Build fallback chain starting from configured provider
    # Offline mode: no cascade, local-only
    if provider == "local" and config.get("fleet", {}).get("offline_mode", False):
        chain = ["local"]
    else:
        chain = [provider]
        for p in FALLBACK_CHAIN:
            if p not in chain:
                chain.append(p)

    last_error = None
    fallback_used = None

    for i, prov in enumerate(chain):
        try:
            if prov == "gemini":
                result = _call_gemini(system, user, models, max_tokens)
            elif prov == "local":
                result = _call_local(system, user, models, max_tokens)
            else:  # claude
                result = _call_claude(system, user, models, max_tokens, cache_system,
                                      skill_name=skill_name, task_id=task_id, agent_name=agent_name)

            if i > 0:
                fallback_used = prov
                import sys
                print(f"[HA] {skill_name}: primary '{provider}' failed, completed via '{prov}'",
                      file=sys.stderr)
            return result

        except Exception as e:
            last_error = e
            if i < len(chain) - 1:
                import sys
                print(f"[HA] {skill_name}: '{prov}' failed ({type(e).__name__}), trying next...",
                      file=sys.stderr)
            continue

    # All providers failed
    raise last_error


def call_complex_batch(requests: list, config: dict):
    """
    Submit a batch of requests to the Anthropic Message Batches API.
    CLAUDE.md: "Prefer Message Batches API for bulk/non-real-time (50% savings)"
    
    Expected format for `requests`:
    [
        {"custom_id": "req_1", "system": "...", "user": "...", "max_tokens": 1024},
        ...
    ]
    """
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    models = config.get("models", {})
    model_id = models.get("complex", "claude-sonnet-4-6")
    
    batch_requests = []
    for req in requests:
        # Auto-apply ephemeral caching to the system prompt
        system_param = [{"type": "text", "text": req.get("system", ""), "cache_control": {"type": "ephemeral"}}]
        
        batch_requests.append({
            "custom_id": req["custom_id"],
            "params": {
                "model": model_id,
                "max_tokens": req.get("max_tokens", 2048),
                "system": system_param,
                "messages": [{"role": "user", "content": req["user"]}],
            }
        })
        
    batch = client.messages.batches.create(requests=batch_requests)
    return {"batch_id": batch.id, "status": batch.processing_status}


def check_complex_batch(batch_id: str):
    """Check status of an Anthropic Message Batch and retrieve results if ended."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    batch = client.messages.batches.retrieve(batch_id)
    result = {"status": batch.processing_status}
    
    if batch.processing_status == "ended":
        results = []
        for item in client.messages.batches.results(batch_id):
            if item.result.type == "succeeded":
                results.append({
                    "custom_id": item.custom_id,
                    "text": item.result.message.content[0].text
                })
            else:
                results.append({"custom_id": item.custom_id, "error": "Request failed"})
        result["results"] = results
        
    return result
