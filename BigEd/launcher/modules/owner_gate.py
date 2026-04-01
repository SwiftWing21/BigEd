"""
Owner key verification utility.

Extracted from mod_owner_core.py so it remains available after
the tkinter module tabs were removed.
"""
import os
from pathlib import Path


def verify_owner_key() -> bool:
    """Verify BIGED_OWNER_KEY is set and valid."""
    key = os.environ.get("BIGED_OWNER_KEY", "")
    if not key:
        # Try loading from ~/.secrets
        secrets = Path.home() / ".secrets"
        if secrets.exists():
            for line in secrets.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("export BIGED_OWNER_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    break
    # Validate: must be non-empty and at least 32 chars (basic check)
    return len(key) >= 32


# Backward compat alias
_verify_owner_key = verify_owner_key
