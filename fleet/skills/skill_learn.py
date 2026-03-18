"""
Skill learn — analyzes task failure patterns, discussion gaps, and fleet
capabilities to identify missing skills and auto-queue skill_draft tasks.

Scans:
  1. Failed tasks — recurring error patterns suggest missing/broken skills
  2. Discussion logs — topics where agents say "we lack" or "no skill for"
  3. Existing skills vs idle curricula — unused skill types in curricula
  4. RAG knowledge gaps — queries with zero results

Payload:
  lookback_days   int   how far back to scan (default 14)
  auto_queue      bool  automatically queue skill_draft tasks (default false)
  max_proposals   int   max skill proposals to generate (default 5)

Output: knowledge/reports/skill_gaps_<date>.md
Returns: {proposals: [{name, description, reason, priority}], queued_tasks: int}
"""
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
REPORTS_DIR = KNOWLEDGE_DIR / "reports"
SKILLS_DIR = FLEET_DIR / "skills"

import sys
sys.path.insert(0, str(FLEET_DIR))


def _get_failed_tasks(lookback_days: int) -> list[dict]:
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT type, error, payload_json FROM tasks
            WHERE status='FAILED'
            AND created_at >= date('now', ?)
            ORDER BY created_at DESC
        """, (f"-{lookback_days} days",)).fetchall()
    return [dict(r) for r in rows]


def _get_discussion_gaps() -> list[str]:
    """Find topics where agents identified missing capabilities."""
    import db
    gaps = []
    gap_phrases = ["no skill", "missing skill", "we lack", "doesn't exist",
                   "not implemented", "could use a", "need a skill", "no way to"]
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT body_json FROM messages
            WHERE body_json IS NOT NULL
            ORDER BY created_at DESC LIMIT 500
        """).fetchall()
    for row in rows:
        try:
            body = json.loads(row["body_json"])
            text = body.get("contribution", "") + body.get("topic", "")
            if any(phrase in text.lower() for phrase in gap_phrases):
                gaps.append(text[:200])
        except Exception:
            pass
    return gaps


def _get_existing_skills() -> set[str]:
    return {f.stem for f in SKILLS_DIR.glob("*.py") if not f.stem.startswith("_")}


def _analyze_failures(failures: list[dict]) -> list[dict]:
    """Group failures by type and identify patterns."""
    by_type = Counter()
    error_samples = {}
    for f in failures:
        skill = f["type"]
        by_type[skill] += 1
        if skill not in error_samples:
            error_samples[skill] = f.get("error", "unknown")[:200]

    proposals = []
    for skill, count in by_type.most_common(10):
        if count >= 3:
            proposals.append({
                "name": f"{skill}_fix",
                "description": f"Fix or replace frequently failing skill '{skill}' — {count} failures. Sample error: {error_samples[skill]}",
                "reason": f"Recurring failures ({count}x)",
                "priority": min(count, 10),
            })
    return proposals


def _analyze_module_not_found(failures: list[dict]) -> list[dict]:
    """Find tasks that failed because the skill module doesn't exist."""
    missing = set()
    for f in failures:
        err = f.get("error", "")
        if "No module named" in err or "ModuleNotFoundError" in err:
            match = re.search(r"skills\.(\w+)", err)
            if match:
                missing.add(match.group(1))
        # Also catch import errors from skill dispatch
        if "Skill" in err and "error" in err.lower():
            missing.add(f["type"])

    existing = _get_existing_skills()
    proposals = []
    for skill in missing - existing:
        proposals.append({
            "name": skill,
            "description": f"Skill '{skill}' was requested but doesn't exist — create it based on task payloads",
            "reason": "Module not found in task execution",
            "priority": 8,
        })
    return proposals


def run(payload, config):
    import db

    lookback = payload.get("lookback_days", 14)
    auto_queue = payload.get("auto_queue", False)
    max_proposals = payload.get("max_proposals", 5)

    all_proposals = []

    # 1. Analyze failed tasks
    failures = _get_failed_tasks(lookback)
    all_proposals.extend(_analyze_failures(failures))
    all_proposals.extend(_analyze_module_not_found(failures))

    # 2. Scan discussion gaps
    gaps = _get_discussion_gaps()
    for gap_text in gaps[:3]:
        all_proposals.append({
            "name": "from_discussion",
            "description": gap_text,
            "reason": "Agent discussion identified a gap",
            "priority": 5,
        })

    # 3. Deduplicate and rank
    seen = set()
    unique = []
    for p in sorted(all_proposals, key=lambda x: x["priority"], reverse=True):
        if p["name"] not in seen:
            seen.add(p["name"])
            unique.append(p)
    proposals = unique[:max_proposals]

    # 4. Auto-queue skill_draft tasks if requested
    queued = 0
    if auto_queue and proposals:
        for p in proposals:
            if p["name"] == "from_discussion":
                continue
            db.post_task(
                "skill_draft",
                json.dumps({
                    "skill_name": p["name"],
                    "description": p["description"],
                    "perspective": "software architect",
                    "agent_name": "coder_1",
                }),
                priority=p["priority"],
                assigned_to="coder_1",
            )
            queued += 1

    # 5. Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    report = REPORTS_DIR / f"skill_gaps_{date_str}.md"
    lines = [
        f"# Skill Gap Analysis — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Lookback:** {lookback} days",
        f"**Failed tasks analyzed:** {len(failures)}",
        f"**Discussion gaps found:** {len(gaps)}",
        f"**Proposals:** {len(proposals)}",
        f"**Auto-queued:** {queued}",
        "",
    ]
    for i, p in enumerate(proposals, 1):
        lines.append(f"## {i}. {p['name']} (priority {p['priority']})")
        lines.append(f"**Reason:** {p['reason']}")
        lines.append(f"**Description:** {p['description']}")
        lines.append("")
    report.write_text("\n".join(lines))

    return {
        "proposals": proposals,
        "queued_tasks": queued,
        "failures_analyzed": len(failures),
        "gaps_found": len(gaps),
        "saved_to": str(report),
    }
