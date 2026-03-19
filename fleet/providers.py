"""Model provider routing with HA fallback cascade."""
import os
import time
import threading

# Circuit breaker state per provider
_circuit_state = {}  # provider -> {"failures": int, "last_failure": float, "open_until": float}
_circuit_lock = threading.Lock()

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECS = 60
CIRCUIT_WINDOW_SECS = 300  # 5 minutes


def _circuit_is_open(provider: str) -> bool:
    """Check if a provider's circuit breaker is open (should be skipped)."""
    with _circuit_lock:
        state = _circuit_state.get(provider)
        if not state:
            return False
        if time.time() < state.get("open_until", 0):
            return True  # still in cooldown
        # Cooldown expired — reset to half-open (allow retry)
        if state.get("open_until", 0) > 0:
            state["failures"] = 0
            state["open_until"] = 0
        return False


def _circuit_record_failure(provider: str):
    """Record a failure for a provider. Opens circuit after threshold."""
    with _circuit_lock:
        now = time.time()
        state = _circuit_state.setdefault(provider, {"failures": 0, "last_failure": 0, "open_until": 0})
        # Reset if last failure was outside window
        if now - state["last_failure"] > CIRCUIT_WINDOW_SECS:
            state["failures"] = 0
        state["failures"] += 1
        state["last_failure"] = now
        if state["failures"] >= CIRCUIT_FAILURE_THRESHOLD:
            state["open_until"] = now + CIRCUIT_COOLDOWN_SECS
            import sys
            print(f"[CIRCUIT] Provider '{provider}' circuit OPEN — {CIRCUIT_FAILURE_THRESHOLD} failures in {CIRCUIT_WINDOW_SECS}s, cooling down {CIRCUIT_COOLDOWN_SECS}s", file=sys.stderr)


def _circuit_record_success(provider: str):
    """Record a success — reset failure count."""
    with _circuit_lock:
        if provider in _circuit_state:
            _circuit_state[provider]["failures"] = 0
            _circuit_state[provider]["open_until"] = 0

# CT-1: Model pricing per million tokens (as of 2025)
PRICING = {
    # Claude models
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_create": 1.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_create": 18.75},
    # Gemini models (per million tokens, 2025-2026 pricing)
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cache_read": 0.025, "cache_create": 0.025},
    "gemini-2.0-flash-lite": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_create": 0.0},  # free tier
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cache_read": 0.31, "cache_create": 4.50},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.0375, "cache_create": 0.0375},
    # Local models (Ollama — zero API cost, but track for comparison)
    "qwen3:8b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_create": 0.0},
    "qwen3:4b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_create": 0.0},
    "qwen3:1.7b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_create": 0.0},
    "qwen3:0.6b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_create": 0.0},
}

# Task complexity tiers — API model routing
COMPLEXITY_ROUTING = {
    "simple": "claude-haiku-4-5",     # classification, yes/no, simple extraction
    "medium": "claude-sonnet-4-6",    # generation, analysis, code review (default)
    "complex": "claude-opus-4-6",     # multi-step reasoning, architecture decisions
}

# Task complexity tiers — local Ollama model routing
# Maps complexity to fleet.toml [models.tiers] key
LOCAL_COMPLEXITY_ROUTING = {
    "simple": "mid",       # qwen3:4b — fast, fits alongside 8b
    "medium": "default",   # qwen3:8b — main worker model
    "complex": "default",  # qwen3:8b — best local quality
}

# Skills pre-classified by complexity
SKILL_COMPLEXITY = {
    # Simple (API: Haiku, Local: 4b) — classification, extraction, indexing
    "flashcard": "simple", "rag_query": "simple", "summarize": "simple",
    "ingest": "simple", "rag_index": "simple", "code_index": "simple",
    "account_review": "simple", "status_report": "simple",
    "stability_report": "simple", "onboard_checklist": "simple",
    # Medium (API: Sonnet, Local: 8b) — generation, analysis, review
    "web_search": "medium", "code_review": "medium", "discuss": "medium",
    "code_discuss": "medium", "security_audit": "medium", "analyze_results": "medium",
    "fma_review": "medium", "code_quality": "medium", "code_refactor": "medium",
    "curriculum_update": "medium", "dataset_synthesize": "medium",
    "skill_train": "medium", "research_loop": "medium",
    # Complex (API: Opus, Local: 8b) — multi-step reasoning, architecture
    "plan_workload": "complex", "lead_research": "complex", "skill_evolve": "complex",
    "code_write": "complex", "legal_draft": "complex", "claude_code": "complex",
    "swarm_intelligence": "complex", "evolution_coordinator": "complex",
}


def get_optimal_model(skill_name: str, config: dict) -> str:
    """Return the optimal API model for a skill based on complexity classification."""
    complexity = SKILL_COMPLEXITY.get(skill_name, "medium")
    model = COMPLEXITY_ROUTING.get(complexity, "claude-sonnet-4-6")
    # Override with config if specified
    models = config.get("models", {})
    if models.get("complex"):
        # Only downgrade, never upgrade beyond config
        config_model = models["complex"]
        if complexity == "simple" and config_model != "claude-haiku-4-5":
            return model  # use cheaper model for simple tasks
    return model


def get_local_model_for_skill(skill_name: str, config: dict) -> str:
    """Return the optimal local Ollama model for a skill based on complexity.

    Simple skills use the smaller model (e.g. qwen3:4b) to save VRAM and reduce
    latency. Medium/complex skills use the default model (e.g. qwen3:8b).
    """
    complexity = SKILL_COMPLEXITY.get(skill_name, "medium")
    tier_key = LOCAL_COMPLEXITY_ROUTING.get(complexity, "default")
    tiers = config.get("models", {}).get("tiers", {})
    model = tiers.get(tier_key)
    if model:
        return model
    # Fallback to config default
    return config.get("models", {}).get("local", "qwen3:8b")


# v0.45: HA fallback cascade — if primary fails, try next provider
FALLBACK_CHAIN = ["claude", "gemini", "local"]


def calculate_cost(usage, model_id: str) -> float:
    """Calculate USD cost from usage object and model pricing."""
    rates = PRICING.get(model_id, PRICING["claude-sonnet-4-6"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    fresh_input = max(0, usage.input_tokens - cache_read - cache_create)
    cost = (
        fresh_input * rates["input"] / 1_000_000
        + usage.output_tokens * rates["output"] / 1_000_000
        + cache_read * rates["cache_read"] / 1_000_000
        + cache_create * rates["cache_create"] / 1_000_000
    )
    return round(cost, 6)


def calculate_cost_simple(input_tokens: int, output_tokens: int, model_id: str) -> float:
    """Simple cost calculation from raw token counts (no usage object needed)."""
    rates = PRICING.get(model_id, PRICING.get("gemini-2.0-flash", {"input": 0, "output": 0}))
    cost = (
        input_tokens * rates["input"] / 1_000_000
        + output_tokens * rates["output"] / 1_000_000
    )
    return round(cost, 6)


def _call_claude(system: str, user: str, models: dict, max_tokens: int, cache_system: bool = False,
                  skill_name: str = "unknown", task_id=None, agent_name=None) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # CLAUDE.md: Always use cache_control on stable system prompts
    system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}] if cache_system else system

    # CLAUDE.md: Throttle to 20% of rate limits, 300ms min between requests, exponential backoff on 429s
    max_retries = 4
    base_delay = 1.0

    for attempt in range(max_retries):
        try:
            time.sleep(0.3)  # 300ms min between requests
            resp = client.messages.create(
                model=models.get("complex", "claude-sonnet-4-6"),
                max_tokens=max_tokens,
                system=system_param,
                messages=[{"role": "user", "content": user}],
            )
            # CT-1: Capture usage
            try:
                import db
                model_id = models.get("complex", "claude-sonnet-4-6")
                db.log_usage(
                    skill=skill_name, model=model_id,
                    input_tokens=resp.usage.input_tokens,
                    output_tokens=resp.usage.output_tokens,
                    cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                    cache_create_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                    cost_usd=calculate_cost(resp.usage, model_id),
                    task_id=task_id, agent=agent_name,
                )
            except Exception:
                pass  # Usage logging must never break skill execution
            return resp.content[0].text
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))


def _call_gemini(system: str, user: str, models: dict, max_tokens: int,
                 skill_name: str = "unknown", task_id=None, agent_name=None) -> str:
    import google.generativeai as genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set — configure via lead_client.py secret set GEMINI_API_KEY <key>")
    genai.configure(api_key=api_key)
    model_name = models.get("complex", "gemini-2.0-flash")
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system,
    )
    resp = model.generate_content(
        user,
        generation_config={"max_output_tokens": max_tokens},
    )

    # Safety check — Gemini may block responses for policy reasons
    if hasattr(resp, 'candidates') and resp.candidates:
        candidate = resp.candidates[0]
        finish_reason = getattr(candidate, 'finish_reason', None)
        # finish_reason enum: STOP=1, MAX_TOKENS=2, SAFETY=3, RECITATION=4, OTHER=5
        if finish_reason and finish_reason != 1 and finish_reason != 2:
            reason_name = {3: "SAFETY", 4: "RECITATION", 5: "OTHER"}.get(finish_reason, str(finish_reason))
            import sys
            print(f"[GEMINI] Response blocked: finishReason={reason_name} (skill={skill_name})", file=sys.stderr)
            if finish_reason == 3:
                raise RuntimeError(f"Gemini safety block on skill={skill_name} — content policy triggered")

    # Track Gemini usage (best-effort)
    try:
        usage = resp.usage_metadata
        if usage:
            import db
            db.log_usage(
                skill=skill_name,
                model=model_name,
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
                cache_create_tokens=0,
                cost_usd=calculate_cost_simple(
                    getattr(usage, "prompt_token_count", 0) or 0,
                    getattr(usage, "candidates_token_count", 0) or 0,
                    model_name),
                task_id=task_id, agent=agent_name,
            )
    except Exception:
        pass
    return resp.text


_provider_health = {}  # provider -> {"healthy": bool, "last_check": float, "latency_ms": float}


def probe_provider_health(provider: str) -> dict:
    """Lightweight health check for a provider. Returns {healthy, latency_ms}."""
    start = time.time()
    try:
        if provider == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            # Minimal request — just check auth works
            client.messages.create(
                model="claude-haiku-4-5", max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        elif provider == "gemini":
            import urllib.request as _ur
            req = _ur.Request(
                "https://generativelanguage.googleapis.com/v1beta/models?key=" +
                os.environ.get("GEMINI_API_KEY", ""),
                method="GET"
            )
            with _ur.urlopen(req, timeout=5):
                pass
        elif provider == "local":
            import urllib.request as _ur
            with _ur.urlopen("http://localhost:11434/api/tags", timeout=3):
                pass

        latency = (time.time() - start) * 1000
        result = {"healthy": True, "latency_ms": round(latency, 1)}
    except Exception as e:
        latency = (time.time() - start) * 1000
        result = {"healthy": False, "latency_ms": round(latency, 1), "error": str(e)[:100]}

    _provider_health[provider] = {**result, "last_check": time.time()}
    return result


def get_provider_health() -> dict:
    """Return cached health status for all providers."""
    return dict(_provider_health)


def _call_local(system: str, user: str, models: dict, max_tokens: int,
                skill_name: str = "unknown", config: dict | None = None,
                task_id: int | None = None, agent_name: str | None = None) -> str:
    import urllib.request
    import json
    host = models.get("ollama_host", "http://localhost:11434")
    # Per-skill model routing: simple skills → smaller model, medium/complex → default
    if config and skill_name != "unknown":
        model = get_local_model_for_skill(skill_name, config)
    else:
        model = models.get("complex", models.get("local", "qwen3:8b"))
    prompt = f"{system}\n\n{user}"
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())

    # Extract Ollama performance metrics
    eval_count = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 0)  # nanoseconds
    prompt_eval_count = data.get("prompt_eval_count", 0)
    prompt_eval_duration = data.get("prompt_eval_duration", 0)  # nanoseconds

    tok_per_sec = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0
    eval_duration_ms = eval_duration / 1e6 if eval_duration else None
    prompt_duration_ms = prompt_eval_duration / 1e6 if prompt_eval_duration else None

    # Log usage with speed metrics
    try:
        import db
        db.log_usage(
            skill=skill_name, model=model,
            input_tokens=prompt_eval_count,
            output_tokens=eval_count,
            cache_read_tokens=0, cache_create_tokens=0,
            cost_usd=0.0,
            task_id=task_id, agent=agent_name,
            eval_duration_ms=eval_duration_ms,
            prompt_duration_ms=prompt_duration_ms,
            tokens_per_sec=tok_per_sec if tok_per_sec > 0 else None,
        )
    except Exception:
        pass  # Usage logging must never break skill execution

    return data.get("response", "")
