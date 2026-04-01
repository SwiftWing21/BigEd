"""Migration 006: Output feedback and OSS watchlist tables."""
VERSION = 6
DESCRIPTION = "output_feedback, oss_watchlist, flywheel_scores tables"


def up(conn):
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


def down(conn):
    conn.execute("DROP TABLE IF EXISTS flywheel_scores")
    conn.execute("DROP TABLE IF EXISTS oss_watchlist")
    conn.execute("DROP TABLE IF EXISTS output_feedback")
