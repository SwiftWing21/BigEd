"""Audit Snapshot — export sanitized audit snapshots to timestamped JSON files.

Reads the latest scores from fleet.db (same query pattern as audit_scorer.py),
sanitizes the output, writes timestamped JSON to audit-snapshots/, and maintains
a manifest.json for downstream consumers.

Public API:
    export_snapshot(db_path, output_dir, sanitize)  ->  path to written file
    list_snapshots(output_dir)                       ->  list of manifest entries
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("audit_snapshot")

FLEET_DIR = Path(__file__).parent
DEFAULT_OUTPUT_DIR = FLEET_DIR / "audit-snapshots"


# ── Snapshot Export ──────────────────────────────────────────────────────

def export_snapshot(
    db_path: str = "fleet.db",
    output_dir: str | Path | None = None,
    sanitize: bool = True,
) -> Path:
    """Export a sanitized audit snapshot to a timestamped JSON file.

    Reads the latest scores from fleet.db using the same query pattern
    as audit_scorer.get_latest_scores(), sanitizes the output, writes it
    to audit-snapshots/<timestamp>.json, and updates manifest.json.

    Args:
        db_path:    Path to fleet.db (unused directly — we go through db module).
        output_dir: Directory for snapshot files.  Defaults to fleet/audit-snapshots/.
        sanitize:   Whether to run the sanitizer (default True).

    Returns:
        Path to the written snapshot file.
    """
    from audit_scorer import get_latest_scores, score_to_grade

    out = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    # Fetch latest scores
    scores = get_latest_scores()

    # Compute overall score (average of auto_score across dimensions)
    if scores:
        overall_score = sum(s.get("auto_score", 0.0) for s in scores) / len(scores)
        overall_grade = score_to_grade(overall_score)
    else:
        overall_score = 0.0
        overall_grade = "F"

    # Build snapshot payload
    now = datetime.now(timezone.utc)
    ts_str = now.strftime("%Y%m%dT%H%M%SZ")

    snapshot = {
        "timestamp": now.isoformat(),
        "overall_score": round(overall_score, 4),
        "overall_grade": overall_grade,
        "dimensions_count": len(scores),
        "scores": scores,
    }

    # Sanitize if requested
    if sanitize:
        from audit_sanitizer import sanitize as do_sanitize, sanitize_scores
        snapshot["scores"] = sanitize_scores(scores)
        snapshot = do_sanitize(snapshot)

    # Write snapshot file
    filename = f"snapshot_{ts_str}.json"
    snapshot_path = out / filename
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    log.info("Wrote audit snapshot to %s", snapshot_path)

    # Update manifest
    manifest_entry = {
        "file": filename,
        "format": "json",
        "timestamp": now.isoformat(),
        "overall_grade": snapshot.get("overall_grade", overall_grade),
        "overall_score": snapshot.get("overall_score", round(overall_score, 4)),
        "dimensions_count": snapshot.get("dimensions_count", len(scores)),
        "sanitized": sanitize,
    }
    _update_manifest(out, manifest_entry)

    return snapshot_path


# ── Manifest Management ──────────────────────────────────────────────────

def _update_manifest(output_dir: Path, entry: dict) -> None:
    """Append an entry to manifest.json in the output directory.

    Creates the manifest if it doesn't exist.  Maintains entries sorted
    by timestamp (newest first).
    """
    manifest_path = output_dir / "manifest.json"
    entries: list[dict] = []

    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict) and "snapshots" in data:
                entries = data["snapshots"]
        except Exception:
            log.warning("Failed to read existing manifest.json", exc_info=True)

    entries.insert(0, entry)

    manifest_path.write_text(
        json.dumps({"snapshots": entries}, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    log.info("Updated manifest.json (%d entries)", len(entries))


def list_snapshots(output_dir: str | Path | None = None) -> list[dict]:
    """Return all manifest entries (newest first).

    Args:
        output_dir: Directory containing manifest.json.  Defaults to
                    fleet/audit-snapshots/.

    Returns:
        List of manifest entry dicts, or empty list if no manifest exists.
    """
    out = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    manifest_path = out / "manifest.json"

    if not manifest_path.exists():
        return []

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "snapshots" in data:
            return data["snapshots"]
        return []
    except Exception:
        log.warning("Failed to read manifest.json", exc_info=True)
        return []
