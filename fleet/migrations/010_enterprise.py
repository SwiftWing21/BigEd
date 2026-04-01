"""Migration 010: Enterprise — SSO sessions and tenant API keys."""
VERSION = 10
DESCRIPTION = "sso_sessions, tenant_api_keys tables"


def up(conn):
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


def down(conn):
    conn.execute("DROP TABLE IF EXISTS tenant_api_keys")
    conn.execute("DROP TABLE IF EXISTS sso_sessions")
