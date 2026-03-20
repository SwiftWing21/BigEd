"""
BigEd CC -- FileSystem Access Guard (SOC 2 Compliant).

Validates file operations against declared access zones before any I/O.
Enterprise mode: deny_by_default=true, log_all_access=true.

Usage:
    guard = FileSystemGuard(config)
    guard.check_access("fleet/knowledge/file.md", "read", agent="coder_1")
    guard.check_access("fleet/skills/new.py", "write", agent="deploy_skill")
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("fleet.filesystem_guard")

# Access levels (ordered lowest to highest)
ACCESS_LEVELS = {"read": 0, "read_write": 1, "full": 2}

# Map actions to the minimum access level required
ACTION_TO_LEVEL = {
    "read": "read",
    "write": "read_write",
    "create": "read_write",
    "delete": "full",
    "execute": "full",
}

# Project root (same convention as config.py)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class FileSystemGuard:
    """Validates file operations against declared access zones."""

    def __init__(self, config: dict):
        self._fs_cfg = config.get("filesystem", {})
        self._enforce = self._fs_cfg.get("enforce", False)
        self._deny_by_default = self._fs_cfg.get("deny_by_default", False)
        self._log_all = self._fs_cfg.get("log_all_access", False)
        self._zones = self._load_zones()
        self._overrides = self._fs_cfg.get("overrides", {})
        self._log_path = Path(__file__).parent / "logs" / "fs_access.log"

    # -- public API --

    def check_access(
        self,
        path: str,
        action: str,
        agent: str | None = None,
        skill: str | None = None,
    ) -> bool:
        """Validate whether *action* on *path* is allowed.

        Returns True (allowed) or False (denied).  When enforce is off,
        always returns True but still logs if log_all_access is set.
        """
        required_level = ACTION_TO_LEVEL.get(action, "full")
        resolved = self._resolve_path(path)

        # Find matching zone(s)
        matched_zone, zone_access = self._match_zone(resolved)

        # Apply skill override if present
        if skill and skill in self._overrides:
            override = self._overrides[skill]
            override_zones = override.get("zones", [])
            override_access = override.get("access", zone_access)
            if matched_zone and matched_zone in override_zones:
                zone_access = override_access

        # Decide
        if matched_zone is None:
            allowed = not self._deny_by_default
        else:
            allowed = ACCESS_LEVELS.get(zone_access, 0) >= ACCESS_LEVELS.get(
                required_level, 0
            )

        # Log
        if self._log_all or not allowed:
            self.log_access(path, action, agent or "unknown", allowed, skill=skill)

        # Enforce
        if not self._enforce:
            return True
        return allowed

    def log_access(
        self,
        path: str,
        action: str,
        agent: str,
        allowed: bool,
        *,
        skill: str | None = None,
    ) -> None:
        """Append audit entry to filesystem access log."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        status = "ALLOW" if allowed else "DENY"
        skill_tag = f" skill={skill}" if skill else ""
        entry = f"{ts} [{status}] agent={agent}{skill_tag} action={action} path={path}"

        logger.info(entry) if allowed else logger.warning(entry)

        # File-based audit trail (SOC 2)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except OSError:
            logger.debug("Could not write filesystem audit log to %s", self._log_path)

    def get_zones(self) -> dict:
        """Return configured zones as {name: {path, access}}."""
        return dict(self._zones)

    def is_enterprise(self) -> bool:
        """True when enforcement + deny-by-default are both active."""
        return self._enforce and self._deny_by_default

    # -- internal helpers --

    def _load_zones(self) -> dict:
        """Parse [filesystem.zones] into {name: {path: Path, access: str}}."""
        raw = self._fs_cfg.get("zones", {})
        zones: dict = {}
        for name, spec in raw.items():
            zone_path = spec.get("path", "")
            access = spec.get("access", "read")
            # Expand ~ and resolve relative to project root
            resolved = self._resolve_path(zone_path)
            zones[name] = {"path": resolved, "access": access}
        return zones

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path: expand ~ and make relative paths absolute to project root."""
        p = Path(os.path.expanduser(path))
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    def _match_zone(self, resolved: Path) -> tuple:
        """Find the most specific zone that contains *resolved*.

        Returns (zone_name, access) or (None, None) if no zone matches.
        Longest path prefix wins (most specific zone).
        """
        best_name = None
        best_access = None
        best_depth = -1

        for name, spec in self._zones.items():
            zone_path: Path = spec["path"]
            try:
                resolved.relative_to(zone_path)
            except ValueError:
                continue
            depth = len(zone_path.parts)
            if depth > best_depth:
                best_name = name
                best_access = spec["access"]
                best_depth = depth

        return best_name, best_access
