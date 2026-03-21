"""0.10.00: Knowledge pruning — detect bloat and archive stale knowledge files."""
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

SKILL_NAME = "knowledge_prune"
DESCRIPTION = "Detect and archive stale or bloated knowledge files"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
ARCHIVE_DIR = KNOWLEDGE_DIR / "_archive"

def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "scan")
    stale_days = payload.get("stale_days", 30)

    if action == "scan":
        return _scan_for_bloat(stale_days)
    elif action == "archive":
        return _archive_stale(stale_days, payload.get("dry_run", True))
    elif action == "stats":
        return _knowledge_stats()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})

def _scan_for_bloat(stale_days):
    """Scan knowledge dirs for oversized or stale files."""
    findings = []
    cutoff = datetime.now().timestamp() - (stale_days * 86400)

    for subdir in KNOWLEDGE_DIR.iterdir():
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue
        files = list(subdir.rglob("*"))
        md_files = [f for f in files if f.suffix in (".md", ".json", ".jsonl", ".txt")]

        stale = [f for f in md_files if f.stat().st_mtime < cutoff]
        large = [f for f in md_files if f.stat().st_size > 100_000]  # >100KB

        if stale or large:
            findings.append({
                "directory": subdir.name,
                "total_files": len(md_files),
                "stale_files": len(stale),
                "large_files": len(large),
                "total_size_kb": round(sum(f.stat().st_size for f in md_files) / 1024, 1),
            })

    return json.dumps({"status": "ok", "findings": findings, "stale_threshold_days": stale_days})

def _archive_stale(stale_days, dry_run=True):
    """Move stale files to _archive directory, then clean stale RAG entries."""
    cutoff = datetime.now().timestamp() - (stale_days * 86400)
    archived = []

    for subdir in KNOWLEDGE_DIR.iterdir():
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue
        for f in subdir.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                if dry_run:
                    archived.append({"file": str(f.relative_to(KNOWLEDGE_DIR)), "action": "would_archive"})
                else:
                    dest = ARCHIVE_DIR / f.relative_to(KNOWLEDGE_DIR)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(dest))
                    archived.append({"file": str(f.relative_to(KNOWLEDGE_DIR)), "action": "archived"})

    # After archiving (which moves files off disk), clean stale RAG index entries
    rag_cleaned = 0
    if not dry_run and archived:
        try:
            sys.path.insert(0, str(FLEET_DIR))
            from rag import RAGIndex
            idx = RAGIndex()
            rag_result = idx.cleanup_stale()
            rag_cleaned = rag_result["stale_removed"]
        except Exception:
            pass  # RAG cleanup is best-effort

    return json.dumps({
        "status": "ok", "dry_run": dry_run,
        "archived": len(archived), "rag_stale_cleaned": rag_cleaned,
        "files": archived[:20],
    })

def _knowledge_stats():
    """Overall knowledge directory statistics."""
    stats = {}
    for subdir in KNOWLEDGE_DIR.iterdir():
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue
        files = list(subdir.rglob("*"))
        stats[subdir.name] = {
            "files": len([f for f in files if f.is_file()]),
            "size_kb": round(sum(f.stat().st_size for f in files if f.is_file()) / 1024, 1),
        }
    return json.dumps({"status": "ok", "directories": stats})
