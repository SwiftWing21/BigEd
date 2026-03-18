"""
Security apply skill — executes approved fixes from a pending advisory.
Only safe, scoped remediations are automated (chmod, gitignore additions).
Credential rotation and code changes require manual follow-up.
"""
import json
import os
import stat
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
PENDING_DIR = FLEET_DIR / "knowledge" / "security" / "pending"
APPLIED_DIR = FLEET_DIR / "knowledge" / "security" / "applied"


def _apply_chmod(path_str, fix_str):
    """Apply chmod fix extracted from advisory fix string."""
    import re
    m = re.search(r'chmod (\d+) (.+)', fix_str)
    if not m:
        return False, "Could not parse chmod command"
    mode_str, target = m.group(1), m.group(2).strip()
    target_path = Path(target).expanduser()
    if not target_path.exists():
        return False, f"Path not found: {target_path}"
    try:
        os.chmod(target_path, int(mode_str, 8))
        return True, f"chmod {mode_str} applied to {target_path}"
    except Exception as e:
        return False, str(e)


def _apply_gitignore(fix_str, gitignore_path):
    """Append missing entry to .gitignore."""
    import re
    m = re.search(r"Add '(.+?)' to", fix_str)
    if not m:
        return False, "Could not parse gitignore fix"
    entry = m.group(1)
    gpath = Path(gitignore_path)
    if not gpath.exists():
        return False, f".gitignore not found: {gpath}"
    content = gpath.read_text()
    if entry.replace("*.", "").replace("*", "") in content:
        return True, f"'{entry}' already in .gitignore (skipped)"
    gpath.write_text(content.rstrip() + f"\n{entry}\n")
    return True, f"Added '{entry}' to {gpath}"


def run(payload, config):
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    advisory_id = payload.get("advisory_id", "")
    if not advisory_id:
        return {"error": "advisory_id required"}

    advisory_file = PENDING_DIR / f"advisory_{advisory_id}.json"
    if not advisory_file.exists():
        return {"error": f"Advisory {advisory_id} not found in pending/"}

    advisory = json.loads(advisory_file.read_text())

    applied = []
    skipped = []
    manual_required = []

    for finding in advisory.get("findings", []):
        fix = finding.get("fix", "")
        ftype = finding.get("type", "")
        severity = finding.get("severity", "LOW")

        if ftype == "file_permissions" and "chmod" in fix:
            ok, msg = _apply_chmod(finding.get("path", ""), fix)
            (applied if ok else skipped).append({"finding": finding["detail"], "result": msg})

        elif ftype == "gitignore_gap":
            gitignore_path = finding.get("path", "")
            ok, msg = _apply_gitignore(fix, gitignore_path)
            (applied if ok else skipped).append({"finding": finding["detail"], "result": msg})

        elif ftype == "exposed_secret":
            # Never auto-remove secrets — flag for manual rotation
            manual_required.append({
                "severity": severity,
                "finding": finding["detail"],
                "path": finding.get("path", ""),
                "action": fix,
                "note": "Credential rotation must be done manually — rotate key then remove from file",
            })

        else:
            manual_required.append({
                "finding": finding["detail"],
                "action": fix,
                "note": "Manual remediation required",
            })

    # Move advisory to applied/
    APPLIED_DIR.mkdir(parents=True, exist_ok=True)
    advisory["status"] = "APPLIED"
    advisory["applied_at"] = datetime.now().isoformat()
    advisory["apply_results"] = {
        "applied": applied,
        "skipped": skipped,
        "manual_required": manual_required,
    }

    dest = APPLIED_DIR / advisory_file.name
    dest.write_text(json.dumps(advisory, indent=2))
    advisory_file.unlink()

    # Remove markdown pending file too
    md_pending = PENDING_DIR / f"advisory_{advisory_id}.md"
    if md_pending.exists():
        md_pending.rename(APPLIED_DIR / md_pending.name)

    # Write apply report
    report_lines = [
        f"# Security Apply Report — {advisory_id}",
        f"**Applied at:** {advisory['applied_at']}",
        "",
        f"## Automated Fixes Applied ({len(applied)})",
    ]
    for item in applied:
        report_lines.append(f"- {item['finding']}: {item['result']}")

    if skipped:
        report_lines += [f"\n## Skipped ({len(skipped)})"]
        for item in skipped:
            report_lines.append(f"- {item['finding']}: {item['result']}")

    if manual_required:
        report_lines += [f"\n## Manual Action Required ({len(manual_required)})"]
        for item in manual_required:
            report_lines.append(f"- **{item.get('severity', 'INFO')}** {item['finding']}")
            report_lines.append(f"  - Action: {item['action']}")
            if item.get("note"):
                report_lines.append(f"  - Note: {item['note']}")

    report_file = APPLIED_DIR / f"apply_report_{advisory_id}.md"
    report_file.write_text("\n".join(report_lines))

    # Notify lead
    db.post_message(
        from_agent="security",
        to_agent="lead",
        body_json=json.dumps({
            "type": "security_applied",
            "advisory_id": advisory_id,
            "applied_count": len(applied),
            "manual_required_count": len(manual_required),
            "report_file": str(report_file),
        })
    )

    return {
        "advisory_id": advisory_id,
        "applied": len(applied),
        "skipped": len(skipped),
        "manual_required": len(manual_required),
        "report_file": str(report_file),
    }
