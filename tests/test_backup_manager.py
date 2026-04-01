"""Unit tests for fleet/backup_manager.py."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


def _make_bm(config_override=None, tmp_location=None):
    from backup_manager import BackupManager
    cfg = {
        "backup": {
            "enabled": True,
            "interval_secs": 180,
            "depth": 3,
            "prune_enabled": True,
            "verify": False,
            "targets": {
                "fleet_db": False,
                "rag_db": False,
                "config": False,
                "knowledge": False,
            },
        }
    }
    if config_override:
        cfg["backup"].update(config_override)
    if tmp_location:
        cfg["backup"]["location"] = str(tmp_location)
    return BackupManager(cfg)


class TestBackupManagerInit(unittest.TestCase):
    def test_default_interval_minimum(self):
        from backup_manager import BackupManager
        bm = BackupManager({"backup": {"interval_secs": 10}})
        self.assertEqual(bm.interval, 180)  # min 3 minutes

    def test_depth_clamped(self):
        bm = _make_bm({"depth": 100})
        self.assertEqual(bm.depth, 20)  # max 20

    def test_depth_zero_means_infinite(self):
        bm = _make_bm({"depth": 0})
        self.assertEqual(bm.depth, 0)

    def test_enabled_flag(self):
        bm = _make_bm({"enabled": False})
        self.assertFalse(bm.enabled)


class TestPerformBackup(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_creates_backup_directory(self):
        bm = _make_bm(tmp_location=self.tmp_dir)
        with patch.object(bm, "_checkpoint_wal"):
            manifest = bm.perform_backup(trigger="test")
        backup_path = Path(manifest["location"])
        self.assertTrue(backup_path.exists())

    def test_manifest_written(self):
        bm = _make_bm(tmp_location=self.tmp_dir)
        with patch.object(bm, "_checkpoint_wal"):
            manifest = bm.perform_backup(trigger="unit_test")
        backup_path = Path(manifest["location"])
        manifest_file = backup_path / "manifest.json"
        self.assertTrue(manifest_file.exists())
        loaded = json.loads(manifest_file.read_text())
        self.assertEqual(loaded["trigger"], "unit_test")

    def test_manifest_has_expected_keys(self):
        bm = _make_bm(tmp_location=self.tmp_dir)
        with patch.object(bm, "_checkpoint_wal"):
            manifest = bm.perform_backup()
        for key in ("id", "timestamp", "trigger", "location", "files", "total_size_bytes"):
            self.assertIn(key, manifest)

    def test_backup_copies_config_file(self):
        # Create a fake fleet.toml in tmp fleet dir
        fake_fleet = self.tmp_dir / "fake_fleet"
        fake_fleet.mkdir()
        toml_file = fake_fleet / "fleet.toml"
        toml_file.write_text("[fleet]\nenabled = true\n")

        bm = _make_bm(tmp_location=self.tmp_dir / "backups")
        bm.backup_config = True
        bm.backup_fleet_db = False
        bm.backup_rag_db = False
        bm.backup_knowledge = False

        with patch("backup_manager.FLEET_DIR", fake_fleet), \
             patch.object(bm, "_checkpoint_wal"), \
             patch.object(bm, "_verify_db", return_value=True):
            manifest = bm.perform_backup(trigger="config_test")

        self.assertIn("fleet.toml", manifest["files"])


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_backup_dirs(self, names):
        for name in names:
            d = self.tmp_dir / name
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"id": name}))

    def test_prune_keeps_last_n(self):
        self._make_backup_dirs(["20240101_120000", "20240102_120000", "20240103_120000",
                                "20240104_120000", "20240105_120000"])
        bm = _make_bm(tmp_location=self.tmp_dir)
        bm.depth = 3
        bm._prune()
        remaining = sorted([d.name for d in self.tmp_dir.iterdir() if d.is_dir()])
        self.assertEqual(len(remaining), 3)
        # Newest 3 survive
        self.assertIn("20240105_120000", remaining)
        self.assertIn("20240104_120000", remaining)
        self.assertIn("20240103_120000", remaining)

    def test_prune_infinite_depth_skips(self):
        self._make_backup_dirs(["20240101_120000", "20240102_120000"])
        bm = _make_bm(tmp_location=self.tmp_dir)
        bm.depth = 0  # infinite
        bm._prune()
        remaining = [d for d in self.tmp_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(remaining), 2)

    def test_prune_empty_dir_no_error(self):
        bm = _make_bm(tmp_location=self.tmp_dir)
        bm.depth = 3
        bm._prune()  # no error on empty directory


class TestListBackups(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_empty_when_no_backups(self):
        bm = _make_bm(tmp_location=self.tmp_dir)
        self.assertEqual(bm.list_backups(), [])

    def test_lists_valid_backups(self):
        d = self.tmp_dir / "20240101_120000"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"id": "20240101_120000", "trigger": "manual"}))
        bm = _make_bm(tmp_location=self.tmp_dir)
        result = bm.list_backups()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "20240101_120000")

    def test_ignores_dirs_without_manifest(self):
        (self.tmp_dir / "orphan_dir").mkdir()
        bm = _make_bm(tmp_location=self.tmp_dir)
        self.assertEqual(bm.list_backups(), [])


class TestVerifyDb(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil, gc
        gc.collect()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_true_for_valid_db(self):
        import sqlite3, gc
        db_path = Path(self.tmp_dir) / "valid.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
        from backup_manager import BackupManager
        bm = BackupManager({})
        result = bm._verify_db(db_path)
        self.assertTrue(result)

    def test_returns_false_for_corrupt_file(self):
        from backup_manager import BackupManager
        bm = BackupManager({})
        corrupt_path = Path(self.tmp_dir) / "corrupt.db"
        corrupt_path.write_bytes(b"not a sqlite database at all!!")
        result = bm._verify_db(corrupt_path)
        self.assertFalse(result)


class TestAutoSave(unittest.TestCase):
    def test_start_auto_save_skipped_when_disabled(self):
        bm = _make_bm({"enabled": False})
        bm.start_auto_save()
        self.assertFalse(bm._running)

    def test_stop_auto_save_sets_flag(self):
        bm = _make_bm()
        bm._running = True
        bm.stop_auto_save()
        self.assertFalse(bm._running)

    def test_start_auto_save_idempotent(self):
        bm = _make_bm()
        bm._running = True  # already running
        bm.start_auto_save()
        # Should not start a second thread


if __name__ == "__main__":
    unittest.main()
