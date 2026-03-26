"""
Knowledge digest skill — scans knowledge/ subdirectories, counts new files
in the last N hours, summarizes activity per category, and saves a daily digest.
"""
import logging
SKILL_NAME = "knowledge_digest"
DESCRIPTION = "Generate a daily digest of knowledge artifacts across all categories."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ops"
log = logging.getLogger(SKILL_NAME)


def run(payload, config):
    import db
    import logging
    from datetime import date, datetime, timedelta, timezone
    from skills._knowledge import get_output_dir, FLEET_DIR, KNOWLEDGE_DIR
    from skills._report import ReportBuilder

    log = logging.getLogger(SKILL_NAME)

    DIGEST_DIR = get_output_dir("digests")

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
    active = {k: v for k, v in categories.items() if v["new"] > 0}
    inactive = {k: v for k, v in categories.items() if v["new"] == 0}

    rb = ReportBuilder("Knowledge Digest").metadata(
        Period=f"Last {hours} hours",
        New_artifacts=total_new,
        Total_artifacts=total_all,
    )
    if task_summary:
        rb.line(task_summary)
    rb.blank().line("---").blank()

    rb.section("Active Categories")
    if active:
        active_rows = []
        for cat, info in sorted(active.items(), key=lambda x: x[1]["new"], reverse=True):
            recent = ", ".join(info["recent_files"][:5])
            if len(info["recent_files"]) > 5:
                recent += f" (+{len(info['recent_files']) - 5} more)"
            active_rows.append([cat, info["new"], info["total"], recent])
        rb.table(["Category", "New", "Total", "Recent Files"], active_rows)
    else:
        rb.line(f"No new artifacts in the last {hours} hours.").blank()

    rb.section("Inactive Categories")
    if inactive:
        for cat, info in sorted(inactive.items()):
            rb.line(f"- **{cat}**: {info['total']} files (no new activity)")
        rb.blank()
    else:
        rb.line("All categories had activity.").blank()

    rb.section("Summary")
    rb.line(f"- Categories scanned: {len(categories)}")
    rb.line(f"- Active categories: {len(active)}")
    rb.line(f"- New artifacts: {total_new}")
    rb.line(f"- Total artifacts: {total_all}")
    rb.blank()

    report = rb.build()
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
