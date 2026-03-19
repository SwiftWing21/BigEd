"""
Marathon log — writes progress snapshots for long-running projects.

Maintains context across sessions by appending timestamped snapshots to a
per-session markdown file in knowledge/marathon/.

Payload:
  session_id       str       unique session identifier (required)
  goal             str       what the session is trying to accomplish (required)
  completed_steps  list[str] steps completed so far (required)
  next_step        str       what to do next (required)
  blockers         list[str] current blockers (optional)
  notes            str       additional context (optional)

Output: knowledge/marathon/{session_id}.md
Returns: {status, session_id, snapshot_number, file_path}
"""
import json
import re
from datetime import datetime
from pathlib import Path

SKILL_NAME = "marathon_log"
DESCRIPTION = "Write progress snapshots for long-running projects to maintain context across sessions"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
MARATHON_DIR = FLEET_DIR / "knowledge" / "marathon"


def _count_snapshots(content: str) -> int:
    """Count existing snapshot headers in the file."""
    return len(re.findall(r"^## Snapshot \d+", content, re.MULTILINE))


def run(payload, config):
    session_id = payload.get("session_id", "")
    goal = payload.get("goal", "")
    completed_steps = payload.get("completed_steps", [])
    next_step = payload.get("next_step", "")
    blockers = payload.get("blockers", [])
    notes = payload.get("notes", "")

    if not session_id:
        return json.dumps({"status": "error", "error": "No session_id provided"})
    if not goal:
        return json.dumps({"status": "error", "error": "No goal provided"})
    if not next_step:
        return json.dumps({"status": "error", "error": "No next_step provided"})

    try:
        MARATHON_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Cannot create marathon dir: {e}"})

    file_path = MARATHON_DIR / f"{session_id}.md"

    # Read existing content to determine snapshot number
    existing = ""
    if file_path.exists():
        try:
            existing = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            existing = ""

    snapshot_number = _count_snapshots(existing) + 1
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build completed list
    total_steps = len(completed_steps) + 1  # +1 for the next step
    completed_bullets = "\n".join(f"- {step}" for step in completed_steps) if completed_steps else "- (none yet)"

    # Build blockers list
    if blockers:
        blockers_text = "\n".join(f"- {b}" for b in blockers)
    else:
        blockers_text = "None"

    # Build notes section
    notes_text = notes if notes else "(none)"

    snapshot = (
        f"## Snapshot {snapshot_number} — {timestamp}\n"
        f"**Goal:** {goal}\n"
        f"**Progress:** {len(completed_steps)}/{total_steps} steps\n"
        f"**Completed:**\n{completed_bullets}\n"
        f"**Next:** {next_step}\n"
        f"**Blockers:**\n{blockers_text}\n"
        f"**Notes:** {notes_text}\n"
        f"---\n"
    )

    # Write or append
    try:
        if existing:
            file_path.write_text(existing.rstrip() + "\n\n" + snapshot, encoding="utf-8")
        else:
            header = f"# Marathon Log: {session_id}\n\n"
            file_path.write_text(header + snapshot, encoding="utf-8")
    except Exception as e:
        return json.dumps({"status": "error", "error": f"Write failed: {e}"})

    return json.dumps({
        "status": "logged",
        "session_id": session_id,
        "snapshot_number": snapshot_number,
        "file_path": str(file_path),
    })
