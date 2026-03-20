"""
Regression Detector — Tracks quality grades over time, detects hallucinations and regressions.

Monitors intelligence_score trends per skill and agent. When quality drops
below historical baseline, flags for investigation. Checks for hallucination
patterns in LLM outputs (fabricated references, inconsistent facts, confidence
without evidence).

Actions:
  audit       — scan recent tasks for quality regressions and hallucination markers
  grade       — show quality grade report (A-F scale per skill/agent)
  track       — add tracking notes for a specific task/skill
  hallcheck   — deep hallucination check on a specific task result

Grading Scale:
  A: avg IQ >= 0.85 (excellent, consistent quality)
  B: avg IQ >= 0.70 (good, minor issues)
  C: avg IQ >= 0.55 (acceptable, needs improvement)
  D: avg IQ >= 0.40 (poor, frequent issues)
  F: avg IQ <  0.40 (failing, requires intervention)

Usage:
    lead_client.py task '{"type": "regression_detector"}'
    lead_client.py task '{"type": "regression_detector", "payload": {"action": "grade"}}'
    lead_client.py task '{"type": "regression_detector", "payload": {"action": "hallcheck", "task_id": 123}}'
"""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "regression_detector"
DESCRIPTION = "Track quality grades, detect regressions and hallucinations in fleet outputs."
REQUIRES_NETWORK = False

GRADE_THRESHOLDS = {
    "A": 0.85, "B": 0.70, "C": 0.55, "D": 0.40, "F": 0.0,
}

# Hallucination detection patterns
HALLUCINATION_MARKERS = [
    # Fabricated references
    (r"(?:doi|DOI)[:\s]+10\.\d{4,}/[^\s]+", "fabricated_doi",
     "Contains DOI-format reference — verify it exists"),
    (r"(?:arXiv|arxiv)[:\s]+\d{4}\.\d{4,}", "fabricated_arxiv",
     "Contains arXiv ID — verify paper exists"),
    (r"(?:ISBN|isbn)[:\s]+[\d-]{10,}", "fabricated_isbn",
     "Contains ISBN — verify book exists"),

    # Confident assertions without evidence
    (r"studies (?:show|have shown|demonstrate|prove) that", "unsourced_claim",
     "Claims studies show something — no citation provided"),
    (r"according to (?:research|experts|scientists)", "vague_authority",
     "Vague authority appeal — no specific source"),
    (r"it is (?:well known|widely accepted|established) that", "assumed_consensus",
     "Asserts consensus without evidence"),

    # Inconsistency markers
    (r"(?:as (?:I|we) (?:mentioned|said|noted) (?:earlier|above|before))", "self_reference",
     "Self-references prior content — may be fabricating context"),
    (r"(?:in (?:the|my) previous (?:response|message|answer))", "prior_reference",
     "References prior response — check for consistency"),

    # Numerical precision flags
    (r"\b\d{1,3}\.\d{4,}%\b", "false_precision",
     "Suspiciously precise percentage — likely hallucinated"),
    (r"(?:exactly|precisely|specifically) \d+", "exact_claim",
     "Claims exact number — verify source"),
]


def run(payload: dict, config: dict, log) -> dict:
    action = payload.get("action", "audit")

    if action == "audit":
        return _audit(config, log)
    elif action == "grade":
        return _grade_report(config, log)
    elif action == "track":
        return _track_note(payload, config, log)
    elif action == "hallcheck":
        return _hallucination_check(payload, config, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _get_conn():
    return sqlite3.connect(str(FLEET_DIR / "fleet.db"), timeout=10)


def _letter_grade(score: float) -> str:
    for letter, threshold in GRADE_THRESHOLDS.items():
        if score >= threshold:
            return letter
    return "F"


def _audit(config, log) -> dict:
    """Scan for quality regressions and hallucination patterns."""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    findings = []

    # ── 1. Quality regression detection ───────────────────────────────────
    # Compare last 7 days vs previous 7 days
    try:
        current = conn.execute("""
            SELECT type as skill, AVG(intelligence_score) as avg_iq, COUNT(*) as tasks
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-7 days')
            GROUP BY type HAVING tasks >= 3
        """).fetchall()

        previous = conn.execute("""
            SELECT type as skill, AVG(intelligence_score) as avg_iq, COUNT(*) as tasks
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-14 days')
            AND created_at < datetime('now', '-7 days')
            GROUP BY type HAVING tasks >= 3
        """).fetchall()

        prev_map = {r["skill"]: r["avg_iq"] for r in previous}

        for r in current:
            skill = r["skill"]
            current_iq = r["avg_iq"]
            prev_iq = prev_map.get(skill)

            if prev_iq and current_iq < prev_iq * 0.85:
                # 15%+ quality drop = regression
                drop_pct = round((1 - current_iq / prev_iq) * 100, 1)
                findings.append({
                    "type": "regression",
                    "severity": "high" if drop_pct > 25 else "warning",
                    "skill": skill,
                    "message": f"{skill} quality dropped {drop_pct}% "
                               f"(IQ: {prev_iq:.3f} → {current_iq:.3f})",
                    "current_grade": _letter_grade(current_iq),
                    "previous_grade": _letter_grade(prev_iq),
                })

    except Exception as e:
        findings.append({"type": "error", "message": f"Regression query failed: {e}"})

    # ── 2. Agent-level regression ─────────────────────────────────────────
    try:
        agent_scores = conn.execute("""
            SELECT assigned_to as agent, AVG(intelligence_score) as avg_iq,
                   COUNT(*) as tasks,
                   MIN(intelligence_score) as min_iq,
                   MAX(intelligence_score) as max_iq
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-7 days')
            AND assigned_to IS NOT NULL
            GROUP BY assigned_to HAVING tasks >= 3
        """).fetchall()

        for r in agent_scores:
            variance = (r["max_iq"] or 0) - (r["min_iq"] or 0)
            if variance > 0.5:
                findings.append({
                    "type": "inconsistency",
                    "severity": "warning",
                    "agent": r["agent"],
                    "message": f"{r['agent']} has high score variance: "
                               f"{r['min_iq']:.3f}–{r['max_iq']:.3f} (range: {variance:.3f})",
                    "grade": _letter_grade(r["avg_iq"]),
                })

    except Exception:
        pass

    # ── 3. Hallucination spot-check on recent outputs ─────────────────────
    try:
        recent = conn.execute("""
            SELECT id, type, result_json, intelligence_score
            FROM tasks
            WHERE status = 'DONE'
            AND result_json IS NOT NULL
            AND created_at >= datetime('now', '-1 day')
            ORDER BY created_at DESC LIMIT 20
        """).fetchall()

        hall_count = 0
        for task in recent:
            result = task["result_json"] or ""
            markers = _scan_for_hallucinations(result)
            if markers:
                hall_count += 1
                if len(findings) < 20:  # Cap findings
                    findings.append({
                        "type": "hallucination_risk",
                        "severity": "info",
                        "task_id": task["id"],
                        "skill": task["type"],
                        "markers": markers[:3],  # Top 3 markers
                        "message": f"Task #{task['id']} ({task['type']}): "
                                   f"{len(markers)} hallucination marker(s)",
                    })

        if hall_count > 0:
            findings.insert(0, {
                "type": "hallucination_summary",
                "severity": "warning" if hall_count > 5 else "info",
                "message": f"{hall_count}/{len(recent)} recent tasks have hallucination markers",
            })

    except Exception:
        pass

    conn.close()
    log.info(f"Regression audit: {len(findings)} findings")
    return {"findings": findings, "checked_at": datetime.utcnow().isoformat()}


def _grade_report(config, log) -> dict:
    """Generate quality grade report (A-F scale per skill and agent)."""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row

    # Per-skill grades
    skill_grades = []
    try:
        rows = conn.execute("""
            SELECT type as skill,
                   AVG(intelligence_score) as avg_iq,
                   COUNT(*) as scored_tasks,
                   SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done,
                   SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-30 days')
            GROUP BY type
            ORDER BY avg_iq DESC
        """).fetchall()

        for r in rows:
            skill_grades.append({
                "skill": r["skill"],
                "grade": _letter_grade(r["avg_iq"]),
                "avg_iq": round(r["avg_iq"], 3),
                "scored_tasks": r["scored_tasks"],
                "success_rate": round(r["done"] / max(r["done"] + r["failed"], 1) * 100, 1),
            })
    except Exception:
        pass

    # Per-agent grades
    agent_grades = []
    try:
        rows = conn.execute("""
            SELECT assigned_to as agent,
                   AVG(intelligence_score) as avg_iq,
                   COUNT(*) as scored_tasks
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-30 days')
            AND assigned_to IS NOT NULL
            GROUP BY assigned_to
            ORDER BY avg_iq DESC
        """).fetchall()

        for r in rows:
            agent_grades.append({
                "agent": r["agent"],
                "grade": _letter_grade(r["avg_iq"]),
                "avg_iq": round(r["avg_iq"], 3),
                "scored_tasks": r["scored_tasks"],
            })
    except Exception:
        pass

    # Overall fleet grade
    try:
        overall = conn.execute("""
            SELECT AVG(intelligence_score) as fleet_iq,
                   COUNT(*) as total_scored
            FROM tasks
            WHERE intelligence_score IS NOT NULL
            AND created_at >= datetime('now', '-30 days')
        """).fetchone()
        fleet_grade = _letter_grade(overall["fleet_iq"]) if overall["fleet_iq"] else "N/A"
        fleet_iq = round(overall["fleet_iq"], 3) if overall["fleet_iq"] else 0
    except Exception:
        fleet_grade = "N/A"
        fleet_iq = 0

    conn.close()

    # Save report
    report_dir = FLEET_DIR / "knowledge" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"grade_report_{ts}.md"

    md = f"# Fleet Quality Grade Report — {ts}\n\n"
    md += f"**Overall Fleet Grade: {fleet_grade}** (IQ: {fleet_iq})\n\n"
    md += "## Skill Grades\n\n| Skill | Grade | IQ | Tasks | Success |\n"
    md += "|-------|-------|----|-------|---------|\n"
    for s in skill_grades:
        md += f"| {s['skill']} | {s['grade']} | {s['avg_iq']} | {s['scored_tasks']} | {s['success_rate']}% |\n"
    md += "\n## Agent Grades\n\n| Agent | Grade | IQ | Tasks |\n"
    md += "|-------|-------|----|-------|\n"
    for a in agent_grades:
        md += f"| {a['agent']} | {a['grade']} | {a['avg_iq']} | {a['scored_tasks']} |\n"

    report_path.write_text(md, encoding="utf-8")
    log.info(f"Grade report: fleet={fleet_grade}, saved to {report_path.name}")

    return {
        "fleet_grade": fleet_grade,
        "fleet_iq": fleet_iq,
        "skill_grades": skill_grades,
        "agent_grades": agent_grades,
        "report_file": str(report_path),
    }


def _track_note(payload, config, log) -> dict:
    """Add a tracking note for a specific task or skill regression."""
    task_id = payload.get("task_id")
    skill = payload.get("skill", "")
    note = payload.get("note", "")

    if not note:
        return {"error": "note is required"}

    report_dir = FLEET_DIR / "knowledge" / "reports" / "regression_notes"
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    note_file = report_dir / f"note_{ts}.json"
    note_file.write_text(json.dumps({
        "task_id": task_id,
        "skill": skill,
        "note": note,
        "timestamp": datetime.utcnow().isoformat(),
    }, indent=2), encoding="utf-8")

    log.info(f"Regression note saved: {note_file.name}")
    return {"saved": str(note_file), "note": note}


def _hallucination_check(payload, config, log) -> dict:
    """Deep hallucination check on a specific task result."""
    task_id = payload.get("task_id")
    if not task_id:
        return {"error": "task_id required"}

    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    task = conn.execute(
        "SELECT id, type, result_json, intelligence_score, assigned_to FROM tasks WHERE id=?",
        (task_id,)
    ).fetchone()
    conn.close()

    if not task:
        return {"error": f"Task #{task_id} not found"}

    result = task["result_json"] or ""
    markers = _scan_for_hallucinations(result)

    # Additional deep checks
    deep_findings = []

    # Check for internal contradictions (same fact stated differently)
    sentences = [s.strip() for s in result.split('.') if len(s.strip()) > 20]
    if len(sentences) > 5:
        # Simple check: look for negations of earlier statements
        for i, s1 in enumerate(sentences[:10]):
            for s2 in sentences[i+1:i+5]:
                words1 = set(s1.lower().split())
                words2 = set(s2.lower().split())
                overlap = words1 & words2
                if len(overlap) > 3 and ("not" in words2 - words1 or "no" in words2 - words1):
                    deep_findings.append({
                        "type": "potential_contradiction",
                        "sentence_1": s1[:80],
                        "sentence_2": s2[:80],
                    })

    risk_level = "low"
    if len(markers) > 3 or len(deep_findings) > 0:
        risk_level = "high"
    elif len(markers) > 0:
        risk_level = "medium"

    log.info(f"Hallucination check task #{task_id}: {risk_level} risk, "
             f"{len(markers)} markers, {len(deep_findings)} deep findings")

    return {
        "task_id": task_id,
        "skill": task["type"],
        "agent": task["assigned_to"],
        "intelligence_score": task["intelligence_score"],
        "risk_level": risk_level,
        "markers": markers,
        "deep_findings": deep_findings,
        "result_preview": result[:200],
    }


def _scan_for_hallucinations(text: str) -> list:
    """Scan text for hallucination markers using regex patterns."""
    markers = []
    for pattern, marker_type, description in HALLUCINATION_MARKERS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            markers.append({
                "type": marker_type,
                "description": description,
                "count": len(matches),
                "examples": matches[:2],
            })
    return markers
