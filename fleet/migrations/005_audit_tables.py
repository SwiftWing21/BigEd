"""Migration 005: PHI audit and structured audit log tables."""
VERSION = 5
DESCRIPTION = "phi_audit and audit_log tables with indexes"


def up(conn):
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


def down(conn):
    conn.execute("DROP TABLE IF EXISTS audit_log")
    conn.execute("DROP TABLE IF EXISTS phi_audit")
