"""Tenant Key Management — Dashboard REST API (v0.300.00b).

Blueprint providing /api/tenants/* endpoints for encryption key status,
rotation, and tenant listing.

Registered in dashboard.py alongside fleet_bp, health_bp, a2a_bp.
"""
import logging
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

from security import (
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

log = logging.getLogger("tenant_crypto_api")
tenant_crypto_bp = Blueprint("tenant_crypto", __name__)


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


# ── GET /api/tenants — list tenants with encryption status ───────────────────

@tenant_crypto_bp.route("/api/tenants")
def api_tenants_list():
    """List all tenants with encryption key status."""
    try:
        from tenant_crypto import list_tenants_with_status, is_encryption_enabled
        tenants = list_tenants_with_status()
        return jsonify({
            "tenants": tenants,
            "total": len(tenants),
            "encryption_enabled": is_encryption_enabled(),
        })
    except Exception as e:
        log.warning("Failed to list tenants: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ── GET /api/tenants/<id>/key-status — key age, rotation needed ──────────────

@tenant_crypto_bp.route("/api/tenants/<tenant_id>/key-status")
def api_tenant_key_status(tenant_id):
    """Get encryption key status for a specific tenant.

    Returns key age, creation date, last rotation, and whether rotation
    is recommended based on enterprise.encryption.key_rotation_days.
    """
    try:
        from tenant_crypto import get_key_status
        status = get_key_status(tenant_id)
        return jsonify(status)
    except Exception as e:
        log.warning("Failed to get key status for tenant '%s': %s", tenant_id, e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/tenants/<id>/rotate-key — trigger key rotation (admin) ─────────

@tenant_crypto_bp.route("/api/tenants/<tenant_id>/rotate-key", methods=["POST"])
@_require_role("admin")
def api_tenant_rotate_key(tenant_id):
    """Rotate the encryption key for a tenant. Requires admin role.

    The old key is replaced. Caller must re-encrypt any data that was
    encrypted with the previous key (use the data migration endpoint or
    handle offline).
    """
    try:
        from tenant_crypto import rotate_tenant_key, get_key_status
        rotate_tenant_key(tenant_id)
        status = get_key_status(tenant_id)
        return jsonify({
            "ok": True,
            "message": f"Key rotated for tenant '{tenant_id}'",
            "status": status,
        })
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log.warning("Failed to rotate key for tenant '%s': %s", tenant_id, e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


# ── POST /api/tenants/<id>/generate-key — create initial key (admin) ─────────

@tenant_crypto_bp.route("/api/tenants/<tenant_id>/generate-key", methods=["POST"])
@_require_role("admin")
def api_tenant_generate_key(tenant_id):
    """Generate an encryption key for a new tenant. Requires admin role."""
    try:
        from tenant_crypto import generate_tenant_key, get_key_status
        generate_tenant_key(tenant_id)
        status = get_key_status(tenant_id)
        return jsonify({
            "ok": True,
            "message": f"Key generated for tenant '{tenant_id}'",
            "status": status,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 409  # Conflict — key already exists
    except Exception as e:
        log.warning("Failed to generate key for tenant '%s': %s", tenant_id, e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500
