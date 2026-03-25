"""
Cross-fleet knowledge sync — scans the local knowledge/ directory tree,
builds a manifest, and compares against a stored baseline to detect drift.

Produces export-ready manifests and drift reports for federation sync.
Output:
  - knowledge/sync/manifest_YYYY-MM-DD.json
  - knowledge/sync/drift_YYYY-MM-DD.md
"""
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

SKILL_NAME = "cross_fleet_knowledge_sync"
DESCRIPTION = "Synchronize knowledge artifacts across fleet instances by building manifests and detecting drift."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
SYNC_DIR = KNOWLEDGE_DIR / "sync"
BASELINE_FILE = SYNC_DIR / "manifest_baseline.json"

log = logging.getLogger(__name__)

# File extensions to include in the manifest
INCLUDED_EXTENSIONS = {
    ".md", ".json", ".jsonl", ".txt", ".py", ".csv", ".yaml", ".yml", ".toml",
}


def _categorize_path(rel_path):
    """Assign a category based on the first directory component."""
    parts = rel_path.parts
    if len(parts) < 2:
        return "root"
    return parts[0]


def _build_manifest(knowledge_dir):
    """Walk the knowledge/ tree and build a manifest of all files."""
    entries = {}
    try:
        for fpath in sorted(knowledge_dir.rglob("*")):
            if not fpath.is_file():
                continue
            if fpath.suffix.lower() not in INCLUDED_EXTENSIONS:
                continue
            # Skip the sync directory itself to avoid self-referencing
            try:
                fpath.relative_to(SYNC_DIR)
                continue
            except ValueError:
                pass  # Not inside sync dir, include it

            rel = fpath.relative_to(knowledge_dir)
            rel_str = rel.as_posix()  # Normalize to forward slashes

            try:
                stat = fpath.stat()
                mtime = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
                size = stat.st_size
            except Exception:
                log.warning("Failed to stat %s", fpath, exc_info=True)
                mtime = "unknown"
                size = 0

            entries[rel_str] = {
                "size": size,
                "modified": mtime,
                "category": _categorize_path(rel),
                "extension": fpath.suffix.lower(),
            }
    except Exception:
        log.warning("Error scanning knowledge directory", exc_info=True)

    return entries


def _diff_manifests(baseline, current):
    """Compare two manifests and return added, removed, and modified files."""
    base_keys = set(baseline.keys())
    curr_keys = set(current.keys())

    added = sorted(curr_keys - base_keys)
    removed = sorted(base_keys - curr_keys)

    modified = []
    for key in sorted(base_keys & curr_keys):
        b_entry = baseline[key]
        c_entry = current[key]
        changes = []
        if b_entry.get("size") != c_entry.get("size"):
            changes.append(f"size {b_entry.get('size', '?')} -> {c_entry.get('size', '?')}")
        if b_entry.get("modified") != c_entry.get("modified"):
            changes.append("content updated")
        if changes:
            modified.append((key, ", ".join(changes)))

    return added, removed, modified


def _category_summary(manifest):
    """Summarize file counts and total size by category."""
    cats = {}
    for entry in manifest.values():
        cat = entry.get("category", "unknown")
        cats.setdefault(cat, {"count": 0, "size": 0})
        cats[cat]["count"] += 1
        cats[cat]["size"] += entry.get("size", 0)
    return cats


def _build_drift_report(added, removed, modified, current_manifest, is_first_run):
    """Render the drift report as Markdown."""
    today = date.today().isoformat()
    lines = [
        f"# Knowledge Sync Drift Report — {today}",
        "",
    ]

    cats = _category_summary(current_manifest)
    total_files = len(current_manifest)
    total_size = sum(e.get("size", 0) for e in current_manifest.values())
    total_size_kb = round(total_size / 1024, 1)

    lines.append(f"**Total Files:** {total_files} | **Total Size:** {total_size_kb} KB")
    lines.append("")

    if is_first_run:
        lines.append("**First run — baseline manifest saved.** No drift to report.")
        lines.append("")
        lines.append("## Category Summary")
        lines.append("")
        lines.append("| Category | Files | Size (KB) |")
        lines.append("|----------|-------|-----------|")
        for cat, stats in sorted(cats.items()):
            size_kb = round(stats["size"] / 1024, 1)
            lines.append(f"| {cat} | {stats['count']} | {size_kb} |")
        lines.append("")
        return "\n".join(lines)

    drift_total = len(added) + len(removed) + len(modified)
    lines.append(
        f"**Total Drift:** {drift_total} | "
        f"Added: {len(added)} | Removed: {len(removed)} | "
        f"Modified: {len(modified)}"
    )
    lines.append("")

    if not drift_total:
        lines.append("No knowledge drift detected. Local tree matches baseline.")
        lines.append("")
        return "\n".join(lines)

    if added:
        lines.append("## Added Files")
        lines.append("")
        for f in added:
            entry = current_manifest.get(f, {})
            size_kb = round(entry.get("size", 0) / 1024, 1)
            lines.append(f"- `{f}` ({size_kb} KB, {entry.get('category', '?')})")
        lines.append("")

    if removed:
        lines.append("## Removed Files")
        lines.append("")
        for f in removed:
            lines.append(f"- `{f}`")
        lines.append("")

    if modified:
        lines.append("## Modified Files")
        lines.append("")
        lines.append("| File | Changes |")
        lines.append("|------|---------|")
        for f, change_desc in modified:
            lines.append(f"| `{f}` | {change_desc} |")
        lines.append("")

    lines.append("## Category Summary")
    lines.append("")
    lines.append("| Category | Files | Size (KB) |")
    lines.append("|----------|-------|-----------|")
    for cat, stats in sorted(cats.items()):
        size_kb = round(stats["size"] / 1024, 1)
        lines.append(f"| {cat} | {stats['count']} | {size_kb} |")
    lines.append("")

    return "\n".join(lines)


def run(payload, config):
    """Build knowledge manifest, detect drift, and produce sync artifacts."""
    import db

    SYNC_DIR.mkdir(parents=True, exist_ok=True)

    if not KNOWLEDGE_DIR.exists():
        return {"status": "error", "message": "knowledge/ directory not found"}

    # Build current manifest
    try:
        current = _build_manifest(KNOWLEDGE_DIR)
    except Exception:
        log.warning("Failed to build knowledge manifest", exc_info=True)
        return {"status": "error", "message": "Failed to scan knowledge directory"}

    is_first_run = not BASELINE_FILE.exists()
    today = date.today().isoformat()

    if is_first_run:
        # Save baseline manifest
        try:
            BASELINE_FILE.write_text(
                json.dumps(current, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            log.warning("Failed to save baseline manifest", exc_info=True)
            return {"status": "error", "message": "Failed to save baseline manifest"}

        added, removed, modified = [], [], []
        drift_report = _build_drift_report([], [], [], current, is_first_run=True)
    else:
        # Load baseline and diff
        try:
            baseline = json.loads(
                BASELINE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            log.warning("Failed to read baseline manifest", exc_info=True)
            return {"status": "error", "message": "Failed to read baseline manifest"}

        added, removed, modified = _diff_manifests(baseline, current)
        drift_report = _build_drift_report(added, removed, modified, current, is_first_run=False)

    # Save dated manifest
    manifest_path = SYNC_DIR / f"manifest_{today}.json"
    try:
        manifest_path.write_text(
            json.dumps(current, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        log.warning("Failed to write dated manifest", exc_info=True)
        return {"status": "error", "message": "Failed to write manifest"}

    # Save drift report
    drift_path = SYNC_DIR / f"drift_{today}.md"
    try:
        drift_path.write_text(drift_report, encoding="utf-8")
    except Exception:
        log.warning("Failed to write drift report", exc_info=True)
        return {"status": "error", "message": "Failed to write drift report"}

    # Update baseline to current state after diffing
    if not is_first_run:
        try:
            BASELINE_FILE.write_text(
                json.dumps(current, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            log.warning("Failed to update baseline manifest", exc_info=True)

    # Optionally record the sync event in the DB
    try:
        def _log_sync():
            import json as _json
            with db.get_conn() as conn:
                conn.execute("""
                    INSERT INTO tasks (type, status, payload_json, result_json)
                    VALUES (?, 'DONE', ?, ?)
                """, (
                    "cross_fleet_knowledge_sync",
                    _json.dumps({"first_run": is_first_run, "date": today}),
                    _json.dumps({
                        "total_files": len(current),
                        "added": len(added),
                        "removed": len(removed),
                        "modified": len(modified),
                    }),
                ))
        db._retry_write(_log_sync)
    except Exception:
        log.warning("Failed to log sync event to DB", exc_info=True)
        # Non-fatal — manifest and report were already saved

    cats = _category_summary(current)

    return {
        "status": "ok",
        "first_run": is_first_run,
        "total_files": len(current),
        "added": len(added),
        "removed": len(removed),
        "modified": len(modified),
        "categories": {k: v["count"] for k, v in cats.items()},
        "manifest_path": str(manifest_path),
        "drift_report_path": str(drift_path),
    }
