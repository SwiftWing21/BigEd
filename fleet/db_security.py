"""Security advisory management — split from db.py (TD-04)."""
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def get_pending_advisories():
    """Return list of pending security advisories with metadata.

    Reads advisory_*.md files from fleet/knowledge/security/pending/ and
    enriches with JSON sidecar data when available.
    """
    pending_dir = Path(__file__).parent / "knowledge" / "security" / "pending"
    if not pending_dir.exists():
        return []
    result = []
    for md_path in sorted(pending_dir.glob("advisory_*.md")):
        stem = md_path.stem
        advisory_id = stem.replace("advisory_", "", 1)

        title = ""
        try:
            for line in md_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    title = stripped
                    break
        except Exception:
            title = advisory_id

        json_path = md_path.with_suffix(".json")
        severity = "UNKNOWN"
        if json_path.exists():
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
                severity = meta.get("severity", "UNKNOWN").upper()
            except Exception:
                pass

        try:
            mtime = datetime.fromtimestamp(md_path.stat().st_mtime)
            created = mtime.strftime("%Y-%m-%d")
        except Exception:
            created = ""

        result.append({
            "id": advisory_id,
            "path": str(md_path),
            "json_path": str(json_path) if json_path.exists() else None,
            "severity": severity,
            "title": title,
            "created": created,
        })
    return result


def dismiss_advisory(advisory_id):
    """Archive an advisory by moving its files to archived/ subfolder.

    Moves both the .md and .json (if present) from pending/ to pending/archived/.
    Creates the archived/ directory if it doesn't exist.

    Returns:
        dict with 'moved' count and list of 'files' moved, or 'error' string.
    """
    pending_dir = Path(__file__).parent / "knowledge" / "security" / "pending"
    archive_dir = pending_dir / "archived"

    if not pending_dir.exists():
        return {"error": "pending directory not found", "moved": 0, "files": []}

    candidates = list(pending_dir.glob(f"advisory_{advisory_id}.*"))
    if not candidates:
        return {"error": f"no advisory found with id '{advisory_id}'", "moved": 0, "files": []}

    archive_dir.mkdir(parents=True, exist_ok=True)
    moved_files = []
    for src in candidates:
        if src.is_file():
            dst = archive_dir / src.name
            try:
                src.rename(dst)
                moved_files.append(str(dst))
            except Exception as e:
                return {"error": str(e), "moved": len(moved_files), "files": moved_files}

    return {"moved": len(moved_files), "files": moved_files}
