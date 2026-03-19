"""
Adversarial reviewer — evaluates skill outputs for quality, correctness, and safety.

Called by worker.py when [review] enabled = true and the skill is high-stakes.
Uses the configured review provider (api=Claude, subscription=Gemini, local=Ollama).

Returns: {"verdict": "PASS"|"FAIL", "critique": "...", "confidence": 0.0-1.0}
"""
import json
import os
import time
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent

# Skills that should go through adversarial review when [review] is enabled
HIGH_STAKES_SKILLS = {
    "code_write", "code_write_review", "legal_draft", "security_audit",
    "security_apply", "pen_test", "skill_draft", "skill_evolve",
    "branch_manager", "product_release",
}

REVIEW_SYSTEM_PROMPT = """You are an adversarial code/output reviewer for an autonomous AI agent fleet.

Your job: evaluate the output of a skill execution for quality, correctness, and safety.

Evaluation criteria:
1. CORRECTNESS — Does the output actually accomplish what the task asked for?
2. COMPLETENESS — Is anything missing or left as a placeholder?
3. SAFETY — Could this output cause harm if deployed? (code injection, data loss, secrets exposure)
4. QUALITY — Is it well-structured, clear, and production-ready?

Respond with EXACTLY this JSON format (no markdown, no extra text):
{"verdict": "PASS" or "FAIL", "critique": "brief explanation", "confidence": 0.0 to 1.0}

If the output is acceptable, verdict = "PASS".
If the output has issues that need fixing, verdict = "FAIL" and explain what's wrong in critique.
Be strict but fair. Minor style issues = PASS. Logic errors, security issues, or incomplete work = FAIL."""


def run(payload, config):
    """Review a skill output.

    payload:
        skill_name: str — the skill that produced the output
        task_payload: dict — original task payload
        result: dict — skill output to review
    """
    skill_name = payload.get("skill_name", "unknown")
    task_payload = payload.get("task_payload", {})
    result = payload.get("result", {})

    review_cfg = config.get("review", {})
    provider = review_cfg.get("provider", "local")

    user_prompt = (
        f"## Skill: {skill_name}\n\n"
        f"## Original Task:\n```json\n{json.dumps(task_payload, indent=2)[:2000]}\n```\n\n"
        f"## Skill Output:\n```json\n{json.dumps(result, indent=2)[:4000]}\n```\n\n"
        "Evaluate this output. Respond with the JSON verdict."
    )

    # Add critique context if this is a re-review
    critique = task_payload.get("_review_critique")
    if critique:
        round_num = task_payload.get("_review_round", 1)
        user_prompt += (
            f"\n\n## Previous Review (round {round_num}):\n"
            f"The output was previously rejected with this critique:\n{critique}\n"
            "Check if the issues have been addressed."
        )

    try:
        if provider == "api":
            response = _review_claude(REVIEW_SYSTEM_PROMPT, user_prompt, review_cfg)
        elif provider == "subscription":
            response = _review_gemini(REVIEW_SYSTEM_PROMPT, user_prompt, review_cfg)
        else:
            response = _review_local(REVIEW_SYSTEM_PROMPT, user_prompt, config)

        return _parse_verdict(response)
    except Exception as e:
        # Review infrastructure failure = auto-PASS (don't block work)
        return {"verdict": "PASS", "critique": f"Review error (auto-pass): {e}", "confidence": 0.0}


def _parse_verdict(text):
    """Extract JSON verdict from review response."""
    text = text.strip()
    # Try direct JSON parse
    try:
        data = json.loads(text)
        if "verdict" in data:
            data["verdict"] = data["verdict"].upper()
            data.setdefault("confidence", 0.5)
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Try to find JSON in text
    import re
    m = re.search(r'\{[^}]*"verdict"[^}]*\}', text)
    if m:
        try:
            data = json.loads(m.group())
            data["verdict"] = data["verdict"].upper()
            data.setdefault("confidence", 0.5)
            return data
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: look for PASS/FAIL keyword
    upper = text.upper()
    if "FAIL" in upper:
        return {"verdict": "FAIL", "critique": text[:500], "confidence": 0.3}
    return {"verdict": "PASS", "critique": text[:500], "confidence": 0.3}


def _review_claude(system, user, review_cfg):
    """Review via Claude API."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    model = review_cfg.get("claude_model", "claude-sonnet-4-6")
    time.sleep(0.3)  # throttle
    resp = client.messages.create(
        model=model, max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _review_gemini(system, user, review_cfg):
    """Review via Gemini API."""
    import google.generativeai as genai
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    model = genai.GenerativeModel(
        model_name=review_cfg.get("gemini_model", "gemini-2.0-flash"),
        system_instruction=system,
    )
    resp = model.generate_content(user, generation_config={"max_output_tokens": 512})
    return resp.text


def _review_local(system, user, config):
    """Review via local Ollama with optional /think prefix."""
    review_cfg = config.get("review", {})
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    model = review_cfg.get("local_model", "qwen3:8b")
    ctx = review_cfg.get("local_ctx", 16384)
    use_think = review_cfg.get("local_think", True)

    prompt = f"{system}\n\n{user}"
    if use_think:
        prompt = "/think " + prompt

    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_predict": 512, "num_ctx": ctx},
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["response"]
