"""
Shared security utilities for fleet skills.

All skills that accept file paths or user-provided strings from payloads
should use these helpers to prevent path traversal, injection, etc.
"""
import re
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
PROJECT_DIR = FLEET_DIR.parent

# Allowed roots for file operations
ALLOWED_ROOTS = [
    FLEET_DIR,
    PROJECT_DIR / "BigEd",
    PROJECT_DIR / "autoresearch",
    Path("/mnt/c/Users/max/Projects/Education"),  # WSL equivalent
]


def safe_path(user_path: str, allowed_roots: list[Path] = None) -> Path | None:
    """
    Resolve a user-provided path and verify it's within allowed directories.
    Returns resolved Path if safe, None if traversal detected.
    """
    if not user_path:
        return None

    roots = allowed_roots or ALLOWED_ROOTS

    # Resolve to absolute, collapsing .. and symlinks
    try:
        resolved = Path(user_path).resolve()
    except (ValueError, OSError):
        return None

    # Also try relative to fleet dir
    if not resolved.is_absolute():
        resolved = (FLEET_DIR / user_path).resolve()

    # Check against allowed roots
    for root in roots:
        try:
            root_resolved = root.resolve()
            if resolved == root_resolved or resolved.is_relative_to(root_resolved):
                return resolved
        except (ValueError, OSError):
            continue

    return None


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Sanitize a filename — strip traversal chars, limit length."""
    # Remove path separators and traversal
    name = re.sub(r'[/\\]', '_', name)
    name = name.replace('..', '')
    # Keep only safe chars
    name = re.sub(r'[^a-zA-Z0-9._\-]', '_', name)
    return name[:max_len]


def sanitize_discord_content(text: str, max_len: int = 2000) -> str:
    """
    Sanitize Discord message content before processing.
    Strips @everyone/@here mentions, excessive whitespace, and control chars.
    """
    # Strip Discord mentions that could cause pings
    text = re.sub(r'@(everyone|here)', '[mention blocked]', text)
    # Strip zero-width and control characters (except newline/tab)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Collapse excessive whitespace
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text[:max_len]
