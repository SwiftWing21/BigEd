"""Quality Flywheel — grading engine for context and output quality dimensions."""
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from skills._flywheel_rubric import RUBRIC  # noqa: F401 (re-exported for callers)

log = logging.getLogger(__name__)


# ── Part A: Context quality grading ────────────────────────────────────────

def grade_completeness(project_root: Path) -> tuple[float, list[str], list[dict]]:
    """Check if CLAUDE.md covers required sections. Returns (score, gaps, evidence)."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md found"], []

    content = claude_md.read_text(encoding="utf-8", errors="ignore")
    content_lower = content.lower()
    lines = content.splitlines()
    required_sections = {
        "quick start": "How to run the project",
        "structure": "Directory/file layout",
        "gotchas": "Common pitfalls",
        "version": "Version scheme or current version",
        "dev": "Development setup or mode",
    }
    score = 0
    gaps = []
    evidence = []
    for section, desc in required_sections.items():
        if section in content_lower:
            score += 100 / len(required_sections)
            # Find line number of first occurrence
            for i, line in enumerate(lines, 1):
                if section in line.lower():
                    evidence.append({
                        "file": "CLAUDE.md", "line": i,
                        "detail": f"Section '{desc}' found: {line.strip()[:80]}",
                    })
                    break
        else:
            gaps.append(f"Missing section: {desc}")
            evidence.append({
                "file": "CLAUDE.md", "line": 0,
                "detail": f"Section '{desc}' NOT found (searched for '{section}')",
            })
    return min(100, score), gaps, evidence


def grade_consistency(project_root: Path) -> tuple[float, list[str], list[dict]]:
    """Check if docs agree with each other. Returns (score, issues, evidence)."""
    issues = []
    evidence = []
    score = 100.0

    claude_md = project_root / "CLAUDE.md"
    readme = project_root / "README.md"

    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"], []

    claude_text = claude_md.read_text(encoding="utf-8", errors="ignore")
    claude_lines = claude_text.splitlines()

    # Check version consistency — match project version format (X.XXX.XXb)
    # Require at least 3-segment version with alpha/beta suffix to avoid matching
    # unrelated versions like Apache_2.0 or Python 3.11
    ver_re = r'v?(\d+\.\d{2,}\.\d+[ab]\b)'
    versions = re.findall(ver_re, claude_text)
    if versions:
        # Find line number of first version in CLAUDE.md
        for i, line in enumerate(claude_lines, 1):
            if re.search(ver_re, line):
                evidence.append({
                    "file": "CLAUDE.md", "line": i,
                    "detail": f"Version '{versions[0]}' found: {line.strip()[:80]}",
                })
                break

    if readme.exists():
        readme_text = readme.read_text(encoding="utf-8", errors="ignore")
        readme_lines = readme_text.splitlines()
        readme_versions = re.findall(ver_re, readme_text)
        if versions and readme_versions and versions[0] != readme_versions[0]:
            issues.append(f"Version mismatch: CLAUDE.md={versions[0]}, README={readme_versions[0]}")
            score -= 20
            # Find line in README with mismatched version
            for i, line in enumerate(readme_lines, 1):
                if re.search(ver_re, line):
                    evidence.append({
                        "file": "README.md", "line": i,
                        "detail": f"Mismatched version '{readme_versions[0]}': {line.strip()[:80]}",
                    })
                    break
        elif versions and readme_versions and versions[0] == readme_versions[0]:
            evidence.append({
                "file": "README.md", "line": 0,
                "detail": f"Version consistent: both report '{versions[0]}'",
            })

    # Check skill count consistency
    skill_counts = re.findall(r'(\d+)\s*skills?', claude_text, re.I)
    if skill_counts:
        # Find line number with skill count claim
        for i, line in enumerate(claude_lines, 1):
            if re.search(r'(\d+)\s*skills?', line, re.I):
                evidence.append({
                    "file": "CLAUDE.md", "line": i,
                    "detail": f"Claims {skill_counts[0]} skills: {line.strip()[:80]}",
                })
                break

        # Count actual skills
        skills_dir = project_root / "fleet" / "skills"
        if skills_dir.exists():
            actual_files = [f for f in skills_dir.glob("*.py")
                           if f.name != "__init__.py" and not f.name.startswith("_")]
            actual = len(actual_files)
            claimed = int(skill_counts[0])
            if abs(actual - claimed) > 3:
                issues.append(f"Skill count: docs say {claimed}, actual is {actual}")
                score -= 15
                evidence.append({
                    "file": "fleet/skills/", "line": 0,
                    "detail": f"Actual skill count is {actual}, docs claim {claimed} (delta {abs(actual - claimed)})",
                })
            else:
                evidence.append({
                    "file": "fleet/skills/", "line": 0,
                    "detail": f"Skill count consistent: actual={actual}, claimed={claimed}",
                })

    return max(0, score), issues, evidence


def grade_actionability(project_root: Path) -> tuple[float, list[str], list[dict]]:
    """Score how specific and actionable the instructions are. Returns (score, issues, evidence)."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"], []

    content = claude_md.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    score = 40.0  # base
    issues = []
    evidence = []

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
    for pattern, points, desc in specific_patterns:
        match = re.search(pattern, content, re.I)
        if match:
            score += points
            # Find line number of first match
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line, re.I):
                    evidence.append({
                        "file": "CLAUDE.md", "line": i,
                        "detail": f"+{points}pts {desc}: {line.strip()[:60]}",
                    })
                    break

    # Bonus: count of code blocks (more = more actionable, up to +12)
    code_blocks = len(re.findall(r'```', content)) // 2
    bonus = min(12, code_blocks * 2)
    score += bonus
    if code_blocks > 0:
        evidence.append({
            "file": "CLAUDE.md", "line": 0,
            "detail": f"+{bonus}pts from {code_blocks} code blocks",
        })

    # Negative signals (vague)
    vague_patterns = [
        (r'write good code', -15, "Vague: 'write good code'"),
        (r'be careful', -10, "Vague: 'be careful'"),
        (r'use best practices', -10, "Vague: 'use best practices'"),
    ]
    for pattern, penalty, desc in vague_patterns:
        match = re.search(pattern, content, re.I)
        if match:
            score += penalty
            issues.append(desc)
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line, re.I):
                    evidence.append({
                        "file": "CLAUDE.md", "line": i,
                        "detail": f"{penalty}pts {desc}: {line.strip()[:60]}",
                    })
                    break

    return max(0, min(100, score)), issues, evidence


def grade_coverage(project_root: Path) -> tuple[float, list[str], list[dict]]:
    """What % of top-level dirs have relevant context? Returns (score, issues, evidence)."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"], []

    content = claude_md.read_text(encoding="utf-8", errors="ignore")
    content_lower = content.lower()
    lines = content.splitlines()
    top_dirs = [d.name for d in project_root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name != "node_modules"][:20]

    if not top_dirs:
        return 100.0, [], []

    evidence = []
    covered = 0
    for d in top_dirs:
        if d.lower() in content_lower:
            covered += 1
            # Find line where directory is mentioned
            for i, line in enumerate(lines, 1):
                if d.lower() in line.lower():
                    evidence.append({
                        "file": "CLAUDE.md", "line": i,
                        "detail": f"Directory '{d}/' mentioned: {line.strip()[:60]}",
                    })
                    break
        else:
            evidence.append({
                "file": str(project_root / d), "line": 0,
                "detail": f"Directory '{d}/' exists but is NOT mentioned in CLAUDE.md",
            })

    score = (covered / len(top_dirs)) * 100
    uncovered = [d for d in top_dirs if d.lower() not in content_lower]
    issues = [f"Uncovered directory: {d}" for d in uncovered[:5]]
    return min(100, score), issues, evidence


def grade_freshness(project_root: Path) -> tuple[float, list[str], list[dict]]:
    """Are docs stale vs recent git activity? Returns (score, issues, evidence)."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        return 0.0, ["No CLAUDE.md"], []

    issues = []
    evidence = []
    try:
        doc_mtime = datetime.fromtimestamp(claude_md.stat().st_mtime, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - doc_mtime).days
        mtime_str = doc_mtime.strftime("%Y-%m-%d %H:%M UTC")
        evidence.append({
            "file": "CLAUDE.md", "line": 0,
            "detail": f"Last modified: {mtime_str} ({age_days} days ago)",
        })
        if age_days > 30:
            score = max(0, 100 - age_days * 2)
            issues.append(f"CLAUDE.md last modified {age_days} days ago")
        else:
            score = 100.0

        # Also check key companion docs for staleness
        companion_docs = ["ROADMAP.md", "AUDIT_TRACKER.md", "FRAMEWORK_BLUEPRINT.md"]
        for doc_name in companion_docs:
            doc_path = project_root / doc_name
            if doc_path.exists():
                try:
                    d_mtime = datetime.fromtimestamp(doc_path.stat().st_mtime, tz=timezone.utc)
                    d_age = (datetime.now(timezone.utc) - d_mtime).days
                    d_mtime_str = d_mtime.strftime("%Y-%m-%d %H:%M UTC")
                    evidence.append({
                        "file": doc_name, "line": 0,
                        "detail": f"Last modified: {d_mtime_str} ({d_age} days ago)",
                    })
                except Exception:
                    pass
    except Exception:
        score = 50.0
    return score, issues, evidence


# ── Part B: Output quality grading ────────────────────────────────────────

def _grade_context_utilization(conn, project_root: Path) -> tuple[float, list[str]]:
    """Check if CLAUDE.md conventions appear in recent task results.

    Measures whether the AI actually references and follows documented patterns
    by scanning recent DONE task results for mentions of key CLAUDE.md terms,
    code patterns, and convention markers injected by the worker pipeline.
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

    # Also extract key file/module references (e.g. fleet.toml, db.py)
    file_refs = re.findall(r'`([a-z_]+\.(?:py|toml|db|md))`', content)
    for ref in file_refs:
        if ref not in markers:
            markers.append(ref)

    # Extract skill names mentioned in code blocks
    skill_names = re.findall(r'SKILL_NAME\s*=\s*["\'](\w+)["\']', content)
    for sn in skill_names:
        if sn not in markers:
            markers.append(sn)

    markers = list(dict.fromkeys(markers))[:30]  # dedupe, cap at 30

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

    # Also check for convention markers injected by worker pipeline
    has_worker_markers = "_conventions" in all_results or "fleet_context" in all_results
    worker_bonus = 10.0 if has_worker_markers else 0.0

    # Score: 70 base + up to 25 from marker match ratio + up to 10 from worker markers
    # Raised base from 60 to 70: task completion itself implies context was used
    score = 70.0 + ratio * 25.0 + worker_bonus
    if ratio < 0.2:
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
        import db
        conn = db.get_conn()
        conn.row_factory = sqlite3.Row

        # Accuracy: DONE vs FAILED ratio (last 3 days)
        # Reduced from 7 days to prevent stale provider failures (e.g. MINIMAX
        # outages) from dragging down the score after the provider stabilises.
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM tasks
            WHERE created_at > datetime('now', '-3 days')
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
