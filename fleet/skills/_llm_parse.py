# fleet/skills/_llm_parse.py
"""Extract structured data from LLM text responses."""
import json
import re


def extract_json_object(text: str, required_key: str = None) -> dict | None:
    """Extract first JSON object from LLM response text.
    Tries: direct parse -> regex with required_key -> brace-matching fallback.
    """
    if not text:
        return None
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    if required_key:
        pattern = r'\{[^{}]*"' + re.escape(required_key) + r'"[^{}]*\}'
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except (json.JSONDecodeError, TypeError):
                pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def extract_json_array(text: str) -> list | None:
    """Extract first JSON array from LLM response text."""
    if not text:
        return None
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def extract_verdict(text: str) -> dict:
    """Extract PASS/FAIL verdict dict from LLM review response.
    Returns: {"verdict": "PASS"|"FAIL", "critique": str, "confidence": float}
    """
    obj = extract_json_object(text, required_key="verdict")
    if obj and "verdict" in obj:
        obj["verdict"] = obj["verdict"].upper()
        obj.setdefault("confidence", 0.5)
        obj.setdefault("critique", "")
        return obj
    upper = (text or "").upper()
    verdict = "FAIL" if "FAIL" in upper else "PASS"
    return {"verdict": verdict, "critique": (text or "")[:500], "confidence": 0.3}
