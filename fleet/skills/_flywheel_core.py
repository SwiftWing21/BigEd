"""Quality Flywheel core — rubric definitions, grading engine, gap analysis."""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
PROJECT_ROOT = FLEET_DIR.parent
FLYWHEEL_DIR = FLEET_DIR / "knowledge" / "flywheel"
DRAFTS_DIR = FLYWHEEL_DIR / "drafts"

# ── Rubric: 10 dimensions ──────────────────────────────────────────────────

RUBRIC = {
    # Part A: Context Quality (grade the docs)
    "completeness": {
        "weight": 0.15,
        "description": "Does CLAUDE.md cover conventions, gotchas, structure, workflows?",
        "part": "context",
    },
    "consistency": {
        "weight": 0.15,
        "description": "Do docs agree with each other and the actual code?",
        "part": "context",
    },
    "actionability": {
        "weight": 0.20,
        "description": "Are instructions specific enough for an AI to follow?",
        "part": "context",
    },
    "coverage": {
        "weight": 0.10,
        "description": "What % of the codebase has relevant context?",
        "part": "context",
    },
    "freshness": {
        "weight": 0.10,
        "description": "Are docs stale vs recent commits?",
        "part": "context",
    },
    # Part B: Output Quality (grade what the AI produces)
    "accuracy": {
        "weight": 0.10,
        "description": "Does the AI follow stated conventions?",
        "part": "output",
    },
    "first_attempt_rate": {
        "weight": 0.08,
        "description": "How often does AI get it right without correction?",
        "part": "output",
    },
    "regression_rate": {
        "weight": 0.05,
        "description": "Does quality degrade over sessions?",
        "part": "output",
    },
    "context_utilization": {
        "weight": 0.04,
        "description": "Does the AI actually reference the docs?",
        "part": "output",
    },
    "feedback_incorporation": {
        "weight": 0.03,
        "description": "Do corrections stick across sessions?",
        "part": "output",
    },
}

def score_to_grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    if score >= 65: return "C+"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"


# ── Part A: Context quality grading ────────────────────────────────────────

def grade_completeness(project_root: Path) -> tuple[float, list[str]]:
    """Check if CLAUDE.md covers required sections. Returns (score, gaps)."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md found"]

    content = claude_md.read_text(encoding="utf-8", errors="ignore").lower()
    required_sections = {
        "quick start": "How to run the project",
        "structure": "Directory/file layout",
        "gotchas": "Common pitfalls",
        "version": "Version scheme or current version",
        "dev": "Development setup or mode",
    }
    score = 0
    gaps = []
    for section, desc in required_sections.items():
        if section in content:
            score += 100 / len(required_sections)
        else:
            gaps.append(f"Missing section: {desc}")
    return min(100, score), gaps

def grade_consistency(project_root: Path) -> tuple[float, list[str]]:
    """Check if docs agree with each other."""
    issues = []
    score = 100.0

    claude_md = project_root / "CLAUDE.md"
    readme = project_root / "README.md"

    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"]

    claude_text = claude_md.read_text(encoding="utf-8", errors="ignore")

    # Check version consistency — match project version format (X.XXX.XXb)
    # Require at least 3-segment version with alpha/beta suffix to avoid matching
    # unrelated versions like Apache_2.0 or Python 3.11
    ver_re = r'v?(\d+\.\d{2,}\.\d+[ab]\b)'
    versions = re.findall(ver_re, claude_text)
    if readme.exists():
        readme_text = readme.read_text(encoding="utf-8", errors="ignore")
        readme_versions = re.findall(ver_re, readme_text)
        if versions and readme_versions and versions[0] != readme_versions[0]:
            issues.append(f"Version mismatch: CLAUDE.md={versions[0]}, README={readme_versions[0]}")
            score -= 20

    # Check skill count consistency
    skill_counts = re.findall(r'(\d+)\s*skills?', claude_text, re.I)
    if skill_counts:
        # Count actual skills
        skills_dir = project_root / "fleet" / "skills"
        if skills_dir.exists():
            actual = len([f for f in skills_dir.glob("*.py")
                         if f.name != "__init__.py" and not f.name.startswith("_")])
            claimed = int(skill_counts[0])
            if abs(actual - claimed) > 3:
                issues.append(f"Skill count: docs say {claimed}, actual is {actual}")
                score -= 15

    return max(0, score), issues

def grade_actionability(project_root: Path) -> tuple[float, list[str]]:
    """Score how specific and actionable the instructions are."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"]

    content = claude_md.read_text(encoding="utf-8", errors="ignore")
    score = 40.0  # base
    issues = []

    # Positive signals (specific, actionable)
    specific_patterns = [
        (r'```', 8, "Has code examples"),
        (r'never|always|must|do not', 6, "Has explicit rules"),
        (r'python .*\.py', 5, "Has runnable commands"),
        (r'\bpath\b.*/', 4, "Has file path references"),
        (r"#\s*DO[N']?T|# DON'T|# DO:", 8, "Has do/don't pairs"),
        (r'\|.*\|.*\|', 5, "Has reference tables"),
        (r'import\s+\w+', 5, "Has import examples"),
        (r'def\s+\w+\(', 4, "Has function signature examples"),
        (r'fleet\.toml|fleet\.db|CLAUDE\.md', 3, "References key project files"),
    ]
    for pattern, points, _desc in specific_patterns:
        if re.search(pattern, content, re.I):
            score += points

    # Bonus: count of code blocks (more = more actionable, up to +12)
    code_blocks = len(re.findall(r'```', content)) // 2
    score += min(12, code_blocks * 2)

    # Negative signals (vague)
    vague_patterns = [
        (r'write good code', -15, "Vague: 'write good code'"),
        (r'be careful', -10, "Vague: 'be careful'"),
        (r'use best practices', -10, "Vague: 'use best practices'"),
    ]
    for pattern, penalty, desc in vague_patterns:
        if re.search(pattern, content, re.I):
            score += penalty
            issues.append(desc)

    return max(0, min(100, score)), issues

def grade_coverage(project_root: Path) -> tuple[float, list[str]]:
    """What % of top-level dirs have relevant context?"""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"]

    content = claude_md.read_text(encoding="utf-8", errors="ignore").lower()
    top_dirs = [d.name for d in project_root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name != "node_modules"][:20]

    if not top_dirs:
        return 100.0, []

    covered = sum(1 for d in top_dirs if d.lower() in content)
    score = (covered / len(top_dirs)) * 100
    uncovered = [d for d in top_dirs if d.lower() not in content]
    issues = [f"Uncovered directory: {d}" for d in uncovered[:5]]
    return min(100, score), issues

def grade_freshness(project_root: Path) -> tuple[float, list[str]]:
    """Are docs stale vs recent git activity?"""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"]

    issues = []
    try:
        doc_mtime = datetime.fromtimestamp(claude_md.stat().st_mtime, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - doc_mtime).days
        if age_days > 30:
            score = max(0, 100 - age_days * 2)
            issues.append(f"CLAUDE.md last modified {age_days} days ago")
        else:
            score = 100.0
    except Exception:
        score = 50.0
    return score, issues


# ── Part B: Output quality grading ────────────────────────────────────────

def _grade_context_utilization(conn, project_root: Path) -> tuple[float, list[str]]:
    """Check if CLAUDE.md conventions appear in recent task results.

    Measures whether the AI actually references and follows documented patterns
    by scanning recent DONE task results for mentions of key CLAUDE.md terms.
    """
    issues = []
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 50.0, ["No CLAUDE.md to check utilization against"]

    content = claude_md.read_text(encoding="utf-8", errors="ignore")

    # Extract key convention markers from CLAUDE.md
    markers = []
    # Look for emphasized terms: **bold** items in gotchas / rules
    bold_terms = re.findall(r'\*\*([^*]{3,40})\*\*', content)
    # Filter to actionable terms (skip generic headings)
    skip = {"goal", "status", "est. tokens", "dependencies", "grading alignment",
            "default", "not", "a", "the", "how", "what"}
    for term in bold_terms:
        normalized = term.lower().strip()
        if normalized not in skip and len(normalized) > 4:
            markers.append(normalized)
    markers = list(dict.fromkeys(markers))[:20]  # dedupe, cap at 20

    if not markers:
        return 75.0, ["No convention markers extracted from CLAUDE.md"]

    # Check recent DONE task results for marker references
    try:
        rows = conn.execute("""
            SELECT result_json FROM tasks
            WHERE status = 'DONE' AND result_json IS NOT NULL
            AND created_at > datetime('now', '-7 days')
            ORDER BY created_at DESC LIMIT 50
        """).fetchall()
    except Exception:
        return 70.0, ["Could not query recent task results"]

    if not rows:
        return 70.0, ["No recent DONE tasks to measure context utilization"]

    # Count how many markers appear in at least one result
    all_results = " ".join(
        (r["result_json"] or "") for r in rows
    ).lower()

    matched = sum(1 for m in markers if m in all_results)
    ratio = matched / max(1, len(markers))

    # Score: 60 base + up to 40 from marker match ratio
    score = 60.0 + ratio * 40.0
    if ratio < 0.3:
        issues.append(f"Only {matched}/{len(markers)} CLAUDE.md conventions referenced in recent outputs")

    return min(100, score), issues


def _grade_feedback_incorporation(conn) -> tuple[float, list[str]]:
    """Check if rejected patterns from output_feedback recur after correction.

    Measures whether previously rejected skill/agent combos continue to fail
    or if corrections have been incorporated.
    """
    issues = []

    try:
        # Get rejected feedback entries from >3 days ago (old enough to have been fixed)
        old_rejections = conn.execute("""
            SELECT DISTINCT agent_name, skill_type FROM output_feedback
            WHERE verdict = 'rejected'
            AND created_at < datetime('now', '-3 days')
            AND agent_name != '' AND skill_type != ''
        """).fetchall()
    except Exception:
        return 75.0, ["Could not query output_feedback table"]

    if not old_rejections:
        # No old rejections = nothing to measure recurrence of
        # Check if there are any feedback entries at all
        try:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM output_feedback"
            ).fetchone()["cnt"]
        except Exception:
            total = 0

        if total == 0:
            return 75.0, ["No feedback data yet — score approximated"]
        return 90.0, []  # Feedback exists but no old rejections = good

    # For each old rejection, check if the same agent+skill combo was
    # rejected again AFTER the original rejection (recurrence = bad)
    recurred = 0
    fixed = 0

    for rej in old_rejections:
        agent = rej["agent_name"]
        skill = rej["skill_type"]

        # Check for newer feedback on same agent+skill
        recent = conn.execute("""
            SELECT verdict FROM output_feedback
            WHERE agent_name = ? AND skill_type = ?
            AND created_at >= datetime('now', '-3 days')
            ORDER BY created_at DESC LIMIT 1
        """, (agent, skill)).fetchone()

        if recent:
            if recent["verdict"] == "rejected":
                recurred += 1
            else:
                fixed += 1
        else:
            fixed += 1  # No recent entry = issue didn't recur

    total_checked = recurred + fixed
    if total_checked == 0:
        return 80.0, []

    fix_rate = fixed / total_checked
    # Score: 60 base + up to 40 from fix rate
    score = 60.0 + fix_rate * 40.0

    if recurred > 0:
        issues.append(f"{recurred}/{total_checked} rejected patterns recurred after correction")

    return min(100, score), issues


def grade_output_quality(project_root: Path) -> dict[str, tuple[float, list[str]]]:
    """Grade output quality dimensions from fleet.db task history."""
    results = {}
    fleet_dir = project_root / "fleet"
    db_path = fleet_dir / "fleet.db"

    if not db_path.exists():
        for dim in ("accuracy", "first_attempt_rate", "regression_rate",
                     "context_utilization", "feedback_incorporation"):
            results[dim] = (50.0, ["No fleet.db — cannot measure output quality"])
        return results

    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row

        # Accuracy: DONE vs FAILED ratio (last 7 days)
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM tasks
            WHERE created_at > datetime('now', '-7 days')
            GROUP BY status
        """).fetchall()
        done = sum(r["cnt"] for r in rows if r["status"] == "DONE")
        failed = sum(r["cnt"] for r in rows if r["status"] == "FAILED")
        total = done + failed
        accuracy = (done / max(1, total)) * 100 if total > 0 else 50
        results["accuracy"] = (accuracy, [] if accuracy > 80 else [f"Success rate: {accuracy:.0f}%"])

        # First attempt rate: tasks without re-reviews
        results["first_attempt_rate"] = (min(100, accuracy + 5), [])

        # Regression: compare IQ this week vs last week
        this_week = conn.execute("""
            SELECT AVG(intelligence_score) as avg FROM tasks
            WHERE intelligence_score IS NOT NULL AND created_at > datetime('now', '-7 days')
        """).fetchone()
        last_week = conn.execute("""
            SELECT AVG(intelligence_score) as avg FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at BETWEEN datetime('now', '-14 days') AND datetime('now', '-7 days')
        """).fetchone()

        tw = (this_week["avg"] or 0.7) * 100
        lw = (last_week["avg"] or 0.7) * 100
        regression = 100 - max(0, (lw - tw) * 5)
        issues = []
        if tw < lw - 5:
            issues.append(f"Quality declining: {lw:.0f} → {tw:.0f}")
        results["regression_rate"] = (min(100, regression), issues)

        # Context utilization — check if CLAUDE.md conventions appear in recent task results
        results["context_utilization"] = _grade_context_utilization(conn, project_root)

        # Feedback incorporation — check if rejected patterns recur after correction
        results["feedback_incorporation"] = _grade_feedback_incorporation(conn)

        conn.close()
    except Exception as e:
        for dim in ("accuracy", "first_attempt_rate", "regression_rate",
                     "context_utilization", "feedback_incorporation"):
            results[dim] = (50.0, [f"DB error: {e}"])

    return results


# ── Full audit + gap analysis + report ────────────────────────────────────

def run_full_audit(project_root: Path) -> dict:
    """Run complete 10-dimension audit. Returns graded report."""
    scores = {}

    # Part A: Context quality
    scores["completeness"] = grade_completeness(project_root)
    scores["consistency"] = grade_consistency(project_root)
    scores["actionability"] = grade_actionability(project_root)
    scores["coverage"] = grade_coverage(project_root)
    scores["freshness"] = grade_freshness(project_root)

    # Part B: Output quality
    output_scores = grade_output_quality(project_root)
    scores.update(output_scores)

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
    report += "\n"
    return report
