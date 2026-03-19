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
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_create": 1.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_create": 18.75},
}

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


def _call_gemini(system: str, user: str, models: dict, max_tokens: int) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    model = genai.GenerativeModel(
        model_name=models.get("complex", "gemini-2.0-flash"),
        system_instruction=system,
    )
    resp = model.generate_content(
        user,
        generation_config={"max_output_tokens": max_tokens},
    )
    return resp.text


def _call_local(system: str, user: str, models: dict, max_tokens: int) -> str:
    import urllib.request
    import json
    host = models.get("ollama_host", "http://localhost:11434")
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
        return json.loads(r.read())["response"]
