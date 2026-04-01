"""Migration 011: User profiles and sessions for local auth."""
VERSION = 11
DESCRIPTION = "User profiles and sessions for local auth"


def up(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
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
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES user_profiles(id),
        created_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT NOT NULL,
        ip_address TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions(expires_at)")

    # Add reviewer column to output_feedback if the table already exists
    try:
        conn.execute("ALTER TABLE output_feedback ADD COLUMN reviewer TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists or table doesn't exist yet


def down(conn):
    conn.execute("DROP TABLE IF EXISTS user_sessions")
    conn.execute("DROP TABLE IF EXISTS user_profiles")
