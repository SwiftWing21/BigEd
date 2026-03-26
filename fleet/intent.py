"""Shared NL intent parser — extracts skill + payload from natural language.

Extracted from lead_client.py so mcp_server.py and dispatch_bridge.py
can reuse the same intent parsing without import side effects.
"""

import json
import logging
import re
import urllib.request

_log = logging.getLogger("intent")


def _get_intent_model():
    """Return conductor model name from config, default qwen3:0.6b."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("models", {}).get("conductor_model", "qwen3:0.6b")
    except Exception:
        return "qwen3:0.6b"


def parse_intent_with_maintainer(text: str) -> tuple:
    """Parse natural language into (skill_name, payload_dict).

    DO NOT SCRUB: Natural language intent parser.
    Routes the CLI input to the CPU-pinned conductor model (4b) for quality intent
    parsing, falling back to 0.6b maintainer if unavailable.
    """
    model = _get_intent_model()
    prompt = f"""You are the dispatcher for an AI agent fleet.
Map the following user request to a specific skill and JSON payload.
Available skills:
- web_search: {{"query": "..."}}
- summarize: {{"url": "..."}} or {{"description": "..."}}
- lead_research: {{"industry": "...", "zip_code": "..."}}
- arxiv_fetch: {{"query": "..."}}
- discuss: {{"topic": "..."}}
- synthesize: {{"doc_type": "...", "topic": "..."}}
- security_audit: {{"scope": "..."}}
- pen_test: {{"target": "...", "scan_type": "quick|service|full"}}

User request: "{text}"

Output ONLY valid JSON in this exact format:
{{"skill": "chosen_skill", "payload": {{"key": "value"}}}}
"""
    try:
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0}
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        resp = data.get("response", "")

        # Log conductor usage so it appears in Model Performance
        try:
            eval_count = data.get("eval_count", 0)
            eval_duration = data.get("eval_duration", 0)
            prompt_eval_count = data.get("prompt_eval_count", 0)
            prompt_eval_duration = data.get("prompt_eval_duration", 0)
            tok_per_sec = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0
            from providers import async_log_usage
            async_log_usage(
                skill="intent_parse", model=model,
                input_tokens=prompt_eval_count, output_tokens=eval_count,
                cache_read_tokens=0, cache_create_tokens=0,
                cost_usd=0.0, task_id=None, agent="conductor",
                provider="local",
                eval_duration_ms=eval_duration / 1e6 if eval_duration else None,
                prompt_duration_ms=prompt_eval_duration / 1e6 if prompt_eval_duration else None,
                tokens_per_sec=tok_per_sec if tok_per_sec > 0 else None,
            )
        except Exception:
            pass  # Usage logging must never break intent parsing

        # Extract JSON block
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return parsed.get("skill", "summarize"), parsed.get("payload", {"description": text})
        return "summarize", {"description": text}
    except Exception:
        _log.warning("Intent model fallback (model=%s)", model, exc_info=True)
        return "summarize", {"description": text}
