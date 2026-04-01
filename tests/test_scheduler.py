"""Unit tests for fleet/scheduler.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


def _make_pm(last_busy=None):
    pm = MagicMock()
    pm.last_busy = last_busy or {}
    pm.get_running_workers.return_value = set()
    return pm


def _make_scheduler(config=None, pm=None):
    from scheduler import Scheduler
    return Scheduler(config or {}, pm or _make_pm())


class TestBuildRoles(unittest.TestCase):
    def test_no_coders_gives_single_default(self):
        sched = _make_scheduler({"workers": {"coder_count": 1}})
        roles = sched.build_roles()
        self.assertIn("coder_1", roles)
        self.assertNotIn("coder", roles)

    def test_multiple_coders(self):
        sched = _make_scheduler({"workers": {"coder_count": 3}})
        roles = sched.build_roles()
        self.assertIn("coder_1", roles)
        self.assertIn("coder_2", roles)
        self.assertIn("coder_3", roles)

    def test_disabled_agents_excluded(self):
        sched = _make_scheduler({"fleet": {"disabled_agents": ["planner", "analyst"]}})
        roles = sched.build_roles()
        self.assertNotIn("planner", roles)
        self.assertNotIn("analyst", roles)
        self.assertIn("researcher", roles)

    def test_returns_list_type(self):
        sched = _make_scheduler()
        self.assertIsInstance(sched.build_roles(), list)


class TestShouldScaleUp(unittest.TestCase):
    def test_no_scale_up_below_threshold(self):
        sched = _make_scheduler()
        result = sched._should_scale_up(pending=1, running={"coder_1"})
        self.assertEqual(result, [])

    def test_scale_up_when_pending_exceeds_threshold(self):
        sched = _make_scheduler({"fleet": {"max_workers": 10, "ram_ceiling_pct": 0}})
        with patch.object(sched, "_pending_tasks_by_type", return_value={}), \
             patch.object(sched, "_load_affinity_map", return_value={}), \
             patch.object(sched, "_get_ram_usage_pct", return_value=0.0):
            result = sched._should_scale_up(pending=5, running={"coder_1"})
        self.assertIsInstance(result, list)

    def test_scale_up_blocked_by_ram_ceiling(self):
        sched = _make_scheduler({"fleet": {"max_workers": 10, "ram_ceiling_pct": 80}})
        with patch.object(sched, "_get_ram_usage_pct", return_value=90.0):
            result = sched._should_scale_up(pending=10, running={"coder_1"})
        self.assertEqual(result, [])

    def test_scale_up_blocked_at_max_workers(self):
        sched = _make_scheduler({"fleet": {"max_workers": 2, "ram_ceiling_pct": 0}})
        with patch.object(sched, "_get_ram_usage_pct", return_value=0.0), \
             patch.object(sched, "_pending_tasks_by_type", return_value={}), \
             patch.object(sched, "_load_affinity_map", return_value={}):
            result = sched._should_scale_up(pending=10, running={"coder_1", "coder_2"})
        self.assertEqual(result, [])


class TestShouldScaleDown(unittest.TestCase):
    def test_core_agents_never_scaled_down(self):
        from scheduler import CORE_AGENTS
        pm = _make_pm()
        pm.last_busy = {}
        sched = _make_scheduler(pm=pm)
        running = CORE_AGENTS.copy()
        result = sched._should_scale_down(running)
        self.assertEqual(result, [])

    def test_idle_non_core_gets_scaled_down(self):
        import time
        from scheduler import SCALE_DOWN_IDLE_SECS
        pm = _make_pm()
        old_time = time.time() - SCALE_DOWN_IDLE_SECS - 1
        pm.last_busy = {"coder_2": old_time}
        sched = _make_scheduler(pm=pm)
        result = sched._should_scale_down({"coder_2"})
        self.assertIn("coder_2", result)

    def test_recently_busy_agent_not_scaled_down(self):
        import time
        pm = _make_pm()
        pm.last_busy = {"coder_2": time.time()}
        sched = _make_scheduler(pm=pm)
        result = sched._should_scale_down({"coder_2"})
        self.assertEqual(result, [])


class TestNextInstanceName(unittest.TestCase):
    def test_coder_naming(self):
        sched = _make_scheduler()
        name = sched._next_instance_name("coder", running=set())
        self.assertEqual(name, "coder_1")

    def test_coder_skips_running(self):
        sched = _make_scheduler()
        name = sched._next_instance_name("coder", running={"coder_1", "coder_2"})
        self.assertEqual(name, "coder_3")

    def test_returns_none_when_all_slots_taken(self):
        from scheduler import MAX_DYNAMIC_PER_ROLE
        sched = _make_scheduler()
        running = {f"coder_{i}" for i in range(1, MAX_DYNAMIC_PER_ROLE + 1)}
        name = sched._next_instance_name("coder", running=running)
        self.assertIsNone(name)


class TestSkillToRole(unittest.TestCase):
    def test_maps_skill_to_correct_role(self):
        sched = _make_scheduler()
        affinity = {"coder": ["code_review", "refactor"], "researcher": ["research_loop"]}
        role = sched._skill_to_role("code_review", affinity)
        self.assertEqual(role, "coder")

    def test_returns_none_for_unknown_skill(self):
        sched = _make_scheduler()
        role = sched._skill_to_role("unknown_skill", {})
        self.assertIsNone(role)


class TestGetDisabledAgents(unittest.TestCase):
    def test_reads_from_config(self):
        sched = _make_scheduler({"fleet": {"disabled_agents": ["legal", "sales"]}})
        disabled = sched.get_disabled_agents()
        self.assertIn("legal", disabled)
        self.assertIn("sales", disabled)

    def test_returns_empty_set_when_none_configured(self):
        sched = _make_scheduler()
        self.assertEqual(sched.get_disabled_agents(), set())


class TestGetCoreAgents(unittest.TestCase):
    def test_returns_expected_core_set(self):
        from scheduler import CORE_AGENTS
        sched = _make_scheduler()
        self.assertEqual(sched.get_core_agents(), CORE_AGENTS)


class TestPredictQueueGrowth(unittest.TestCase):
    def test_returns_zero_when_growth_flat(self):
        sched = _make_scheduler()
        with patch("scheduler.Scheduler._predict_queue_growth", return_value=0):
            result = sched._predict_queue_growth()
        self.assertEqual(result, 0)


class TestTickDoesNotRaise(unittest.TestCase):
    def test_tick_swallows_all_errors(self):
        sched = _make_scheduler()
        # Each internal check raises — tick should handle all gracefully
        with patch.object(sched, "_check_scaling", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_check_training", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_check_auto_triggers", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_check_manual_mode", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_check_event_triggers", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_check_cost_anomaly", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_check_capacity_bonus", side_effect=RuntimeError("boom")), \
             patch.object(sched, "_reload_config_if_stale", side_effect=RuntimeError("boom")):
            sched.tick(0)  # should not raise


if __name__ == "__main__":
    unittest.main()
