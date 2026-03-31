"""Unit tests for fleet/security.py — RBAC, CSRF, error sanitization."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest

import security


# ── 1. PERMISSIONS — all five roles exist ─────────────────────────────────────

def test_permissions_has_five_roles():
    assert set(security.PERMISSIONS.keys()) == {
        "admin", "operator", "developer", "viewer", "auditor"
    }


# ── 2. admin has all permissions ──────────────────────────────────────────────

def test_admin_has_all_permissions():
    admin = security.PERMISSIONS["admin"]
    for action in ("read", "write", "delete", "configure", "deploy", "audit", "manage_users"):
        assert action in admin, f"admin missing: {action}"


# ── 3. viewer has only read / audit ───────────────────────────────────────────

def test_viewer_permissions():
    viewer = security.PERMISSIONS["viewer"]
    assert "read" in viewer
    assert "audit" in viewer
    assert "write" not in viewer
    assert "delete" not in viewer
    assert "deploy" not in viewer


# ── 4. developer can read, write, deploy — not delete/audit ──────────────────

def test_developer_permissions():
    dev = security.PERMISSIONS["developer"]
    assert {"read", "write", "deploy"}.issubset(dev)
    assert "delete" not in dev
    assert "manage_users" not in dev


# ── 5. auditor has read and audit only ───────────────────────────────────────

def test_auditor_permissions():
    auditor = security.PERMISSIONS["auditor"]
    assert "read" in auditor
    assert "audit" in auditor
    assert "write" not in auditor


# ── 6. check_permission — valid cases ────────────────────────────────────────

def test_check_permission_admin_can_delete():
    assert security.check_permission("admin", "delete") is True


def test_check_permission_viewer_cannot_write():
    assert security.check_permission("viewer", "write") is False


def test_check_permission_unknown_role():
    assert security.check_permission("superuser", "read") is False


def test_check_permission_unknown_action():
    assert security.check_permission("admin", "hack_everything") is False


# ── 7. safe_error — strips Windows paths ─────────────────────────────────────

def test_safe_error_strips_windows_path():
    err = Exception("Failed to open C:\\Users\\max\\secret\\data.json")
    result = security.safe_error(err)
    assert "C:\\" not in result
    assert "[path]" in result


def test_safe_error_strips_unix_path():
    err = Exception("FileNotFoundError: /home/user/fleet/secret.db")
    result = security.safe_error(err)
    assert "/home/user" not in result
    assert "[path]" in result


def test_safe_error_preserves_simple_messages():
    err = Exception("Connection refused")
    result = security.safe_error(err)
    assert "Connection refused" in result


# ── 8. CSRF token generate and validate ───────────────────────────────────────

def test_csrf_generate_and_validate():
    token = security.generate_csrf_token()
    assert len(token) == 64  # hex(32) = 64 chars
    assert security.validate_csrf_token(token) is True


def test_csrf_token_single_use():
    """Once consumed, the token should not be valid again."""
    token = security.generate_csrf_token()
    security.validate_csrf_token(token)  # consume
    assert security.validate_csrf_token(token) is False


def test_csrf_invalid_token():
    assert security.validate_csrf_token("bogus_token") is False


def test_csrf_empty_token():
    assert security.validate_csrf_token("") is False


def test_csrf_none_token():
    assert security.validate_csrf_token(None) is False


# ── 9. CSRF pool capped at 100 ────────────────────────────────────────────────

def test_csrf_pool_capped():
    """Pool should not grow beyond 100 tokens."""
    for _ in range(120):
        security.generate_csrf_token()
    assert len(security._csrf_tokens) <= 100


# ── 10. get_request_role — token resolution (with mock Flask request) ─────────

def test_get_request_role_admin_token():
    """Admin token in Authorization header maps to 'admin' role."""
    from unittest import mock
    mock_req = mock.MagicMock()
    mock_req.headers.get.side_effect = lambda k, *d: (
        "Bearer admin-secret" if k == "Authorization" else (d[0] if d else "")
    )
    mock_req.args.get.return_value = ""

    def loader():
        return {"security": {"admin_token": "admin-secret"}}

    with mock.patch("security.request", mock_req):
        role = security.get_request_role(loader, req=mock_req)
    assert role == "admin"


def test_get_request_role_defaults_to_viewer():
    """No matching token defaults to viewer."""
    from unittest import mock
    mock_req = mock.MagicMock()
    mock_req.headers.get.return_value = ""
    mock_req.args.get.return_value = ""

    def loader():
        return {"security": {}}

    with mock.patch("security.request", mock_req):
        role = security.get_request_role(loader, req=mock_req)
    assert role == "viewer"
