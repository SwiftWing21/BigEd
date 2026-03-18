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


def call_complex(system: str, user: str, config: dict, max_tokens: int = 2048) -> str:
    """Route a complex inference call based on fleet.toml complex_provider."""
    models = config.get("models", {})
    provider = models.get("complex_provider", "claude")

    if provider == "gemini":
        return _call_gemini(system, user, models, max_tokens)
    elif provider == "local":
        return _call_local(system, user, models, max_tokens)
    else:  # default: claude
        return _call_claude(system, user, models, max_tokens)


def _call_claude(system: str, user: str, models: dict, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model=models.get("complex", "claude-sonnet-4-6"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


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
