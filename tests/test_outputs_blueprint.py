"""Unit tests for fleet/outputs_blueprint.py."""
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Put fleet/ on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

from flask import Flask


def _make_app(knowledge_dir: Path) -> Flask:
    """Create a Flask test app with outputs_bp registered and KNOWLEDGE_DIR patched."""
    import outputs_blueprint as ob
    ob.KNOWLEDGE_DIR = knowledge_dir
    # Also patch the helper that uses KNOWLEDGE_DIR at call time via closure
    import dashboard_utils
    dashboard_utils.KNOWLEDGE_DIR = knowledge_dir

    app = Flask(__name__)
    app.register_blueprint(ob.outputs_bp)
    app.config["TESTING"] = True
    return app


def _seed_knowledge(knowledge_dir: Path) -> dict:
    """Create a fake knowledge/ structure with test .md files.

    Returns a dict of {subdir: [file_path, ...]} for assertions.
    """
    created = {}
    for subdir in ("code_reviews", "security", "quality"):
        d = knowledge_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(2):
            f = d / f"file_{i}.md"
            f.write_text(f"# Test content {i}\n\nBody text for {subdir} file {i}.", encoding="utf-8")
            files.append(f)
        created[subdir] = files
    return created


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def knowledge_dir(tmp_path):
    kd = tmp_path / "knowledge"
    kd.mkdir()
    return kd


@pytest.fixture
def seeded_knowledge(knowledge_dir):
    files = _seed_knowledge(knowledge_dir)
    return knowledge_dir, files


@pytest.fixture
def client(knowledge_dir):
    app = _make_app(knowledge_dir)
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded_client(seeded_knowledge):
    knowledge_dir, files = seeded_knowledge
    app = _make_app(knowledge_dir)
    with app.test_client() as c:
        yield c, knowledge_dir, files


# ── 1. Categories endpoint ────────────────────────────────────────────────────


class TestCategoriesEndpoint:
    def test_returns_list_with_all_entry(self, seeded_client):
        c, kd, files = seeded_client
        import outputs_blueprint as ob
        ob._cat_cache["data"] = None  # bust cache
        resp = c.get("/api/outputs/categories")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        names = [item["name"] for item in data]
        assert "All" in names

    def test_counts_md_files(self, seeded_client):
        c, kd, files = seeded_client
        import outputs_blueprint as ob
        ob._cat_cache["data"] = None
        resp = c.get("/api/outputs/categories")
        assert resp.status_code == 200
        data = resp.get_json()
        # Each seeded subdir has 2 files; 3 subdirs = 6 total
        all_entry = next(item for item in data if item["name"] == "All")
        assert all_entry["count"] == 6

    def test_category_count_matches_subdir(self, seeded_client):
        c, kd, files = seeded_client
        import outputs_blueprint as ob
        ob._cat_cache["data"] = None
        resp = c.get("/api/outputs/categories")
        data = resp.get_json()
        code_reviews = next((d for d in data if d.get("dir") == "code_reviews"), None)
        assert code_reviews is not None
        assert code_reviews["count"] == 2

    def test_empty_knowledge_dir_returns_zeros(self, client):
        import outputs_blueprint as ob
        ob._cat_cache["data"] = None
        resp = client.get("/api/outputs/categories")
        assert resp.status_code == 200
        data = resp.get_json()
        all_entry = next(item for item in data if item["name"] == "All")
        assert all_entry["count"] == 0


# ── 2. Files endpoint ─────────────────────────────────────────────────────────


class TestFilesEndpoint:
    def _get_files(self, client, **params):
        return client.get("/api/outputs/files", query_string=params)

    def test_returns_correct_fields(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = self._get_files(c)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "files" in data
        assert "total" in data
        if data["files"]:
            f = data["files"][0]
            for field in ("name", "path", "category", "size", "mtime", "verdict"):
                assert field in f, f"Missing field: {field}"

    def test_total_matches_seeded_count(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = self._get_files(c)
        data = resp.get_json()
        assert data["total"] == 6

    def test_verdict_field_present(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = self._get_files(c)
        data = resp.get_json()
        for f in data["files"]:
            assert "verdict" in f

    def test_filter_by_category(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = self._get_files(c, category="Code Reviews")
        data = resp.get_json()
        assert data["total"] == 2
        for f in data["files"]:
            assert f["category"] == "Code Reviews"

    def test_unknown_category_returns_400(self, client):
        resp = self._get_files(client, category="DoesNotExist")
        assert resp.status_code == 400

    def test_pagination_limit(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = self._get_files(c, limit=2, offset=0)
        data = resp.get_json()
        assert len(data["files"]) <= 2
        assert data["total"] == 6


# ── 3. File content endpoint ──────────────────────────────────────────────────


class TestFileContentEndpoint:
    def test_returns_content(self, seeded_client):
        c, kd, files = seeded_client
        rel = "code_reviews/file_0.md"
        with patch("db.get_feedback", return_value=None):
            resp = c.get("/api/outputs/file", query_string={"path": rel})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "content" in data
        assert "Test content 0" in data["content"]

    def test_returns_expected_fields(self, seeded_client):
        c, kd, files = seeded_client
        rel = "code_reviews/file_0.md"
        with patch("db.get_feedback", return_value=None):
            resp = c.get("/api/outputs/file", query_string={"path": rel})
        data = resp.get_json()
        for field in ("path", "content", "size", "verdict", "feedback_notes"):
            assert field in data, f"Missing field: {field}"

    def test_truncation_at_32kb(self, seeded_client):
        c, kd, files = seeded_client
        # Write a file larger than 32KB
        big_file = kd / "code_reviews" / "bigfile.md"
        big_file.write_text("x" * (33 * 1024), encoding="utf-8")
        with patch("db.get_feedback", return_value=None):
            resp = c.get("/api/outputs/file", query_string={"path": "code_reviews/bigfile.md"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "[Truncated" in data["content"]

    def test_missing_file_returns_404(self, client):
        with patch("db.get_feedback", return_value=None):
            resp = client.get("/api/outputs/file", query_string={"path": "code_reviews/ghost.md"})
        assert resp.status_code == 404

    def test_verdict_from_feedback(self, seeded_client):
        c, kd, files = seeded_client
        rel = "code_reviews/file_0.md"
        mock_fb = {"verdict": "approved", "feedback_text": "looks good"}
        with patch("db.get_feedback", return_value=mock_fb):
            resp = c.get("/api/outputs/file", query_string={"path": rel})
        data = resp.get_json()
        assert data["verdict"] == "approved"
        assert data["feedback_notes"] == "looks good"


# ── 4. Path traversal rejection ───────────────────────────────────────────────


class TestPathTraversalRejection:
    @pytest.mark.parametrize("bad_path", [
        "../../../etc/passwd",
        "../../fleet.toml",
        "/etc/passwd",
        "\\windows\\system32\\config",
        "%2e%2e/etc/passwd",
        "code_reviews/../../fleet.db",
    ])
    def test_rejects_unsafe_path(self, client, bad_path):
        resp = client.get("/api/outputs/file", query_string={"path": bad_path})
        # Should be 400 (invalid path) — not 200 or 500
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for path {bad_path!r}, got {resp.status_code}"
        )

    def test_dotdot_in_path_returns_400(self, client):
        resp = client.get("/api/outputs/file", query_string={"path": "../secret.md"})
        assert resp.status_code == 400

    def test_absolute_path_returns_400(self, client):
        resp = client.get("/api/outputs/file", query_string={"path": "/etc/passwd"})
        assert resp.status_code == 400

    def test_backslash_path_returns_400(self, client):
        resp = client.get("/api/outputs/file", query_string={"path": "\\windows\\system32"})
        assert resp.status_code == 400


# ── 5. Feedback submission ────────────────────────────────────────────────────

class TestFeedbackSubmission:
    @pytest.mark.parametrize("verdict", ["approved", "rejected", "neutral"])
    def test_valid_verdicts_call_submit_feedback(self, seeded_client, verdict):
        c, kd, files = seeded_client
        rel = "code_reviews/file_0.md"
        with patch("security.get_request_role", return_value="operator"), \
             patch("db.submit_feedback") as mock_submit:
            resp = c.post(
                "/api/outputs/feedback",
                json={"path": rel, "verdict": verdict, "notes": "test note"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        mock_submit.assert_called_once()
        call_args = mock_submit.call_args
        # First arg is the absolute path (str), second is verdict
        assert call_args[0][1] == verdict

    def test_submit_feedback_passes_notes(self, seeded_client):
        c, kd, files = seeded_client
        rel = "code_reviews/file_0.md"
        with patch("security.get_request_role", return_value="operator"), \
             patch("db.submit_feedback") as mock_submit:
            c.post(
                "/api/outputs/feedback",
                json={"path": rel, "verdict": "approved", "notes": "great work"},
            )
        _, kwargs = mock_submit.call_args
        assert kwargs.get("feedback_text") == "great work"


# ── 6. Invalid verdict rejection ─────────────────────────────────────────────


class TestInvalidVerdictRejection:
    @pytest.mark.parametrize("bad_verdict", [
        "hacked",
        "yes",
        "APPROVED",
        "",
        "null",
        "1",
    ])
    def test_invalid_verdict_returns_400(self, seeded_client, bad_verdict):
        c, kd, files = seeded_client
        rel = "code_reviews/file_0.md"
        with patch("security.get_request_role", return_value="operator"):
            resp = c.post(
                "/api/outputs/feedback",
                json={"path": rel, "verdict": bad_verdict},
            )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "verdict" in data.get("error", "").lower()


# ── 7. Unreviewed endpoint ────────────────────────────────────────────────────


class TestUnreviewedEndpoint:
    def test_returns_only_unreviewed(self, seeded_client):
        c, kd, files = seeded_client
        # Mark one file as reviewed
        reviewed_abs = str(files["code_reviews"][0].resolve())
        with patch("db.get_feedback_bulk", return_value={reviewed_abs: "approved"}):
            resp = c.get("/api/outputs/unreviewed")
        assert resp.status_code == 200
        data = resp.get_json()
        # 6 total, 1 reviewed → 5 unreviewed
        assert data["total"] == 5

    def test_unreviewed_contains_snippet(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = c.get("/api/outputs/unreviewed")
        data = resp.get_json()
        for f in data["files"]:
            assert "snippet" in f

    def test_all_reviewed_returns_empty(self, seeded_client):
        c, kd, files = seeded_client
        all_abs = {
            str(fp.resolve()): "approved"
            for fps in files.values()
            for fp in fps
        }
        with patch("db.get_feedback_bulk", return_value=all_abs):
            resp = c.get("/api/outputs/unreviewed")
        data = resp.get_json()
        assert data["total"] == 0
        assert data["files"] == []

    def test_limit_respected(self, seeded_client):
        c, kd, files = seeded_client
        with patch("db.get_feedback_bulk", return_value={}):
            resp = c.get("/api/outputs/unreviewed", query_string={"limit": 2})
        data = resp.get_json()
        assert len(data["files"]) <= 2


# ── 8. Missing category ───────────────────────────────────────────────────────


class TestMissingCategory:
    def test_unknown_category_files_returns_400(self, client):
        resp = client.get("/api/outputs/files", query_string={"category": "Nonexistent"})
        assert resp.status_code == 400

    def test_known_category_with_empty_dir_returns_empty(self, client):
        with patch("db.get_feedback_bulk", return_value={}):
            resp = client.get("/api/outputs/files", query_string={"category": "Security"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["files"] == []
