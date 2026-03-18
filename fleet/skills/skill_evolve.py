"""
Skill evolve — takes an existing skill + its review findings and generates
an improved version without starting from scratch.

Reads the current skill source, finds its most recent review(s), and generates
a new version addressing the findings while preserving working logic.

Payload:
  skill_name    str   skill to evolve (e.g. "web_search", "lead_research")
  focus         str   optional focus area (e.g. "error handling", "performance")
  perspective   str   "software architect" | "code critic / reviewer" | "performance optimizer"
  agent_name    str   "coder_1"

Output: knowledge/code_drafts/<skill>_evolved_<date>_<agent>.py
Returns: {evolved, original_lines, changes_summary, saved_to}
"""
import re
from datetime import datetime
from pathlib import Path

import httpx

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
REVIEWS_DIR = FLEET_DIR / "knowledge" / "code_reviews"
FMA_REVIEWS_DIR = FLEET_DIR / "knowledge" / "fma_reviews"
DRAFTS_DIR = FLEET_DIR / "knowledge" / "code_drafts"


def _ollama(prompt: str, config: dict) -> str:
    resp = httpx.post(
        f"{config['models']['ollama_host']}/api/generate",
        json={"model": config["models"]["local"], "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _find_reviews(skill_name: str) -> str:
    """Gather review findings for this skill."""
    reviews = []
    safe = skill_name.replace(".py", "").replace("/", "_")
    for review_dir in [REVIEWS_DIR, FMA_REVIEWS_DIR]:
        if not review_dir.exists():
            continue
        for f in sorted(review_dir.glob(f"*{safe}*_review_*.md"), reverse=True)[:3]:
            content = f.read_text(errors="ignore")
            # Extract findings section
            match = re.search(r"## Findings\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
            if match:
                reviews.append(f"### From {f.name}\n{match.group(1).strip()}")
    return "\n\n".join(reviews) if reviews else ""


def _extract_code(raw: str) -> str:
    m = re.search(r"```python\s*(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw.strip()


def run(payload, config):
    skill_name = payload.get("skill_name", "")
    focus = payload.get("focus", "")
    perspective = payload.get("perspective", "software architect")
    agent_name = payload.get("agent_name", "coder_1")

    if not skill_name:
        return {"error": "No skill_name provided"}

    # Find the skill file
    skill_file = SKILLS_DIR / f"{skill_name.replace('.py', '')}.py"
    if not skill_file.exists():
        return {"error": f"Skill not found: {skill_file}"}

    source = skill_file.read_text(errors="ignore")
    source_lines = len(source.splitlines())

    # Gather review findings
    reviews = _find_reviews(skill_name)

    focus_text = f"\nFOCUS YOUR CHANGES ON: {focus}" if focus else ""

    prompt = f"""You are a {perspective} evolving an existing fleet skill.
Your job is to improve the skill based on review findings while preserving its working interface.

RULES:
- Keep the same run(payload, config) -> dict interface
- Preserve all working functionality
- Apply the review findings as improvements
- Don't change the module docstring payload schema unless adding new optional fields
- Add improvements incrementally — don't rewrite from scratch
{focus_text}

CURRENT SKILL ({skill_name}, {source_lines} lines):
```python
{source[:4000]}
```

{"REVIEW FINDINGS:" + chr(10) + reviews[:2000] if reviews else "No formal reviews found — apply general best practices for: error handling, input validation, logging, and efficiency."}

Write the complete improved Python file. Explain your changes in a comment block at the top.
Respond with ONLY the Python code in a ```python ... ``` block."""

    raw = _ollama(prompt, config)
    evolved = _extract_code(raw)

    # Save evolved version
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = skill_name.replace(".py", "")
    date_str = datetime.now().strftime("%Y%m%d")
    out_file = DRAFTS_DIR / f"{safe_name}_evolved_{date_str}_{agent_name}.py"
    out_file.write_text(evolved)

    evolved_lines = len(evolved.splitlines())

    return {
        "evolved": True,
        "skill_name": skill_name,
        "original_lines": source_lines,
        "evolved_lines": evolved_lines,
        "reviews_used": bool(reviews),
        "saved_to": str(out_file),
        "preview": evolved[:500],
    }
