"""Migration 001: Initial core schema."""
VERSION = 1
DESCRIPTION = "Core tables — agents, tasks, messages, notes, locks, usage, trusted_models, idle_runs, deployments"


def up(conn):
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
    """)


def down(conn):
    """Rollback — initial schema cannot be safely rolled back."""
    pass
