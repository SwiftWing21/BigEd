"""0.06.00: Database encryption — migrate plaintext SQLite to SQLCipher."""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

SKILL_NAME = "db_encrypt"
DESCRIPTION = "Encrypt fleet.db using SQLCipher (AES-256) for data-at-rest protection"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
DB_PATH = FLEET_DIR / "fleet.db"


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "status")

    if action == "status":
        return _check_status()
    elif action == "encrypt":
        key = payload.get("key") or os.environ.get("BIGED_DB_KEY", "")
        if not key:
            return json.dumps({"error": "Encryption key required. Set BIGED_DB_KEY in ~/.secrets or pass key in payload."})
        return _encrypt_db(key)
    elif action == "verify":
        key = payload.get("key") or os.environ.get("BIGED_DB_KEY", "")
        return _verify_encrypted(key)
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _check_status():
    """Check if fleet.db is encrypted."""
    if not DB_PATH.exists():
        return json.dumps({"status": "no_db", "path": str(DB_PATH)})

    # Try opening with standard sqlite3 — if it works, it's plaintext
    import sqlite3
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("SELECT count(*) FROM sqlite_master")
        conn.close()
        size_mb = round(DB_PATH.stat().st_size / 1e6, 2)
        return json.dumps({"encrypted": False, "size_mb": size_mb, "path": str(DB_PATH)})
    except sqlite3.DatabaseError:
        size_mb = round(DB_PATH.stat().st_size / 1e6, 2)
        return json.dumps({"encrypted": True, "size_mb": size_mb, "path": str(DB_PATH)})


def _encrypt_db(key: str):
    """Migrate plaintext fleet.db to SQLCipher encrypted version."""
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        return json.dumps({"error": "sqlcipher3-wheels not installed. Run: pip install sqlcipher3-wheels"})

    if not DB_PATH.exists():
        return json.dumps({"error": f"Database not found: {DB_PATH}"})

    # Backup first
    backup_path = DB_PATH.with_suffix(f".plaintext.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
    shutil.copy2(DB_PATH, backup_path)

    encrypted_path = DB_PATH.with_suffix(".encrypted")

    try:
        # Open plaintext DB
        conn = sqlcipher.connect(str(DB_PATH))

        # Attach new encrypted DB
        conn.execute(f"ATTACH DATABASE '{encrypted_path}' AS encrypted KEY '{key}'")

        # Export all schema + data
        conn.execute("SELECT sqlcipher_export('encrypted')")

        # Set WAL mode on encrypted DB
        conn.execute("PRAGMA encrypted.journal_mode=WAL")

        # Detach
        conn.execute("DETACH DATABASE encrypted")
        conn.close()

        # Verify encrypted DB works
        verify_conn = sqlcipher.connect(str(encrypted_path))
        verify_conn.execute(f"PRAGMA key='{key}'")
        count = verify_conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        verify_conn.close()

        if count == 0:
            encrypted_path.unlink()
            return json.dumps({"error": "Encryption verification failed — encrypted DB has no tables"})

        # Swap files
        os.replace(str(encrypted_path), str(DB_PATH))

        return json.dumps({
            "status": "encrypted",
            "backup": str(backup_path),
            "tables": count,
            "size_mb": round(DB_PATH.stat().st_size / 1e6, 2),
        })
    except Exception as e:
        # Restore backup on failure
        if backup_path.exists() and not DB_PATH.exists():
            shutil.copy2(backup_path, DB_PATH)
        if encrypted_path.exists():
            encrypted_path.unlink()
        return json.dumps({"error": f"Encryption failed: {e}", "backup": str(backup_path)})


def _verify_encrypted(key: str):
    """Verify an encrypted DB can be opened with the given key."""
    if not key:
        return json.dumps({"error": "Key required for verification"})
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        return json.dumps({"error": "sqlcipher3-wheels not installed"})

    try:
        conn = sqlcipher.connect(str(DB_PATH))
        conn.execute(f"PRAGMA key='{key}'")
        tables = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        agents = conn.execute("SELECT count(*) FROM agents").fetchone()[0]
        tasks = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        conn.close()
        return json.dumps({
            "verified": True,
            "tables": tables,
            "agents": agents,
            "tasks": tasks,
        })
    except Exception as e:
        return json.dumps({"verified": False, "error": str(e)})
