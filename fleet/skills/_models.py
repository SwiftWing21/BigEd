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
import time

# CT-1: Model pricing per million tokens (as of 2025)
PRICING = {
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_create": 1.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_create": 18.75},
}


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


def check_budget(skill_name: str, config: dict) -> dict | None:
    """Check if a skill has a token budget and current usage. Returns budget info or None."""
    budgets = config.get("budgets", {})
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
        }
    except Exception:
        return None


def call_complex(system: str, user: str, config: dict, max_tokens: int = 2048, cache_system: bool = False,
                 skill_name: str = "unknown", task_id=None, agent_name=None) -> str:
    """Route a complex inference call based on fleet.toml complex_provider."""
    models = config.get("models", {})
    provider = models.get("complex_provider", "claude")

    # Offline mode: force local provider (no external API calls)
    if config.get("fleet", {}).get("offline_mode", False):
        provider = "local"

    # CT-4: Budget check (warn, don't block)
    try:
        budget = check_budget(skill_name, config)
        if budget and budget["exceeded"]:
            import sys
            print(f"[BUDGET] Warning: {skill_name} exceeded daily budget "
                  f"(${budget['spent_usd']:.4f} / ${budget['budget_usd']:.4f})",
                  file=sys.stderr)
    except Exception:
        pass  # budget checking must never break skill execution

    if provider == "gemini":
        return _call_gemini(system, user, models, max_tokens)
    elif provider == "local":
        return _call_local(system, user, models, max_tokens)
    else:  # default: claude
        return _call_claude(system, user, models, max_tokens, cache_system,
                            skill_name=skill_name, task_id=task_id, agent_name=agent_name)


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
