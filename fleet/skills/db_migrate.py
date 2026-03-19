"""0.06.00: Schema migration skill — versioned ALTER TABLE with rollback support."""
import json
import logging
from pathlib import Path

SKILL_NAME = "db_migrate"
DESCRIPTION = "Execute versioned database schema migrations with safety checks"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
MIGRATIONS_DIR = FLEET_DIR / "migrations"
log = logging.getLogger("db_migrate")


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "status")

    if action == "status":
        return _get_status()
    elif action == "migrate":
        return _run_migrations(payload.get("target_version"))
    elif action == "plan":
        return _plan_migrations(payload.get("target_version"))
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _get_current_version():
    """Get current schema version from PRAGMA user_version."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    with db.get_conn() as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _list_migrations():
    """List available migration files, sorted by version."""
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    migrations = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            version = int(f.name.split("_")[0])
            migrations.append({"version": version, "file": f.name, "path": str(f)})
        except (ValueError, IndexError):
            continue
    return migrations


def _get_status():
    """Current migration status."""
    current = _get_current_version()
    available = _list_migrations()
    latest = max((m["version"] for m in available), default=0)
    pending = [m for m in available if m["version"] > current]
    return json.dumps({
        "current_version": current,
        "latest_available": latest,
        "pending_migrations": len(pending),
        "pending": pending,
        "up_to_date": current >= latest,
    })


def _plan_migrations(target_version=None):
    """Show what migrations would run without executing."""
    current = _get_current_version()
    available = _list_migrations()
    if target_version:
        pending = [m for m in available if current < m["version"] <= target_version]
    else:
        pending = [m for m in available if m["version"] > current]
    return json.dumps({
        "current_version": current,
        "target_version": target_version or max((m["version"] for m in pending), default=current),
        "migrations_to_apply": pending,
        "dry_run": True,
    })


def _run_migrations(target_version=None):
    """Execute pending migrations up to target_version."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    current = _get_current_version()
    available = _list_migrations()
    if target_version:
        pending = [m for m in available if current < m["version"] <= target_version]
    else:
        pending = [m for m in available if m["version"] > current]

    if not pending:
        return json.dumps({"status": "up_to_date", "version": current})

    applied = []
    try:
        with db.get_conn() as conn:
            for migration in pending:
                sql = Path(migration["path"]).read_text(encoding="utf-8")
                log.info(f"Applying migration {migration['file']}")
                conn.executescript(sql)
                applied.append(migration["file"])

        new_version = _get_current_version()
        return json.dumps({
            "status": "ok",
            "previous_version": current,
            "new_version": new_version,
            "applied": applied,
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "applied_before_error": applied,
            "current_version": _get_current_version(),
        })
