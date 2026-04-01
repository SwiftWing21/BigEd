"""Migration 003: Speed tracking and provider columns on usage table."""
VERSION = 3
DESCRIPTION = "Add eval_duration_ms, prompt_duration_ms, tokens_per_sec, provider to usage"


def _has_column(conn, table, column):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def up(conn):
    for col, definition in [
        ("eval_duration_ms", "REAL DEFAULT NULL"),
        ("prompt_duration_ms", "REAL DEFAULT NULL"),
        ("tokens_per_sec", "REAL DEFAULT NULL"),
    ]:
        if not _has_column(conn, "usage", col):
            conn.execute(f"ALTER TABLE usage ADD COLUMN {col} {definition}")

    if not _has_column(conn, "usage", "provider"):
        conn.execute("ALTER TABLE usage ADD COLUMN provider TEXT DEFAULT NULL")
        conn.execute("UPDATE usage SET provider='claude' WHERE provider IS NULL AND model LIKE 'claude-%'")
        conn.execute("UPDATE usage SET provider='gemini' WHERE provider IS NULL AND model LIKE 'gemini-%'")
        conn.execute("UPDATE usage SET provider='local' WHERE provider IS NULL AND model NOT LIKE 'claude-%' AND model NOT LIKE 'gemini-%' AND model IS NOT NULL")


def down(conn):
    pass
