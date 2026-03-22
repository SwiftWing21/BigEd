"""Quality Flywheel — audit context files, grade quality, propose improvements, learn from feedback."""
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

SKILL_NAME = "quality_flywheel"
DESCRIPTION = "Audit project context files, grade quality, propose improvements, learn from feedback"
COMPLEXITY = "complex"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent


def run(payload: dict, config: dict, log=None) -> dict:
    if log is None:
        log = logging.getLogger(__name__)
    action = payload.get("action", "audit")

    if action == "audit":
        return _audit(payload, config, log)
    elif action == "gaps":
        return _gaps(payload, config, log)
    elif action == "draft":
        return _draft(payload, config, log)
    elif action == "apply":
        return _apply(payload, config, log)
    elif action == "history":
        return _history(payload, config, log)
    elif action == "calibrate":
        return _calibrate(payload, config, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _audit(payload, config, log):
    """Run full 10-dimension audit."""
    from skills._flywheel_core import run_full_audit, format_audit_report, FLYWHEEL_DIR

    project_root = Path(payload.get("project_path", str(FLEET_DIR.parent)))
    audit = run_full_audit(project_root)

    # Save report
    FLYWHEEL_DIR.mkdir(parents=True, exist_ok=True)
    report = format_audit_report(audit, project_root.name)
    report_path = FLYWHEEL_DIR / f"audit_{time.strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    # Store scores in DB
    try:
        import db
        def _store():
            with db.get_conn() as conn:
                for dim, data in audit["scores"].items():
                    conn.execute(
                        "INSERT INTO flywheel_scores (project_path, dimension, grade, score, details_json) VALUES (?,?,?,?,?)",
                        (str(project_root), dim, data["grade"], data["score"], json.dumps(data["issues"])))
        db._retry_write(_store)
    except Exception:
        pass

    log.info(f"Flywheel audit: {audit['overall_grade']} ({audit['overall_score']}/100)")
    return {
        "overall_grade": audit["overall_grade"],
        "overall_score": audit["overall_score"],
        "scores": audit["scores"],
        "gaps": audit["gaps"],
        "saved_to": str(report_path),
    }


def _gaps(payload, config, log):
    """Show gap analysis only."""
    result = _audit(payload, config, log)
    return {"gaps": result.get("gaps", []), "overall_grade": result.get("overall_grade")}


def _draft(payload, config, log):
    """Generate proposed context file improvements."""
    from skills._flywheel_core import run_full_audit, FLYWHEEL_DIR, DRAFTS_DIR

    project_root = Path(payload.get("project_path", str(FLEET_DIR.parent)))
    dimension = payload.get("dimension", "")

    audit = run_full_audit(project_root)

    # Find lowest-scoring dimensions to improve
    targets = []
    if dimension:
        data = audit["scores"].get(dimension)
        if data:
            targets.append((dimension, data))
    else:
        sorted_dims = sorted(audit["scores"].items(), key=lambda x: x[1]["score"])
        targets = sorted_dims[:3]  # bottom 3

    # Read current CLAUDE.md
    claude_md = project_root / "CLAUDE.md"
    current_content = ""
    if claude_md.exists():
        current_content = claude_md.read_text(encoding="utf-8", errors="ignore")[:3000]

    # LLM: generate improvement drafts
    from skills._models import call_complex
    drafts = []
    for dim, data in targets:
        if data["score"] >= 85:
            continue  # already good

        prompt = (
            f"You are improving a project's CLAUDE.md context file.\n\n"
            f"Dimension: {dim} (current score: {data['score']}/100, grade: {data['grade']})\n"
            f"Issues found: {json.dumps(data['issues'])}\n\n"
            f"Current CLAUDE.md (excerpt):\n{current_content[:1500]}\n\n"
            f"Generate a specific, actionable addition to CLAUDE.md that would improve "
            f"the '{dim}' score. Output ONLY the markdown text to add (not the full file). "
            f"Be concrete — use exact file paths, command examples, and specific rules."
        )

        try:
            suggestion = call_complex(
                system="You improve AI context files. Be specific, actionable, concise.",
                user=prompt, config=config, max_tokens=512,
                skill_name="quality_flywheel")
        except Exception as e:
            suggestion = f"(Draft generation failed: {e})"

        draft = {
            "id": f"draft_{int(time.time())}_{dim}",
            "dimension": dim,
            "current_score": data["score"],
            "current_grade": data["grade"],
            "suggestion": suggestion,
            "target_grade": "B+" if data["score"] < 75 else "A",
        }
        drafts.append(draft)

    # Save drafts
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    for d in drafts:
        draft_path = DRAFTS_DIR / f"{d['id']}.json"
        draft_path.write_text(json.dumps(d, indent=2), encoding="utf-8")

    return {"drafts": drafts, "count": len(drafts)}


def _apply(payload, config, log):
    """Apply an approved draft to CLAUDE.md and re-grade."""
    from skills._flywheel_core import DRAFTS_DIR

    draft_id = payload.get("draft_id", "")
    if not draft_id:
        return {"error": "draft_id required"}

    draft_path = DRAFTS_DIR / f"{draft_id}.json"
    if not draft_path.exists():
        return {"error": f"Draft not found: {draft_id}"}

    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    project_root = Path(payload.get("project_path", str(FLEET_DIR.parent)))
    claude_md = project_root / "CLAUDE.md"

    if not claude_md.exists():
        return {"error": "No CLAUDE.md to apply to"}

    # Append the suggestion
    current = claude_md.read_text(encoding="utf-8")
    updated = current.rstrip() + "\n\n" + draft["suggestion"] + "\n"
    claude_md.write_text(updated, encoding="utf-8")

    # Re-grade to verify improvement
    re_audit = _audit(payload, config, log)

    # Log to reinforcement
    try:
        import db
        db.submit_feedback(f"flywheel_{draft_id}", "approved",
                           feedback_text=f"Applied to CLAUDE.md, dimension: {draft['dimension']}")
    except Exception:
        pass

    return {
        "applied": draft_id,
        "dimension": draft["dimension"],
        "previous_score": draft["current_score"],
        "new_score": re_audit["scores"].get(draft["dimension"], {}).get("score", 0),
        "new_grade": re_audit["overall_grade"],
    }


def _history(payload, config, log):
    """Show score trend over time."""
    project_root = str(Path(payload.get("project_path", str(FLEET_DIR.parent))))
    days = payload.get("days", 30)
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT dimension, grade, score, created_at FROM flywheel_scores
                WHERE project_path = ? AND created_at > datetime('now', ?)
                ORDER BY created_at
            """, (project_root, f"-{days} days")).fetchall()
        return {"history": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def _calibrate(payload, config, log):
    """Re-calibrate rubric weights from feedback history."""
    # Check which dimensions have most approved vs rejected drafts
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT output_path, verdict FROM output_feedback
                WHERE output_path LIKE 'flywheel_%'
            """).fetchall()

        approved = sum(1 for r in rows if r["verdict"] == "approved")
        rejected = sum(1 for r in rows if r["verdict"] == "rejected")
        total = approved + rejected

        if total < 5:
            return {"message": "Not enough feedback to calibrate (need 5+)", "total": total}

        approval_rate = approved / max(1, total)
        return {
            "calibrated": True,
            "total_feedback": total,
            "approval_rate": round(approval_rate, 2),
            "message": f"Approval rate: {approval_rate:.0%}. Weights maintained." if approval_rate > 0.6
                       else f"Low approval rate ({approval_rate:.0%}) — consider reviewing rubric weights",
        }
    except Exception as e:
        return {"error": str(e)}
