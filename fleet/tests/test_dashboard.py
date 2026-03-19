"""Dashboard API endpoint contract tests.

Tests the Flask app's HTTP contract (status codes, response shapes)
using an in-memory SQLite database — no running fleet required.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add fleet directory to path so dashboard imports resolve
FLEET_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FLEET_DIR))


def _create_test_db(path: str):
    """Initialize a minimal fleet.db schema for testing."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT UNIQUE NOT NULL,
            role            TEXT NOT NULL,
            status          TEXT DEFAULT 'IDLE',
            current_task_id INTEGER,
            last_heartbeat  TEXT,
            pid             INTEGER
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT DEFAULT (datetime('now')),
            assigned_to  TEXT,
            status       TEXT DEFAULT 'PENDING',
            priority     INTEGER DEFAULT 5,
            type         TEXT NOT NULL,
            payload_json TEXT,
            result_json  TEXT,
            error        TEXT,
            parent_id    INTEGER,
            depends_on   TEXT,
            review_rounds INTEGER DEFAULT 0,
            conditions   TEXT,
            classification TEXT DEFAULT 'internal',
            intelligence_score REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL,
            to_agent   TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            read_at    TEXT,
            body_json  TEXT,
            channel    TEXT DEFAULT 'fleet'
        );
        CREATE INDEX IF NOT EXISTS idx_messages_inbox
            ON messages (to_agent, channel, read_at);
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            channel    TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            body_json  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notes_channel_created
            ON notes (channel, created_at);
        CREATE TABLE IF NOT EXISTS locks (
            name        TEXT PRIMARY KEY,
            holder      TEXT NOT NULL,
            acquired_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS usage (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            skill               TEXT NOT NULL,
            model               TEXT NOT NULL,
            input_tokens        INTEGER NOT NULL DEFAULT 0,
            output_tokens       INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
            cache_create_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd            REAL NOT NULL DEFAULT 0.0,
            task_id             INTEGER,
            agent               TEXT,
            eval_duration_ms    REAL DEFAULT NULL,
            prompt_duration_ms  REAL DEFAULT NULL,
            tokens_per_sec      REAL DEFAULT NULL,
            provider            TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_skill ON usage(skill);
        CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);
        CREATE TABLE IF NOT EXISTS idle_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            agent       TEXT NOT NULL,
            skill       TEXT NOT NULL,
            result      TEXT,
            cost_usd    REAL DEFAULT 0.0
        );
    """)
    # Seed minimal data so endpoints return non-empty results
    conn.execute(
        "INSERT INTO agents (name, role, status, last_heartbeat) VALUES (?, ?, ?, datetime('now'))",
        ("test_worker", "coder", "IDLE"),
    )
    conn.execute(
        "INSERT INTO tasks (type, status, assigned_to) VALUES (?, ?, ?)",
        ("code_review", "DONE", "test_worker"),
    )
    conn.execute(
        "INSERT INTO tasks (type, status) VALUES (?, ?)",
        ("code_discuss", "PENDING"),
    )
    conn.execute(
        "INSERT INTO messages (from_agent, to_agent, body_json, channel) VALUES (?, ?, ?, ?)",
        ("test_worker", "broadcast", json.dumps({"topic": "test_topic", "round": 1}), "fleet"),
    )
    conn.commit()
    conn.close()


class TestDashboardEndpoints(unittest.TestCase):
    """Contract tests for dashboard API endpoints using Flask test client."""

    _tmpdir = None
    _db_path = None

    @classmethod
    def setUpClass(cls):
        """Create temp DB, patch DB_PATH, and build Flask test client."""
        cls._tmpdir = tempfile.mkdtemp()
        cls._db_path = os.path.join(cls._tmpdir, "fleet.db")
        _create_test_db(cls._db_path)

        # Patch DB_PATH in dashboard module before importing
        import dashboard
        dashboard.DB_PATH = Path(cls._db_path)

        # Also patch the process_control module's DB_PATH if it uses its own
        try:
            import process_control
            process_control.DB_PATH = Path(cls._db_path)
        except Exception:
            pass

        dashboard.app.config["TESTING"] = True
        cls.client = dashboard.app.test_client()

    @classmethod
    def tearDownClass(cls):
        """Clean up temp files."""
        import shutil
        if cls._tmpdir and os.path.exists(cls._tmpdir):
            shutil.rmtree(cls._tmpdir, ignore_errors=True)

    # ── Core status endpoints ────────────────────────────────────────────────

    def test_api_status_returns_agents_and_tasks(self):
        """GET /api/status returns agents list and task counts."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("agents", data)
        self.assertIsInstance(data["agents"], list)
        self.assertIn("tasks", data)
        self.assertIsInstance(data["tasks"], dict)
        # Task counts should have standard status keys
        for key in ("PENDING", "RUNNING", "DONE", "FAILED"):
            self.assertIn(key, data["tasks"])

    def test_api_activity_returns_30_day_list(self):
        """GET /api/activity returns a list of daily activity buckets."""
        resp = self.client.get("/api/activity")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 30)
        # Each entry should have day + status counts
        entry = data[0]
        self.assertIn("day", entry)
        for key in ("DONE", "FAILED", "PENDING", "RUNNING"):
            self.assertIn(key, entry)

    def test_api_skills_returns_dict(self):
        """GET /api/skills returns skill usage breakdown."""
        resp = self.client.get("/api/skills")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, dict)
        # Our seeded task is code_review
        if "code_review" in data:
            self.assertIn("DONE", data["code_review"])

    # ── Discussion / comms endpoints ─────────────────────────────────────────

    def test_api_discussions_returns_list(self):
        """GET /api/discussions returns discussion summaries."""
        resp = self.client.get("/api/discussions")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)
        if data:
            entry = data[0]
            self.assertIn("topic", entry)
            self.assertIn("agents", entry)
            self.assertIn("rounds", entry)
            self.assertIn("contributions", entry)

    def test_api_comms_returns_channel_dict(self):
        """GET /api/comms returns per-channel message stats."""
        resp = self.client.get("/api/comms")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, dict)
        # Should have channel keys
        for ch in ("fleet",):
            if ch in data:
                self.assertIn("messages", data[ch])
                self.assertIn("unread", data[ch])

    # ── Timeline endpoint ────────────────────────────────────────────────────

    def test_api_timeline_returns_event_list(self):
        """GET /api/timeline returns recent events."""
        resp = self.client.get("/api/timeline")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)
        if data:
            entry = data[0]
            self.assertIn("time", entry)
            self.assertIn("type", entry)
            self.assertIn("detail", entry)
            self.assertIn("status", entry)

    # ── Alerts endpoint ──────────────────────────────────────────────────────

    def test_api_alerts_returns_list(self):
        """GET /api/alerts returns alerts array."""
        resp = self.client.get("/api/alerts")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)

    def test_api_alerts_ack_nonexistent_returns_404(self):
        """POST /api/alerts/ack/<id> with invalid id returns 404."""
        resp = self.client.post("/api/alerts/ack/999999")
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.data)
        self.assertFalse(data["ok"])

    # ── CSRF token endpoint ──────────────────────────────────────────────────

    def test_api_csrf_returns_token(self):
        """GET /api/csrf returns a CSRF token string."""
        resp = self.client.get("/api/csrf")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("token", data)
        self.assertIsInstance(data["token"], str)
        self.assertGreater(len(data["token"]), 0)

    # ── Knowledge endpoint ───────────────────────────────────────────────────

    def test_api_knowledge_returns_dict(self):
        """GET /api/knowledge returns category dict (may be empty)."""
        resp = self.client.get("/api/knowledge")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, dict)

    # ── Code stats endpoint ──────────────────────────────────────────────────

    def test_api_code_stats_returns_structure(self):
        """GET /api/code_stats returns commit/line stats (zeros if no git workspace)."""
        resp = self.client.get("/api/code_stats")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        for key in ("commits", "lines_added", "lines_deleted", "files_changed"):
            self.assertIn(key, data)

    # ── Reviews endpoint ─────────────────────────────────────────────────────

    def test_api_reviews_returns_list(self):
        """GET /api/reviews returns a list of review summaries."""
        resp = self.client.get("/api/reviews")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)

    # ── Thermal endpoint ─────────────────────────────────────────────────────

    def test_api_thermal_returns_structure(self):
        """GET /api/thermal returns thermal data with expected keys."""
        resp = self.client.get("/api/thermal")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        for key in ("gpu_temp_c", "gpu_power_w", "thermal_state", "model_tier"):
            self.assertIn(key, data)
        self.assertIn("thresholds", data)
        self.assertIsInstance(data["thresholds"], dict)

    # ── RAG endpoint ─────────────────────────────────────────────────────────

    def test_api_rag_returns_structure(self):
        """GET /api/rag returns file/chunk counts (zeros if no rag.db)."""
        resp = self.client.get("/api/rag")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("files", data)
        self.assertIn("chunks", data)

    # ── Resolutions endpoint ─────────────────────────────────────────────────

    def test_api_resolutions_returns_list(self):
        """GET /api/resolutions returns list (empty if no data file)."""
        resp = self.client.get("/api/resolutions")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)

    # ── Fleet health endpoint (from process_control blueprint) ───────────────

    def test_api_fleet_health_returns_structure(self):
        """GET /api/fleet/health returns workers, supervisors, ollama status."""
        resp = self.client.get("/api/fleet/health")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("workers", data)
        self.assertIn("supervisors", data)
        self.assertIn("ollama", data)
        self.assertIsInstance(data["workers"], dict)
        self.assertIn("total", data["workers"])
        self.assertIn("active", data["workers"])

    def test_api_fleet_workers_returns_list(self):
        """GET /api/fleet/workers returns worker list."""
        resp = self.client.get("/api/fleet/workers")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)

    def test_api_fleet_uptime_returns_structure(self):
        """GET /api/fleet/uptime returns uptime info."""
        resp = self.client.get("/api/fleet/uptime")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("status", data)

    # ── Training endpoint ────────────────────────────────────────────────────

    def test_api_training_returns_lock_status(self):
        """GET /api/training returns training lock info."""
        resp = self.client.get("/api/training")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("locked", data)
        self.assertIsInstance(data["locked"], bool)
        self.assertIn("holder", data)
        self.assertIn("timeout_s", data)

    # ── 404 on unknown routes ────────────────────────────────────────────────

    def test_404_on_invalid_api_route(self):
        """Unknown /api/* routes return 404."""
        resp = self.client.get("/api/nonexistent_endpoint_xyz")
        self.assertEqual(resp.status_code, 404)

    # ── Data stats endpoint ──────────────────────────────────────────────────

    def test_api_data_stats_returns_dict(self):
        """GET /api/data_stats returns database and file statistics."""
        resp = self.client.get("/api/data_stats")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, dict)

    # ── Response content type ────────────────────────────────────────────────

    def test_api_responses_are_json(self):
        """All API endpoints return application/json content type."""
        endpoints = [
            "/api/status",
            "/api/alerts",
            "/api/csrf",
            "/api/thermal",
            "/api/skills",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            self.assertIn(
                "application/json",
                resp.content_type,
                f"{ep} did not return JSON content type",
            )


if __name__ == "__main__":
    unittest.main()
