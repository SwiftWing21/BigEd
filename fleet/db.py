"""SQLite data layer — all DB access goes through this module.

Decomposed (TD-04): core connection/schema/init lives here.
Domain functions live in db_tasks, db_agents, db_feedback, db_usage,
db_alerts, db_hitl, db_security. All are re-exported below for
backward compatibility (callers can still `import db; db.post_task(...)`).
"""
import json
import logging
import os
import sqlite3
import threading
import time
import random
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def utc_to_local(utc_str: str | None) -> str:
    """Convert a UTC datetime string from the DB to local time string."""
    if not utc_str:
        return "never"
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_str

DB_PATH = Path(__file__).parent / "fleet.db"


def get_tenant_db_path(tenant_id: str = None) -> Path:
    """Get DB path for a tenant. None = default (single-tenant mode).

    In multi-tenant mode (enterprise.multi_tenant = true in fleet.toml),
    each tenant gets an isolated database under fleet/tenants/<tenant_id>/.
    The directory is auto-created if it doesn't exist.

    Returns fleet/fleet.db for single-tenant (default) mode.
    """
    base = Path(__file__).parent
    if not tenant_id:
        return base / "fleet.db"
    tenant_dir = base / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir / "fleet.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

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
    FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE SET NULL
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
    agent               TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_skill ON usage(skill);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);

CREATE TABLE IF NOT EXISTS trusted_models (
    model       TEXT PRIMARY KEY,
    trusted_at  TEXT DEFAULT (datetime('now')),
    accept_count INTEGER DEFAULT 0,
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS idle_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    agent       TEXT NOT NULL,
    skill       TEXT NOT NULL,
    result      TEXT,
    cost_usd    REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS deployments (
    deploy_id       TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending',
    manifest_json   TEXT,
    received_at     REAL,
    applied_at      REAL,
    size_mb         REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS audit_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
    tier         TEXT    NOT NULL,
    dimension    TEXT    NOT NULL,
    auto_score   REAL,
    auto_detail  TEXT,
    manual_grade TEXT,
    divergence   INTEGER DEFAULT 0,
    acknowledged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_scores(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_dim ON audit_scores(dimension);

CREATE TABLE IF NOT EXISTS user_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now')),
    score      REAL    NOT NULL,
    scope      TEXT    NOT NULL,
    session_id TEXT,
    text       TEXT,
    inferred   TEXT,
    actor      TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_scope_ts ON user_feedback(scope, timestamp);
"""


# ── Thread-local connection pool ────────────────────────────────────────────
_local = threading.local()
_pool_lock = threading.Lock()
_pool_conns: dict = {}  # thread_id -> connection (tracks live connections)
_MAX_POOL = 20  # max concurrent thread-local connections


def _create_connection(db_path=None):
    """Create a raw SQLite connection with WAL, busy_timeout, and optional SQLCipher."""
    path = db_path or DB_PATH
    try:
        import sqlcipher3 as sqlite3_mod
        conn = sqlite3_mod.connect(str(path), check_same_thread=False, timeout=30)
        key = os.environ.get("BIGED_DB_KEY", "")
        if key:
            safe_key = key.replace("'", "''")
            conn.execute(f"PRAGMA key = '{safe_key}'")
        conn.row_factory = sqlite3_mod.Row
    except ImportError:
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_conn(db_path=None):
    """Get a thread-local SQLite connection (reused per thread).

    Each thread keeps one connection alive for the lifetime of the thread.
    Connections are validated before reuse. A global cap (_MAX_POOL) prevents
    runaway thread counts from exhausting file descriptors.

    Pass db_path to get a non-pooled connection for a specific database
    (e.g. tenant DBs). Only the default DB_PATH is pooled.

    Honors FLEET_TEST_DB env var -- set to a file path or ":memory:" for test
    isolation. For :memory: DBs, the connection is cached so the same in-memory
    DB is reused across calls (otherwise each call gets a fresh empty DB).
    """
    if db_path is None:
        db_path = os.environ.get("FLEET_TEST_DB") or None

    if db_path is not None:
        # Cache :memory: connections so tables persist across calls
        if db_path == ":memory:":
            mem_conn = getattr(_local, '_mem_conn', None)
            if mem_conn is not None:
                try:
                    mem_conn.execute("SELECT 1")
                    return mem_conn
                except Exception:
                    pass
            mem_conn = _create_connection(db_path)
            _local._mem_conn = mem_conn
            return mem_conn
        return _create_connection(db_path)

    conn = getattr(_local, 'conn', None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            log.warning("Thread-local DB connection stale, replacing")
            _close_thread_conn()

    with _pool_lock:
        dead = [tid for tid in _pool_conns if not any(
            t.ident == tid for t in threading.enumerate()
        )]
        for tid in dead:
            try:
                _pool_conns[tid].close()
            except Exception:
                pass
            del _pool_conns[tid]

        if len(_pool_conns) >= _MAX_POOL:
            raise RuntimeError(f"Connection pool exhausted ({_MAX_POOL})")

    try:
        conn = _create_connection()
        _local.conn = conn
        with _pool_lock:
            _pool_conns[threading.get_ident()] = conn
        return conn
    except Exception:
        raise


def _close_thread_conn():
    """Close this thread's pooled connection and remove from pool tracking."""
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
        with _pool_lock:
            _pool_conns.pop(threading.get_ident(), None)


def close_all():
    """Shutdown hook — close the calling thread's pooled connection."""
    _close_thread_conn()


def shutdown():
    """Full shutdown — checkpoint WAL, close connections, release file locks."""
    try:
        conn = getattr(_local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                log.warning("WAL checkpoint failed: %s", e)
            finally:
                conn.close()
        else:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                log.warning("WAL checkpoint failed: %s", e)
            _close_thread_conn()
    except Exception as e:
        log.warning("db.shutdown() error: %s", e)
    log.info("Database shutdown complete")


def _retry_write(fn, retries=8):
    """Retry a write operation with jittered backoff on OperationalError (locked)."""
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == retries - 1:
                raise
            time.sleep(0.2 * (2 ** attempt) + random.uniform(0, 0.1))


def acquire_fleet_lock(conn, timeout_ms=5000):
    """Acquire an exclusive SQLite advisory lock for federation write serialization."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    attempt = 0
    while True:
        try:
            conn.execute("BEGIN EXCLUSIVE")
            return True
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            wait = min(0.05 * (2 ** attempt) + random.uniform(0, 0.01), remaining)
            time.sleep(wait)
            attempt += 1


def release_fleet_lock(conn, commit=True):
    """Release the exclusive fleet lock."""
    try:
        conn.execute("COMMIT" if commit else "ROLLBACK")
    except sqlite3.OperationalError:
        pass


VALID_TASK_STATUSES = {"PENDING", "RUNNING", "DONE", "FAILED", "WAITING", "REVIEW", "WAITING_HUMAN", "FORWARDED"}


def init_db(path: str | None = None):
    """Initialize the database schema."""
    db_path = path or os.environ.get("FLEET_TEST_DB") or None
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "parent_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER")
        if "depends_on" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT")
        if "review_rounds" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN review_rounds INTEGER DEFAULT 0")
        if "conditions" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN conditions TEXT")
        if "classification" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN classification TEXT DEFAULT 'internal'")
        if "intelligence_score" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN intelligence_score REAL DEFAULT NULL")
        if "trace_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN trace_id TEXT DEFAULT NULL")
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "channel" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN channel TEXT DEFAULT 'fleet'")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_messages_inbox
            ON messages (to_agent, channel, read_at)""")
        usage_cols = {r[1] for r in conn.execute("PRAGMA table_info(usage)").fetchall()}
        if "eval_duration_ms" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN eval_duration_ms REAL DEFAULT NULL")
        if "prompt_duration_ms" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN prompt_duration_ms REAL DEFAULT NULL")
        if "tokens_per_sec" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN tokens_per_sec REAL DEFAULT NULL")
        if "provider" not in usage_cols:
            conn.execute("ALTER TABLE usage ADD COLUMN provider TEXT DEFAULT NULL")
            conn.execute("UPDATE usage SET provider='claude' WHERE provider IS NULL AND model LIKE 'claude-%'")
            conn.execute("UPDATE usage SET provider='gemini' WHERE provider IS NULL AND model LIKE 'gemini-%'")
            conn.execute("UPDATE usage SET provider='local' WHERE provider IS NULL AND model NOT LIKE 'claude-%' AND model NOT LIKE 'gemini-%' AND model IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_depends ON tasks(depends_on) WHERE depends_on IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS phi_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                action TEXT NOT NULL,
                data_scope TEXT,
                model_used TEXT,
                phi_detected BOOLEAN DEFAULT 0,
                deidentified BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_phi_audit_date ON phi_audit(created_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
                actor         TEXT NOT NULL,
                action        TEXT NOT NULL,
                resource      TEXT,
                detail        TEXT,
                cost_usd      REAL NOT NULL DEFAULT 0.0,
                metadata_json TEXT,
                ip_address    TEXT,
                role          TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS output_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                output_path TEXT NOT NULL,
                verdict TEXT NOT NULL,
                feedback_text TEXT,
                operator TEXT DEFAULT 'human',
                agent_name TEXT,
                skill_type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_path ON output_feedback(output_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_verdict ON output_feedback(verdict)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oss_watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_url TEXT NOT NULL UNIQUE,
                project_name TEXT,
                last_review_at TEXT,
                last_grade TEXT,
                review_frequency TEXT DEFAULT 'weekly',
                baseline_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flywheel_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_path TEXT NOT NULL,
                dimension TEXT NOT NULL,
                grade TEXT NOT NULL,
                score REAL NOT NULL,
                details_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flywheel_project ON flywheel_scores(project_path, created_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                skill_name TEXT NOT NULL,
                variant_path TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at REAL NOT NULL,
                results_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_skill ON experiments(skill_name, status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                variant TEXT NOT NULL,
                agent TEXT,
                success INTEGER NOT NULL DEFAULT 0,
                score REAL,
                created_at REAL NOT NULL,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expr_results_exp ON experiment_results(experiment_id, variant)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ml_experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                experiment_type TEXT NOT NULL,
                hypothesis TEXT,
                config_json TEXT,
                metrics_before_json TEXT,
                metrics_after_json TEXT,
                status TEXT NOT NULL DEFAULT 'PROPOSED',
                auto_approved INTEGER NOT NULL DEFAULT 0,
                artifact_path TEXT,
                previous_artifact TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_exp_agent ON ml_experiments(agent, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_exp_type ON ml_experiments(experiment_type, status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_sources (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'huggingface',
                dataset TEXT,
                skill TEXT NOT NULL,
                agent_role TEXT,
                content_column TEXT,
                batch_size INTEGER DEFAULT 50,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                row_id INTEGER,
                title TEXT,
                content_preview TEXT,
                token_estimate INTEGER,
                destination TEXT DEFAULT 'tasks',
                skill TEXT,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            conn.execute("SELECT dispatch_failures FROM ingest_staging LIMIT 0")
        except Exception:
            conn.execute("ALTER TABLE ingest_staging ADD COLUMN dispatch_failures INTEGER DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS module_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_name TEXT NOT NULL,
                reason TEXT NOT NULL,
                relevance_score REAL NOT NULL,
                dismissed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(module_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS module_registry (
                name TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                module_type TEXT NOT NULL,
                description TEXT DEFAULT '',
                requires TEXT DEFAULT '[]',
                conflicts TEXT DEFAULT '[]',
                recommends TEXT DEFAULT '[]',
                rollback_safe INTEGER DEFAULT 1,
                min_fleet_version TEXT DEFAULT '0.0.0',
                source TEXT DEFAULT 'disk',
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        try:
            conn.execute("SELECT module_type FROM module_registry LIMIT 0")
        except Exception:
            conn.execute("DROP TABLE IF EXISTS module_registry")
            conn.execute("""
                CREATE TABLE module_registry (
                    name TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    module_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    requires TEXT DEFAULT '[]',
                    conflicts TEXT DEFAULT '[]',
                    recommends TEXT DEFAULT '[]',
                    rollback_safe INTEGER DEFAULT 1,
                    min_fleet_version TEXT DEFAULT '0.0.0',
                    source TEXT DEFAULT 'disk',
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS module_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                state_json TEXT NOT NULL,
                dep_graph_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                created_by TEXT DEFAULT 'system'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_created ON module_snapshots(created_at DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_sessions (
                session_id TEXT PRIMARY KEY,
                user_data TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenant_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                label TEXT DEFAULT '',
                created_at REAL NOT NULL,
                last_used_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tak_hash ON tenant_api_keys (key_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tak_tenant ON tenant_api_keys (tenant_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_idle_runs_agent ON idle_runs(agent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_idle_runs_created ON idle_runs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent)")
        # ── User profiles + sessions (local auth) ──────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',
                password_hash TEXT,
                avatar_color TEXT DEFAULT '#3b82f6',
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT,
                is_active INTEGER DEFAULT 1,
                sso_user_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES user_profiles(id),
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                ip_address TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions(expires_at)")
        # Add reviewer column to output_feedback if missing
        fb_cols = {r[1] for r in conn.execute("PRAGMA table_info(output_feedback)").fetchall()}
        if "reviewer" not in fb_cols:
            conn.execute("ALTER TABLE output_feedback ADD COLUMN reviewer TEXT DEFAULT ''")


# ── Channel Constants (extracted to comms.py) ─────────────────────────────────
from comms import CH_SUP, CH_AGENT, CH_FLEET, CH_POOL  # noqa: E402,F401

# ── Messaging (extracted to comms.py) ────────────────────────────────────────
from comms import post_message, get_messages, broadcast_message, post_note, get_notes, get_note_count  # noqa: E402,F401

# ── Diagnostics (extracted to diagnostics.py) ────────────────────────────────
from diagnostics import quarantine_agent, clear_quarantine, get_failure_streaks, get_stuck_reviews  # noqa: E402,F401

# ── Cost Tracking (extracted to cost_tracking.py) ────────────────────────────
from cost_tracking import log_usage, get_usage_summary, get_usage_delta  # noqa: E402,F401

# ── Idle Evolution (extracted to idle_evolution.py) ───────────────────────────
from idle_evolution import log_idle_run, get_idle_stats, get_least_evolved_skill  # noqa: E402,F401

# ── Re-exports: Tasks (db_tasks.py) ──────────────────────────────────────────
from db_tasks import (  # noqa: E402,F401
    update_intelligence_score, get_skill_quality_stats, queue_depth,
    claim_tasks, claim_task, complete_task, fail_task,
    _promote_waiting_tasks, _cascade_fail_dependents,
    validate_dag, post_task_chain, checkpoint_chain, resume_chain,
    requeue_task, review_task, reject_task, post_task,
    recover_stale_tasks, get_task_result, get_pending_count,
    get_fleet_status, cancel_task, get_dag_graph,
)

# ── Re-exports: Agents (db_agents.py) ────────────────────────────────────────
from db_agents import (  # noqa: E402,F401
    register_agent, heartbeat, mark_disabled_agents, acquire_lock, release_lock, check_lock,
    delete_user_data,
)

# ── Re-exports: Feedback (db_feedback.py) ────────────────────────────────────
from db_feedback import submit_feedback, get_feedback, get_feedback_stats, get_feedback_bulk  # noqa: E402,F401

# ── Re-exports: Usage/Models (db_usage.py) ───────────────────────────────────
from db_usage import is_model_trusted, record_model_accept, get_registered_models, get_model_speed_stats  # noqa: E402,F401

# ── Re-exports: Alerts & Audit (db_alerts.py) ────────────────────────────────
from db_alerts import log_alert, get_alerts, acknowledge_alert, log_audit_run, get_audit_runs  # noqa: E402,F401

# ── Re-exports: HITL (db_hitl.py) ────────────────────────────────────────────
from db_hitl import request_human_input, respond_to_agent, get_waiting_human_tasks, get_waiting_human_details  # noqa: E402,F401

# ── Re-exports: Security (db_security.py) ────────────────────────────────────
from db_security import get_pending_advisories, dismiss_advisory  # noqa: E402,F401
