"""Unit tests for fleet/db.py — core data layer."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))


class TestInitDb:
    """init_db creates expected tables and is idempotent."""

    def test_creates_expected_tables(self, tmp_db):
        import db
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in rows}
        for expected in ("tasks", "messages", "usage", "agents", "notes", "locks"):
            assert expected in table_names, f"Missing table: {expected}"

    def test_idempotent(self, tmp_db):
        import db
        # Second call should not raise
        db.init_db(tmp_db)
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchall()
        assert len(rows) == 1


class TestGetConn:
    """get_conn returns a usable connection."""

    def test_returns_connection(self, tmp_db):
        import db
        conn = db.get_conn()
        assert conn is not None
        # Should be able to execute a query
        result = conn.execute("SELECT 1").fetchone()
        assert result is not None


class TestRetryWrite:
    """_retry_write succeeds on first try."""

    def test_succeeds_first_try(self, tmp_db):
        import db
        calls = []

        def work():
            calls.append(1)
            return 42

        result = db._retry_write(work)
        assert result == 42
        assert len(calls) == 1


class TestTasksSchema:
    """Key columns exist in the tasks table."""

    def test_key_columns(self, tmp_db):
        import db
        conn = db.get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        for col in ("id", "status", "type", "priority", "payload_json",
                     "result_json", "assigned_to", "parent_id", "depends_on"):
            assert col in cols, f"Missing column: {col}"


class TestPostAndClaimTask:
    """post_task + claim_task round-trip."""

    def test_round_trip(self, tmp_db):
        import db
        payload = json.dumps({"prompt": "hello"})
        task_id = db.post_task("summarize", payload, priority=5)
        assert task_id is not None
        assert isinstance(task_id, int)

        claimed = db.claim_task("worker_1")
        assert claimed is not None
        assert claimed["id"] == task_id
        assert claimed["type"] == "summarize"
        assert json.loads(claimed["payload_json"])["prompt"] == "hello"

    def test_claim_returns_none_when_empty(self, tmp_db):
        import db
        result = db.claim_task("worker_1")
        assert result is None

    def test_double_claim_prevention(self, tmp_db):
        import db
        db.post_task("web_search", json.dumps({"q": "test"}))
        first = db.claim_task("worker_1")
        assert first is not None
        # Second claim should get nothing — only one task exists
        second = db.claim_task("worker_2")
        assert second is None


class TestCompleteTask:
    """complete_task sets status to DONE."""

    def test_marks_done(self, tmp_db):
        import db
        task_id = db.post_task("summarize", json.dumps({"text": "hi"}))
        db.claim_task("worker_1")
        db.complete_task(task_id, json.dumps({"result": "summary"}))

        conn = db.get_conn()
        row = conn.execute(
            "SELECT status, result_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["status"] == "DONE"
        assert json.loads(row["result_json"])["result"] == "summary"


class TestMessages:
    """post_message + get_messages round-trip (via comms module)."""

    def test_round_trip(self, tmp_db):
        import db
        body = json.dumps({"text": "hello from test"})
        db.post_message("agent_a", "agent_b", body)

        msgs = db.get_messages("agent_b", unread_only=True)
        assert len(msgs) >= 1
        found = any("hello from test" in (m.get("body_json", "") or "") for m in msgs)
        assert found, f"Message not found in {msgs}"
