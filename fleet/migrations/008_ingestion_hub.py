"""Migration 008: Ingestion Hub tables."""
VERSION = 8
DESCRIPTION = "ingest_sources, ingest_staging (with dispatch_failures), module_suggestions"


def _has_column(conn, table, column):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def up(conn):
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
    if not _has_column(conn, "ingest_staging", "dispatch_failures"):
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


def down(conn):
    conn.execute("DROP TABLE IF EXISTS module_suggestions")
    conn.execute("DROP TABLE IF EXISTS ingest_staging")
    conn.execute("DROP TABLE IF EXISTS ingest_sources")
