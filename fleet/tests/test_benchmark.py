"""Tests for Gemma 4 benchmark infrastructure."""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import json

# Point at a temp DB for test isolation
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FLEET_TEST_DB"] = _tmp.name

import db


class TestBenchmarksTable(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_benchmarks_table_exists(self):
        with db.get_conn() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmarks'"
            )
            self.assertIsNotNone(cur.fetchone(), "benchmarks table should exist")

    def test_insert_and_query_benchmark(self):
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO benchmarks
                   (model, variant, metric, value, unit, judge_model, kv_cache_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("gemma4:e4b", "e4b", "tokens_per_sec", 42.5, "tok/s", "claude-haiku-4-5", "q8_0"),
            )
            row = conn.execute("SELECT * FROM benchmarks WHERE model = ?", ("gemma4:e4b",)).fetchone()
            self.assertIsNotNone(row)

    def tearDown(self):
        try:
            os.unlink(os.environ["FLEET_TEST_DB"])
        except Exception:
            pass


class TestHwSupervisorModelSizes(unittest.TestCase):
    @patch("hw_supervisor.open", create=True)
    def test_model_sizes_populated_from_fleet_toml(self, mock_open):
        import hw_supervisor
        # Simulate fleet.toml with gemma4 variants
        fake_toml = {
            "models": {
                "gemma4": {
                    "variants": {
                        "e4b": {"vram_estimate_gb": 7},
                        "31b": {"vram_estimate_gb": 20},
                    }
                }
            }
        }
        sizes = hw_supervisor._build_model_sizes(fake_toml)
        self.assertEqual(sizes["gemma4:e4b"], 7.0)
        self.assertEqual(sizes["gemma4:31b"], 20.0)
        # Hardcoded models still present
        self.assertIn("qwen3:8b", sizes)

    def test_unknown_model_falls_back_to_default(self):
        import hw_supervisor
        sizes = hw_supervisor._build_model_sizes({})
        self.assertEqual(sizes.get("unknown:model", 4.0), 4.0)


class TestEnsureModelAvailable(unittest.TestCase):
    @patch("skills.model_suite.urllib.request.urlopen")
    def test_model_already_installed(self, mock_urlopen):
        from skills.model_suite import ensure_model_available
        # Mock /api/version then /api/tags (called in sequence)
        version_resp = MagicMock()
        version_resp.read.return_value = json.dumps({"version": "0.20.0"}).encode()
        version_resp.__enter__ = lambda s: s
        version_resp.__exit__ = MagicMock(return_value=False)
        tags_resp = MagicMock()
        tags_resp.read.return_value = json.dumps(
            {"models": [{"name": "gemma4:e4b"}]}
        ).encode()
        tags_resp.__enter__ = lambda s: s
        tags_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [version_resp, tags_resp]
        result = ensure_model_available("gemma4:e4b", host="http://localhost:11434")
        self.assertEqual(result["status"], "ready")

    @patch("skills.model_suite._pull_model")
    @patch("skills.model_suite._get_installed")
    def test_model_not_installed_triggers_pull(self, mock_installed, mock_pull):
        from skills.model_suite import ensure_model_available
        mock_installed.return_value = ["qwen3:8b"]
        mock_pull.return_value = {"status": "installed", "model": "gemma4:e4b"}
        result = ensure_model_available("gemma4:e4b", host="http://localhost:11434")
        mock_pull.assert_called_once_with("gemma4:e4b", "http://localhost:11434")
        self.assertEqual(result["status"], "installed")

    @patch("skills.model_suite.urllib.request.urlopen")
    def test_ollama_version_check_warns_below_020(self, mock_urlopen):
        from skills.model_suite import ensure_model_available
        # First call: /api/version, second: /api/tags
        version_resp = MagicMock()
        version_resp.read.return_value = json.dumps({"version": "0.19.0"}).encode()
        version_resp.__enter__ = lambda s: s
        version_resp.__exit__ = MagicMock(return_value=False)
        tags_resp = MagicMock()
        tags_resp.read.return_value = json.dumps(
            {"models": [{"name": "gemma4:e4b"}]}
        ).encode()
        tags_resp.__enter__ = lambda s: s
        tags_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [version_resp, tags_resp]
        result = ensure_model_available("gemma4:e4b", host="http://localhost:11434")
        self.assertIn("warning", result)
        self.assertIn("0.20.0", result["warning"])


class TestBenchmarkSkill(unittest.TestCase):
    @patch("skills.benchmark_model._run_prompt")
    @patch("skills.model_suite.ensure_model_available")
    def test_benchmark_single_model(self, mock_ensure, mock_run):
        mock_ensure.return_value = {"status": "ready"}
        mock_run.return_value = {
            "response": "test output",
            "eval_count": 50,
            "eval_duration": 1_000_000_000,  # 1 second
            "prompt_eval_count": 20,
        }
        from skills.benchmark_model import run_benchmark
        results = run_benchmark(
            model="gemma4:e4b",
            prompt_category="coding",
            host="http://localhost:11434",
            kv_cache_type="q8_0",
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["model"], "gemma4:e4b")
        self.assertEqual(results[0]["kv_cache_type"], "q8_0")
        self.assertIn("tokens_per_sec", [r["metric"] for r in results])


class TestPartialOffload(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_num_gpu_passed_in_options(self, mock_urlopen):
        """When a model has num_gpu_layers != -1, num_gpu should appear in options."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": "test response",
            "eval_count": 10, "eval_duration": 1000000000,
            "prompt_eval_count": 5,
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from providers import _call_local
        config = {
            "models": {
                "local": "gemma4:31b",
                "complex": "gemma4:31b",
                "ollama_host": "http://localhost:11434",
                "gemma4": {"variants": {"31b": {
                    "vram_estimate_gb": 20,
                    "num_gpu_layers": 24,
                    "context_length": 8192,
                }}},
            },
        }
        # Use skill_name="unknown" to bypass get_local_model_for_skill routing
        _call_local("system", "user", config["models"], max_tokens=100,
                     skill_name="unknown", config=config)

        # Inspect the request body sent to Ollama
        call_args = mock_urlopen.call_args
        req = call_args[0][0]  # urllib.request.Request object
        body = json.loads(req.data)
        self.assertEqual(body["options"]["num_gpu"], 24)

    @patch("urllib.request.urlopen")
    def test_no_num_gpu_when_full_offload(self, mock_urlopen):
        """When num_gpu_layers is -1, num_gpu should NOT be in options."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": "test response",
            "eval_count": 10, "eval_duration": 1000000000,
            "prompt_eval_count": 5,
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from providers import _call_local
        config = {
            "models": {
                "local": "gemma4:e4b",
                "complex": "gemma4:e4b",
                "ollama_host": "http://localhost:11434",
                "gemma4": {"variants": {"e4b": {
                    "vram_estimate_gb": 7,
                    "num_gpu_layers": -1,
                    "context_length": 8192,
                }}},
            },
        }
        _call_local("system", "user", config["models"], max_tokens=100,
                     skill_name="unknown", config=config)

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data)
        self.assertNotIn("num_gpu", body["options"])


class TestBenchmarkCLI(unittest.TestCase):
    @patch("skills.benchmark_model.run_benchmark")
    @patch("skills.benchmark_model.save_results")
    def test_cmd_benchmark_single_model(self, mock_save, mock_run):
        mock_run.return_value = [{"model": "gemma4:e4b", "metric": "tokens_per_sec",
                                   "value": 42.5, "unit": "tok/s",
                                   "variant": "e4b", "kv_cache_type": "q8_0"}]
        mock_save.return_value = 1
        from lead_client import cmd_benchmark
        import argparse
        args = argparse.Namespace(
            model="gemma4:e4b", suite=None, compare=None,
            category="coding", kv_cache_type="q8_0",
        )
        # Should not raise
        cmd_benchmark(args)
        mock_run.assert_called_once()


class TestContextOverflow(unittest.TestCase):
    def test_truncation_when_exceeding_context_length(self):
        from skills._models import _estimate_tokens, _truncate_for_context
        # Simulate a long input
        system = "You are a helpful assistant."
        user = " ".join(["word"] * 10000)  # ~10000 words -> ~13000 estimated tokens
        estimated = _estimate_tokens(system, user, skill_name="analysis")
        self.assertGreater(estimated, 8192)

        truncated_system, truncated_user, was_truncated = _truncate_for_context(
            system, user, context_length=8192, skill_name="analysis"
        )
        self.assertTrue(was_truncated)
        new_est = _estimate_tokens(truncated_system, truncated_user, skill_name="analysis")
        self.assertLessEqual(new_est, 8192)
        # System prompt preserved
        self.assertEqual(truncated_system, system)

    def test_no_truncation_when_within_limit(self):
        from skills._models import _estimate_tokens, _truncate_for_context
        system = "You are a helpful assistant."
        user = "Short prompt."
        _, _, was_truncated = _truncate_for_context(
            system, user, context_length=8192, skill_name="analysis"
        )
        self.assertFalse(was_truncated)

    def test_estimation_uses_1_3x_default(self):
        from skills._models import _estimate_tokens
        system = "sys"  # 1 word
        user = "a b c d e f g h i j"  # 10 words
        est = _estimate_tokens(system, user, skill_name="analysis")
        # 11 words * 1.3 = 14.3, int() truncates -> 14
        self.assertEqual(est, 14)


class TestBenchmarkEndpoint(unittest.TestCase):
    def setUp(self):
        db.init_db()
        # Insert test data
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO benchmarks
                   (model, variant, metric, value, unit, judge_model, kv_cache_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("gemma4:e4b", "e4b", "tokens_per_sec", 42.5, "tok/s", "", "q8_0"),
            )

    def test_compare_endpoint_returns_json(self):
        from dashboard import app
        with app.test_client() as client:
            resp = client.get("/api/benchmarks/compare?models=gemma4:e4b")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIsInstance(data, list)
            self.assertGreater(len(data), 0)

    def tearDown(self):
        try:
            os.unlink(os.environ["FLEET_TEST_DB"])
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
