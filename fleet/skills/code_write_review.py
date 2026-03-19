"""
Code write review — reviews aider-generated code and produces an approval/rejection report.

This is the gate between code_write output and deployment. Multiple coder perspectives
review the same code independently. Code is only considered safe when reviewed.

Payload:
  project_dir   str   path to the workspace (default: knowledge/code_writes/workspace)
  file          str   specific file to review (optional — reviews all changed if omitted)
  perspective   str   "software architect" | "code critic / reviewer" | "performance optimizer"
  agent_name    str   "coder_1"

Output: knowledge/code_writes/reviews/<file>_review_<date>_<agent>.md
"""
import re
from datetime import datetime
from pathlib import Path

from skills._models import call_complex

SKILL_NAME = "code_write_review"
DESCRIPTION = "Code write review — reviews aider-generated code and produces an approval/reject"

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
WRITES_DIR = KNOWLEDGE_DIR / "code_writes"
REVIEWS_DIR = WRITES_DIR / "reviews"

PERSPECTIVE_FOCUS = {
    "software architect":     "module structure, interface design, coupling, extensibility, payload contract",
    "code critic / reviewer": "bugs, error handling, edge cases, security, input validation, code clarity",
    "performance optimizer":  "I/O patterns, timeouts, memory usage, unnecessary work, scaling concerns",
}


def _get_recent_changes(project_dir: Path) -> dict:
    """Get files changed in last commit with their content."""
    import subprocess
    r = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only"],
        cwd=str(project_dir), capture_output=True, text=True, timeout=10,
    )
    files = {}
    for name in r.stdout.strip().splitlines():
        if not name:
            continue
        p = project_dir / name
        if p.exists() and p.suffix == ".py":
            files[name] = p.read_text(errors="ignore")[:8000]
    return files


def run(payload, config):
    project_dir = Path(payload.get("project_dir", str(WRITES_DIR / "workspace")))
    target_file = payload.get("file", "")
    perspective = payload.get("perspective", "software architect")
    agent_name = payload.get("agent_name", "coder_1")

    focus = PERSPECTIVE_FOCUS.get(perspective, PERSPECTIVE_FOCUS["software architect"])

    # Get code to review
    if target_file:
        p = project_dir / target_file
        if not p.exists():
            return {"error": f"File not found: {target_file}"}
        files = {target_file: p.read_text(errors="ignore")[:8000]}
    else:
        files = _get_recent_changes(project_dir)

    if not files:
        return {"error": "No files to review"}

    reviews = []
    for filename, code in files.items():
        system = f"You are a {perspective} reviewing auto-generated code for a local AI agent fleet. REVIEW FOCUS: {focus}"

        user = f"""FILE: {filename}
```python
{code}
```

Produce a structured review:

1. **VERDICT**: APPROVE / NEEDS_CHANGES / REJECT
2. **Summary**: 1-2 sentence assessment
3. **Findings**: List each issue as:
   - [SEVERITY] Line N: description (severity: CRITICAL / WARNING / NOTE)
4. **Recommendations**: What to fix before deployment

Be strict — this code was AI-generated and needs human-quality validation.
Output the review in markdown format."""

        review = call_complex(system, user, config, skill_name="code_write_review")
        reviews.append({"file": filename, "review": review})

    # Save review
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^a-z0-9_]", "_", (target_file or "workspace")[:30].lower())
    out_file = REVIEWS_DIR / f"{safe_name}_review_{ts}_{agent_name}.md"

    content = f"# Code Write Review — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    content += f"**Reviewer**: {agent_name} ({perspective})\n"
    content += f"**Project**: {project_dir}\n\n"

    for r in reviews:
        content += f"## {r['file']}\n\n{r['review']}\n\n---\n\n"

    out_file.write_text(content)

    # Extract verdict from first review
    verdict = "UNKNOWN"
    if reviews:
        v_match = re.search(r'VERDICT[:\s]*(APPROVE|NEEDS_CHANGES|REJECT)', reviews[0]["review"], re.I)
        if v_match:
            verdict = v_match.group(1).upper()

    return {
        "verdict": verdict,
        "files_reviewed": list(files.keys()),
        "reviewer": f"{agent_name} ({perspective})",
        "saved_to": str(out_file),
        "preview": reviews[0]["review"][:500] if reviews else "",
    }