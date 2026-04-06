"""Wiki lint skill — periodic health check for the knowledge wiki.

Identifies: broken cross-links, orphan pages, stale pages, missing
cross-references. Part of the Karpathy LLM Wiki pattern (WS-6).
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

SKILL_NAME = "wiki_lint"
DESCRIPTION = "Lint the knowledge wiki for broken links, orphans, and stale pages"
VERSION = "1.0.0"
COMPLEXITY = "simple"
REQUIRES_NETWORK = False
SUITE = "knowledge"
TAGS = ["wiki", "lint", "knowledge"]
log = logging.getLogger(SKILL_NAME)

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
WIKI_DIR = KNOWLEDGE_DIR / "wiki"


def run(payload, config):
    if not WIKI_DIR.exists():
        return {"status": "no_wiki", "message": "knowledge/wiki/ directory not found"}

    issues = []
    pages = list(WIKI_DIR.glob("*.md"))
    page_names = {p.stem for p in pages}

    # 1. Broken cross-links
    for page in pages:
        try:
            content = page.read_text(encoding="utf-8")
            for match in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', content):
                link_text, link_target = match.groups()
                if link_target.endswith(".md") and "/" not in link_target:
                    target_name = link_target.replace(".md", "")
                    if target_name not in page_names:
                        issues.append({
                            "type": "broken_link",
                            "severity": "warn",
                            "page": page.name,
                            "target": link_target,
                            "link_text": link_text,
                        })
                elif link_target.startswith("../") and not link_target.startswith("../index"):
                    # Check if referenced directory/file exists
                    resolved = (WIKI_DIR / link_target).resolve()
                    if not resolved.exists():
                        issues.append({
                            "type": "broken_ref",
                            "severity": "info",
                            "page": page.name,
                            "target": link_target,
                        })
        except Exception as e:
            log.warning("Failed to lint %s: %s", page.name, e)

    # 2. Orphan pages (no incoming links from other wiki pages)
    linked_pages = set()
    for page in pages:
        try:
            content = page.read_text(encoding="utf-8")
            for match in re.finditer(r'\]\(([^)]+\.md)\)', content):
                target = match.group(1).replace(".md", "")
                linked_pages.add(target)
        except Exception:
            pass

    for page in pages:
        if page.stem not in linked_pages and page.stem != "overview":
            issues.append({
                "type": "orphan",
                "severity": "info",
                "page": page.name,
                "message": "No incoming links from other wiki pages",
            })

    # 3. Stale pages (last modified > 7 days ago)
    stale_threshold = datetime.now() - timedelta(days=7)
    for page in pages:
        try:
            mtime = datetime.fromtimestamp(page.stat().st_mtime)
            if mtime < stale_threshold:
                issues.append({
                    "type": "stale",
                    "severity": "info",
                    "page": page.name,
                    "last_modified": mtime.strftime("%Y-%m-%d"),
                    "days_old": (datetime.now() - mtime).days,
                })
        except Exception:
            pass

    # 4. Missing index.md reference
    index_path = KNOWLEDGE_DIR / "index.md"
    if index_path.exists():
        index_content = index_path.read_text(encoding="utf-8")
        for page in pages:
            rel = f"wiki/{page.name}"
            if rel not in index_content:
                issues.append({
                    "type": "missing_index",
                    "severity": "warn",
                    "page": page.name,
                    "message": "Not listed in knowledge/index.md",
                })

    # Log the lint run
    try:
        from skills._knowledge import log_operation
        log_operation("wiki_lint", "lint", f"{len(pages)} pages, {len(issues)} issues found")
    except Exception:
        pass

    return {
        "status": "ok",
        "pages_scanned": len(pages),
        "issues_found": len(issues),
        "issues": issues,
        "by_type": _count_by_type(issues),
    }


def _count_by_type(issues):
    counts = {}
    for i in issues:
        t = i["type"]
        counts[t] = counts.get(t, 0) + 1
    return counts
