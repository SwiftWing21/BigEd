"""
Shared model routing for fleet skills.
All complex-inference calls go through call_complex() — provider is determined
by config['models']['complex_provider'] in fleet.toml.

Providers:
  claude  — Anthropic API (ANTHROPIC_API_KEY)
  gemini  — Google Gemini API (GEMINI_API_KEY)
  minimax — MiniMax API (MINIMAX_API_KEY)
  local   — Ollama local model (same as config['models']['local'])

Note: Provider-specific imports (anthropic, etc.) are deferred to function
bodies to avoid ImportError when optional dependencies are not installed.
"""
import os
import threading

# Thread-local tracking of which provider served the last call_complex()
_last_provider = threading.local()


def get_last_provider():
    """Return the provider that served the last call_complex() in this thread."""
    return getattr(_last_provider, 'name', 'unknown')

from providers import (
    PRICING, FALLBACK_CHAIN, calculate_cost,
    _call_claude, _call_gemini, _call_minimax, _call_local,
    _circuit_is_open, _circuit_record_failure, _circuit_record_success,
    get_optimal_model, get_local_model_for_skill,
)


def check_budget(skill_name: str, config: dict) -> dict | None:
    """Check if a skill has a token budget and current usage. Returns budget info with enforcement mode."""
    budgets = config.get("budgets", {})
    enforcement = budgets.get("enforcement", "warn")  # warn | throttle | block
    period = budgets.get("period", "day")  # day | week | month
    if not budgets or skill_name not in budgets:
        return None
    budget_usd = budgets[skill_name]
    if not isinstance(budget_usd, (int, float)):
        return None  # skip non-numeric entries like 'enforcement' and 'period'
    try:
        import db
        summary = db.get_usage_summary(period=period, group_by="skill")
        current = next((r for r in summary if r.get("skill") == skill_name), None)
        spent = current["total_cost"] if current else 0.0
        return {
            "skill": skill_name,
            "budget_usd": budget_usd,
            "spent_usd": round(spent, 6),
            "remaining_usd": round(budget_usd - spent, 6),
            "exceeded": spent >= budget_usd,
            "enforcement": enforcement,
            "period": period,
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
            budget_period = budget.get("period", "day")
            print(f"[BUDGET] {mode.upper()}: {skill_name} exceeded {budget_period} budget "
                  f"(${budget['spent_usd']:.4f} / ${budget['budget_usd']:.4f})",
                  file=sys.stderr)
            if mode == "block":
                return f"[BUDGET BLOCKED] {skill_name} exceeded {budget_period} budget."
            elif mode == "throttle":
                import time
                time.sleep(5)  # 5-second delay as soft throttle
    except Exception:
        pass  # budget checking must never break skill execution

    # Cost-aware routing: use cheaper model for simple skills
    try:
        optimal = get_optimal_model(skill_name, config)
        if optimal != models.get("complex", "claude-sonnet-4-6"):
            models = {**models, "complex": optimal}  # override for this call
    except Exception:
        pass

    # OWASP LLM04: Pre-execution cost estimation
    # Code-heavy skills get higher token multiplier (code has more tokens per word)
    CODE_SKILLS = {"code_write", "code_review", "code_discuss", "refactor_verify", "skill_test", "skill_evolve"}
    try:
        token_multiplier = 2.0 if skill_name in CODE_SKILLS else 1.3
        estimated_input_tokens = int((len(system.split()) + len(user.split())) * token_multiplier)
        from providers import PRICING
        model_id = models.get("complex", "claude-sonnet-4-6")
        rates = PRICING.get(model_id, PRICING.get("claude-sonnet-4-6", {}))
        estimated_cost = estimated_input_tokens * rates.get("input", 3.0) / 1_000_000
        # Check against budget
        budget = check_budget(skill_name, config)
        if budget and budget.get("enforcement") == "block":
            remaining = budget.get("remaining_usd", 999)
            if estimated_cost > remaining:
                import sys
                print(f"[COST] Rejected: estimated ${estimated_cost:.4f} exceeds remaining budget ${remaining:.4f}", file=sys.stderr)
                return f"[COST BLOCKED] Estimated cost ${estimated_cost:.4f} exceeds remaining budget"
    except Exception:
        pass  # cost estimation must never block

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
        # Circuit breaker: skip providers with open circuits
        if _circuit_is_open(prov):
            if i < len(chain) - 1:
                import sys
                print(f"[CIRCUIT] {skill_name}: skipping '{prov}' (circuit open), trying next...",
                      file=sys.stderr)
                continue
            # Last provider — try anyway (better than giving up)

        try:
            if prov == "gemini":
                result = _call_gemini(system, user, models, max_tokens,
                                      skill_name=skill_name, task_id=task_id, agent_name=agent_name)
            elif prov == "minimax":
                result = _call_minimax(system, user, models, max_tokens,
                                       skill_name=skill_name, task_id=task_id, agent_name=agent_name)
            elif prov == "local":
                result = _call_local(system, user, models, max_tokens,
                                     skill_name=skill_name, config=config,
                                     task_id=task_id, agent_name=agent_name)
            else:  # claude
                result = _call_claude(system, user, models, max_tokens, cache_system,
                                      skill_name=skill_name, task_id=task_id, agent_name=agent_name)

            _circuit_record_success(prov)

            # ToS: Tag which provider served this response (for Gemini exclusion in training)
            _last_provider.name = prov

            if i > 0:
                fallback_used = prov
                import sys
                print(f"[HA] {skill_name}: primary '{provider}' failed, completed via '{prov}'",
                      file=sys.stderr)
            return result

        except Exception as e:
            _circuit_record_failure(prov)
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
                err = item.result.error
                detail = f"{err.type}: {err.message}" if err else "unknown"
                results.append({"custom_id": item.custom_id, "error": f"Request failed: {detail}"})
        result["results"] = results
        
    return result
