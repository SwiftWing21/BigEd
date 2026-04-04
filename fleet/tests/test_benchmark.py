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


if __name__ == "__main__":
    unittest.main()
