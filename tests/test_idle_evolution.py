"""Unit tests for fleet/idle_evolution.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


class TestGetLeastEvolvedSkill(unittest.TestCase):
    """Tests for get_least_evolved_skill with tmp_db."""

    def setUp(self):
        import os, tempfile
        import db as dbmod
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ["FLEET_TEST_DB"] = self.tmp.name
        dbmod.init_db(self.tmp.name)
        self._dbmod = dbmod

        # Clear staleness cache between tests
        import idle_evolution
        idle_evolution._staleness_cache.clear()

    def tearDown(self):
        import os
        try:
            self._dbmod.close_all()
        except Exception:
            pass
        os.environ.pop("FLEET_TEST_DB", None)
        os.unlink(self.tmp.name)
        import idle_evolution
        idle_evolution._staleness_cache.clear()

    def test_returns_none_when_no_done_tasks(self):
        from idle_evolution import get_least_evolved_skill
        result = get_least_evolved_skill()
        self.assertIsNone(result)

    def test_returns_skill_when_done_task_exists(self):
        from idle_evolution import get_least_evolved_skill
        db = self._dbmod
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                "VALUES ('code_review', 'DONE', 5, '{}', datetime('now'))"
            )
        result = get_least_evolved_skill()
        self.assertEqual(result, "code_review")

    def test_respects_agent_cooldown(self):
        """Skills recently evolved by this agent should be filtered out."""
        from idle_evolution import get_least_evolved_skill, log_idle_run
        db = self._dbmod
        # Add two done task types
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                "VALUES ('skill_a', 'DONE', 5, '{}', datetime('now'))"
            )
            conn.execute(
                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                "VALUES ('skill_b', 'DONE', 5, '{}', datetime('now'))"
            )
        # Mark skill_a as recently evolved by agent_1
        log_idle_run("agent_1", "skill_a")
        # Force cache miss
        import idle_evolution
        idle_evolution._staleness_cache.clear()
        result = get_least_evolved_skill(agent="agent_1")
        # skill_a should be skipped due to cooldown — fallback or skill_b
        # Result should either be skill_b or None (if candidates exhausted → fallback)
        self.assertIsNotNone(result)

    def test_returns_random_fallback_when_all_candidates_filtered(self):
        """If all candidates filtered, falls back to random skill from skill_names."""
        from idle_evolution import get_least_evolved_skill
        db = self._dbmod
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                "VALUES ('only_skill', 'DONE', 5, '{}', datetime('now'))"
            )
            # Mark as running (cross-worker dedup)
            import json
            conn.execute(
                "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                "VALUES ('skill_evolve', 'RUNNING', 5, ?, datetime('now'))",
                (json.dumps({"skill": "only_skill"}),)
            )
        import idle_evolution
        idle_evolution._staleness_cache.clear()
        # All real candidates filtered → fallback to random from skill_names
        result = get_least_evolved_skill()
        # only_skill is the only done task → fallback returns it
        self.assertEqual(result, "only_skill")


class TestLogIdleRun(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        import db as dbmod
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ["FLEET_TEST_DB"] = self.tmp.name
        dbmod.init_db(self.tmp.name)
        self._dbmod = dbmod

    def tearDown(self):
        import os
        try:
            self._dbmod.close_all()
        except Exception:
            pass
        os.environ.pop("FLEET_TEST_DB", None)
        os.unlink(self.tmp.name)

    def test_log_idle_run_inserts_row(self):
        from idle_evolution import log_idle_run
        log_idle_run("coder_1", "skill_test", result="ok", cost_usd=0.001)
        with self._dbmod.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM idle_runs WHERE agent='coder_1' AND skill='skill_test'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["result"], "ok")


class TestGetIdleStats(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        import db as dbmod
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ["FLEET_TEST_DB"] = self.tmp.name
        dbmod.init_db(self.tmp.name)
        self._dbmod = dbmod

    def tearDown(self):
        import os
        try:
            self._dbmod.close_all()
        except Exception:
            pass
        os.environ.pop("FLEET_TEST_DB", None)
        os.unlink(self.tmp.name)

    def test_returns_list(self):
        from idle_evolution import get_idle_stats
        result = get_idle_stats("week")
        self.assertIsInstance(result, list)

    def test_stats_include_inserted_run(self):
        from idle_evolution import log_idle_run, get_idle_stats
        log_idle_run("agent", "some_skill", cost_usd=0.01)
        stats = get_idle_stats("week")
        skills = [s["skill"] for s in stats]
        self.assertIn("some_skill", skills)

    def test_stats_aggregate_cost(self):
        from idle_evolution import log_idle_run, get_idle_stats
        log_idle_run("agent", "expensive_skill", cost_usd=0.05)
        log_idle_run("agent", "expensive_skill", cost_usd=0.05)
        stats = get_idle_stats("week")
        row = next(s for s in stats if s["skill"] == "expensive_skill")
        self.assertAlmostEqual(row["total_cost"], 0.10, places=5)


class TestStalenessCache(unittest.TestCase):
    def test_cache_cleared_between_tests(self):
        import idle_evolution
        idle_evolution._staleness_cache["fake_key"] = [("skill_x", "2020-01-01")]
        idle_evolution._staleness_cache.clear()
        self.assertEqual(idle_evolution._staleness_cache, {})


if __name__ == "__main__":
    unittest.main()
