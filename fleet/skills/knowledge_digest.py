"""
Knowledge digest skill — scans knowledge/ subdirectories, counts new files
in the last N hours, summarizes activity per category, and saves a daily digest.
"""
SKILL_NAME = "knowledge_digest"
DESCRIPTION = "Generate a daily digest of knowledge artifacts across all categories."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ops"


def run(payload, config):
    import db
    import logging
    from datetime import date, datetime, timedelta, timezone
    from pathlib import Path

    log = logging.getLogger(SKILL_NAME)

    FLEET_DIR = Path(__file__).parent.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    DIGEST_DIR = KNOWLEDGE_DIR / "digests"
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    hours = payload.get("hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Scan every subdirectory under knowledge/
    categories = {}
    total_new = 0
    total_all = 0

    try:
        for entry in sorted(KNOWLEDGE_DIR.iterdir()):
            if not entry.is_dir():
                continue
            cat_name = entry.name
            all_files = []
            new_files = []
            try:
                for f in entry.rglob("*"):
                    if not f.is_file():
                        continue
                    all_files.append(f)
                    try:
                        mtime = datetime.fromtimestamp(
                            f.stat().st_mtime, tz=timezone.utc
                        )
                        if mtime >= cutoff:
                            new_files.append(f)
                    except Exception:
                        pass
            except Exception:
                log.warning("Error scanning %s", cat_name, exc_info=True)

            categories[cat_name] = {
                "total": len(all_files),
                "new": len(new_files),
                "recent_files": [f.name for f in new_files[:10]],
            }
            total_new += len(new_files)
            total_all += len(all_files)
    except Exception:
        log.warning("Failed to scan knowledge directory", exc_info=True)
        return {"status": "error", "message": "Cannot scan knowledge directory"}

    # Gather task context from DB
    task_summary = ""
    try:
        with db.get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as n,
                       SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done
                FROM tasks
                WHERE created_at >= datetime('now', ? || ' hours')
            """, (f"-{hours}",)).fetchone()
            task_count = row["n"] if row else 0
            task_done = row["done"] if row else 0
            task_summary = (
                f"**Fleet tasks (last {hours}h):** {task_count} total, "
                f"{task_done} completed"
            )
    except Exception:
        log.warning("Could not query task stats for digest", exc_info=True)

    # Build markdown digest
    today = date.today().isoformat()
    md = [
        f"# Knowledge Digest — {today}",
        "",
        f"**Period:** Last {hours} hours | "
        f"**New artifacts:** {total_new} | **Total artifacts:** {total_all}",
    ]
    if task_summary:
        md.append(task_summary)
    md.append("")
    md.append("---")
    md.append("")

    # Active categories (those with new files)
    active = {k: v for k, v in categories.items() if v["new"] > 0}
    inactive = {k: v for k, v in categories.items() if v["new"] == 0}

    md.append("## Active Categories")
    md.append("")
    if active:
        md.append("| Category | New | Total | Recent Files |")
        md.append("|----------|-----|-------|--------------|")
        for cat, info in sorted(active.items(), key=lambda x: x[1]["new"],
                                reverse=True):
            recent = ", ".join(info["recent_files"][:5])
            if len(info["recent_files"]) > 5:
                recent += f" (+{len(info['recent_files']) - 5} more)"
            md.append(f"| {cat} | {info['new']} | {info['total']} | {recent} |")
    else:
        md.append("No new artifacts in the last {hours} hours.")
    md.append("")

    md.append("## Inactive Categories")
    md.append("")
    if inactive:
        for cat, info in sorted(inactive.items()):
            md.append(f"- **{cat}**: {info['total']} files (no new activity)")
    else:
        md.append("All categories had activity.")
    md.append("")

    # Summary stats
    md.append("## Summary")
    md.append("")
    md.append(f"- Categories scanned: {len(categories)}")
    md.append(f"- Active categories: {len(active)}")
    md.append(f"- New artifacts: {total_new}")
    md.append(f"- Total artifacts: {total_all}")
    md.append("")

    report = "\n".join(md)
    out_file = DIGEST_DIR / f"digest_{today}.md"
    out_file.write_text(report, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "period_hours": hours,
        "categories_scanned": len(categories),
        "active_categories": len(active),
        "new_artifacts": total_new,
        "total_artifacts": total_all,
    }
