"""Quality Flywheel — audit orchestration, gap analysis, and report formatting."""
import logging
import time
from pathlib import Path

from skills._flywheel_rubric import RUBRIC, score_to_grade
from skills._flywheel_grading import (
    grade_completeness,
    grade_consistency,
    grade_actionability,
    grade_coverage,
    grade_freshness,
    grade_output_quality,
)

log = logging.getLogger(__name__)


# ── Full audit + gap analysis + report ────────────────────────────────────

def _unpack_grade(result):
    """Normalize grade results to (score, issues, evidence).

    Grading functions may return 2-tuples (score, issues) from Part B
    or 3-tuples (score, issues, evidence) from Part A.  This helper
    normalises both to a consistent 3-tuple.
    """
    if len(result) == 3:
        return result[0], result[1], result[2]
    return result[0], result[1], []


def run_full_audit(project_root: Path) -> dict:
    """Run complete 10-dimension audit. Returns graded report.

    Backward-compatible: output dict structure unchanged from A-tier.
    Evidence is collected internally but not exposed in the return dict;
    use run_evidence_audit() for S-tier with evidence.
    """
    raw_scores = {}

    # Part A: Context quality (returns 3-tuples with evidence)
    raw_scores["completeness"] = grade_completeness(project_root)
    raw_scores["consistency"] = grade_consistency(project_root)
    raw_scores["actionability"] = grade_actionability(project_root)
    raw_scores["coverage"] = grade_coverage(project_root)
    raw_scores["freshness"] = grade_freshness(project_root)

    # Part B: Output quality (returns 2-tuples, no evidence yet)
    output_scores = grade_output_quality(project_root)
    raw_scores.update(output_scores)

    # Normalise to (score, issues) for backward-compat calculations
    scores = {}
    for dim, result in raw_scores.items():
        s, i, _ev = _unpack_grade(result)
        scores[dim] = (s, i)

    # Calculate overall
    overall = 0
    for dim, (score, _) in scores.items():
        weight = RUBRIC[dim]["weight"]
        overall += score * weight

    # Gap analysis
    gaps = find_gaps(scores)

    return {
        "scores": {dim: {"score": s, "grade": score_to_grade(s), "issues": i}
                   for dim, (s, i) in scores.items()},
        "overall_score": round(overall, 1),
        "overall_grade": score_to_grade(overall),
        "gaps": gaps,
    }


def find_gaps(scores: dict) -> list[dict]:
    """Find where context quality doesn't match output quality."""
    gaps = []
    context_avg = sum(s for dim, (s, _) in scores.items()
                      if RUBRIC.get(dim, {}).get("part") == "context") / 5
    output_avg = sum(s for dim, (s, _) in scores.items()
                     if RUBRIC.get(dim, {}).get("part") == "output") / 5

    if context_avg > 75 and output_avg < 60:
        gaps.append({
            "type": "context_not_effective",
            "message": "Context exists but output quality is low — context may need rewording",
            "context_avg": round(context_avg, 1),
            "output_avg": round(output_avg, 1),
        })
    if output_avg > 80 and context_avg < 60:
        gaps.append({
            "type": "undocumented_quality",
            "message": "AI producing good output despite poor context — document what's working",
            "context_avg": round(context_avg, 1),
            "output_avg": round(output_avg, 1),
        })

    # Per-dimension gaps
    for dim, (score, issues) in scores.items():
        if score < 60 and issues:
            gaps.append({
                "type": "low_score",
                "dimension": dim,
                "score": score,
                "issues": issues,
            })

    return gaps


def discover_novel_patterns(project_root: Path) -> list:
    """Find patterns in the project that go beyond standard best practices.

    Lightweight pattern-matching only (no LLM calls). Scans CLAUDE.md for
    indicators of advanced engineering practices that are rare in typical
    projects.
    """
    discoveries = []

    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return discoveries

    content = claude_md.read_text(encoding="utf-8", errors="ignore")
    content_lower = content.lower()

    # Check for patterns that exceed typical CLAUDE.md files
    novel_checks = [
        ("quality_flywheel", "Self-auditing quality system",
         "quality_flywheel" in content_lower),
        ("reinforcement_loop", "Human feedback reinforcement",
         "reinforcement" in content_lower or "feedback" in content_lower),
        ("multi_provider_fallback", "HA fallback chain documented",
         "fallback" in content_lower and "chain" in content_lower),
        ("compliance_framework", "HIPAA/SOC2 compliance integration",
         "hipaa" in content_lower or "soc" in content_lower),
        ("event_triggers", "Automated event-driven task dispatch",
         "trigger" in content_lower and (
             "file_watch" in content_lower or "webhook" in content_lower)),
        ("context_windows", "Per-agent conversation memory",
         "context_manager" in content_lower or "context window" in content_lower),
        ("capacity_tracking", "API capacity window optimization",
         "capacity" in content_lower and "bonus" in content_lower),
    ]

    for pattern_id, description, found in novel_checks:
        if found:
            discoveries.append({
                "pattern": pattern_id,
                "description": description,
                "status": "implemented",
                "rarity": "novel",  # not found in typical projects
            })

    return discoveries


def format_audit_report(audit: dict, project_name: str = "") -> str:
    """Format audit results as markdown report."""
    ts = time.strftime("%Y-%m-%d %H:%M")
    report = f"# Quality Flywheel Audit\n"
    report += f"**Project:** {project_name or 'current'} | **Date:** {ts}\n"
    report += f"**Overall:** {audit['overall_grade']} ({audit['overall_score']}/100)\n\n"

    report += "## Report Card\n"
    report += "| Dimension | Grade | Score | Part |\n|-----------|-------|-------|------|\n"
    for dim, data in audit["scores"].items():
        part = RUBRIC[dim]["part"]
        report += f"| {dim} | {data['grade']} | {data['score']:.0f}/100 | {part} |\n"
    report += f"| **Overall** | **{audit['overall_grade']}** | **{audit['overall_score']:.0f}/100** | |\n\n"

    if audit["gaps"]:
        report += "## Gap Analysis\n"
        for gap in audit["gaps"]:
            report += f"- **{gap['type']}**: {gap.get('message', gap.get('dimension', ''))}\n"
            if gap.get("issues"):
                for issue in gap["issues"]:
                    report += f"  - {issue}\n"

    # S-tier sections (only present in evidence audits)
    if audit.get("s_tier_eligible") is not None:
        report += "\n## S-Tier Assessment\n"
        report += f"- **Eligible:** {'Yes' if audit['s_tier_eligible'] else 'No'}\n"
        report += f"- **S-Tier Grade:** {audit.get('s_tier_grade', 'N/A')}\n"
        if audit.get("hallucinations"):
            report += f"- **Hallucinations detected:** {len(audit['hallucinations'])}\n"
            for h in audit["hallucinations"]:
                report += f"  - [{h['dimension']}] {h['issue']}: {h['claim']}\n"

    # Evidence section (only present in evidence audits)
    has_evidence = any(data.get("evidence") for data in audit["scores"].values())
    if has_evidence:
        report += "\n## Evidence Citations\n"
        for dim, data in audit["scores"].items():
            if data.get("evidence"):
                report += f"\n### {dim}\n"
                for ev in data["evidence"]:
                    loc = f"{ev['file']}:{ev['line']}" if ev.get("line") else ev["file"]
                    report += f"- `{loc}` — {ev['detail']}\n"

    report += "\n"
    return report


# ── S-Tier: evidence-only scoring + hallucination detection ───────────────

def run_evidence_audit(project_root: Path) -> dict:
    """S-tier audit: every score must cite file:line evidence. No approximations.

    Wraps the same grading functions as run_full_audit() but collects evidence
    from every dimension and demotes any score that lacks citations (capped at 80).
    """
    raw_scores = {}

    # Part A: Context quality (returns 3-tuples with evidence)
    raw_scores["completeness"] = grade_completeness(project_root)
    raw_scores["consistency"] = grade_consistency(project_root)
    raw_scores["actionability"] = grade_actionability(project_root)
    raw_scores["coverage"] = grade_coverage(project_root)
    raw_scores["freshness"] = grade_freshness(project_root)

    # Part B: Output quality (returns 2-tuples, no evidence yet)
    output_scores = grade_output_quality(project_root)
    raw_scores.update(output_scores)

    # Build scored dict with evidence
    scores = {}
    flat_scores = {}  # (score, issues) for find_gaps compat
    for dim, result in raw_scores.items():
        s, i, ev = _unpack_grade(result)
        scores[dim] = {
            "score": s,
            "grade": score_to_grade(s),
            "issues": list(i),
            "evidence": list(ev),
        }
        flat_scores[dim] = (s, i)

    # Validate every dimension has evidence — demote if missing
    for dim, data in scores.items():
        if not data["evidence"]:
            data["score"] = min(data["score"], 80)
            data["issues"].append(f"S-tier: no file:line evidence for {dim}")
            data["grade"] = score_to_grade(data["score"])

    # Calculate overall
    overall = 0
    for dim, data in scores.items():
        weight = RUBRIC[dim]["weight"]
        overall += data["score"] * weight

    # Gap analysis (uses flat_scores format)
    gaps = find_gaps(flat_scores)

    # S-tier eligibility: all dimensions must be >= 95
    all_95 = all(d["score"] >= 95 for d in scores.values())

    return {
        "scores": scores,
        "overall_score": round(overall, 1),
        "overall_grade": score_to_grade(overall),
        "gaps": gaps,
        "s_tier_eligible": all_95,
        "s_tier_grade": "S" if all_95 else score_to_grade(overall),
    }


def _check_hallucinations(audit: dict, project_root: Path) -> list[dict]:
    """Verify each evidence citation actually exists in the file.

    Scans every evidence entry in the audit and confirms:
    1. The cited file exists on disk
    2. The cited detail text appears in the file (first 30 chars checked)

    Returns a list of hallucination dicts for any citation that fails verification.
    """
    hallucinations = []
    resolved_root = project_root.resolve()
    for dim, data in audit["scores"].items():
        for ev in data.get("evidence", []):
            file_path = project_root / ev["file"]

            # Guard against path traversal (e.g. "../../etc/passwd")
            try:
                if not file_path.resolve().is_relative_to(resolved_root):
                    hallucinations.append({
                        "dimension": dim,
                        "claim": ev,
                        "issue": "path traversal outside project root",
                    })
                    continue
            except (ValueError, OSError):
                hallucinations.append({
                    "dimension": dim,
                    "claim": ev,
                    "issue": "invalid path",
                })
                continue

            # Skip directory references (path ends with /)
            if ev["file"].endswith("/"):
                if not file_path.exists():
                    hallucinations.append({
                        "dimension": dim,
                        "claim": ev,
                        "issue": "directory not found",
                    })
                continue

            if not file_path.exists():
                hallucinations.append({
                    "dimension": dim,
                    "claim": ev,
                    "issue": "file not found",
                })
                continue

            # Skip aggregate evidence (line=0 means no specific line citation)
            if not ev.get("line") or ev["line"] == 0:
                continue

            # Check if cited content actually appears near the cited line
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                detail_snippet = ev.get("detail", "")
                # Extract the actual quoted content from the detail string
                # Details often look like: "+8pts Has code examples: ```bash"
                # We check the last portion after the colon, if present
                if ": " in detail_snippet:
                    check_text = detail_snippet.split(": ", 1)[1][:30]
                else:
                    check_text = detail_snippet[:30]

                if check_text and check_text not in content:
                    hallucinations.append({
                        "dimension": dim,
                        "claim": ev,
                        "issue": "content not found in file",
                    })
            except Exception:
                hallucinations.append({
                    "dimension": dim,
                    "claim": ev,
                    "issue": "could not read file",
                })

    return hallucinations
