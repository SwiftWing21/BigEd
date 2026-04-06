"""Migration 012: Add ray tracing columns to audit_scores."""
VERSION = 12
DESCRIPTION = "interaction_delta, combined_score, ci_95_lo, ci_95_hi columns on audit_scores"


def up(conn):
    # Add columns safely — ALTER TABLE ADD COLUMN is idempotent-ish in SQLite
    for col, typedef in [
        ("interaction_delta", "REAL"),
        ("combined_score", "REAL"),
        ("ci_95_lo", "REAL"),
        ("ci_95_hi", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE audit_scores ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # Column already exists


def down(conn):
    # SQLite doesn't support DROP COLUMN before 3.35 — no-op
    pass
