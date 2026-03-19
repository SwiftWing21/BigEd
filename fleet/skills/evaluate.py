"""
Evaluate skill — adversarial quality evaluation of any skill output.

Wraps skill output with a structured critique and PASS/FAIL verdict using
the configured complex model (Claude/Gemini/Ollama via _models.call_complex).

Payload:
  skill_name  str        skill that produced the output (required)
  output      str|dict   the output to evaluate (required)
  criteria    list[str]  evaluation axes (default: ["accuracy", "completeness", "clarity"])
  strict      bool       include suggested_improvements on FAIL (default False)

Output: knowledge/evaluations/{skill_name}_eval_{date}.md
Returns: {verdict, skill_name, criteria, critique, suggestions}
"""
import json
import re
from datetime import datetime
from pathlib import Path

from skills._models import call_complex

SKILL_NAME = "evaluate"
DESCRIPTION = "Adversarial quality evaluation of any skill output — wraps output with critique and verdict"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
EVAL_DIR = FLEET_DIR / "knowledge" / "evaluations"

EVAL_SYSTEM_PROMPT = """You are a critical, adversarial reviewer for an AI agent fleet.
Your job is to evaluate skill outputs rigorously against given criteria.

You MUST respond with EXACTLY this JSON format (no markdown fences, no extra text):
{"verdict": "PASS" or "FAIL", "critique": "detailed critique per criterion", "suggestions": ["improvement 1", "improvement 2"]}

Rules:
- Be strict but fair. Minor style issues = PASS. Substantive gaps = FAIL.
- Critique must address EACH criterion individually.
- suggestions list is required if verdict is FAIL, optional if PASS."""


def _parse_eval_response(text: str) -> dict:
    """Extract evaluation JSON from model response."""
    text = text.strip()
    # Direct parse
    try:
        data = json.loads(text)
        if "verdict" in data:
            data["verdict"] = data["verdict"].upper()
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Find JSON in text
    m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group())
            data["verdict"] = data["verdict"].upper()
            return data
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: keyword detection
    upper = text.upper()
    verdict = "FAIL" if "FAIL" in upper else "PASS"
    return {"verdict": verdict, "critique": text[:1000], "suggestions": []}


def run(payload, config):
    skill_name = payload.get("skill_name", "")
    output = payload.get("output", "")
    criteria = payload.get("criteria", ["accuracy", "completeness", "clarity"])
    strict = payload.get("strict", False)

    if not skill_name:
        return json.dumps({"verdict": "FAIL", "error": "No skill_name provided"})
    if not output:
        return json.dumps({"verdict": "FAIL", "error": "No output provided"})

    # Serialize output if dict
    if isinstance(output, dict):
        output_str = json.dumps(output, indent=2)
    else:
        output_str = str(output)

    # Step 1: Build evaluation prompt
    criteria_str = ", ".join(criteria)
    user_prompt = (
        f"## Skill: {skill_name}\n\n"
        f"## Evaluation Criteria: {criteria_str}\n\n"
        f"## Output to Evaluate:\n```\n{output_str[:6000]}\n```\n\n"
        f"Evaluate this output against each criterion. "
        f"Give a verdict (PASS/FAIL) and specific critique per criterion."
    )
    if strict:
        user_prompt += "\n\nThis is a STRICT evaluation. If verdict is FAIL, include detailed suggested_improvements."

    # Step 2: Call model
    try:
        response = call_complex(
            system=EVAL_SYSTEM_PROMPT,
            user=user_prompt,
            config=config,
            max_tokens=1024,
            skill_name=SKILL_NAME,
        )
    except Exception as e:
        return json.dumps({
            "verdict": "FAIL",
            "skill_name": skill_name,
            "criteria": criteria,
            "critique": f"Evaluation model error: {e}",
            "suggestions": [],
        })

    # Step 3: Parse response
    result = _parse_eval_response(response)
    verdict = result.get("verdict", "FAIL")
    critique = result.get("critique", "No critique returned")
    suggestions = result.get("suggestions", [])

    # Step 4: If strict and FAIL, ensure suggestions exist
    if strict and verdict == "FAIL" and not suggestions:
        suggestions = [critique]

    # If not strict, omit suggestions on PASS
    if not strict and verdict == "PASS":
        suggestions = []

    # Step 5: Save evaluation report
    try:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        report = EVAL_DIR / f"{skill_name}_eval_{date_str}.md"
        report.write_text(
            f"# Evaluation: `{skill_name}`\n\n"
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Verdict:** {verdict}\n"
            f"**Criteria:** {criteria_str}\n\n"
            f"## Critique\n{critique}\n\n"
            f"## Suggestions\n"
            + ("\n".join(f"- {s}" for s in suggestions) if suggestions else "None")
            + "\n\n## Raw Output (truncated)\n```\n"
            + output_str[:2000]
            + "\n```\n"
        )
    except Exception:
        pass  # Report save failure must not break evaluation

    out = {
        "verdict": verdict,
        "skill_name": skill_name,
        "criteria": criteria,
        "critique": critique,
    }
    if strict or suggestions:
        out["suggestions"] = suggestions

    return json.dumps(out)
