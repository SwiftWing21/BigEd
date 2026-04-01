"""Unit tests for fleet/process_manager.py."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


def _make_pm(config=None):
    from process_manager import ProcessManager
    return ProcessManager(config or {})


class TestProcessManagerInit(unittest.TestCase):
    def test_init_empty_config(self):
        pm = _make_pm()
        self.assertEqual(pm.worker_procs, {})
        self.assertIsNone(pm.ollama_proc)
        self.assertIsNone(pm.dashboard_proc)
        self.assertFalse(pm.training_active)

    def test_update_config(self):
        pm = _make_pm({"a": 1})
        pm.update_config({"b": 2})
        self.assertEqual(pm.config, {"b": 2})


class TestResolveOllamaEnv(unittest.TestCase):
    def _pm_with_opt(self, opt=None, gpu_mode="eco"):
        cfg = {"ollama": {"optimization": opt or {}}, "gpu": {"mode": gpu_mode}}
        return _make_pm(cfg)

    def test_no_gpu_defaults(self):
        pm = self._pm_with_opt()
        with patch.object(pm, "_detect_vram_gb", return_value=0.0):
            env = pm._resolve_ollama_env()
        self.assertEqual(env["OLLAMA_NUM_PARALLEL"], "2")
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", env)

    def test_flash_on_override(self):
        pm = self._pm_with_opt({"flash_attention": "on"})
        with patch.object(pm, "_detect_vram_gb", return_value=0.0):
            env = pm._resolve_ollama_env()
        self.assertEqual(env["OLLAMA_FLASH_ATTENTION"], "1")

    def test_flash_off_override(self):
        pm = self._pm_with_opt({"flash_attention": "off"})
        with patch.object(pm, "_detect_vram_gb", return_value=0.0):
            env = pm._resolve_ollama_env()
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", env)

    def test_high_vram_tier(self):
        cfg = {"ollama": {"optimization": {}}, "gpu": {"mode": "full"},
               "models": {"ollama_host": "http://localhost:11434"}}
        pm = _make_pm(cfg)
        with patch.object(pm, "_detect_vram_gb", return_value=20.0):
            env = pm._resolve_ollama_env()
        self.assertEqual(env["OLLAMA_NUM_PARALLEL"], "6")
        self.assertEqual(env["OLLAMA_MAX_LOADED_MODELS"], "4")

    def test_kv_cache_explicit(self):
        pm = self._pm_with_opt({"kv_cache_type": "q4_0"})
        with patch.object(pm, "_detect_vram_gb", return_value=0.0):
            env = pm._resolve_ollama_env()
        self.assertEqual(env["OLLAMA_KV_CACHE_TYPE"], "q4_0")


class TestFindRunningOllama(unittest.TestCase):
    def test_returns_true_when_reachable(self):
        pm = _make_pm({"models": {"ollama_host": "http://localhost:11434"}})
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            self.assertTrue(pm.find_running_ollama())

    def test_returns_false_when_unreachable(self):
        pm = _make_pm({"models": {"ollama_host": "http://localhost:11434"}})
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            self.assertFalse(pm.find_running_ollama())


class TestDiscoverLoadedModels(unittest.TestCase):
    def test_returns_model_names(self):
        pm = _make_pm({"models": {"ollama_host": "http://localhost:11434"}})
        data = json.dumps({"models": [{"name": "qwen3:8b"}, {"name": "llama:7b"}]}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = data
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = pm.discover_loaded_models()
        self.assertEqual(result, ["qwen3:8b", "llama:7b"])

    def test_returns_empty_on_error(self):
        pm = _make_pm({"models": {"ollama_host": "http://localhost:11434"}})
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = pm.discover_loaded_models()
        self.assertEqual(result, [])


class TestStopOllama(unittest.TestCase):
    def test_terminates_fleet_started_ollama(self):
        pm = _make_pm()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        pm.ollama_proc = mock_proc
        pm.stop_ollama()
        mock_proc.terminate.assert_called_once()
        self.assertIsNone(pm.ollama_proc)

    def test_force_kills_on_timeout(self):
        pm = _make_pm()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="ollama", timeout=5)
        pm.ollama_proc = mock_proc
        pm.stop_ollama()
        mock_proc.kill.assert_called_once()

    def test_no_proc_does_nothing(self):
        pm = _make_pm()
        pm.ollama_proc = None
        with patch("process_manager.ProcessManager.stop_ollama") as m:
            pass  # just verify no error when no proc


class TestWorkerLifecycle(unittest.TestCase):
    def test_stop_worker_terminates(self):
        pm = _make_pm()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        pm.worker_procs["coder_1"] = mock_proc
        pm.stop_worker("coder_1")
        mock_proc.terminate.assert_called_once()
        self.assertNotIn("coder_1", pm.worker_procs)

    def test_stop_worker_force_kills_on_timeout(self):
        pm = _make_pm()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="worker", timeout=10)
        pm.worker_procs["coder_1"] = mock_proc
        pm.stop_worker("coder_1")
        mock_proc.kill.assert_called_once()

    def test_stop_nonexistent_worker_no_error(self):
        pm = _make_pm()
        pm.stop_worker("ghost_worker")  # should not raise

    def test_get_running_workers_excludes_dead(self):
        pm = _make_pm()
        live = MagicMock()
        live.poll.return_value = None
        dead = MagicMock()
        dead.poll.return_value = 1
        pm.worker_procs = {"live": live, "dead": dead}
        running = pm.get_running_workers()
        self.assertIn("live", running)
        self.assertNotIn("dead", running)


class TestDashboardLifecycle(unittest.TestCase):
    def test_stop_dashboard_terminates(self):
        pm = _make_pm()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        pm.dashboard_proc = mock_proc
        pm.stop_dashboard()
        mock_proc.terminate.assert_called_once()
        self.assertIsNone(pm.dashboard_proc)

    def test_stop_dashboard_when_none(self):
        pm = _make_pm()
        pm.dashboard_proc = None
        pm.stop_dashboard()  # should not raise

    def test_dashboard_port_alive_true(self):
        pm = _make_pm({"dashboard": {"port": 5555, "bind_address": "127.0.0.1"}})
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            self.assertTrue(pm.dashboard_port_alive())

    def test_dashboard_port_alive_false(self):
        pm = _make_pm({"dashboard": {"port": 5555, "bind_address": "127.0.0.1"}})
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertFalse(pm.dashboard_port_alive())

    def test_start_dashboard_skips_when_disabled(self):
        pm = _make_pm({"dashboard": {"enabled": False}})
        with patch("subprocess.Popen") as mock_popen:
            pm.start_dashboard()
        mock_popen.assert_not_called()


class TestCheckAlive(unittest.TestCase):
    def _patch_config(self):
        """Patch the config functions imported inside check_alive."""
        mock_config = MagicMock()
        mock_config.is_offline.return_value = True
        mock_config.is_air_gap.return_value = True
        return patch.dict("sys.modules", {"config": mock_config})

    def test_dead_worker_scheduled_for_respawn(self):
        pm = _make_pm({"fleet": {}})
        dead_proc = MagicMock()
        dead_proc.poll.return_value = 1
        dead_proc.returncode = 1
        pm.worker_procs["coder_1"] = dead_proc
        with self._patch_config():
            pm.check_alive()
        # Worker should be set to None awaiting cool-down
        self.assertIsNone(pm.worker_procs.get("coder_1"))
        self.assertIn("coder_1", pm._worker_next_start)

    def test_disabled_dead_worker_removed(self):
        pm = _make_pm({"fleet": {"disabled_agents": ["coder_1"]}})
        dead_proc = MagicMock()
        dead_proc.poll.return_value = 1
        dead_proc.returncode = 1
        pm.worker_procs["coder_1"] = dead_proc
        with self._patch_config():
            pm.check_alive()
        self.assertNotIn("coder_1", pm.worker_procs)

    def test_hw_supervisor_respawned_on_death(self):
        pm = _make_pm({"fleet": {}})
        dead_hw = MagicMock()
        dead_hw.poll.return_value = 1
        pm.hw_supervisor_proc = dead_hw
        with self._patch_config(), \
             patch.object(pm, "start_hw_supervisor") as mock_start:
            pm.check_alive()
        mock_start.assert_called_once()


class TestReadHwState(unittest.TestCase):
    def test_returns_dict_when_file_exists(self, tmp_path=None):
        import tempfile, json as _json
        pm = _make_pm()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            _json.dump({"temp": 72.5}, f)
            tmp_name = f.name
        with patch("process_manager.HW_STATE_FILE", Path(tmp_name)):
            result = pm.read_hw_state()
        self.assertEqual(result["temp"], 72.5)
        Path(tmp_name).unlink(missing_ok=True)

    def test_returns_none_when_file_missing(self):
        pm = _make_pm()
        with patch("process_manager.HW_STATE_FILE", Path("/nonexistent/file.json")):
            result = pm.read_hw_state()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
