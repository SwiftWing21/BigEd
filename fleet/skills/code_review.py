"""
Code review skill — reads an actual fleet Python file and produces a structured review.

Unlike code_discuss (abstract topic discussion), this skill reads the real file content
and produces line-level findings: bugs, error handling gaps, security issues, quality,
and performance notes.

Payload:
  file        str   path relative to fleet root, e.g. "skills/web_search.py"
              OR    absolute path on the WSL filesystem
  perspective str   "software architect" | "code critic / reviewer" | "performance optimizer"
  agent_name  str   "coder_1" (for logging)
  focus       str   optional — narrow the review, e.g. "error handling only"

Output: knowledge/code_reviews/<filename>_review_<date>.md
"""
import json
import re
from datetime import datetime
from pathlib import Path

import httpx

FLEET_DIR      = Path(__file__).parent.parent
KNOWLEDGE_DIR  = FLEET_DIR / "knowledge"
REVIEWS_DIR    = KNOWLEDGE_DIR / "code_reviews"

# Files the coders should rotate through when no specific file is given
DEFAULT_REVIEW_QUEUE = [
    "db.py",
    "worker.py",
    "supervisor.py",
    "lead_client.py",
    "skills/web_search.py",
    "skills/lead_research.py",
    "skills/synthesize.py",
    "skills/web_crawl.py",
    "skills/marketing.py",
    "skills/generate_image.py",
    "skills/generate_video.py",
    "skills/security_audit.py",
]

PERSPECTIVE_FOCUS = {
    "software architect":       "module structure, interface design, coupling, extensibility",
    "code critic / reviewer":   "bugs, error handling, edge cases, security, code clarity",
    "performance optimizer":    "query efficiency, I/O patterns, timeouts, caching opportunities",
}


def _ollama(prompt: str, config: dict) -> str:
    resp = httpx.post(
        f"{config['models']['ollama_host']}/api/generate",
        json={"model": config["models"]["local"], "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _pick_file(requested: str) -> Path | None:
    if requested:
        # Try absolute first, then relative to fleet root
        p = Path(requested)
        if p.is_absolute() and p.exists():
            return p
        rel = FLEET_DIR / requested
        if rel.exists():
            return rel
        return None
    # Pick the least-recently-reviewed file from the queue
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    reviewed = {
        re.sub(r"_review_\d{8}.*\.md$", "", f.stem).replace("_", "/")
        for f in REVIEWS_DIR.glob("*_review_*.md")
    }
    for candidate in DEFAULT_REVIEW_QUEUE:
        key = candidate.replace("/", "_").replace(".py", "")
        already = any(key in r.replace("/", "_") for r in reviewed)
        path = FLEET_DIR / candidate
        if not already and path.exists():
            return path
    # All reviewed — start over with first available
    for candidate in DEFAULT_REVIEW_QUEUE:
        path = FLEET_DIR / candidate
        if path.exists():
            return path
    return None


def run(payload, config):
    requested    = payload.get("file", "")
    perspective  = payload.get("perspective", "code critic / reviewer")
    agent_name   = payload.get("agent_name", "coder_2")
    focus_hint   = payload.get("focus", "")

    target = _pick_file(requested)
    if target is None:
        return {"error": "No reviewable file found", "requested": requested}

    try:
        source = target.read_text(errors="ignore")
    except Exception as e:
        return {"error": str(e), "file": str(target)}

    # Trim to fit context — review up to ~400 lines
    lines = source.splitlines()
    if len(lines) > 400:
        source_excerpt = "\n".join(lines[:400]) + f"\n\n... ({len(lines) - 400} more lines truncated)"
    else:
        source_excerpt = source

    focus_area = focus_hint or PERSPECTIVE_FOCUS.get(perspective, "general code quality")
    rel_path   = str(target.relative_to(FLEET_DIR)) if target.is_relative_to(FLEET_DIR) else str(target)

    prompt = f"""You are a {perspective} conducting a code review of a Python file in a local AI agent fleet system.

FILE: {rel_path}
REVIEW FOCUS: {focus_area}

SOURCE CODE:
```python
{source_excerpt}
```

Produce a structured code review with these sections:
## Summary
One paragraph — what this file does and your overall impression.

## Findings
List each finding as:
- [SEVERITY] Line ~N: description of issue and suggested fix
Severity levels: CRITICAL | HIGH | MEDIUM | LOW | NOTE

Focus your findings on {focus_area}.
Be specific — reference actual variable names, function names, and line numbers.
Limit to 8 most important findings.

## Top Recommendation
The single most impactful change you would make first."""

    review_text = _ollama(prompt, config)

    # Save report
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name  = rel_path.replace("/", "_").replace(".py", "")
    date_str   = datetime.now().strftime("%Y%m%d")
    out_file   = REVIEWS_DIR / f"{safe_name}_review_{date_str}_{agent_name}.md"

    header = (
        f"# Code Review: `{rel_path}`\n"
        f"**Reviewer:** {agent_name} ({perspective})\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Focus:** {focus_area}\n\n---\n\n"
    )
    out_file.write_text(header + review_text)

    return {
        "file_reviewed": rel_path,
        "perspective":   perspective,
        "saved_to":      str(out_file),
        "findings_preview": review_text[:300],
    }
