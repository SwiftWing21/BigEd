"""Generate categorized changelog from git commit history."""

SKILL_NAME = "changelog_generate"
DESCRIPTION = "Generate categorized changelog from git commit history."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "content"


def run(payload, config):
    import subprocess
    import re
    from datetime import date
    from pathlib import Path

    FLEET_DIR = Path(__file__).parent.parent
    PROJECT_DIR = FLEET_DIR.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    CHANGELOGS_DIR = KNOWLEDGE_DIR / "changelogs"
    CHANGELOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Commit range: tag..HEAD, hash..hash, or last N commits
    commit_range = payload.get("range", "")
    last_n = payload.get("last", 50)
    version = payload.get("version", date.today().isoformat())

    try:
        if commit_range:
            cmd = ["git", "log", "--pretty=format:%H|%s|%an|%ai", commit_range]
        else:
            cmd = ["git", "log", f"--max-count={last_n}", "--pretty=format:%H|%s|%an|%ai"]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return {"error": f"git log failed: {result.stderr[:200]}", "status": "fail"}

        lines = result.stdout.strip().split("\n")
        if not lines or not lines[0]:
            return {"status": "skip", "message": "No commits found"}

    except Exception as e:
        return {"error": f"Git error: {e}", "status": "fail"}

    # Parse and categorize commits
    categories = {
        "feat": {"label": "Features", "commits": []},
        "fix": {"label": "Bug Fixes", "commits": []},
        "docs": {"label": "Documentation", "commits": []},
        "refactor": {"label": "Refactoring", "commits": []},
        "style": {"label": "Styling", "commits": []},
        "test": {"label": "Tests", "commits": []},
        "perf": {"label": "Performance", "commits": []},
        "chore": {"label": "Chores", "commits": []},
        "other": {"label": "Other Changes", "commits": []},
    }

    prefix_pattern = re.compile(r'^(feat|fix|docs|refactor|style|test|perf|chore)[\(:]?\s*(.+)')
    authors = set()
    total = 0

    for line in lines:
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        sha, subject, author, timestamp = parts[0][:8], parts[1], parts[2], parts[3][:10]
        authors.add(author)
        total += 1

        match = prefix_pattern.match(subject)
        if match:
            category = match.group(1)
            message = match.group(2).strip().lstrip(":).").strip()
            # Extract scope if present: feat(scope): message
            scope_match = re.match(r'\(([^)]+)\)\s*:?\s*(.*)', message)
            if scope_match:
                scope = scope_match.group(1)
                message = scope_match.group(2) or message
            else:
                scope = None
        else:
            category = "other"
            message = subject
            scope = None

        categories[category]["commits"].append({
            "sha": sha,
            "message": message,
            "author": author,
            "date": timestamp,
            "scope": scope,
        })

    # Generate markdown
    md_lines = [
        f"# Changelog — {version}",
        f"",
        f"Generated: {date.today().isoformat()} | Commits: {total} | Authors: {', '.join(sorted(authors))}",
        f"",
        f"---",
        f"",
    ]

    for key in ["feat", "fix", "refactor", "perf", "docs", "style", "test", "chore", "other"]:
        cat = categories[key]
        if not cat["commits"]:
            continue
        md_lines.append(f"## {cat['label']}")
        md_lines.append(f"")
        for c in cat["commits"]:
            scope_str = f"**{c['scope']}**: " if c.get("scope") else ""
            md_lines.append(f"- {scope_str}{c['message']} (`{c['sha']}`)")
        md_lines.append(f"")

    # Summary stats
    md_lines.append(f"---")
    md_lines.append(f"")
    md_lines.append(f"**Stats:** {total} commits | " + " | ".join(
        f"{cat['label']}: {len(cat['commits'])}" for cat in categories.values() if cat["commits"]
    ))

    changelog = "\n".join(md_lines)
    safe_version = re.sub(r'[^a-zA-Z0-9._-]', '_', version)
    out_file = CHANGELOGS_DIR / f"changelog_{safe_version}.md"
    out_file.write_text(changelog, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "total_commits": total,
        "categories": {k: len(v["commits"]) for k, v in categories.items() if v["commits"]},
        "authors": sorted(authors),
    }
