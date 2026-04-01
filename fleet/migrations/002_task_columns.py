"""Migration 002: Additional task and message columns."""
VERSION = 2
DESCRIPTION = "Add review_rounds, conditions, classification, intelligence_score, trace_id to tasks; channel to messages"


def _has_column(conn, table, column):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def up(conn):
    for col, definition in [
        ("review_rounds", "INTEGER DEFAULT 0"),
        ("conditions", "TEXT"),
        ("classification", "TEXT DEFAULT 'internal'"),
        ("intelligence_score", "REAL DEFAULT NULL"),
        ("trace_id", "TEXT DEFAULT NULL"),
    ]:
        if not _has_column(conn, "tasks", col):
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")

    if not _has_column(conn, "messages", "channel"):
        conn.execute("ALTER TABLE messages ADD COLUMN channel TEXT DEFAULT 'fleet'")


def down(conn):
    pass  # SQLite cannot DROP COLUMN before 3.35
