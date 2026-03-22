"""Claude Code companion skill for HITL response editing.

Usage from Claude Code:  /hitl-respond
Reads pending hitl-response-{id}.md files, shows context, helps draft response.
"""
SKILL_NAME = "hitl_respond"
DESCRIPTION = "Help operator draft HITL responses for fleet agents via VS Code"
REQUIRES_NETWORK = False


def run(task: dict, context: dict) -> dict:
    import db
    from pathlib import Path

    response_dir = Path(__file__).parent.parent / "hitl-responses"
    if not response_dir.exists():
        return {"status": "ok", "result": "No pending HITL responses."}

    pending = sorted(response_dir.glob("hitl-response-*.md"))
    if not pending:
        return {"status": "ok", "result": "No pending HITL responses."}

    results = []
    for p in pending:
        text = p.read_text(encoding="utf-8")
        tid = p.stem.replace("hitl-response-", "")
        results.append({
            "task_id": tid,
            "file": str(p),
            "content": text[:2000],
        })

    return {
        "status": "ok",
        "result": f"Found {len(results)} pending HITL response(s).",
        "pending": results,
        "instructions": (
            "Open the response file, write your response under '## Your Response', "
            "then save. BigEd will automatically detect the save and deliver it."
        ),
    }
