# fleet/skills/_knowledge.py
"""Shared knowledge directory management and file-save helpers."""
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
PROJECT_DIR = FLEET_DIR.parent


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
