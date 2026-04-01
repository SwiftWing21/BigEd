"""Database migration runner — replaces monolithic init_db() DDL."""
import importlib
import logging
from pathlib import Path

log = logging.getLogger("migrator")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_current_version(conn) -> int:
    """Read the highest applied migration version from schema_version table."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER NOT NULL, applied_at TEXT)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def get_available_migrations() -> list[tuple[int, str]]:
    """Return [(version, module_stem), ...] sorted by version."""
    migrations = []
    for f in sorted(MIGRATIONS_DIR.glob("[0-9]*.py")):
        if f.stem == "__init__":
            continue
        mod = importlib.import_module(f"migrations.{f.stem}")
        migrations.append((mod.VERSION, f.stem))
    return sorted(migrations, key=lambda x: x[0])


def migrate(conn, target: int | None = None) -> int:
    """Run all pending migrations up to target (or latest).

    Each migration runs inside its own transaction (via conn.commit()).
    Returns the number of migrations applied.
    """
    current = get_current_version(conn)
    available = get_available_migrations()
    if not available:
        return 0
    if target is None:
        target = max(v for v, _ in available)

    pending = [(v, name) for v, name in available if current < v <= target]
    for version, name in pending:
        mod = importlib.import_module(f"migrations.{name}")
        log.info("Applying migration %d: %s", version, mod.DESCRIPTION)
        mod.up(conn)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        conn.commit()

    return len(pending)


def rollback(conn, target: int) -> int:
    """Roll back migrations down to target version (exclusive).

    Applies down() for each migration above target, in reverse order.
    Returns the number of migrations rolled back.
    """
    current = get_current_version(conn)
    if current <= target:
        return 0

    available = get_available_migrations()
    to_rollback = sorted(
        [(v, name) for v, name in available if target < v <= current],
        key=lambda x: x[0],
        reverse=True,
    )
    for version, name in to_rollback:
        mod = importlib.import_module(f"migrations.{name}")
        log.info("Rolling back migration %d: %s", version, mod.DESCRIPTION)
        if hasattr(mod, "down"):
            mod.down(conn)
        conn.execute("DELETE FROM schema_version WHERE version = ?", (version,))
        conn.commit()

    return len(to_rollback)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import db
    db.init_db()
    conn = db.get_conn()
    current = get_current_version(conn)
    available = get_available_migrations()
    latest = max(v for v, _ in available) if available else 0
    print(f"Schema version: {current}/{latest}")
    if current < latest:
        print(f"Pending: {latest - current} migration(s)")
    else:
        print("All migrations applied.")
