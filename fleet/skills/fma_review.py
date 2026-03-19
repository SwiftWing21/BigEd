"""
FMA review skill — coder agents review BigEd CC files and discuss
enhancements, optimizations, and new feature ideas.

Combines code_review (line-level findings) with code_discuss (multi-agent discussion)
specifically targeting the FMA launcher codebase.

Payload:
  agent_name       str   "coder_1" | "coder_2" | "coder_3"
  perspective      str   "software architect" | "code critic / reviewer" | "performance optimizer"
  file             str   FMA file to review (default: auto-rotate through all)
  focus            str   optional narrow focus, e.g. "UX improvements"
  round            int   discussion round (default 1)
  mode             str   "review" | "discuss" | "both" (default "both")

Output:
  - Reviews:     knowledge/fma_reviews/<file>_review_<date>_<agent>.md
  - Discussion:  knowledge/fma_reviews/discussion_round<N>.md
  - Messages:    posted to messages table for cross-agent visibility
"""
import json
from datetime import datetime
from pathlib import Path

SKILL_NAME = "fma_review"
DESCRIPTION = "FMA review skill — coder agents review BigEd CC files and discuss"

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
FMA_REVIEWS_DIR = KNOWLEDGE_DIR / "fma_reviews"

# FMA source files — resolve relative to project root (cross-platform)
FMA_DIR = FLEET_DIR.parent / "BigEd" / "launcher"
FMA_FILES = [
    "launcher.py",
    "updater.py",
    "installer.py",
    "uninstaller.py",
    "generate_icon.py",
]

PERSPECTIVE_FOCUS = {
    "software architect": "module structure, separation of concerns, extensibility, new feature opportunities, GUI architecture",
    "code critic / reviewer": "bugs, error handling, edge cases, UX issues, accessibility, user experience gaps",
    "performance optimizer": "startup time, memory usage, threading efficiency, I/O patterns, resource management",
}

ENHANCEMENT_CATEGORIES = [
    "New feature ideas",
    "UX/UI improvements",
    "Performance optimizations",
    "Code quality & maintainability",
    "Error handling & resilience",
    "Integration opportunities (Discord, OpenClaw, fleet skills)",
]

from skills._models import call_complex


def _pick_file(requested: str) -> tuple[Path, str] | tuple[None, str]:
    """Pick an FMA file to review. Returns (path, relative_name) or (None, error)."""
    if requested:
        p = FMA_DIR / requested
        if p.exists():
            return p, requested
        return None, f"File not found: {requested}"
    # Auto-rotate: pick least-recently-reviewed
    FMA_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    existing = {f.stem for f in FMA_REVIEWS_DIR.glob("*_review_*.md")}
    for fname in FMA_FILES:
        key = fname.replace(".py", "")
        if not any(key in e for e in existing):
            p = FMA_DIR / fname
            if p.exists():
                return p, fname
    # All reviewed — start over with launcher.py
    p = FMA_DIR / "launcher.py"
    return (p, "launcher.py") if p.exists() else (None, "No FMA files found")


def _load_prior_discussion(topic: str) -> str:
    """Load prior FMA discussion from messages table."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT from_agent, body_json FROM messages
            WHERE json_extract(body_json, '$.topic') = ?
              AND channel IN ('agent', 'fleet')
            ORDER BY created_at ASC
        """, (topic,)).fetchall()
    contributions = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
            contributions.append(f"[{row['from_agent']}]: {body.get('contribution', '')}")
        except Exception:
            pass
    return "\n\n".join(contributions[-6:])  # last 6 contributions for context


def _do_review(file_path: Path, fname: str, perspective: str, agent_name: str, focus: str, config: dict) -> dict:
    """Produce a structured review of an FMA file."""
    try:
        source = file_path.read_text(errors="ignore")
    except Exception as e:
        return {"error": str(e)}

    lines = source.splitlines()
    if len(lines) > 500:
        source_excerpt = "\n".join(lines[:500]) + f"\n\n... ({len(lines) - 500} more lines truncated)"
    else:
        source_excerpt = source

    focus_area = focus or PERSPECTIVE_FOCUS.get(perspective, "general quality and enhancement opportunities")
    categories = "\n".join(f"- {c}" for c in ENHANCEMENT_CATEGORIES)

    prompt = f"""You are a {perspective} reviewing the BigEd CC (FMA) — a Windows GUI launcher
for a local AI agent fleet. The app uses customtkinter with a dark/brick theme.

FILE: {fname} ({len(lines)} lines)
REVIEW FOCUS: {focus_area}

SOURCE CODE:
```python
{source_excerpt}
```

Produce a structured review with these sections:

## Summary
One paragraph — what this file does, overall quality assessment.

## Findings
List each as: - [SEVERITY] Line ~N: issue and suggested fix
Severity: CRITICAL | HIGH | MEDIUM | LOW | NOTE
Focus on: {focus_area}
Limit to 8 most important findings.

## Enhancement Recommendations
Propose 3-5 concrete enhancements in these categories:
{categories}

For each: describe the enhancement, estimated complexity (low/med/high), and expected impact.

## Top Priority
The single most impactful change you'd make first, with a brief implementation sketch."""

    review_text = call_complex("You are a code reviewer for the FMA launcher.", prompt, config)

    FMA_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    safe_name = fname.replace(".py", "")
    out_file = FMA_REVIEWS_DIR / f"{safe_name}_review_{date_str}_{agent_name}.md"

    header = (
        f"# FMA Review: `{fname}`\n"
        f"**Reviewer:** {agent_name} ({perspective})\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Focus:** {focus_area}\n\n---\n\n"
    )
    out_file.write_text(header + review_text)

    return {
        "file_reviewed": fname,
        "perspective": perspective,
        "saved_to": str(out_file),
        "findings_preview": review_text[:400],
    }


def _do_discuss(agent_name: str, perspective: str, round_num: int, focus: str, config: dict) -> dict:
    """Contribute to the ongoing FMA enhancement discussion."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    topic = "FMA enhancement and optimization discussion"
    prior = _load_prior_discussion(topic)

    # Load a summary of FMA structure for context
    fma_summary_parts = []
    for fname in FMA_FILES:
        p = FMA_DIR / fname
        if p.exists():
            lines = p.read_text(errors="ignore").splitlines()
            fma_summary_parts.append(f"- {fname}: {len(lines)} lines")
    fma_overview = "\n".join(fma_summary_parts)

    categories = "\n".join(f"- {c}" for c in ENHANCEMENT_CATEGORIES)

    prompt = f"""You are the {perspective} in a recurring FMA (BigEd CC) enhancement discussion.

The FMA is a Windows GUI (customtkinter, dark/brick theme) that controls a local AI agent fleet:
{fma_overview}

Key FMA capabilities: fleet start/stop, worker status, GPU monitoring, task dispatch, log viewing,
security advisory review, eco mode toggle, auto-update system.

DISCUSSION FOCUS: {focus or 'enhancements, optimizations, and new features'}
ROUND: {round_num}
CATEGORIES TO CONSIDER:
{categories}

{f"PRIOR DISCUSSION:{chr(10)}{prior}" if prior else "You are opening this discussion round."}

As the {perspective}, contribute your analysis:
- Build on prior contributions — don't repeat what was said
- Propose concrete, actionable enhancements with estimated effort
- Consider integration with Discord bot, OpenClaw, and fleet skills
- Prioritize by impact vs effort
- 4-6 bullet points max, be specific and technical"""

    contribution = call_complex("You are a helpful assistant.", prompt, config)

    # Post to messages table
    db.post_message(
        from_agent=agent_name,
        to_agent="all",
        body_json=json.dumps({
            "topic": topic,
            "round": round_num,
            "role_perspective": perspective,
            "contribution": contribution,
            "timestamp": datetime.now().isoformat(),
        }),
        channel="agent",
    )

    # Append to discussion log
    FMA_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = FMA_REVIEWS_DIR / f"discussion_round{round_num}.md"
    with open(log_file, "a") as f:
        f.write(f"\n## [{agent_name}] — {perspective} — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{contribution}\n")

    return {"contribution": contribution, "topic": topic, "round": round_num}


def run(payload, config):
    agent_name = payload.get("agent_name", "coder_1")
    perspective = payload.get("perspective", PERSPECTIVE_FOCUS.get(agent_name, "code critic / reviewer"))
    # Map coder names to perspectives if perspective wasn't explicit
    if perspective not in PERSPECTIVE_FOCUS:
        perspective_map = {"coder_1": "software architect", "coder_2": "code critic / reviewer", "coder_3": "performance optimizer"}
        perspective = perspective_map.get(agent_name, "code critic / reviewer")
    requested_file = payload.get("file", "")
    focus = payload.get("focus", "")
    round_num = payload.get("round", 1)
    mode = payload.get("mode", "both")

    results = {}

    if mode in ("review", "both"):
        target, fname = _pick_file(requested_file)
        if target:
            results["review"] = _do_review(target, fname, perspective, agent_name, focus, config)
        else:
            results["review"] = {"error": fname}

    if mode in ("discuss", "both"):
        results["discuss"] = _do_discuss(agent_name, perspective, round_num, focus, config)

    return results