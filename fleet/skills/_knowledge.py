# fleet/skills/_knowledge.py
"""Shared knowledge directory management, file-save helpers, and wiki maintenance.

Wiki maintenance follows the Karpathy LLM Wiki pattern (WS-6):
  - log_operation() → appends to knowledge/log.md
  - ensure_in_index() → maintains knowledge/index.md
  - update_wiki_page() → updates/creates pages in knowledge/wiki/
"""
import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger("skills._knowledge")

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
PROJECT_DIR = FLEET_DIR.parent
INDEX_PATH = KNOWLEDGE_DIR / "index.md"
LOG_PATH = KNOWLEDGE_DIR / "log.md"


def get_output_dir(*parts: str) -> Path:
    """Return knowledge/<parts> directory, creating it if needed."""
    d = KNOWLEDGE_DIR.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_report(subdir: str, prefix: str, content: str, ext: str = ".md") -> Path:
    """Save a date-stamped report: knowledge/<subdir>/<prefix>_YYYYMMDD.<ext>."""
    d = get_output_dir(subdir)
    path = d / f"{prefix}_{datetime.now().strftime('%Y%m%d')}{ext}"
    path.write_text(content, encoding="utf-8")
    return path


def save_timestamped(subdir: str, prefix: str, content: str, ext: str = ".md") -> Path:
    """Save with full timestamp: knowledge/<subdir>/<prefix>_YYYYMMDD_HHMM.<ext>."""
    d = get_output_dir(subdir)
    path = d / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}{ext}"
    path.write_text(content, encoding="utf-8")
    return path


# ── Wiki maintenance (WS-6: Karpathy LLM Wiki pattern) ──────────────────


def log_operation(agent: str, action: str, description: str) -> None:
    """Append an entry to knowledge/log.md."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- {timestamp} — {agent} — {action}: {description}\n"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        log.warning("Failed to append to knowledge log: %s", e)


def ensure_in_index(rel_path: str, title: str, summary: str) -> None:
    """Ensure a file is listed in knowledge/index.md.

    If the path already appears, the entry is updated. Otherwise appended.

    Args:
        rel_path: path relative to knowledge/ (e.g. "code_reviews/review_2026-04-06.md")
        title: human-readable title
        summary: one-line summary
    """
    try:
        if not INDEX_PATH.exists():
            return

        date_str = datetime.now().strftime("%Y-%m-%d")
        new_entry = f"- [{title}]({rel_path}) — {summary} (updated {date_str})"

        lines = INDEX_PATH.read_text(encoding="utf-8").splitlines()

        updated = False
        for i, line in enumerate(lines):
            if rel_path in line:
                lines[i] = new_entry
                updated = True
                break

        if not updated:
            lines.append(new_entry)

        INDEX_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        log.warning("Failed to update index.md for %s: %s", rel_path, e)


def update_wiki_page(page_name: str, section: str, content: str) -> None:
    """Update a section within a wiki page, or append if section doesn't exist.

    Args:
        page_name: wiki page filename (e.g. "security.md")
        section: markdown heading to find/replace (e.g. "## Recent Findings")
        content: new content for that section
    """
    wiki_dir = KNOWLEDGE_DIR / "wiki"
    if not wiki_dir.exists():
        return

    page_path = wiki_dir / page_name
    try:
        if not page_path.exists():
            page_path.write_text(
                f"# {page_name.replace('.md', '').replace('-', ' ').title()}\n\n"
                f"> Last updated: {datetime.now().strftime('%Y-%m-%d')}\n\n"
                f"{section}\n{content}\n\n"
                f"## Related\n- [Index](../index.md)\n",
                encoding="utf-8",
            )
            return

        text = page_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        section_idx = None
        next_section_idx = None
        for i, line in enumerate(lines):
            if line.strip() == section:
                section_idx = i
            elif section_idx is not None and line.startswith("## ") and i > section_idx:
                next_section_idx = i
                break

        if section_idx is not None:
            end = next_section_idx if next_section_idx else len(lines)
            new_lines = lines[:section_idx + 1] + [content, ""] + lines[end:]
            page_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        else:
            related_idx = None
            for i, line in enumerate(lines):
                if line.strip().startswith("## Related"):
                    related_idx = i
                    break
            insert_at = related_idx if related_idx else len(lines)
            lines.insert(insert_at, f"\n{section}\n{content}\n")
            page_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Update the "Last updated" line
        text = page_path.read_text(encoding="utf-8")
        if "> Last updated:" in text:
            text = re.sub(
                r"> Last updated: \d{4}-\d{2}-\d{2}",
                f"> Last updated: {datetime.now().strftime('%Y-%m-%d')}",
                text,
            )
            page_path.write_text(text, encoding="utf-8")
    except Exception as e:
        log.warning("Failed to update wiki page %s: %s", page_name, e)
