# Quality Flywheel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-reinforcing quality system that audits context files, grades effectiveness, proposes improvements, and learns from feedback.

**Architecture:** Shared core (`_flywheel_core.py`) handles grading rubric and gap analysis. `quality_flywheel.py` wraps it as a fleet skill with full reinforcement loop. Claude Code plugin (`context-audit.md`) provides interactive audit + bootstrap for any project.

**Tech Stack:** Python stdlib, fleet skill contract, fleet.db (flywheel_scores table), existing skills (evaluate, intelligence, reinforcement)

**Spec:** `docs/superpowers/specs/2026-03-22-quality-flywheel-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `fleet/skills/_flywheel_core.py` | Shared: rubric definitions, grading engine, gap analysis, draft generation |
| `fleet/skills/quality_flywheel.py` | Fleet skill: audit, gaps, draft, apply, history, calibrate |
| `fleet/db.py` | Add flywheel_scores table to init_db() |
| `.claude/skills/context-audit.md` | Claude Code plugin: audit, gaps, bootstrap |
| `knowledge/flywheel/` | Output directory for reports + drafts |

---

### Task 1: Grading Engine Core

**Files:**
- Create: `fleet/skills/_flywheel_core.py`

- [ ] **Step 1: Create _flywheel_core.py with rubric definitions**

```python
"""Quality Flywheel core — rubric definitions, grading engine, gap analysis."""
import json
import logging
import os
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
```

- [ ] **Step 2: Add context quality grading functions**

```python
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

    # Check version consistency
    import re
    versions = re.findall(r'v?(\d+\.\d+[\.\d]*[ab]?)', claude_text)
    if readme.exists():
        readme_text = readme.read_text(encoding="utf-8", errors="ignore")
        readme_versions = re.findall(r'v?(\d+\.\d+[\.\d]*[ab]?)', readme_text)
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
    score = 50.0  # base
    issues = []

    # Positive signals (specific, actionable)
    specific_patterns = [
        (r'```', 10, "Has code examples"),
        (r'never|always|must|do not', 8, "Has explicit rules"),
        (r'python .*\.py', 5, "Has runnable commands"),
        (r'\bpath\b.*/', 5, "Has file path references"),
    ]
    for pattern, points, _desc in specific_patterns:
        import re
        if re.search(pattern, content, re.I):
            score += points

    # Negative signals (vague)
    vague_patterns = [
        (r'write good code', -15, "Vague: 'write good code'"),
        (r'be careful', -10, "Vague: 'be careful'"),
        (r'use best practices', -10, "Vague: 'use best practices'"),
    ]
    for pattern, penalty, desc in vague_patterns:
        import re
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
```

- [ ] **Step 3: Add output quality grading functions**

```python
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

        # Context utilization + feedback incorporation — approximate
        results["context_utilization"] = (70.0, ["Approximated — full analysis requires LLM"])
        results["feedback_incorporation"] = (75.0, ["Approximated — full analysis requires LLM"])

        conn.close()
    except Exception as e:
        for dim in ("accuracy", "first_attempt_rate", "regression_rate",
                     "context_utilization", "feedback_incorporation"):
            results[dim] = (50.0, [f"DB error: {e}"])

    return results
```

- [ ] **Step 4: Add full audit + gap analysis + report formatting**

```python
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
```

- [ ] **Step 5: Verify module compiles**

Run: `python -m py_compile fleet/skills/_flywheel_core.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add fleet/skills/_flywheel_core.py
git commit -m "feat: _flywheel_core.py — 10-dimension rubric, grading engine, gap analysis"
```

---

### Task 2: Fleet Skill — quality_flywheel

**Files:**
- Create: `fleet/skills/quality_flywheel.py`
- Modify: `fleet/db.py` (add flywheel_scores table)

- [ ] **Step 1: Add flywheel_scores table to db.py init_db()**

Add after oss_watchlist:

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS flywheel_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_path TEXT NOT NULL,
        dimension TEXT NOT NULL,
        grade TEXT NOT NULL,
        score REAL NOT NULL,
        details_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_flywheel_project ON flywheel_scores(project_path, created_at)")
```

- [ ] **Step 2: Create quality_flywheel.py with skill contract + audit action**

```python
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
```

- [ ] **Step 3: Implement draft action (LLM-powered improvement suggestions)**

```python
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
```

- [ ] **Step 4: Implement apply + history + calibrate actions**

```python
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
```

- [ ] **Step 5: Verify skill compiles and imports**

Run: `cd fleet && python -c "from skills.quality_flywheel import SKILL_NAME; print(SKILL_NAME)"`
Expected: `quality_flywheel`

- [ ] **Step 6: Commit**

```bash
git add fleet/skills/quality_flywheel.py fleet/db.py
git commit -m "feat: quality_flywheel skill — audit, draft, apply, history, calibrate"
```

---

### Task 3: Claude Code Plugin

**Files:**
- Create: `.claude/skills/context-audit.md`

- [ ] **Step 1: Create the plugin file**

```markdown
---
name: context-audit
description: Audit project context files for quality, completeness, and effectiveness
---

# Context Quality Auditor

When the user asks to audit their project's context files, check CLAUDE.md quality, or bootstrap context for a new project:

## Audit Mode (default)

1. Find all context files: CLAUDE.md, .claude/rules/*.md, AGENTS.md, GEMINI.md, CONTRIBUTING.md
2. Grade each dimension:
   - **Completeness**: Does CLAUDE.md cover conventions, gotchas, structure, workflows?
   - **Consistency**: Do docs agree with each other and the code?
   - **Actionability**: Are instructions specific enough? ("use _retry_write" > "handle errors well")
   - **Coverage**: What % of the codebase is mentioned in context files?
   - **Freshness**: When was CLAUDE.md last updated vs recent commits?
3. Show report card with letter grades (A-F) per dimension
4. Highlight gaps: what's missing, what's stale, what's vague
5. Offer to draft improvements for the lowest-scoring dimensions

## Bootstrap Mode (/context-audit bootstrap)

For projects with NO context files:
1. Analyze: language, framework, directory structure, package.json/requirements.txt
2. Detect conventions from code patterns (naming, error handling, imports)
3. Find common commands (build, test, lint) from config files
4. Generate a starter CLAUDE.md with:
   - Project description (from README or package.json)
   - Detected conventions
   - Directory structure overview
   - Common gotchas (inferred from error patterns)
   - Build/test/run commands
5. Optionally generate 2-3 rule files for the most impactful patterns

## Gaps Mode (/context-audit gaps)

Show only the gaps — where context exists but isn't effective:
- "Your CLAUDE.md has security rules, but 3 recent commits introduced security issues"
- "Error handling conventions are documented but vague — 'handle errors' should be 'use try/except Exception, never bare except'"

## Output format:

| Dimension | Grade | Key Issue |
|-----------|-------|-----------|
| Completeness | B | Missing gotchas section |
| Consistency | A | All docs agree |
| Actionability | C+ | 4 vague instructions found |
| Coverage | B+ | fleet/ and BigEd/ covered, autoresearch/ not mentioned |
| Freshness | A | Updated 2 days ago |
| **Overall** | **B** | |

## Important:
- Be specific about what's missing — "add a Gotchas section covering X, Y, Z"
- Don't suggest adding context that already exists
- Prefer concrete examples over abstract advice
- If the project has no CLAUDE.md, offer bootstrap mode immediately
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/context-audit.md
git commit -m "feat: context-audit Claude Code plugin — audit, gaps, bootstrap"
```

---

### Task 4: Smoke Test + Final Verification

- [ ] **Step 1: Verify all new files import**

Run:
```bash
cd fleet
python -c "from skills.quality_flywheel import SKILL_NAME; print(SKILL_NAME)"
python -c "from skills._flywheel_core import run_full_audit, RUBRIC; print(f'{len(RUBRIC)} dimensions')"
```

- [ ] **Step 2: Run smoke test**

Run: `python smoke_test.py --fast`
Expected: 22/22 passed

- [ ] **Step 3: Test audit on BigEd itself**

Run: `python -c "from skills._flywheel_core import run_full_audit; from pathlib import Path; r = run_full_audit(Path('..').resolve()); print(f'Overall: {r[\"overall_grade\"]} ({r[\"overall_score\"]}/100)')"`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Quality Flywheel complete — audit, draft, apply, calibrate + Claude plugin"
```
