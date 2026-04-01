"""Migration 009: Module registry and snapshots."""
VERSION = 9
DESCRIPTION = "module_registry (with schema migration from old layout), module_snapshots"


def up(conn):
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
    # Handle old schema that had 'type'/'manifest_json' instead of 'module_type'
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


def down(conn):
    conn.execute("DROP TABLE IF EXISTS module_snapshots")
    conn.execute("DROP TABLE IF EXISTS module_registry")
