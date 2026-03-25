"""Audit documentation files for stale values (counts, versions, dates).

Scans key docs (CLAUDE.md, fleet/CLAUDE.md, README.md, FRAMEWORK_BLUEPRINT.md,
OPERATIONS.md, CONTRIBUTING.md, docs/WHAT_IS_BIGED.md) for hardcoded metrics
and compares against live codebase values.

Can run standalone: python fleet/skills/doc_freshness.py
"""
import logging
import re
from pathlib import Path

SKILL_NAME = "doc_freshness"
DESCRIPTION = "Audit docs for stale skill counts, versions, endpoint counts, and smoke test numbers."
REQUIRES_NETWORK = False

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
PROJECT_DIR = FLEET_DIR.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"


def _count_skills():
    """Count .py files in fleet/skills/ excluding __init__ and __pycache__."""
    skills_dir = FLEET_DIR / "skills"
    if not skills_dir.exists():
        return 0
    return sum(
        1 for f in skills_dir.glob("*.py")
        if f.name != "__init__.py" and not f.name.startswith("_")
    )


def _count_endpoints():
    """Count route decorators across dashboard + blueprints."""
    count = 0
    for py_file in FLEET_DIR.glob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
            count += len(re.findall(r'@\w+\.route\(', text))
        except Exception:
            pass
    return count


def _count_db_tables():
    """Count tables in fleet.db."""
    try:
        import db
        db_path = FLEET_DIR / "fleet.db"
        if not db_path.exists():
            return 0
        conn = db.get_conn()
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        return tables
    except Exception:
        return 0


def _count_smoke_tests():
    """Parse smoke_test.py for test count."""
    smoke_file = FLEET_DIR / "smoke_test.py"
    if not smoke_file.exists():
        return 0
    text = smoke_file.read_text(encoding="utf-8", errors="replace")
    # Count test functions
    return len(re.findall(r'def test_\w+', text))


def _get_version():
    """Extract version from CLAUDE.md header."""
    claude_md = PROJECT_DIR / "CLAUDE.md"
    if not claude_md.exists():
        return "unknown"
    first_line = claude_md.read_text(encoding="utf-8").split("\n")[0]
    m = re.search(r'\d+\.\d+\.\d+b?', first_line)
    return m.group() if m else "unknown"


def _scan_doc(filepath, live_values):
    """Scan a document for stale numeric references."""
    if not filepath.exists():
        return []

    findings = []
    text = filepath.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    rel_path = str(filepath.relative_to(PROJECT_DIR))

    for i, line in enumerate(lines, 1):
        # Check skill counts
        for m in re.finditer(r'(\d+)[\-\s]*skill', line, re.IGNORECASE):
            stated = int(m.group(1))
            actual = live_values["skills"]
            if stated < actual - 5:  # allow small lag
                findings.append({
                    "file": rel_path,
                    "line": i,
                    "type": "skill_count",
                    "stated": stated,
                    "actual": actual,
                    "text": line.strip()[:100],
                })

        # Check endpoint counts
        for m in re.finditer(r'(\d+)\+?\s*endpoint', line, re.IGNORECASE):
            stated = int(m.group(1))
            actual = live_values["endpoints"]
            if stated < actual - 20:
                findings.append({
                    "file": rel_path,
                    "line": i,
                    "type": "endpoint_count",
                    "stated": stated,
                    "actual": actual,
                    "text": line.strip()[:100],
                })

        # Check smoke test counts
        for m in re.finditer(r'(\d+)/\d+.*(?:smoke|test)', line, re.IGNORECASE):
            stated = int(m.group(1))
            actual = live_values["smoke_tests"]
            if stated < actual - 2:
                findings.append({
                    "file": rel_path,
                    "line": i,
                    "type": "smoke_count",
                    "stated": stated,
                    "actual": actual,
                    "text": line.strip()[:100],
                })

        # Check DB table counts
        for m in re.finditer(r'(\d+)\s*(?:DB\s*)?table', line, re.IGNORECASE):
            stated = int(m.group(1))
            actual = live_values["db_tables"]
            if stated < actual - 2 and stated > 5:  # filter noise
                findings.append({
                    "file": rel_path,
                    "line": i,
                    "type": "table_count",
                    "stated": stated,
                    "actual": actual,
                    "text": line.strip()[:100],
                })

    return findings


def run(payload, config):
    from datetime import date

    OUT_DIR = KNOWLEDGE_DIR / "freshness"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Gather live values
    live_values = {
        "skills": _count_skills(),
        "endpoints": _count_endpoints(),
        "db_tables": _count_db_tables(),
        "smoke_tests": _count_smoke_tests(),
        "version": _get_version(),
    }

    # Docs to scan
    docs_to_scan = [
        PROJECT_DIR / "CLAUDE.md",
        PROJECT_DIR / "README.md",
        PROJECT_DIR / "FRAMEWORK_BLUEPRINT.md",
        PROJECT_DIR / "OPERATIONS.md",
        PROJECT_DIR / "CONTRIBUTING.md",
        PROJECT_DIR / "SETUP.md",
        PROJECT_DIR / "docs" / "WHAT_IS_BIGED.md",
        FLEET_DIR / "CLAUDE.md",
    ]

    all_findings = []
    for doc in docs_to_scan:
        try:
            findings = _scan_doc(doc, live_values)
            all_findings.extend(findings)
        except Exception:
            log.warning("Failed to scan %s", doc, exc_info=True)

    # Build report
    md = [
        f"# Doc Freshness Audit — {date.today().isoformat()}",
        "",
        "## Live Values",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Skills | {live_values['skills']} |",
        f"| Endpoints | {live_values['endpoints']} |",
        f"| DB Tables | {live_values['db_tables']} |",
        f"| Smoke Tests | {live_values['smoke_tests']} |",
        f"| Version | {live_values['version']} |",
        "",
    ]

    if all_findings:
        md.append(f"## Stale References ({len(all_findings)} found)")
        md.append("")
        md.append("| File | Line | Type | Stated | Actual | Context |")
        md.append("|------|------|------|--------|--------|---------|")
        for f in sorted(all_findings, key=lambda x: (x["file"], x["line"])):
            md.append(
                f"| {f['file']} | {f['line']} | {f['type']} | "
                f"{f['stated']} | {f['actual']} | {f['text'][:60]} |"
            )
        md.append("")
    else:
        md.append("## Result: All docs are fresh!")
        md.append("")

    report_text = "\n".join(md)
    out_file = OUT_DIR / f"freshness_{date.today().isoformat()}.md"
    out_file.write_text(report_text, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "live_values": live_values,
        "stale_count": len(all_findings),
        "findings": all_findings,
    }


# --- Standalone execution ---
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = run({}, {})
    print(f"\nDoc Freshness Audit")
    print(f"  Skills: {result['live_values']['skills']}")
    print(f"  Endpoints: {result['live_values']['endpoints']}")
    print(f"  DB Tables: {result['live_values']['db_tables']}")
    print(f"  Smoke Tests: {result['live_values']['smoke_tests']}")
    print(f"  Stale references: {result['stale_count']}")
    if result['findings']:
        print("\nStale references found:")
        for f in result['findings']:
            print(f"  {f['file']}:{f['line']} — {f['type']}: says {f['stated']}, actual {f['actual']}")
    print(f"\nReport: {result['saved_to']}")
