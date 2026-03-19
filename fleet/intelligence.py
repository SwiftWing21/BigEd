"""Intelligence scoring -- hybrid quality evaluation for task outputs.

Tier 1 (every task): Mechanical checks -- format, completeness, error-free
Tier 2 (sampled): LLM evaluation -- coherence, correctness, depth (placeholder)
"""
import json

_SKILL_CHECKS = {
    "code_review": lambda r: 0.2 if _has_key(r, "findings") or _has_sections(r) else 0.0,
    "summarize": lambda r: 0.2 if _reasonable_length(r, 80) else 0.0,
    "web_search": lambda r: 0.2 if _has_key(r, "results") or _has_key(r, "links") else 0.0,
    "discuss": lambda r: 0.2 if _multi_paragraph(r) else 0.0,
    "code_discuss": lambda r: 0.2 if _multi_paragraph(r) else 0.0,
}


def score_task_output(skill_name, result, config=None):
    """Return intelligence score 0.0-1.0 for a completed task output.

    Tier 1 mechanical scoring (always runs, fast):
    - Has content (not empty/error): 0.3 base
    - Reasonable length (not trivially short): +0.1
    - Structured output (dict with keys): +0.1
    - No error field: +0.1
    - Skill-specific format checks: up to +0.2
    """
    parsed = _parse_result(result)
    if parsed is None:
        return 0.0
    score = _base_score(parsed) + _skill_specific_score(skill_name, parsed)
    return round(min(1.0, max(0.0, score)), 3)


def _parse_result(result):
    """Normalize result to a Python object."""
    if result is None:
        return None
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result if result.strip() else None
    return result


def _base_score(parsed):
    """Tier 1 base scoring: 0.0-0.6 based on format and completeness."""
    if not parsed:
        return 0.0
    score = 0.3  # has content
    text = json.dumps(parsed) if not isinstance(parsed, str) else parsed
    if len(text) >= 50:
        score += 0.1  # reasonable length
    if isinstance(parsed, dict) and len(parsed) >= 1:
        score += 0.1  # structured output
    if isinstance(parsed, dict) and not parsed.get("error"):
        score += 0.1  # no error field
    elif isinstance(parsed, str):
        score += 0.1  # strings don't have error fields
    return score


def _skill_specific_score(skill_name, parsed):
    """Tier 1 skill-specific checks: 0.0-0.2."""
    checker = _SKILL_CHECKS.get(skill_name)
    if checker:
        try:
            return checker(parsed)
        except Exception:
            return 0.0
    text = json.dumps(parsed) if not isinstance(parsed, str) else parsed
    return 0.1 if len(text) >= 100 else 0.0


def _has_key(parsed, key):
    return isinstance(parsed, dict) and key in parsed

def _has_sections(parsed):
    text = json.dumps(parsed) if isinstance(parsed, dict) else str(parsed)
    return text.count("##") >= 2 or text.count("\\n\\n") >= 2

def _reasonable_length(parsed, min_chars):
    text = json.dumps(parsed) if not isinstance(parsed, str) else parsed
    return len(text) >= min_chars

def _multi_paragraph(parsed):
    text = json.dumps(parsed) if not isinstance(parsed, str) else parsed
    return text.count("\\n\\n") >= 1 or text.count("\n\n") >= 1 or len(text) >= 200

def llm_score_output(skill_name, result, config=None):
    """Tier 2 placeholder -- LLM-based quality evaluation (not yet implemented)."""
    return None
