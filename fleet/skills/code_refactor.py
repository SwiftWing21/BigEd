"""
Code refactor skill — applies refactoring principles to existing code without
changing behavior. Focuses on DRY, SOLID, readability, and performance.

Generated refactoring is NEVER auto-applied — the operator reviews the report
and applies changes manually.

Payload:
  file_path    str        path to the file to refactor (required)
  principles   list[str]  refactoring axes (default: ["DRY", "readability"])
  dry_run      bool       if True, only report; if False, include full refactored code (default True)

Output: knowledge/code_writes/refactors/{filename}_refactor_{date}.md
Returns: {file_path, principles, issues_found, changes_suggested, risk, report_path, refactored_code}
"""
import json
import re
from datetime import datetime
from pathlib import Path

from skills._models import call_complex

SKILL_NAME = "code_refactor"
DESCRIPTION = "Apply refactoring principles to existing code without changing behavior — DRY, SOLID, performance"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
REFACTOR_DIR = FLEET_DIR / "knowledge" / "code_writes" / "refactors"

REFACTOR_SYSTEM_PROMPT = """You are a senior code reviewer specializing in refactoring.
Analyze the provided code and suggest refactoring improvements.

IMPORTANT:
- Do NOT change external behavior (inputs, outputs, side effects must stay the same).
- Focus only on the requested principles.
- Be specific: reference line numbers, function names, variable names.

You MUST respond with EXACTLY this JSON format (no markdown fences, no extra text):
{
  "issues": [{"description": "...", "location": "...", "principle": "..."}],
  "changes": [{"description": "what to change", "before": "snippet", "after": "snippet"}],
  "risk": "low" or "medium" or "high",
  "refactored_code": "full refactored file content (or empty string if too large)"
}

risk levels:
- low: cosmetic/naming/formatting only
- medium: structural changes (extract function, reorder logic)
- high: changes to control flow, error handling, or data structures"""


def _parse_refactor_response(text: str) -> dict:
    """Extract refactoring JSON from model response."""
    text = text.strip()
    # Direct parse
    try:
        data = json.loads(text)
        if "issues" in data or "changes" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Find JSON block in text
    # Try to find the outermost { ... } containing "issues"
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start:i + 1]
                try:
                    data = json.loads(candidate)
                    if "issues" in data or "changes" in data:
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
                start = None
    # Fallback
    return {
        "issues": [{"description": text[:500], "location": "unknown", "principle": "unknown"}],
        "changes": [],
        "risk": "medium",
        "refactored_code": "",
    }


def run(payload, config):
    file_path = payload.get("file_path", "")
    principles = payload.get("principles", ["DRY", "readability"])
    dry_run = payload.get("dry_run", True)

    if not file_path:
        return json.dumps({"error": "No file_path provided"})

    target = Path(file_path)

    # Step 1: Read the target file
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: {file_path}"})
    except Exception as e:
        return json.dumps({"error": f"Cannot read file: {e}"})

    if not content.strip():
        return json.dumps({"error": f"File is empty: {file_path}"})

    # Step 2: Build refactoring prompt
    principles_str = ", ".join(principles)
    user_prompt = (
        f"## File: {target.name}\n\n"
        f"## Refactoring Principles: {principles_str}\n\n"
        f"## Code:\n```\n{content[:8000]}\n```\n\n"
        f"Analyze this code and suggest refactoring improvements based on: {principles_str}.\n"
        f"Show the specific changes as before/after snippets. Do NOT change external behavior."
    )
    if not dry_run:
        user_prompt += "\n\nInclude the FULL refactored file content in refactored_code."

    # Step 3: Call model
    try:
        response = call_complex(
            system=REFACTOR_SYSTEM_PROMPT,
            user=user_prompt,
            config=config,
            max_tokens=2048,
            skill_name=SKILL_NAME,
        )
    except Exception as e:
        return json.dumps({
            "file_path": file_path,
            "principles": principles,
            "error": f"Model call failed: {e}",
        })

    # Step 4: Parse response
    result = _parse_refactor_response(response)
    issues = result.get("issues", [])
    changes = result.get("changes", [])
    risk = result.get("risk", "medium")
    refactored_code = result.get("refactored_code", "")

    # Validate risk value
    if risk not in ("low", "medium", "high"):
        risk = "medium"

    changes_summary = [c.get("description", "unknown change") for c in changes]

    # Step 5: Save report
    report_path = ""
    try:
        REFACTOR_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        filename = target.stem
        report = REFACTOR_DIR / f"{filename}_refactor_{date_str}.md"
        report_path = str(report)

        lines = [
            f"# Refactoring Report: `{target.name}`",
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**Principles:** {principles_str}",
            f"**Risk:** {risk}",
            f"**Issues Found:** {len(issues)}",
            f"**Changes Suggested:** {len(changes)}",
            "",
            "## Issues",
        ]
        for i, issue in enumerate(issues, 1):
            lines.append(f"{i}. **{issue.get('principle', '?')}** — {issue.get('description', '?')} (at {issue.get('location', '?')})")
        lines.append("")
        lines.append("## Suggested Changes")
        for i, change in enumerate(changes, 1):
            lines.append(f"### Change {i}: {change.get('description', '?')}")
            if change.get("before"):
                lines.append(f"**Before:**\n```\n{change['before']}\n```")
            if change.get("after"):
                lines.append(f"**After:**\n```\n{change['after']}\n```")
            lines.append("")

        if not dry_run and refactored_code:
            lines.append("## Full Refactored Code")
            lines.append(f"```\n{refactored_code}\n```")

        report.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass  # Report save failure must not break the skill

    # Step 6: Build return payload
    out = {
        "file_path": file_path,
        "principles": principles,
        "issues_found": len(issues),
        "changes_suggested": changes_summary,
        "risk": risk,
        "report_path": report_path,
    }
    if not dry_run:
        out["refactored_code"] = refactored_code

    return json.dumps(out)
