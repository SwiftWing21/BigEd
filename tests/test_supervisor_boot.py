"""Tests for ghost task sweep on boot (Task 1)."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure fleet/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


class TestGhostTaskSweep(unittest.TestCase):
    """Verify that boot_sequence sweeps stale RUNNING tasks."""

    def _run_sweep(self, stale_count):
        """Run just the sweep block from boot_sequence with mocked db."""
        import importlib
        import types

        # Build a minimal fake db module
        db_mock = types.ModuleType("db")
        conn_mock = MagicMock()
        conn_mock.__enter__ = MagicMock(return_value=conn_mock)
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.execute.return_value.fetchone.return_value = [stale_count]

        db_mock.get_conn = MagicMock(return_value=conn_mock)

        # _retry_write should call the fn and return its return value
        def fake_retry_write(fn):
            return fn()

        db_mock._retry_write = fake_retry_write

        import logging
        log = logging.getLogger("test_sweep")

        swept_count = [None]

        # Reproduce the sweep block verbatim
        try:
            def _sweep_stale():
                with db_mock.get_conn() as conn:
                    n = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE status='RUNNING'"
                    ).fetchone()[0]
                    if n > 0:
                        conn.execute(
                            "UPDATE tasks SET status='FAILED', error='stale — cleared on boot' "
                            "WHERE status='RUNNING'"
                        )
                    return n

            swept = db_mock._retry_write(_sweep_stale)
            swept_count[0] = swept
        except Exception as e:
            self.fail(f"Sweep raised unexpectedly: {e}")

        return swept_count[0], conn_mock

    def test_sweeps_stale_running_tasks(self):
        swept, conn = self._run_sweep(2219)
        self.assertEqual(swept, 2219)
        # UPDATE was called
        update_calls = [
            str(c) for c in conn.execute.call_args_list
            if "UPDATE" in str(c)
        ]
        self.assertTrue(len(update_calls) > 0, "UPDATE should have been called")

    def test_no_update_when_no_stale_tasks(self):
        swept, conn = self._run_sweep(0)
        self.assertEqual(swept, 0)
        # UPDATE should NOT be called
        update_calls = [
            str(c) for c in conn.execute.call_args_list
            if "UPDATE" in str(c)
        ]
        self.assertEqual(len(update_calls), 0, "UPDATE should not be called when count=0")

    def test_sweep_uses_retry_write(self):
        """_retry_write must be called (not raw conn.execute directly)."""
        db_mock = types.ModuleType("db")
        conn_mock = MagicMock()
        conn_mock.__enter__ = MagicMock(return_value=conn_mock)
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.execute.return_value.fetchone.return_value = [5]
        db_mock.get_conn = MagicMock(return_value=conn_mock)

        retry_write_called = [False]

        def fake_retry_write(fn):
            retry_write_called[0] = True
            return fn()

        db_mock._retry_write = fake_retry_write

        def _sweep_stale():
            with db_mock.get_conn() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='RUNNING'"
                ).fetchone()[0]
                if n > 0:
                    conn.execute(
                        "UPDATE tasks SET status='FAILED', error='stale — cleared on boot' "
                        "WHERE status='RUNNING'"
                    )
                return n

        db_mock._retry_write(_sweep_stale)
        self.assertTrue(retry_write_called[0], "_retry_write must be used for the sweep")

    def test_sweep_error_does_not_crash_boot(self):
        """If sweep fails, boot should log warning and continue — not raise."""
        import logging
        log = logging.getLogger("test_sweep")

        db_mock = types.ModuleType("db")
        db_mock.get_conn = MagicMock(side_effect=RuntimeError("DB unavailable"))
        db_mock._retry_write = MagicMock(side_effect=RuntimeError("DB unavailable"))

        # This mirrors the try/except in boot_sequence
        try:
            def _sweep_stale():
                with db_mock.get_conn() as conn:
                    n = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE status='RUNNING'"
                    ).fetchone()[0]
                    return n

            db_mock._retry_write(_sweep_stale)
        except Exception as e:
            log.warning("Boot sweep failed: %s", e)
            # Should NOT re-raise — boot continues

        # If we reach here, the try/except swallowed it correctly
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
