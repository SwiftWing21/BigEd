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
            resp = json.loads(r.read())["response"]

        # Extract JSON block
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return parsed.get("skill", "summarize"), parsed.get("payload", {"description": text})
        return "summarize", {"description": text}
    except Exception:
        _log.warning("Intent model fallback (model=%s)", model, exc_info=True)
        return "summarize", {"description": text}
