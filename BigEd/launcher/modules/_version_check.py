"""Version comparison utility for module deprecation."""

import re

_CURRENT_VERSION = "0.22"


def parse_version(v: str) -> tuple:
    """Parse 'v0.22' or '0.22' into (0, 22)."""
    m = re.match(r"v?(\d+)\.(\d+)", v or "")
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def is_past_sunset(sunset_version: str) -> bool:
    """Return True if current version >= sunset version."""
    if not sunset_version:
        return False
    current = parse_version(_CURRENT_VERSION)
    sunset = parse_version(sunset_version)
    return current >= sunset


def set_current_version(v: str):
    """Update the running version for deprecation checks."""
    global _CURRENT_VERSION
    _CURRENT_VERSION = v
