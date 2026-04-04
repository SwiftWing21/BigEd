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
import logging
import os
import threading

# Thread-local tracking of which provider served the last call_complex()
_last_provider = threading.local()


def get_last_provider():
    """Return the provider that served the last call_complex() in this thread."""
    return getattr(_last_provider, 'name', 'unknown')

from providers import (
    PRICING, FALLBACK_CHAIN, _get_fallback_chain, calculate_cost,
    _call_claude, _call_gemini, _call_minimax, _call_local,
    _circuit_is_open, _circuit_record_failure, _circuit_record_success,
    get_optimal_model, get_local_model_for_skill,
    is_missing_key_error, _audit_auth_failure,
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


_CODE_SKILLS = {"code_write", "code_review", "code_discuss", "refactor_verify", "skill_test", "skill_evolve"}


def _estimate_tokens(system: str, user: str, skill_name: str = "unknown") -> int:
    """Estimate token count using word-count heuristic with safety margin."""
    multiplier = 2.0 if skill_name in _CODE_SKILLS else 1.3
    return int((len(system.split()) + len(user.split())) * multiplier)


def _truncate_for_context(
    system: str, user: str, context_length: int, skill_name: str = "unknown"
) -> tuple[str, str, bool]:
    """Truncate user input if estimated tokens exceed context_length.

    Preserves system prompt entirely. Trims user text from the front (oldest).
    Returns (system, user, was_truncated).
    """
    estimated = _estimate_tokens(system, user, skill_name)
    if estimated <= context_length:
        return system, user, False

    # Budget for user after reserving system tokens + output buffer (512 tokens)
    system_tokens = _estimate_tokens(system, "", skill_name)
    output_buffer = 512
    user_budget = context_length - system_tokens - output_buffer
    if user_budget <= 0:
        return system, "", True

    # Trim user words to fit budget
    multiplier = 2.0 if skill_name in _CODE_SKILLS else 1.3
    max_words = int(user_budget / multiplier)
    words = user.split()
    if len(words) > max_words:
        # Keep the latest words (trim from front)
        words = words[-max_words:]
    return system, " ".join(words), True


def call_complex(system: str, user: str, config: dict, max_tokens: int = 2048, cache_system: bool = False,
                 skill_name: str = "unknown", task_id=None, agent_name=None,
                 purpose: str = "task") -> str:
    """Route a complex inference call with HA fallback cascade."""
    models = config.get("models", {})
    provider = models.get("complex_provider", "claude")

    # Offline mode: force local provider (no external API calls)
    if config.get("fleet", {}).get("offline_mode", False):
        provider = "local"

    # API gate check: reject non-local providers if gate disallows
    if provider != "local":
        import api_gate
        allowed, reason = api_gate.check(provider, purpose, config)
        if not allowed:
            import logging
            logging.getLogger("_models").info(
                "api_gate rejected %s for %s: %s — using local", provider, skill_name, reason)
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

    # Context overflow check for models with known context limits
    gemma4_variants = config.get("models", {}).get("gemma4", {}).get("variants", {})
    model_name = models.get("local", "") or models.get("complex", "")
    variant_key = model_name.split(":")[-1] if ":" in model_name else ""
    variant_cfg = gemma4_variants.get(variant_key, {})
    context_limit = variant_cfg.get("context_length", 0)
    if context_limit > 0:
        system, user, was_truncated = _truncate_for_context(
            system, user, context_limit, skill_name
        )
        if was_truncated:
            log = logging.getLogger("_models")
            log.warning("Context truncated for %s (limit %d) on skill %s",
                        model_name, context_limit, skill_name)

    # v0.45: Build fallback chain starting from configured provider
    # Offline mode: no cascade, local-only
    if provider == "local" and config.get("fleet", {}).get("offline_mode", False):
        chain = ["local"]
    else:
        chain = [provider]
        for p in _get_fallback_chain(provider, config):
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
                                      skill_name=skill_name, task_id=task_id, agent_name=agent_name,
                                      purpose=purpose, config=config)
            elif prov == "minimax":
                # _call_minimax expects full config (not models sub-dict) for model name resolution
                result = _call_minimax(system, user, config, max_tokens,
                                       skill_name=skill_name, task_id=task_id, agent_name=agent_name,
                                       purpose=purpose)
            elif prov == "local":
                result = _call_local(system, user, models, max_tokens,
                                     skill_name=skill_name, config=config,
                                     task_id=task_id, agent_name=agent_name)
            else:  # claude
                result = _call_claude(system, user, models, max_tokens, cache_system,
                                      skill_name=skill_name, task_id=task_id, agent_name=agent_name,
                                      purpose=purpose, config=config)

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
            last_error = e
            # Missing-key errors are permanent config issues, not transient failures.
            # Don't penalise the circuit breaker — the provider will work once configured.
            if is_missing_key_error(e):
                import sys
                print(f"[AUTH] {skill_name}: '{prov}' missing API key, skipping (no circuit penalty)...",
                      file=sys.stderr)
            else:
                _circuit_record_failure(prov)
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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _audit_auth_failure("claude", "batch", "ANTHROPIC_API_KEY not set")
        raise RuntimeError("ANTHROPIC_API_KEY not set — configure via lead_client.py secret set ANTHROPIC_API_KEY <key>")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _audit_auth_failure("claude", "batch_check", "ANTHROPIC_API_KEY not set")
        raise RuntimeError("ANTHROPIC_API_KEY not set — configure via lead_client.py secret set ANTHROPIC_API_KEY <key>")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    
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
