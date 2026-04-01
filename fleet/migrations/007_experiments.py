"""Migration 007: A/B testing and ML experiment tables."""
VERSION = 7
DESCRIPTION = "experiments, experiment_results, ml_experiments tables"


def up(conn):
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


def down(conn):
    conn.execute("DROP TABLE IF EXISTS ml_experiments")
    conn.execute("DROP TABLE IF EXISTS experiment_results")
    conn.execute("DROP TABLE IF EXISTS experiments")
