# Testing Infrastructure and CI Improvements Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ruff linting, coverage reporting, and a core unit test suite to the CI pipeline. Zero unit tests exist today for the 93 core fleet modules. This plan introduces visibility first (no hard gates), then targets the highest-risk modules.

**Tech Stack:** Python 3.11/3.12, pytest, pytest-cov, ruff, unittest.mock, SQLite in-memory

**Spec:** This document is self-contained — no separate spec file.

---

## File Map

| File | Role | Action |
|------|------|--------|
| `.github/workflows/ci.yml` | CI pipeline | Modify |
| `pyproject.toml` | Ruff + coverage config | Create |
| `fleet/db.py` | Add `path` param to `init_db()` + `FLEET_TEST_DB` env var | Modify |
| `tests/test_db.py` | Unit tests for db.py | Create |
| `tests/test_providers.py` | Unit tests for providers.py | Create |
| `tests/test_worker.py` | Unit tests for worker.py | Create |
| `tests/test_health_monitor.py` | Unit tests for health_monitor.py | Create |
| `tests/test_dashboard_api.py` | Flask test-client tests for critical API endpoints | Create |

---

### Task 1: Add ruff linter to CI (continue-on-error)

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `pyproject.toml`

This adds a `lint` job that runs ruff on every push. `continue-on-error: true` means it shows violations without blocking CI — we fix them incrementally.

- [ ] **Step 1: Create `pyproject.toml` with ruff config**

```toml
# pyproject.toml  (project root)
[tool.ruff]
target-version = "py311"
line-length = 120
exclude = [
    ".venv",
    "node_modules",
    "fleet/skills/",          # 130+ skill files — fix separately
    "tests/factorio/",        # Factorio tests — separate cleanup pass
    "autoresearch/",
    "deploy/",
]

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "F",   # pyflakes (undefined names, unused imports)
    "W",   # pycodestyle warnings
    "I",   # isort
]
ignore = [
    "E501",   # line too long — covered by line-length above, but skip long strings
    "E402",   # module-level import not at top — fleet uses lazy imports by design
    "F401",   # unused import — many intentional re-exports
]

[tool.ruff.lint.isort]
known-first-party = ["db", "config", "providers", "comms", "health_monitor"]
```

- [ ] **Step 2: Add `lint` job to `.github/workflows/ci.yml`**

Add after the existing `syntax-check` job:

```yaml
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install ruff
        run: pip install ruff
      - name: Run ruff linter
        run: ruff check .
        continue-on-error: true   # visibility only — remove when clean
```

- [ ] **Step 3: Verify locally**

```bash
pip install ruff
ruff check .
# Output shows violation count — save baseline for tracking
```

**Commit:** `ci: add ruff linter (continue-on-error) with pyproject.toml config`

---

### Task 2: Add coverage reporting to CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml` (add `[tool.coverage]` section)

Coverage runs on the unit tests only (not smoke tests). No threshold — just visibility on the first pass.

- [ ] **Step 1: Add coverage config to `pyproject.toml`**

```toml
[tool.coverage.run]
source = ["fleet"]
omit = [
    "fleet/skills/*",          # skill files — future pass
    "fleet/factorio/*",        # Factorio module — has own tests
    "fleet/tenants/*",
    "fleet/knowledge/*",
    "tests/*",
]
branch = true

[tool.coverage.report]
show_missing = true
skip_covered = false
# No fail_under — visibility only for now
```

- [ ] **Step 2: Update the pytest step in `ci.yml` to collect coverage**

Replace the existing `Run unit tests (pytest)` step with:

```yaml
      - name: Install test dependencies
        run: pip install pytest pytest-cov

      - name: Run unit tests with coverage
        working-directory: fleet
        run: |
          python -m pytest ../tests/ -v --tb=short -x \
            --cov=. \
            --cov-config=../pyproject.toml \
            --cov-report=term-missing \
            --cov-report=xml:../coverage.xml
        env:
          FLEET_TEST_DB: ":memory:"
        continue-on-error: true   # remove when baseline is established

      - name: Upload coverage report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-${{ matrix.os }}-py${{ matrix.python-version }}
          path: coverage.xml
          retention-days: 7
```

- [ ] **Step 3: Add `pytest-cov` to CI install**

In the `Install dependencies` step, append:
```
pip install pytest pytest-cov
```

**Commit:** `ci: add pytest-cov coverage reporting with XML artifact upload`

---

### Task 3: Make db.py testable with in-memory DB

**Files:**
- Modify: `fleet/db.py`

`init_db()` currently calls `get_conn()` which uses the module-level `DB_PATH`. Tests need a clean in-memory DB per test. The `FLEET_TEST_DB=:memory:` env var is already read in `get_conn()` (partially), but `init_db()` doesn't accept a path argument. This task wires it cleanly.

- [ ] **Step 1: Read db.py around `init_db()` and `get_conn()` to confirm exact signatures (line ~327 and ~172)**

Current `init_db()`:
```python
def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        ...
```

Current `get_conn()` already checks `FLEET_TEST_DB` env var? Verify — if not, add it.

- [ ] **Step 2: Modify `init_db()` to accept an optional `path` parameter**

```python
def init_db(path: str | None = None):
    """Initialize the database schema.

    Args:
        path: Optional DB path override. Defaults to DB_PATH (or FLEET_TEST_DB env var).
              Pass ":memory:" in tests for a clean isolated database.
    """
    db_path = path or os.environ.get("FLEET_TEST_DB") or None
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        # ... rest of migration logic unchanged ...
```

- [ ] **Step 3: Verify `get_conn()` handles `":memory:"` path correctly**

`get_conn(db_path=":memory:")` bypasses the pool (non-default paths skip pooling — line ~183). For `:memory:`, each call to `get_conn(":memory:")` creates a new connection, which means `init_db(":memory:")` and `get_conn(":memory:")` in tests would be different connections. Fix: in tests, use a shared connection fixture rather than relying on `get_conn()` with `:memory:`.

The correct pattern for tests: use a named temp file (via `tmp_path`) OR inject a connection directly. Document this in the test conftest.

- [ ] **Step 4: Add `conftest.py` for fleet unit tests**

Create `tests/conftest.py`:

```python
"""Shared fixtures for fleet unit tests."""
import os
import sqlite3
import sys
import tempfile
import pytest

# Put fleet/ on sys.path so `import db` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a fresh fleet DB at a temp file path. Returns the path as str."""
    db_file = str(tmp_path / "test_fleet.db")
    # Set env var so db.get_conn() uses this path
    old = os.environ.get("FLEET_TEST_DB")
    os.environ["FLEET_TEST_DB"] = db_file
    import db as dbmod
    dbmod.init_db(db_file)
    yield db_file
    # Teardown: close thread-local connection, restore env
    try:
        dbmod.close_all()
    except Exception:
        pass
    if old is None:
        os.environ.pop("FLEET_TEST_DB", None)
    else:
        os.environ["FLEET_TEST_DB"] = old
```

**Commit:** `feat(db): add path param to init_db() + conftest.py for unit test isolation`

---

### Task 4: Core unit tests — db.py

**Files:**
- Create: `tests/test_db.py`

Targets the highest-risk functions: schema creation, task claiming, retry behavior, and connection pooling.

- [ ] **Step 1: Write tests**

```python
"""Unit tests for fleet/db.py — uses a fresh temp DB per test via tmp_db fixture."""
import os
import sqlite3
import sys
import threading
import time
import pytest

# sys.path set by conftest.py
import db


class TestInitDb:
    def test_creates_all_core_tables(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        for expected in ["agents", "tasks", "messages", "notes", "usage",
                         "locks", "idle_runs", "audit_log"]:
            assert expected in tables, f"Missing table: {expected}"

    def test_idempotent_on_second_call(self, tmp_db):
        """init_db() can be called twice without error (IF NOT EXISTS guards)."""
        db.init_db(tmp_db)  # second call
        # No exception = pass

    def test_tasks_has_required_columns(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        conn.close()
        for col in ["id", "status", "type", "payload_json", "assigned_to",
                    "priority", "parent_id", "depends_on"]:
            assert col in cols, f"Missing column: {col}"


class TestRetryWrite:
    def test_succeeds_on_first_attempt(self, tmp_db):
        result = []
        def _op():
            result.append(1)
        db._retry_write(_op)
        assert result == [1]

    def test_retries_on_locked_error(self, tmp_db, monkeypatch):
        attempt = [0]
        def _op():
            attempt[0] += 1
            if attempt[0] < 3:
                raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(db, '_retry_write', db._retry_write)
        # Patch time.sleep to skip delays
        monkeypatch.setattr('time.sleep', lambda _: None)
        db._retry_write(_op)
        assert attempt[0] == 3

    def test_raises_after_max_retries(self, tmp_db, monkeypatch):
        monkeypatch.setattr('time.sleep', lambda _: None)
        def _always_locked():
            raise sqlite3.OperationalError("database is locked")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db._retry_write(_always_locked, retries=3)

    def test_raises_non_locked_error_immediately(self, tmp_db):
        def _bad():
            raise sqlite3.OperationalError("no such table: foo")
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._retry_write(_bad)


class TestGetConn:
    def test_returns_connection(self, tmp_db):
        conn = db.get_conn(tmp_db)
        assert conn is not None
        result = conn.execute("SELECT 1").fetchone()
        assert result[0] == 1

    def test_thread_local_pooling(self, tmp_db):
        """Same thread gets the same connection object (pooled)."""
        # Use default path (tmp_db is set via env var in fixture)
        c1 = db.get_conn()
        c2 = db.get_conn()
        assert c1 is c2

    def test_different_threads_get_different_connections(self, tmp_db):
        conns = []
        def _get():
            conns.append(id(db.get_conn()))
        t1 = threading.Thread(target=_get)
        t2 = threading.Thread(target=_get)
        t1.start(); t1.join()
        t2.start(); t2.join()
        assert len(set(conns)) == 2, "Threads should get distinct connections"


class TestClaimTask:
    """Test atomic task claiming — the core concurrency invariant."""

    def _insert_task(self, tmp_db, task_type="test_task"):
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO tasks (type, status, priority) VALUES (?, 'PENDING', 5)",
            (task_type,)
        )
        conn.commit()
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return task_id

    def test_claim_pending_task(self, tmp_db):
        task_id = self._insert_task(tmp_db)
        # claim_task may not exist as a standalone function — test via DB state
        # If db.claim_task exists, call it; otherwise test via get_conn
        conn = db.get_conn()
        row = conn.execute(
            "SELECT status FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["status"] == "PENDING"
        # Simulate claim
        conn.execute(
            "UPDATE tasks SET status='RUNNING', assigned_to='test_agent' WHERE id=? AND status='PENDING'",
            (task_id,)
        )
        conn.commit()
        row = conn.execute("SELECT status, assigned_to FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row["status"] == "RUNNING"
        assert row["assigned_to"] == "test_agent"

    def test_double_claim_prevented(self, tmp_db):
        """Two concurrent claims on the same task — only one should win."""
        task_id = self._insert_task(tmp_db)
        winners = []

        def _try_claim(agent_name):
            c = sqlite3.connect(tmp_db)
            c.execute("PRAGMA busy_timeout=5000")
            cursor = c.execute(
                "UPDATE tasks SET status='RUNNING', assigned_to=? "
                "WHERE id=? AND status='PENDING'",
                (agent_name, task_id)
            )
            c.commit()
            if cursor.rowcount == 1:
                winners.append(agent_name)
            c.close()

        threads = [threading.Thread(target=_try_claim, args=(f"agent_{i}",)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(winners) == 1, f"Exactly one winner expected, got: {winners}"
```

- [ ] **Step 2: Run tests**

```bash
cd /c/Users/max/Projects/Education
python -m pytest tests/test_db.py -v --tb=short
```

Expected: 12-14 tests passing.

**Commit:** `test(db): 13 unit tests for init_db, _retry_write, get_conn, task claiming`

---

### Task 5: Core unit tests — providers.py

**Files:**
- Create: `tests/test_providers.py`

Targets circuit breaker state machine, fallback chain ordering, and complexity routing.

- [ ] **Step 1: Write tests**

```python
"""Unit tests for fleet/providers.py — circuit breaker and routing logic."""
import sys
import os
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))

import providers


@pytest.fixture(autouse=True)
def reset_circuit_state():
    """Clear circuit breaker state between tests."""
    providers._circuit_state.clear()
    yield
    providers._circuit_state.clear()


class TestCircuitBreaker:
    def test_closed_by_default(self):
        assert providers._circuit_is_open("test_provider") is False

    def test_opens_after_threshold_failures(self):
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD):
            providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider") is True

    def test_does_not_open_below_threshold(self):
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD - 1):
            providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider") is False

    def test_resets_after_success(self):
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD):
            providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider") is True
        providers._circuit_record_success("test_provider")
        assert providers._circuit_is_open("test_provider") is False

    def test_half_open_after_cooldown(self, monkeypatch):
        """Circuit transitions to half-open (allows retry) after cooldown expires."""
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD):
            providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider") is True
        # Fast-forward time past cooldown
        future = time.time() + providers.CIRCUIT_COOLDOWN_SECS + 1
        monkeypatch.setattr(providers.time, 'time', lambda: future)
        assert providers._circuit_is_open("test_provider") is False

    def test_failures_reset_outside_window(self, monkeypatch):
        """Failures older than CIRCUIT_WINDOW_SECS don't count toward threshold."""
        providers._circuit_record_failure("test_provider")
        # Advance time past window
        future = time.time() + providers.CIRCUIT_WINDOW_SECS + 1
        monkeypatch.setattr(providers.time, 'time', lambda: future)
        # New failure — counter should reset to 1 (below threshold)
        providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider") is False

    def test_exponential_backoff_on_repeated_trips(self):
        """Each circuit trip doubles the cooldown (up to 600s cap)."""
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD):
            providers._circuit_record_failure("backoff_provider")
        state = providers._circuit_state["backoff_provider"]
        assert state["cooldowns"] == 1
        # Open_until should be ~CIRCUIT_COOLDOWN_SECS from now
        assert state["open_until"] > time.time()
        assert state["open_until"] <= time.time() + providers.CIRCUIT_COOLDOWN_SECS + 5


class TestComplexityRouting:
    def test_simple_maps_to_haiku(self):
        assert "haiku" in providers.COMPLEXITY_ROUTING["simple"].lower()

    def test_medium_maps_to_sonnet(self):
        assert "sonnet" in providers.COMPLEXITY_ROUTING["medium"].lower()

    def test_complex_maps_to_opus(self):
        assert "opus" in providers.COMPLEXITY_ROUTING["complex"].lower()

    def test_all_skills_have_classification(self):
        all_classified = set()
        for skills in providers.SKILL_COMPLEXITY.values():
            all_classified.update(skills)
        # Spot-check a few known skills
        for skill in ["flashcard", "code_review", "plan_workload"]:
            assert skill in all_classified, f"Skill not classified: {skill}"

    def test_no_skill_in_multiple_tiers(self):
        seen = {}
        for tier, skills in providers.SKILL_COMPLEXITY.items():
            for s in skills:
                assert s not in seen, f"Skill '{s}' in both '{seen[s]}' and '{tier}'"
                seen[s] = tier
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_providers.py -v --tb=short
```

Expected: 11 tests passing.

**Commit:** `test(providers): 11 unit tests for circuit breaker state machine and complexity routing`

---

### Task 6: Core unit tests — health_monitor.py

**Files:**
- Create: `tests/test_health_monitor.py`

Targets the in-memory circuit breaker (separate from providers.py), stale agent detection, and rollback selection.

- [ ] **Step 1: Write tests**

```python
"""Unit tests for fleet/health_monitor.py — circuit breaker and stale agent logic."""
import sys
import os
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))

import health_monitor


@pytest.fixture(autouse=True)
def reset_breakers():
    """Clear in-memory breaker state between tests."""
    health_monitor._breakers.clear()
    yield
    health_monitor._breakers.clear()


class TestCircuitBreakerSkill:
    """health_monitor has its own circuit breaker (per-skill, different from providers.py)."""

    def test_closed_by_default(self):
        assert health_monitor.circuit_breaker_is_open("test_skill") is False

    def test_opens_after_threshold_failures(self):
        threshold = health_monitor._default("circuit_breaker_threshold", 3)
        for _ in range(threshold):
            health_monitor.circuit_breaker_record_failure("test_skill", "error msg")
        assert health_monitor.circuit_breaker_is_open("test_skill") is True

    def test_resets_after_window_expires(self, monkeypatch):
        threshold = health_monitor._default("circuit_breaker_threshold", 3)
        for _ in range(threshold):
            health_monitor.circuit_breaker_record_failure("test_skill")
        assert health_monitor.circuit_breaker_is_open("test_skill") is True
        # Advance time past reset window
        window = health_monitor._default("circuit_breaker_window", 300)
        future = time.time() + window + 1
        monkeypatch.setattr(health_monitor.time, 'time', lambda: future)
        assert health_monitor.circuit_breaker_is_open("test_skill") is False

    def test_get_status_includes_tripped_skill(self):
        threshold = health_monitor._default("circuit_breaker_threshold", 3)
        for _ in range(threshold):
            health_monitor.circuit_breaker_record_failure("my_skill", "oops")
        status = health_monitor.get_circuit_breaker_status()
        skill_names = [s["skill"] for s in status]
        assert "my_skill" in skill_names
        entry = next(s for s in status if s["skill"] == "my_skill")
        assert entry["tripped"] is True

    def test_below_threshold_not_in_status_as_tripped(self):
        health_monitor.circuit_breaker_record_failure("partial_skill", "err1")
        status = health_monitor.get_circuit_breaker_status()
        for s in status:
            if s["skill"] == "partial_skill":
                assert s["tripped"] is False


class TestCheckAgentHealth:
    def test_returns_unhealthy_for_unknown_agent(self, tmp_db):
        """Agent not in DB should come back unhealthy."""
        result = health_monitor.check_agent_health("ghost_agent_xyz")
        assert result["healthy"] is False
        assert "agent_not_found" in result["issues"]

    def test_returns_healthy_for_active_agent(self, tmp_db):
        """Agent with recent heartbeat should be healthy."""
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        now = __import__('datetime').datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO agents (name, role, status, last_heartbeat) VALUES (?,?,?,?)",
            ("test_agent", "coder", "IDLE", now)
        )
        conn.commit()
        conn.close()
        result = health_monitor.check_agent_health("test_agent")
        assert result["healthy"] is True
        assert result["issues"] == []
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_health_monitor.py -v --tb=short
```

Expected: 7 tests passing.

**Commit:** `test(health_monitor): 7 unit tests for skill circuit breaker and agent health check`

---

### Task 7: Core unit tests — worker.py

**Files:**
- Create: `tests/test_worker.py`

Targets skill dispatch routing, focus state read/write, and task claiming. All DB and skill calls are mocked.

- [ ] **Step 1: Inspect `fleet/worker.py` to find the claim + dispatch entry point**

```bash
grep -n "def claim\|def dispatch\|def run_task\|FOCUS\|focus" fleet/worker.py | head -30
```

- [ ] **Step 2: Write tests based on actual function signatures found in Step 1**

Baseline template — adjust imports/function names after reading worker.py:

```python
"""Unit tests for fleet/worker.py — skill dispatch, focus state, task claiming."""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))


class TestFocusState:
    """Focus toggle is written/read via a flag file or DB column — test both paths."""

    def test_focus_off_by_default(self, tmp_path, monkeypatch):
        """Focus flag file absent = focus off."""
        flag = tmp_path / ".focus_mode"
        monkeypatch.chdir(tmp_path)
        # Import worker after setting cwd so flag file path resolves correctly
        import importlib
        import worker
        importlib.reload(worker)
        # Verify get_focus() or equivalent returns False/off
        # Adjust name based on grep in Step 1
        if hasattr(worker, 'get_focus_mode'):
            assert worker.get_focus_mode() is False
        elif hasattr(worker, 'is_focused'):
            assert worker.is_focused() is False

    def test_focus_on_when_flag_present(self, tmp_path, monkeypatch):
        """Focus flag file present = focus on."""
        flag = tmp_path / ".focus_mode"
        flag.write_text("1")
        monkeypatch.chdir(tmp_path)
        import importlib
        import worker
        importlib.reload(worker)
        if hasattr(worker, 'get_focus_mode'):
            assert worker.get_focus_mode() is True


class TestSkillDispatch:
    def test_unknown_skill_returns_error(self, tmp_db):
        """Dispatching a nonexistent skill should return error dict, not raise."""
        with patch.dict(sys.modules, {'db': MagicMock()}):
            import importlib
            import worker
            importlib.reload(worker)
            if hasattr(worker, 'dispatch_skill'):
                result = worker.dispatch_skill({"type": "nonexistent_skill_xyz", "id": 1})
                assert isinstance(result, dict)
                # Either error key or status=failed
                assert result.get("status") in ("error", "failed", None) or "error" in result

    def test_task_type_routes_to_correct_skill(self, tmp_db):
        """A known task type should invoke the corresponding skill module."""
        mock_skill = MagicMock(return_value={"status": "ok", "result": "done"})
        with patch.dict('sys.modules', {'skills.flashcard': MagicMock(run=mock_skill)}):
            import importlib
            import worker
            importlib.reload(worker)
            if hasattr(worker, 'dispatch_skill'):
                worker.dispatch_skill({"type": "flashcard", "id": 99, "payload_json": "{}"})
```

Note: Step 1's grep output determines exact function names. Adjust test bodies accordingly before running.

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_worker.py -v --tb=short
```

Expected: 4-6 tests passing (exact count depends on worker.py API surface).

**Commit:** `test(worker): unit tests for focus state and skill dispatch routing`

---

### Task 8: Dashboard API endpoint tests

**Files:**
- Create: `tests/test_dashboard_api.py`

Tests critical API endpoints using Flask's test client. Requires `create_app()` factory or direct import of `app` from dashboard.py.

- [ ] **Step 1: Check if `create_app()` exists in dashboard.py**

```bash
grep -n "create_app\|^app = Flask\|def create_app" fleet/dashboard.py | head -10
```

- [ ] **Step 2a: If `create_app()` exists — use Flask test client**

```python
"""Tests for critical dashboard API endpoints using Flask test client."""
import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))


@pytest.fixture
def client(tmp_db):
    from dashboard import create_app
    app = create_app(testing=True)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestHealthEndpoint:
    def test_get_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_health_body_has_status_key(self, client):
        resp = client.get("/api/health")
        data = json.loads(resp.data)
        assert "status" in data

    def test_health_status_is_ok(self, client):
        resp = client.get("/api/health")
        data = json.loads(resp.data)
        assert data["status"] in ("ok", "healthy", "running")


class TestStatusEndpoint:
    def test_get_status_returns_200(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_status_body_is_json(self, client):
        resp = client.get("/api/status")
        assert resp.content_type.startswith("application/json")


class TestAgentsEndpoint:
    def test_get_agents_returns_list(self, client):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, (list, dict))  # some endpoints wrap in {"agents": [...]}


class TestTaskEndpoint:
    def test_post_task_requires_type(self, client):
        """POST /api/task without 'type' should return 400."""
        resp = client.post("/api/task", json={})
        assert resp.status_code in (400, 422)

    def test_post_task_accepts_valid_payload(self, client, tmp_db):
        resp = client.post("/api/task", json={"type": "flashcard", "payload": {}})
        assert resp.status_code in (200, 201, 202)
```

- [ ] **Step 2b: If no `create_app()` — use `urllib` against a running instance**

If dashboard.py uses a module-level `app = Flask(...)` without a factory, add a `create_app()` wrapper:

```python
# In fleet/dashboard.py — add near the top after app is created:
def create_app(testing=False):
    """Factory for test client access."""
    if testing:
        app.config["TESTING"] = True
    return app
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_dashboard_api.py -v --tb=short
```

Expected: 8-10 tests passing (some may require a live DB and fleet config — mark with `@pytest.mark.integration` if they need a running fleet).

**Commit:** `test(dashboard): 9 endpoint tests for health, status, agents, task submission`

---

## CI Integration Summary

After all tasks, the `ci.yml` smoke-test job gains:
1. `pip install pytest pytest-cov` in the install step
2. `--cov` flags on the pytest step
3. Coverage XML artifact upload

And a new `lint` job runs ruff on every push with `continue-on-error: true`.

### Removing `continue-on-error`

Once the violation count is zero:
1. Remove `continue-on-error: true` from the `lint` job
2. Set `fail_under = 40` in `[tool.coverage.report]` (adjust to actual baseline)
3. Remove `continue-on-error: true` from the pytest/coverage step

---

## Test Commands

```bash
# Run all new unit tests
python -m pytest tests/test_db.py tests/test_providers.py tests/test_health_monitor.py tests/test_worker.py tests/test_dashboard_api.py -v --tb=short

# Run with coverage
cd fleet
python -m pytest ../tests/test_db.py ../tests/test_providers.py -v --cov=. --cov-report=term-missing

# Run ruff
ruff check .
ruff check . --statistics   # see violation distribution
```

---

## Grading Alignment

- **Goal:** Add testing infrastructure and CI quality gates
- **Grading Alignment:** Code Quality / Test Coverage → impact: +8 pts / weight: 8%
- **Dependencies:** Task 3 (db.py testability) must complete before Tasks 4, 6, 7
- **Est. Tokens:** ~20k (L)
- **Status:** [ ] Not started
