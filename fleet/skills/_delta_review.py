"""Delta review helper — shared logic for repeat-scan skills.

Instead of creating a new report every run, compares current findings
with the previous scan and only writes a report if something changed.
Produces delta reports showing new findings, resolved findings, and
unchanged counts.

Usage in a skill:
    from _delta_review import DeltaReviewer
    dr = DeltaReviewer("security", reviews_dir)
    delta = dr.compare(current_findings)
    if delta["changed"]:
        dr.write_report(delta, title="Security Review")
    else:
        return {"status": "no_change", "unchanged": delta["unchanged_count"]}
"""
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("delta_review")


def _finding_key(f: dict) -> str:
    """Stable key for a finding — identifies the same issue across runs."""
    return f"{f.get('file', '')}:{f.get('line', 0)}:{f.get('category', '')}:{f.get('severity', '')}"


def _finding_hash(f: dict) -> str:
    """Content hash — detects if the detail changed for the same finding."""
    raw = f"{_finding_key(f)}|{f.get('detail', '')}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class DeltaReviewer:
    """Manages delta comparisons for repeat-scan review skills."""

    def __init__(self, skill_name: str, reviews_dir: Path):
        self.skill_name = skill_name
        self.reviews_dir = reviews_dir
        self.state_file = reviews_dir / f".{skill_name}_last_scan.json"
        self.reviews_dir.mkdir(parents=True, exist_ok=True)

    def _load_previous(self) -> dict:
        """Load the previous scan state (finding keys + hashes)."""
        if not self.state_file.exists():
            return {"findings": {}, "timestamp": None}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {"findings": {}, "timestamp": None}

    def _save_state(self, findings: list):
        """Save current scan state for next comparison."""
        state = {
            "findings": {_finding_key(f): _finding_hash(f) for f in findings},
            "timestamp": datetime.now().isoformat(),
            "count": len(findings),
        }
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def compare(self, current_findings: list) -> dict:
        """Compare current findings with previous scan.

        Returns:
            {
                "changed": bool,
                "new_findings": [...],       # findings not in previous scan
                "resolved_findings": [...],  # keys in previous but not current
                "unchanged_count": int,
                "current_total": int,
                "previous_total": int,
                "previous_timestamp": str | None,
            }
        """
        prev = self._load_previous()
        prev_keys = set(prev.get("findings", {}).keys())
        prev_hashes = prev.get("findings", {})

        current_map = {}
        for f in current_findings:
            key = _finding_key(f)
            current_map[key] = f

        current_keys = set(current_map.keys())

        new_keys = current_keys - prev_keys
        resolved_keys = prev_keys - current_keys
        unchanged_keys = current_keys & prev_keys

        # Check if any unchanged findings changed their detail
        modified_keys = set()
        for key in unchanged_keys:
            if _finding_hash(current_map[key]) != prev_hashes.get(key, ""):
                modified_keys.add(key)

        new_findings = [current_map[k] for k in sorted(new_keys)]
        modified_findings = [current_map[k] for k in sorted(modified_keys)]
        resolved_finding_keys = sorted(resolved_keys)

        changed = bool(new_keys or resolved_keys or modified_keys)

        # Save current state for next comparison
        self._save_state(current_findings)

        return {
            "changed": changed,
            "new_findings": new_findings,
            "modified_findings": modified_findings,
            "resolved_finding_keys": resolved_finding_keys,
            "unchanged_count": len(unchanged_keys) - len(modified_keys),
            "current_total": len(current_findings),
            "previous_total": len(prev_keys),
            "previous_timestamp": prev.get("timestamp"),
        }

    def write_report(self, delta: dict, title: str, header_extra: list | None = None) -> Path:
        """Write a delta report only if something changed.

        Returns the report path.
        """
        now = datetime.now()
        date_str = now.strftime("%Y%m%d_%H%M")
        report_path = self.reviews_dir / f"{self.skill_name}_review_{date_str}.md"

        lines = [f"# {title} -- {now.strftime('%Y-%m-%d %H:%M')}"]
        lines.append(f"**Mode:** Delta (compared with {delta['previous_timestamp'] or 'first scan'})")
        lines.append(f"**Current findings:** {delta['current_total']} | "
                      f"**Previous:** {delta['previous_total']}")

        if header_extra:
            for h in header_extra:
                lines.append(h)
        lines.append("")

        # New findings
        if delta["new_findings"]:
            lines.append(f"## New Findings ({len(delta['new_findings'])})")
            for f in delta["new_findings"]:
                line_ref = f"line {f['line']}" if f.get("line") else "file-level"
                lines.append(
                    f"- **[{f['severity']}]** `{f['file']}` ({line_ref}) "
                    f"-- {f['category']}: {f['detail']}"
                )
            lines.append("")

        # Modified findings
        if delta["modified_findings"]:
            lines.append(f"## Modified Findings ({len(delta['modified_findings'])})")
            for f in delta["modified_findings"]:
                line_ref = f"line {f['line']}" if f.get("line") else "file-level"
                lines.append(
                    f"- **[{f['severity']}]** `{f['file']}` ({line_ref}) "
                    f"-- {f['category']}: {f['detail']}"
                )
            lines.append("")

        # Resolved findings
        if delta["resolved_finding_keys"]:
            lines.append(f"## Resolved ({len(delta['resolved_finding_keys'])})")
            for key in delta["resolved_finding_keys"]:
                lines.append(f"- ~~{key}~~")
            lines.append("")

        # Summary
        lines.append(f"## Summary")
        lines.append(f"| Status | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| New | {len(delta['new_findings'])} |")
        lines.append(f"| Modified | {len(delta['modified_findings'])} |")
        lines.append(f"| Resolved | {len(delta['resolved_finding_keys'])} |")
        lines.append(f"| Unchanged | {delta['unchanged_count']} |")
        lines.append(f"| **Total** | **{delta['current_total']}** |")
        lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Delta report: %d new, %d resolved, %d unchanged → %s",
                 len(delta["new_findings"]), len(delta["resolved_finding_keys"]),
                 delta["unchanged_count"], report_path.name)
        return report_path

    def update_existing(self, delta: dict, title: str) -> Path | None:
        """Update the latest existing report instead of creating a new one.

        If the latest report exists and nothing changed, returns None.
        If changed, overwrites the latest report with delta info appended.
        """
        existing = sorted(self.reviews_dir.glob(f"{self.skill_name}_review_*.md"),
                          key=lambda f: f.stat().st_mtime, reverse=True)
        if existing and not delta["changed"]:
            return None  # nothing changed, keep existing

        # Write new delta report
        return self.write_report(delta, title)
