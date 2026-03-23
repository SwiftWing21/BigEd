"""
Tenant Key Management + Encrypted Storage (v0.300.00b).

Per-tenant Fernet encryption with master-key-encrypted keystore.
Keys stored in fleet/data/tenant_keys.db, master key derived from
BIGED_MASTER_KEY env var or auto-generated at fleet/certs/master.key.

Usage:
    from tenant_crypto import encrypt_field, decrypt_field
    ct = encrypt_field("tenant_abc", "sensitive data")
    pt = decrypt_field("tenant_abc", ct)
"""
import base64
import logging
import os
import sqlite3
import time
import random
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("tenant_crypto")

FLEET_DIR = Path(__file__).parent
CERTS_DIR = FLEET_DIR / "certs"
DATA_DIR = FLEET_DIR / "data"
KEYSTORE_PATH = DATA_DIR / "tenant_keys.db"
MASTER_KEY_PATH = CERTS_DIR / "master.key"

# ── Keystore schema ─────────────────────────────────────────────────────────

_KEYSTORE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS tenant_keys (
    tenant_id       TEXT PRIMARY KEY,
    encrypted_key   BLOB NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    rotated_at      TEXT
);
"""


def _get_keystore_conn() -> sqlite3.Connection:
    """Open (and auto-create) the tenant keystore DB."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(KEYSTORE_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_KEYSTORE_SCHEMA)
    return conn


def _retry_write(fn, retries=8):
    """Retry a write with jittered backoff (mirrors db._retry_write)."""
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == retries - 1:
                raise
            time.sleep(0.2 * (2 ** attempt) + random.uniform(0, 0.1))


# ── Config helper ────────────────────────────────────────────────────────────

def _load_encryption_config() -> dict:
    """Load [enterprise.encryption] from fleet.toml."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    toml_path = FLEET_DIR / "fleet.toml"
    if not toml_path.exists():
        return {}
    try:
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("enterprise", {}).get("encryption", {})
    except Exception:
        log.warning("Failed to load encryption config", exc_info=True)
        return {}


def is_encryption_enabled() -> bool:
    """Check if enterprise encryption is enabled in fleet.toml."""
    return _load_encryption_config().get("enabled", False)


def get_key_rotation_days() -> int:
    """Max key age before rotation is recommended."""
    return _load_encryption_config().get("key_rotation_days", 90)


# ── Master key ───────────────────────────────────────────────────────────────

def get_master_key() -> bytes:
    """Derive master Fernet key from env var or auto-generated file.

    Priority:
      1. BIGED_MASTER_KEY env var (base64-encoded 32-byte key)
      2. fleet/certs/master.key file (auto-generated on first call)

    The master key encrypts per-tenant keys in the keystore.
    Never logged, never returned in API responses.
    """
    from cryptography.fernet import Fernet

    # 1. Env var takes priority
    env_key = os.environ.get("BIGED_MASTER_KEY", "")
    if env_key:
        # Validate it is a valid Fernet key
        try:
            Fernet(env_key.encode() if isinstance(env_key, str) else env_key)
            return env_key.encode() if isinstance(env_key, str) else env_key
        except Exception:
            log.warning("BIGED_MASTER_KEY is set but not a valid Fernet key, regenerating")

    # 2. File-based key
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    if MASTER_KEY_PATH.exists():
        try:
            key = MASTER_KEY_PATH.read_bytes().strip()
            Fernet(key)  # validate
            return key
        except Exception:
            log.warning("master.key is corrupt, regenerating")

    # 3. Auto-generate
    key = Fernet.generate_key()
    MASTER_KEY_PATH.write_bytes(key)
    # Restrict file permissions (best-effort on Windows)
    try:
        os.chmod(str(MASTER_KEY_PATH), 0o600)
    except Exception:
        pass  # Windows may not support POSIX perms
    log.info("Auto-generated master encryption key at %s", MASTER_KEY_PATH)
    return key


def _master_fernet():
    """Get a Fernet instance for the master key."""
    from cryptography.fernet import Fernet
    return Fernet(get_master_key())


# ── Per-tenant key management ────────────────────────────────────────────────

def generate_tenant_key(tenant_id: str) -> bytes:
    """Generate a new Fernet key for a tenant and store it encrypted.

    Returns the raw tenant Fernet key (bytes). The keystore stores it
    encrypted under the master key.

    Raises ValueError if tenant already has a key (use rotate_tenant_key).
    """
    from cryptography.fernet import Fernet

    if not tenant_id:
        raise ValueError("tenant_id is required")

    conn = _get_keystore_conn()
    try:
        existing = conn.execute(
            "SELECT tenant_id FROM tenant_keys WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if existing:
            raise ValueError(f"Tenant '{tenant_id}' already has a key — use rotate_tenant_key()")

        tenant_key = Fernet.generate_key()
        encrypted = _master_fernet().encrypt(tenant_key)
        now = datetime.now(timezone.utc).isoformat()

        def _do():
            conn.execute(
                "INSERT INTO tenant_keys (tenant_id, encrypted_key, created_at) VALUES (?, ?, ?)",
                (tenant_id, encrypted, now),
            )
            conn.commit()

        _retry_write(_do)
        log.info("Generated encryption key for tenant '%s'", tenant_id)
        return tenant_key
    finally:
        conn.close()


def get_tenant_key(tenant_id: str) -> bytes:
    """Retrieve and decrypt the Fernet key for a tenant.

    Raises KeyError if tenant has no key.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")

    conn = _get_keystore_conn()
    try:
        row = conn.execute(
            "SELECT encrypted_key FROM tenant_keys WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"No encryption key for tenant '{tenant_id}'")
        encrypted = row["encrypted_key"]
        # encrypted_key is stored as BLOB; may be bytes or memoryview
        if isinstance(encrypted, memoryview):
            encrypted = bytes(encrypted)
        return _master_fernet().decrypt(encrypted)
    finally:
        conn.close()


def rotate_tenant_key(tenant_id: str) -> bytes:
    """Rotate a tenant's encryption key.

    Generates a new Fernet key, re-encrypts it under the master key,
    and updates the keystore. The caller is responsible for re-encrypting
    any data that was encrypted with the old key.

    Returns the new raw tenant Fernet key.
    """
    from cryptography.fernet import Fernet

    if not tenant_id:
        raise ValueError("tenant_id is required")

    conn = _get_keystore_conn()
    try:
        row = conn.execute(
            "SELECT tenant_id FROM tenant_keys WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"No encryption key for tenant '{tenant_id}' — generate first")

        new_key = Fernet.generate_key()
        encrypted = _master_fernet().encrypt(new_key)
        now = datetime.now(timezone.utc).isoformat()

        def _do():
            conn.execute(
                "UPDATE tenant_keys SET encrypted_key = ?, rotated_at = ? WHERE tenant_id = ?",
                (encrypted, now, tenant_id),
            )
            conn.commit()

        _retry_write(_do)
        log.info("Rotated encryption key for tenant '%s'", tenant_id)
        return new_key
    finally:
        conn.close()


def get_key_status(tenant_id: str) -> dict:
    """Get key metadata for a tenant (no secrets exposed).

    Returns dict with created_at, rotated_at, age_days, rotation_needed.
    """
    conn = _get_keystore_conn()
    try:
        row = conn.execute(
            "SELECT created_at, rotated_at FROM tenant_keys WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            return {"exists": False, "tenant_id": tenant_id}

        created = row["created_at"]
        rotated = row["rotated_at"]
        # Calculate age from last rotation (or creation if never rotated)
        ref_date = rotated or created
        try:
            ref_dt = datetime.fromisoformat(ref_date).replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ref_dt).days
        except Exception:
            age_days = -1

        max_age = get_key_rotation_days()
        return {
            "exists": True,
            "tenant_id": tenant_id,
            "created_at": created,
            "rotated_at": rotated,
            "age_days": age_days,
            "rotation_needed": age_days >= max_age if age_days >= 0 else False,
            "max_age_days": max_age,
        }
    finally:
        conn.close()


def list_tenants_with_status() -> list[dict]:
    """List all tenants in the keystore with encryption status."""
    conn = _get_keystore_conn()
    try:
        rows = conn.execute(
            "SELECT tenant_id, created_at, rotated_at FROM tenant_keys ORDER BY tenant_id"
        ).fetchall()
        results = []
        max_age = get_key_rotation_days()
        for row in rows:
            created = row["created_at"]
            rotated = row["rotated_at"]
            ref_date = rotated or created
            try:
                ref_dt = datetime.fromisoformat(ref_date).replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - ref_dt).days
            except Exception:
                age_days = -1
            results.append({
                "tenant_id": row["tenant_id"],
                "created_at": created,
                "rotated_at": rotated,
                "age_days": age_days,
                "rotation_needed": age_days >= max_age if age_days >= 0 else False,
                "encrypted": True,
            })
        return results
    finally:
        conn.close()


# ── Field-level encryption ───────────────────────────────────────────────────

def _tenant_fernet(tenant_id: str):
    """Get a Fernet instance for a tenant's key."""
    from cryptography.fernet import Fernet
    return Fernet(get_tenant_key(tenant_id))


def encrypt_field(tenant_id: str, plaintext: str) -> str:
    """Encrypt a string field. Returns URL-safe base64 ciphertext."""
    if not plaintext:
        return ""
    token = _tenant_fernet(tenant_id).encrypt(plaintext.encode("utf-8"))
    return base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_field(tenant_id: str, ciphertext: str) -> str:
    """Decrypt a base64 ciphertext back to plaintext string."""
    if not ciphertext:
        return ""
    token = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    return _tenant_fernet(tenant_id).decrypt(token).decode("utf-8")


# ── File-level encryption ────────────────────────────────────────────────────

def encrypt_file(tenant_id: str, path: str) -> None:
    """Encrypt a file in-place using the tenant's Fernet key.

    Reads the file, encrypts the content, writes back the ciphertext.
    The original plaintext is overwritten — keep backups if needed.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    plaintext = file_path.read_bytes()
    ciphertext = _tenant_fernet(tenant_id).encrypt(plaintext)
    file_path.write_bytes(ciphertext)
    log.info("Encrypted file '%s' for tenant '%s'", path, tenant_id)


def decrypt_file(tenant_id: str, path: str) -> None:
    """Decrypt a file in-place using the tenant's Fernet key.

    Reads the encrypted content, decrypts, writes back plaintext.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    ciphertext = file_path.read_bytes()
    try:
        plaintext = _tenant_fernet(tenant_id).decrypt(ciphertext)
    except Exception:
        raise ValueError(f"Failed to decrypt '{path}' — wrong key or corrupted data")
    file_path.write_bytes(plaintext)
    log.info("Decrypted file '%s' for tenant '%s'", path, tenant_id)
