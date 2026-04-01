"""Tests for update_manager — core update logic."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_detect_mode_git(tmp_path):
    (tmp_path / ".git").mkdir()
    import update_manager
    with patch.object(update_manager, "_SEARCH_ROOTS", [tmp_path]):
        assert update_manager.detect_mode() == "git"


def test_detect_mode_release(tmp_path):
    import update_manager
    with patch.object(update_manager, "_SEARCH_ROOTS", [tmp_path]):
        assert update_manager.detect_mode() == "release"


def test_current_version_from_file(tmp_path):
    version_file = tmp_path / ".bigedcc_version"
    version_file.write_text("v0.400.00b")
    import update_manager
    with patch.object(update_manager, "_VERSION_FILE", version_file):
        assert update_manager.current_version() == "v0.400.00b"


def test_current_version_git_fallback(tmp_path):
    version_file = tmp_path / ".bigedcc_version"
    import update_manager
    with patch.object(update_manager, "_VERSION_FILE", version_file):
        with patch.object(update_manager, "_git_describe", return_value="v0.400.00b-3-gabcdef"):
            assert update_manager.current_version() == "v0.400.00b-3-gabcdef"


def test_get_manifest_empty(tmp_path):
    import update_manager
    with patch.object(update_manager, "_MANIFEST_FILE", tmp_path / "nope.json"):
        assert update_manager.get_manifest() == {}


def test_save_and_load_manifest(tmp_path):
    manifest_file = tmp_path / "manifest.json"
    import update_manager
    with patch.object(update_manager, "_MANIFEST_FILE", manifest_file), \
         patch.object(update_manager, "_DIST_DIR", tmp_path):
        update_manager.save_manifest({"__last_date__": "2026-04-01", "foo": "bar"})
        result = update_manager.get_manifest()
        assert result["__last_date__"] == "2026-04-01"
        assert result["foo"] == "bar"


def test_check_for_update_offline():
    import update_manager
    with patch.object(update_manager, "_is_offline", return_value=True):
        result = update_manager.check_for_update()
        assert result["available"] is False


def test_file_hash(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    import update_manager
    h = update_manager.file_hash(f)
    assert isinstance(h, str)
    assert len(h) == 32  # MD5 hex digest


def test_get_status_returns_dict():
    import update_manager
    with patch.object(update_manager, "_is_offline", return_value=False):
        status = update_manager.get_status()
        assert "version" in status
        assert "mode" in status


def test_get_history_empty(tmp_path):
    import update_manager
    with patch.object(update_manager, "_MANIFEST_FILE", tmp_path / "nope.json"):
        assert update_manager.get_history() == []


def test_record_history(tmp_path):
    manifest_file = tmp_path / "manifest.json"
    import update_manager
    with patch.object(update_manager, "_MANIFEST_FILE", manifest_file), \
         patch.object(update_manager, "_DIST_DIR", tmp_path):
        update_manager._record_history("git", tag="v0.400.01b")
        history = update_manager.get_history()
        assert len(history) == 1
        assert history[0]["mode"] == "git"
        assert history[0]["tag"] == "v0.400.01b"


def test_get_repo_info_defaults():
    import update_manager
    with patch.object(update_manager, "_load_fleet_config", return_value={}):
        owner, repo = update_manager._get_repo_info()
        assert owner == "SwiftWing21"
        assert repo == "BigEd"


def test_cancel_update_sets_event():
    import update_manager
    update_manager._apply_cancel.clear()
    update_manager.cancel_update()
    assert update_manager._apply_cancel.is_set()
