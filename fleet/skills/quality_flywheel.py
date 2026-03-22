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
    elif action == "discover":
        from skills._flywheel_core import discover_novel_patterns
        project_root = Path(payload.get("project_path", str(FLEET_DIR.parent)))
        return {"discoveries": discover_novel_patterns(project_root)}
    elif action == "regression_check":
        return _regression_check(payload, config, log)
    elif action == "verify":
        return _verify(payload, config, log)
    elif action == "s_tier":
        return _s_tier(payload, config, log)
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


def _regression_check(payload, config, log):
    """Check if S-tier has been maintained for 7+ consecutive days.

    Returns a lock status dict. When s_tier_locked is True, the project
    qualifies for premium model routing (Opus for complex tasks).
    Gracefully handles cases where not enough historical data exists.
    """
    project_root = str(Path(payload.get("project_path", str(FLEET_DIR.parent))))

    try:
        import db
        with db.get_conn() as conn:
            # Get last 7 days of overall scores (avg per day)
            rows = conn.execute("""
                SELECT DISTINCT date(created_at) as day,
                       AVG(score) as avg_score
                FROM flywheel_scores
                WHERE project_path = ?
                AND created_at > datetime('now', '-7 days')
                GROUP BY date(created_at)
                ORDER BY day
            """, (project_root,)).fetchall()
    except Exception as e:
        return {"s_tier_locked": False, "reason": f"DB error: {e}", "days": 0}

    if len(rows) < 7:
        return {
            "s_tier_locked": False,
            "reason": f"Only {len(rows)} days of data (need 7)",
            "days": len(rows),
        }

    all_above_95 = all(r["avg_score"] >= 95 for r in rows)

    if all_above_95:
        avg = sum(r["avg_score"] for r in rows) / len(rows)
        return {
            "s_tier_locked": True,
            "days_maintained": len(rows),
            "avg_score": round(avg, 1),
            "premium_routing_eligible": True,
        }

    # Find which days dropped below
    drops = [{"day": r["day"], "score": round(r["avg_score"], 1)}
             for r in rows if r["avg_score"] < 95]
    return {
        "s_tier_locked": False,
        "reason": f"{len(drops)} days below 95",
        "drops": drops,
    }


def _parse_llm_grades(response: str) -> dict[str, float]:
    """Parse LLM-generated grades from free-text response.

    Expects the LLM to mention dimension names followed by scores (0-100).
    Tolerant of various formats: "completeness: 85", "completeness — 85/100", etc.
    """
    import re
    grades = {}
    # Match patterns like "completeness: 85" or "completeness — 85/100" or "completeness 85"
    for dim in ("completeness", "consistency", "actionability", "coverage", "freshness",
                "accuracy", "first_attempt_rate", "regression_rate",
                "context_utilization", "feedback_incorporation"):
        # Try several patterns
        patterns = [
            rf'{dim}\s*[:\-—=]\s*(\d{{1,3}})',
            rf'{dim}\s+(\d{{1,3}})',
        ]
        for pat in patterns:
            m = re.search(pat, response, re.I)
            if m:
                score = float(m.group(1))
                grades[dim] = min(100, max(0, score))
                break
    return grades


def _verify(payload, config, log):
    """Multi-agent verification: run evidence audit twice independently, compare scores.

    First run uses the deterministic grading engine. Second run uses an LLM
    as an independent grader. Scores must agree within 5 points per dimension
    for verification to pass.
    """
    from skills._flywheel_core import run_evidence_audit, format_audit_report, FLYWHEEL_DIR, RUBRIC
    from skills._models import call_complex

    project_root = Path(payload.get("project_path", str(FLEET_DIR.parent)))

    # Run 1: deterministic evidence audit
    audit1 = run_evidence_audit(project_root)

    # Run 2: independent LLM grader
    claude_md = project_root / "CLAUDE.md"
    if claude_md.exists():
        claude_md_content = claude_md.read_text(encoding="utf-8", errors="ignore")[:4000]
    else:
        claude_md_content = "(no CLAUDE.md found)"

    dimensions_desc = "\n".join(
        f"- {dim}: {info['description']}" for dim, info in RUBRIC.items()
    )

    prompt = (
        f"Grade this project's CLAUDE.md on these 10 dimensions (0-100 each):\n"
        f"{dimensions_desc}\n\n"
        f"CLAUDE.md content:\n{claude_md_content}\n\n"
        f"Output each dimension name followed by a colon and integer score. "
        f"Example: completeness: 85"
    )

    try:
        response = call_complex(
            system="You are an independent quality auditor. Grade strictly based on evidence in the text. "
                   "Do not inflate scores. A missing section means 0 for that section's contribution.",
            user=prompt, config=config, max_tokens=1024,
            skill_name="quality_flywheel")
        audit2_scores = _parse_llm_grades(response)
    except Exception as e:
        log.warning(f"LLM verification failed: {e}")
        audit2_scores = {}

    # Compare — must agree within 5 points per dimension
    disagreements = []
    for dim in audit1["scores"]:
        s1 = audit1["scores"][dim]["score"]
        s2 = audit2_scores.get(dim, 50)
        if abs(s1 - s2) > 5:
            disagreements.append({
                "dimension": dim,
                "score_engine": s1,
                "score_llm": s2,
                "delta": abs(s1 - s2),
            })

    verified = len(disagreements) == 0 and len(audit2_scores) > 0

    # Save verification report
    FLYWHEEL_DIR.mkdir(parents=True, exist_ok=True)
    report = format_audit_report(audit1, project_root.name)
    report += "\n## Multi-Agent Verification\n"
    report += f"- **Verified:** {'Yes' if verified else 'No'}\n"
    report += f"- **LLM dimensions graded:** {len(audit2_scores)}\n"
    report += f"- **Disagreements:** {len(disagreements)}\n"
    for d in disagreements:
        report += f"  - {d['dimension']}: engine={d['score_engine']}, llm={d['score_llm']} (delta {d['delta']})\n"

    report_path = FLYWHEEL_DIR / f"verify_{time.strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    log.info(f"Flywheel verify: verified={verified}, disagreements={len(disagreements)}")
    return {
        "verified": verified,
        "disagreements": disagreements,
        "audit": audit1,
        "llm_scores": audit2_scores,
        "saved_to": str(report_path),
    }


def _s_tier(payload, config, log):
    """Full S-tier assessment: evidence audit + hallucination check.

    This is the complete S-tier pipeline:
    1. Run evidence-only audit (scores without evidence are capped at 80)
    2. Verify all evidence citations against actual files (hallucination check)
    3. Any hallucinations disqualify S-tier eligibility

    Use action='verify' separately for multi-agent cross-validation.
    """
    from skills._flywheel_core import (
        run_evidence_audit, _check_hallucinations, format_audit_report, FLYWHEEL_DIR
    )

    project_root = Path(payload.get("project_path", str(FLEET_DIR.parent)))

    # Step 1: evidence audit
    audit = run_evidence_audit(project_root)

    # Step 2: hallucination check
    hallucinations = _check_hallucinations(audit, project_root)
    if hallucinations:
        audit["s_tier_eligible"] = False
        audit["hallucinations"] = hallucinations

    # Save report
    FLYWHEEL_DIR.mkdir(parents=True, exist_ok=True)
    report = format_audit_report(audit, project_root.name)
    report_path = FLYWHEEL_DIR / f"s_tier_{time.strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")

    # Store in DB
    try:
        import db
        def _store():
            with db.get_conn() as conn:
                for dim, data in audit["scores"].items():
                    conn.execute(
                        "INSERT INTO flywheel_scores (project_path, dimension, grade, score, details_json) VALUES (?,?,?,?,?)",
                        (str(project_root), dim, data["grade"], data["score"],
                         json.dumps({"issues": data["issues"], "evidence": data.get("evidence", [])})))
        db._retry_write(_store)
    except Exception:
        pass

    log.info(f"S-tier check: eligible={audit['s_tier_eligible']}, "
             f"grade={audit['s_tier_grade']}, hallucinations={len(hallucinations)}")
    return {
        "s_tier_eligible": audit["s_tier_eligible"],
        "s_tier_grade": audit["s_tier_grade"],
        "overall_score": audit["overall_score"],
        "overall_grade": audit["overall_grade"],
        "scores": audit["scores"],
        "hallucinations": hallucinations,
        "gaps": audit["gaps"],
        "saved_to": str(report_path),
    }
