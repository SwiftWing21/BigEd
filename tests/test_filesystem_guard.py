"""Unit tests for fleet/filesystem_guard.py."""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


def _make_guard(fs_cfg=None):
    from filesystem_guard import FileSystemGuard
    return FileSystemGuard({"filesystem": fs_cfg or {}})


def _make_guard_with_zones(zones, enforce=True, deny_by_default=False):
    from filesystem_guard import FileSystemGuard
    cfg = {
        "filesystem": {
            "enforce": enforce,
            "deny_by_default": deny_by_default,
            "zones": zones,
            "log_all_access": False,
        }
    }
    return FileSystemGuard(cfg)


class TestInit(unittest.TestCase):
    def test_default_not_enforcing(self):
        guard = _make_guard()
        self.assertFalse(guard._enforce)

    def test_enterprise_mode(self):
        guard = _make_guard({"enforce": True, "deny_by_default": True})
        self.assertTrue(guard.is_enterprise())

    def test_non_enterprise_when_enforce_only(self):
        guard = _make_guard({"enforce": True, "deny_by_default": False})
        self.assertFalse(guard.is_enterprise())


class TestResolvePath(unittest.TestCase):
    def test_absolute_path_unchanged(self):
        guard = _make_guard()
        result = guard._resolve_path("/tmp/some/file.txt")
        self.assertTrue(result.is_absolute())

    def test_relative_resolved_to_project_root(self):
        guard = _make_guard()
        result = guard._resolve_path("fleet/fleet.toml")
        self.assertTrue(result.is_absolute())
        self.assertIn("fleet", str(result))

    def test_tilde_expanded(self):
        guard = _make_guard()
        home = Path.home()
        result = guard._resolve_path("~/Documents")
        self.assertTrue(result.is_absolute())
        self.assertEqual(str(result), str((home / "Documents").resolve()))


class TestMatchZone(unittest.TestCase):
    def _guard_with_zone(self, zone_path, access="read"):
        from filesystem_guard import FileSystemGuard
        cfg = {
            "filesystem": {
                "zones": {
                    "test_zone": {"path": zone_path, "access": access}
                }
            }
        }
        return FileSystemGuard(cfg)

    def test_path_inside_zone_matches(self):
        tmp = tempfile.mkdtemp()
        guard = self._guard_with_zone(tmp)
        inner = Path(tmp) / "subdir" / "file.txt"
        name, access = guard._match_zone(inner.resolve())
        self.assertEqual(name, "test_zone")

    def test_path_outside_zone_no_match(self):
        guard = self._guard_with_zone("/tmp/restricted_zone")
        name, access = guard._match_zone(Path("/other/path"))
        self.assertIsNone(name)

    def test_most_specific_zone_wins(self):
        from filesystem_guard import FileSystemGuard
        tmp = tempfile.mkdtemp()
        sub = Path(tmp) / "sub"
        sub.mkdir()
        cfg = {
            "filesystem": {
                "zones": {
                    "broad": {"path": tmp, "access": "read"},
                    "narrow": {"path": str(sub), "access": "full"},
                }
            }
        }
        guard = FileSystemGuard(cfg)
        name, access = guard._match_zone((sub / "file.txt").resolve())
        self.assertEqual(name, "narrow")
        self.assertEqual(access, "full")


class TestCheckAccess(unittest.TestCase):
    def test_permissive_mode_always_allows(self):
        """When enforce=False, check_access always returns True."""
        zones = {"fleet_zone": {"path": "/tmp/fleet", "access": "read"}}
        guard = _make_guard_with_zones(zones, enforce=False, deny_by_default=True)
        # Even a "delete" on unmatched path returns True when not enforcing
        result = guard.check_access("/tmp/fleet/secret.db", "delete", agent="attacker")
        self.assertTrue(result)

    def test_enforce_allows_read_in_read_zone(self):
        tmp = tempfile.mkdtemp()
        zones = {"r_zone": {"path": tmp, "access": "read"}}
        guard = _make_guard_with_zones(zones, enforce=True, deny_by_default=True)
        result = guard.check_access(str(Path(tmp) / "file.txt"), "read")
        self.assertTrue(result)

    def test_enforce_denies_write_in_read_zone(self):
        tmp = tempfile.mkdtemp()
        zones = {"r_zone": {"path": tmp, "access": "read"}}
        guard = _make_guard_with_zones(zones, enforce=True, deny_by_default=True)
        result = guard.check_access(str(Path(tmp) / "file.txt"), "write")
        self.assertFalse(result)

    def test_enforce_allows_delete_in_full_zone(self):
        tmp = tempfile.mkdtemp()
        zones = {"f_zone": {"path": tmp, "access": "full"}}
        guard = _make_guard_with_zones(zones, enforce=True)
        result = guard.check_access(str(Path(tmp) / "file.txt"), "delete")
        self.assertTrue(result)

    def test_deny_by_default_blocks_unmatched(self):
        guard = _make_guard_with_zones({}, enforce=True, deny_by_default=True)
        result = guard.check_access("/completely/unknown/path/file.txt", "read")
        self.assertFalse(result)

    def test_allow_by_default_passes_unmatched(self):
        guard = _make_guard_with_zones({}, enforce=True, deny_by_default=False)
        result = guard.check_access("/completely/unknown/path/file.txt", "read")
        self.assertTrue(result)


class TestGetZones(unittest.TestCase):
    def test_returns_loaded_zones(self):
        zones = {
            "knowledge": {"path": "fleet/knowledge", "access": "read_write"},
        }
        guard = _make_guard_with_zones(zones)
        result = guard.get_zones()
        self.assertIn("knowledge", result)

    def test_empty_zones(self):
        guard = _make_guard()
        result = guard.get_zones()
        self.assertIsInstance(result, dict)


class TestLogAccess(unittest.TestCase):
    def test_log_access_writes_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "fs_access.log"
            guard = _make_guard()
            with patch.object(guard, "_log_path", log_path):
                guard.log_access("/some/path", "read", "test_agent", True)
            self.assertTrue(log_path.exists())
            content = log_path.read_text()
            self.assertIn("ALLOW", content)
            self.assertIn("test_agent", content)

    def test_deny_logged_with_DENY(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "fs_access.log"
            guard = _make_guard()
            with patch.object(guard, "_log_path", log_path):
                guard.log_access("/forbidden/path", "write", "bad_agent", False)
            content = log_path.read_text()
            self.assertIn("DENY", content)

    def test_skill_tag_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "fs_access.log"
            guard = _make_guard()
            with patch.object(guard, "_log_path", log_path):
                guard.log_access("/path", "read", "agent", True, skill="my_skill")
            content = log_path.read_text()
            self.assertIn("skill=my_skill", content)


if __name__ == "__main__":
    unittest.main()
