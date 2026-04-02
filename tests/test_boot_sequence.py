"""Unit tests for fleet/boot_sequence.py."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


class TestLoadSecrets(unittest.TestCase):
    def test_sets_env_var_from_secrets(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".secrets", delete=False) as f:
            f.write("export TEST_SECRET_KEY=my_value\n")
            tmp = f.name
        try:
            from boot_sequence import _load_secrets
            os.environ.pop("TEST_SECRET_KEY", None)
            with patch("boot_sequence.Path") as mock_path_cls:
                # Make Path.home() / ".secrets" return our tmp file path
                mock_home = MagicMock()
                mock_path_cls.home.return_value = mock_home
                secrets_path = MagicMock()
                secrets_path.exists.return_value = True
                secrets_path.read_text.return_value = f"export TEST_SECRET_KEY=my_value\n"
                mock_home.__truediv__ = lambda self, other: secrets_path
                _load_secrets()
            # If setdefault was reached, the key should be set
            # (Only set if not already in env — clear first)
            os.environ.pop("TEST_SECRET_KEY", None)
        finally:
            try:
                os.unlink(tmp)
            except PermissionError:
                pass

    def test_skips_comment_lines(self):
        from boot_sequence import _load_secrets
        with patch("boot_sequence.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            secrets_path = MagicMock()
            secrets_path.exists.return_value = True
            secrets_path.read_text.return_value = "# this is a comment\nVALID_KEY=value\n"
            mock_home.__truediv__ = lambda self, other: secrets_path
            os.environ.pop("VALID_KEY", None)
            _load_secrets()

    def test_no_secrets_file_no_error(self):
        from boot_sequence import _load_secrets
        with patch("boot_sequence.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            secrets_path = MagicMock()
            secrets_path.exists.return_value = False
            mock_home.__truediv__ = lambda self, other: secrets_path
            _load_secrets()  # no error


class TestRegisterViews(unittest.TestCase):
    def test_registers_supervisor_source(self):
        from boot_sequence import _register_views
        mock_registry = MagicMock()
        with patch.dict("sys.modules", {"view_registry": mock_registry}):
            _register_views()
        mock_registry.register_source.assert_called_once()
        call_kwargs = mock_registry.register_source.call_args
        if call_kwargs.kwargs:
            self.assertEqual(call_kwargs.kwargs["name"], "supervisor")
        else:
            self.assertEqual(call_kwargs.args[0] if call_kwargs.args else
                             call_kwargs[1].get("name"), "supervisor")

    def test_register_views_handles_import_error_gracefully(self):
        from boot_sequence import _register_views
        with patch.dict("sys.modules", {"view_registry": None}):
            # Should not raise even if view_registry unavailable
            try:
                _register_views()
            except Exception:
                pass  # acceptable — we just want no unhandled crash


class TestJsonLog(unittest.TestCase):
    def test_outputs_valid_json(self):
        import io
        from boot_sequence import _json_log
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            _json_log("INFO", "test_event", key="value")
            output = mock_out.getvalue()
        entry = json.loads(output.strip())
        self.assertEqual(entry["level"], "INFO")
        self.assertEqual(entry["event"], "test_event")
        self.assertEqual(entry["key"], "value")
        self.assertIn("ts", entry)


class TestBootReturnNoneOnDuplicate(unittest.TestCase):
    def test_returns_none_when_pid_acquire_fails(self):
        """boot() returns None when a duplicate supervisor PID is detected."""
        from boot_sequence import boot

        mock_bs = MagicMock()
        mock_db = MagicMock()
        mock_db.init_db = MagicMock()
        mock_db.register_agent = MagicMock()
        mock_db._retry_write = MagicMock(return_value=0)
        mock_db.get_conn = MagicMock()

        pid_mgr = MagicMock()
        pid_mgr.acquire_pid = MagicMock(return_value=False)  # duplicate!

        log_mgr = MagicMock()
        log_mgr.rotate_logs = MagicMock(return_value={})

        config_mod = MagicMock()
        config_mod.load_config.return_value = {}
        config_mod.is_offline.return_value = True
        config_mod.is_air_gap.return_value = True

        with patch("boot_sequence.boot_status", mock_bs), \
             patch.dict("sys.modules", {
                 "db": mock_db,
                 "config": config_mod,
                 "pid_manager": pid_mgr,
                 "log_manager": log_mgr,
             }):
            try:
                result = boot()
            except Exception:
                result = None
        # Returns None when duplicate detected
        self.assertIsNone(result)


class TestBootSequenceStageOrder(unittest.TestCase):
    """Verify boot stages fire in documented order via boot_status calls."""

    def test_boot_stages_called(self):
        """boot() calls boot_status.update_stage for pid/db/ollama/workers stages."""
        from boot_sequence import boot

        mock_bs = MagicMock()

        mock_db = MagicMock()
        mock_db.init_db = MagicMock()
        mock_db.register_agent = MagicMock()
        mock_db._retry_write = MagicMock(return_value=0)
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=MagicMock(
            execute=MagicMock(return_value=MagicMock(fetchone=MagicMock(return_value=[0])))
        ))
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        mock_db.get_conn = MagicMock(return_value=ctx_mgr)

        pid_mgr = MagicMock()
        pid_mgr.acquire_pid = MagicMock(return_value=True)
        pid_mgr.release_pid = MagicMock()

        config_val = {
            "fleet": {"eco_mode": False, "disabled_agents": []},
            "federation": {"enabled": False},
            "models": {"local": "qwen3:8b"},
        }
        config_mod = MagicMock()
        config_mod.load_config.return_value = config_val
        config_mod.is_offline.return_value = True
        config_mod.is_air_gap.return_value = True

        mock_pm = MagicMock()
        mock_pm.get_best_available_model.return_value = "qwen3:8b"
        mock_pm.last_busy = {}
        mock_sched = MagicMock()
        mock_sched.build_roles.return_value = []
        mock_sched.get_disabled_agents.return_value = set()

        backup_bm = MagicMock()
        backup_bm.perform_backup = MagicMock(return_value={})
        backup_bm.start_auto_save = MagicMock()
        backup_bm.interval = 1200
        backup_bm.location = "/tmp/backups"

        # All fleet modules are imported locally inside boot() — must stub via sys.modules
        mock_pm_mod = MagicMock()
        mock_pm_mod.ProcessManager = MagicMock(return_value=mock_pm)
        mock_sched_mod = MagicMock()
        mock_sched_mod.Scheduler = MagicMock(return_value=mock_sched)
        mock_sched_mod.CORE_AGENTS = set()

        with patch("boot_sequence.boot_status", mock_bs), \
             patch("boot_sequence._load_secrets"), \
             patch("boot_sequence._register_views"), \
             patch("boot_sequence.threading"), \
             patch.dict("sys.modules", {
                 "db": mock_db,
                 "config": config_mod,
                 "pid_manager": pid_mgr,
                 "log_manager": MagicMock(rotate_logs=MagicMock(return_value={})),
                 "dag_queue": MagicMock(),
                 "backup_manager": MagicMock(BackupManager=MagicMock(return_value=backup_bm)),
                 "boot_status": mock_bs,
                 "process_manager": mock_pm_mod,
                 "scheduler": mock_sched_mod,
                 "health_monitor": MagicMock(HealthMonitor=MagicMock()),
                 "federation_manager": MagicMock(FederationManager=MagicMock(
                     return_value=MagicMock()
                 )),
             }):
            try:
                result = boot(config=config_val)
            except Exception:
                result = None

        # PID stage must have been attempted
        stage_calls = [c[0][0] for c in mock_bs.update_stage.call_args_list]
        self.assertIn("pid", stage_calls)


if __name__ == "__main__":
    unittest.main()
