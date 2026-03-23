"""Tenant Admin — CRUD, per-tenant skill deployment, and dashboard endpoints.

v0.300.00b: Self-service tenant management with isolated skill directories.
Tenants get their own DB (via db.get_tenant_db_path) and skill directory
(fleet/tenants/<tenant_id>/skills/). Workers check tenant skills first,
falling back to global skills.

Dashboard endpoints registered as a Flask Blueprint (tenant_bp).
All mutating endpoints require admin role via RBAC.
"""
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from security import (
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

FLEET_DIR = Path(__file__).parent
TENANTS_DIR = FLEET_DIR / "tenants"

log = logging.getLogger("tenant_admin")

tenant_bp = Blueprint("tenant_admin", __name__)


# ── Config loader (local — avoids circular import with dashboard) ──────────

def _load_config():
    """Load fleet.toml config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    try:
        with open(FLEET_DIR / "fleet.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _require_role(role):
    """Convenience wrapper for role-based access."""
    return _require_role_raw(role, _load_config)


def _tenant_config():
    """Return enterprise.tenants config with defaults."""
    cfg = _load_config()
    tenants_cfg = cfg.get("enterprise", {}).get("tenants", {})
    return {
        "enabled": tenants_cfg.get("enabled", False),
        "max_tenants": tenants_cfg.get("max_tenants", 100),
        "tenant_data_dir": tenants_cfg.get("tenant_data_dir", "fleet/tenants"),
        "default_max_agents": tenants_cfg.get("default_max_agents", 5),
        "default_max_skills": tenants_cfg.get("default_max_skills", 50),
    }


# ── Tenant DB schema (stored in main fleet.db) ────────────────────────────

TENANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    config_json TEXT,
    created_at REAL NOT NULL,
    suspended_at REAL,
    deleted_at REAL,
    max_agents INTEGER DEFAULT 5,
    max_skills INTEGER DEFAULT 50
);
"""


def _get_conn():
    """Get a connection to the main fleet DB for tenant metadata."""
    import db
    return db.get_conn()


def _ensure_tenant_table():
    """Create the tenants table if it doesn't exist."""
    import db
    def _do():
        conn = _get_conn()
        conn.executescript(TENANT_SCHEMA)
        conn.close()
    db._retry_write(_do)


# ── Tenant CRUD ────────────────────────────────────────────────────────────

def create_tenant(name: str, config: dict | None = None) -> str:
    """Create a new tenant, return tenant_id.

    Raises ValueError if tenants feature is disabled or max_tenants reached.
    """
    import db

    tcfg = _tenant_config()
    if not tcfg["enabled"]:
        raise ValueError("Tenant management is disabled — set enterprise.tenants.enabled = true in fleet.toml")

    _ensure_tenant_table()

    tenant_id = uuid.uuid4().hex[:12]
    now = time.time()
    config_json = json.dumps(config or {})
    max_agents = (config or {}).get("max_agents", tcfg["default_max_agents"])
    max_skills = (config or {}).get("max_skills", tcfg["default_max_skills"])

    def _do():
        conn = _get_conn()
        # Check max_tenants limit
        count = conn.execute(
            "SELECT COUNT(*) FROM tenants WHERE status != 'deleted'"
        ).fetchone()[0]
        if count >= tcfg["max_tenants"]:
            raise ValueError(f"Max tenants ({tcfg['max_tenants']}) reached")
        conn.execute(
            "INSERT INTO tenants (id, name, status, config_json, created_at, max_agents, max_skills) "
            "VALUES (?, ?, 'active', ?, ?, ?, ?)",
            (tenant_id, name, config_json, now, max_agents, max_skills),
        )
        conn.commit()

    db._retry_write(_do)

    # Create tenant directories
    tenant_dir = TENANTS_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "skills").mkdir(exist_ok=True)

    # Initialize tenant's isolated DB
    db.get_tenant_db_path(tenant_id)

    log.info("Created tenant %s (%s)", tenant_id, name)
    return tenant_id


def get_tenant(tenant_id: str) -> dict | None:
    """Return tenant info dict, or None if not found."""
    _ensure_tenant_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
    ).fetchone()
    if not row:
        return None
    return dict(row)


def update_tenant(tenant_id: str, updates: dict) -> bool:
    """Update tenant config/limits. Returns True on success."""
    import db

    _ensure_tenant_table()

    allowed_fields = {"name", "config_json", "max_agents", "max_skills"}
    # Convert config dict to JSON if provided
    if "config" in updates:
        updates["config_json"] = json.dumps(updates.pop("config"))

    set_parts = []
    values = []
    for key, val in updates.items():
        if key in allowed_fields:
            set_parts.append(f"{key} = ?")
            values.append(val)

    if not set_parts:
        return False

    values.append(tenant_id)

    def _do():
        conn = _get_conn()
        conn.execute(
            f"UPDATE tenants SET {', '.join(set_parts)} WHERE id = ? AND status != 'deleted'",
            values,
        )
        conn.commit()

    db._retry_write(_do)
    log.info("Updated tenant %s: %s", tenant_id, list(updates.keys()))
    return True


def suspend_tenant(tenant_id: str) -> bool:
    """Suspend a tenant — tasks will be rejected."""
    import db

    _ensure_tenant_table()

    def _do():
        conn = _get_conn()
        conn.execute(
            "UPDATE tenants SET status = 'suspended', suspended_at = ? WHERE id = ? AND status = 'active'",
            (time.time(), tenant_id),
        )
        conn.commit()

    db._retry_write(_do)
    log.info("Suspended tenant %s", tenant_id)
    return True


def activate_tenant(tenant_id: str) -> bool:
    """Re-activate a suspended tenant."""
    import db

    _ensure_tenant_table()

    def _do():
        conn = _get_conn()
        conn.execute(
            "UPDATE tenants SET status = 'active', suspended_at = NULL WHERE id = ? AND status = 'suspended'",
            (tenant_id,),
        )
        conn.commit()

    db._retry_write(_do)
    log.info("Activated tenant %s", tenant_id)
    return True


def delete_tenant(tenant_id: str) -> bool:
    """Soft-delete a tenant — data retained per retention policy."""
    import db

    _ensure_tenant_table()

    def _do():
        conn = _get_conn()
        conn.execute(
            "UPDATE tenants SET status = 'deleted', deleted_at = ? WHERE id = ? AND status != 'deleted'",
            (time.time(), tenant_id),
        )
        conn.commit()

    db._retry_write(_do)
    log.info("Soft-deleted tenant %s", tenant_id)
    return True


def list_tenants(status: str | None = None) -> list[dict]:
    """List all tenants, optionally filtered by status."""
    _ensure_tenant_table()
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM tenants WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tenants WHERE status != 'deleted' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Per-Tenant Skill Deployment ────────────────────────────────────────────

def _tenant_skills_dir(tenant_id: str) -> Path:
    """Return the skill directory for a tenant, creating it if needed."""
    skills_dir = TENANTS_DIR / tenant_id / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def deploy_skill_to_tenant(tenant_id: str, skill_path: str) -> bool:
    """Copy a skill file to a tenant's skill directory.

    skill_path can be:
      - A skill name (resolved from fleet/skills/<name>.py)
      - An absolute path to a .py file

    Returns True on success. Raises ValueError on validation failure.
    """
    tenant = get_tenant(tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    if tenant["status"] != "active":
        raise ValueError(f"Tenant {tenant_id} is {tenant['status']}, cannot deploy skills")

    # Check max_skills limit
    current_skills = get_tenant_skills(tenant_id)
    if len(current_skills) >= tenant["max_skills"]:
        raise ValueError(f"Tenant {tenant_id} at skill limit ({tenant['max_skills']})")

    # Resolve skill path
    src = Path(skill_path)
    if not src.is_absolute():
        src = FLEET_DIR / "skills" / (skill_path if skill_path.endswith(".py") else f"{skill_path}.py")

    if not src.exists():
        raise ValueError(f"Skill file not found: {src}")
    if not src.suffix == ".py":
        raise ValueError("Skill file must be a .py file")

    # Validate it looks like a skill (has SKILL_NAME)
    content = src.read_text(encoding="utf-8", errors="replace")
    if "SKILL_NAME" not in content:
        raise ValueError(f"{src.name} does not appear to be a valid skill (missing SKILL_NAME)")

    dest_dir = _tenant_skills_dir(tenant_id)
    dest = dest_dir / src.name
    shutil.copy2(str(src), str(dest))

    log.info("Deployed skill %s to tenant %s", src.name, tenant_id)
    return True


def get_tenant_skills(tenant_id: str) -> list[dict]:
    """List skills available to a tenant."""
    skills_dir = _tenant_skills_dir(tenant_id)
    skills = []
    for py_file in sorted(skills_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        info = {"file": py_file.name, "name": py_file.stem}
        # Try to extract SKILL_NAME and DESCRIPTION
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("SKILL_NAME"):
                    info["skill_name"] = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("DESCRIPTION"):
                    desc = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    info["description"] = desc[:200]
        except Exception:
            log.warning("Failed to read skill metadata: %s", py_file.name)
        skills.append(info)
    return skills


def remove_tenant_skill(tenant_id: str, skill_name: str) -> bool:
    """Remove a skill from a tenant's skill directory."""
    skills_dir = _tenant_skills_dir(tenant_id)
    # Accept with or without .py extension
    filename = skill_name if skill_name.endswith(".py") else f"{skill_name}.py"
    target = skills_dir / filename

    if not target.exists():
        raise ValueError(f"Skill {skill_name} not found for tenant {tenant_id}")

    target.unlink()
    log.info("Removed skill %s from tenant %s", skill_name, tenant_id)
    return True


def resolve_skill(tenant_id: str | None, skill_name: str) -> Path | None:
    """Resolve a skill file — check tenant dir first, then global.

    Workers call this to find the correct skill file for a task.
    Returns the Path to the skill .py file, or None if not found.
    """
    if tenant_id:
        tenant_path = TENANTS_DIR / tenant_id / "skills" / f"{skill_name}.py"
        if tenant_path.exists():
            return tenant_path

    global_path = FLEET_DIR / "skills" / f"{skill_name}.py"
    if global_path.exists():
        return global_path

    return None


# ── Dashboard Blueprint ────────────────────────────────────────────────────

@tenant_bp.route("/api/tenants", methods=["GET"])
@_require_role("admin")
def api_list_tenants():
    """GET /api/tenants — list all tenants."""
    try:
        status = request.args.get("status")
        tenants = list_tenants(status=status)
        return jsonify({"tenants": tenants, "count": len(tenants)})
    except Exception as e:
        log.warning("list_tenants failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants", methods=["POST"])
@_require_role("admin")
def api_create_tenant():
    """POST /api/tenants — create tenant {name, config}."""
    try:
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        config = data.get("config", {})
        tenant_id = create_tenant(name, config)
        return jsonify({"tenant_id": tenant_id, "status": "created"}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("create_tenant failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>", methods=["GET"])
@_require_role("admin")
def api_get_tenant(tenant_id):
    """GET /api/tenants/<id> — tenant details."""
    try:
        tenant = get_tenant(tenant_id)
        if not tenant:
            return jsonify({"error": "tenant not found"}), 404
        # Attach skill count
        tenant["skill_count"] = len(get_tenant_skills(tenant_id))
        return jsonify(tenant)
    except Exception as e:
        log.warning("get_tenant failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>", methods=["PUT"])
@_require_role("admin")
def api_update_tenant(tenant_id):
    """PUT /api/tenants/<id> — update tenant."""
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({"error": "no update data provided"}), 400
        ok = update_tenant(tenant_id, data)
        if not ok:
            return jsonify({"error": "no valid fields to update"}), 400
        return jsonify({"status": "updated"})
    except Exception as e:
        log.warning("update_tenant failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>/suspend", methods=["POST"])
@_require_role("admin")
def api_suspend_tenant(tenant_id):
    """POST /api/tenants/<id>/suspend — suspend tenant."""
    try:
        suspend_tenant(tenant_id)
        return jsonify({"status": "suspended"})
    except Exception as e:
        log.warning("suspend_tenant failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>/activate", methods=["POST"])
@_require_role("admin")
def api_activate_tenant(tenant_id):
    """POST /api/tenants/<id>/activate — reactivate tenant."""
    try:
        activate_tenant(tenant_id)
        return jsonify({"status": "activated"})
    except Exception as e:
        log.warning("activate_tenant failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>", methods=["DELETE"])
@_require_role("admin")
def api_delete_tenant(tenant_id):
    """DELETE /api/tenants/<id> — soft delete tenant."""
    try:
        delete_tenant(tenant_id)
        return jsonify({"status": "deleted"})
    except Exception as e:
        log.warning("delete_tenant failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>/skills", methods=["GET"])
@_require_role("admin")
def api_tenant_skills(tenant_id):
    """GET /api/tenants/<id>/skills — tenant's skills."""
    try:
        tenant = get_tenant(tenant_id)
        if not tenant:
            return jsonify({"error": "tenant not found"}), 404
        skills = get_tenant_skills(tenant_id)
        return jsonify({"tenant_id": tenant_id, "skills": skills, "count": len(skills)})
    except Exception as e:
        log.warning("get_tenant_skills failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>/skills", methods=["POST"])
@_require_role("admin")
def api_deploy_tenant_skill(tenant_id):
    """POST /api/tenants/<id>/skills — deploy skill to tenant."""
    try:
        data = request.get_json(silent=True) or {}
        skill_path = data.get("skill", "").strip()
        if not skill_path:
            return jsonify({"error": "skill name or path is required"}), 400
        deploy_skill_to_tenant(tenant_id, skill_path)
        return jsonify({"status": "deployed", "skill": skill_path}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("deploy_skill failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500


@tenant_bp.route("/api/tenants/<tenant_id>/skills/<skill_name>", methods=["DELETE"])
@_require_role("admin")
def api_remove_tenant_skill(tenant_id, skill_name):
    """DELETE /api/tenants/<id>/skills/<name> — remove skill from tenant."""
    try:
        remove_tenant_skill(tenant_id, skill_name)
        return jsonify({"status": "removed", "skill": skill_name})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("remove_tenant_skill failed: %s", e)
        return jsonify({"error": _safe_error(e)}), 500
