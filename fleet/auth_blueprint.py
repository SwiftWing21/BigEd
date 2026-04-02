"""Auth blueprint — local user profiles, login, sessions.

Provides REST endpoints for user profile management and session-based
authentication. Spec: docs/superpowers/specs/2026-04-01-local-user-profiles-design.md
"""
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, make_response

from dashboard_utils import (
    _require_role,
    _get_request_role,
    get_conn,
)

log = logging.getLogger("dashboard.auth")

auth_bp = Blueprint("auth", __name__)

SESSION_TTL_DAYS = 7


# ── Password helpers ────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    """Hash password with PBKDF2-SHA256 + random salt. Returns 'salt_hex$hash_hex'."""
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
    return f"{salt.hex()}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored 'salt_hex$hash_hex'."""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
        return h.hex() == hash_hex
    except Exception:
        return False


# ── Session helpers ─────────────────────────────────────────────────────────


def _create_session(user_id: int, ip_address: str = "") -> str:
    """Create a session token and store in DB. Returns the token."""
    import db as _db

    token = secrets.token_urlsafe(32)
    expires = (datetime.now(tz=None) + timedelta(days=SESSION_TTL_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    def _do():
        with _db.get_conn() as conn:
            conn.execute(
                "INSERT INTO user_sessions (token, user_id, expires_at, ip_address) "
                "VALUES (?, ?, ?, ?)",
                (token, user_id, expires, ip_address),
            )

    _db._retry_write(_do)

    # Update last_login
    def _update_login():
        with _db.get_conn() as conn:
            conn.execute(
                "UPDATE user_profiles SET last_login = datetime('now') WHERE id = ?",
                (user_id,),
            )

    _db._retry_write(_update_login)

    return token


def _set_session_cookie(response, token: str):
    """Set the biged_session cookie on a response."""
    response.set_cookie(
        "biged_session",
        token,
        httponly=True,
        samesite="Strict",
        path="/",
        max_age=SESSION_TTL_DAYS * 86400,
    )
    return response


def _profile_dict(row) -> dict:
    """Convert a user_profiles row to a safe dict (no password_hash)."""
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "avatar_color": row["avatar_color"],
        "created_at": row["created_at"],
        "last_login": row["last_login"],
        "is_active": row["is_active"],
        "has_password": bool(row["password_hash"]),
        "auto_start": bool(row["auto_start"]) if row["auto_start"] is not None else False,
    }


# ── Endpoints ───────────────────────────────────────────────────────────────


@auth_bp.route("/api/auth/profiles", methods=["GET"])
def api_auth_list_profiles():
    """List all active profiles (no passwords)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM user_profiles WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    return jsonify([_profile_dict(r) for r in rows])


@auth_bp.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    """First-run: create initial admin profile. Only works when no profiles exist."""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) as c FROM user_profiles").fetchone()["c"]
    if count > 0:
        return jsonify({"error": "A profile already exists. Use the login screen to sign in.", "code": "ALREADY_SETUP"}), 400

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    display_name = (data.get("display_name") or "").strip()
    password = data.get("password", "")

    if not username or not display_name:
        return jsonify({"error": "Please enter both a username and display name."}), 400

    password_hash = _hash_password(password) if password else None

    import db as _db

    new_id = None

    def _do():
        nonlocal new_id
        with _db.get_conn() as c:
            cur = c.execute(
                "INSERT INTO user_profiles (username, display_name, role, password_hash) "
                "VALUES (?, ?, 'admin', ?)",
                (username, display_name, password_hash),
            )
            new_id = cur.lastrowid

    _db._retry_write(_do)

    token = _create_session(new_id, ip_address=request.remote_addr or "")
    profile = get_conn().execute(
        "SELECT * FROM user_profiles WHERE id = ?", (new_id,)
    ).fetchone()

    resp = make_response(jsonify({"ok": True, "profile": _profile_dict(profile)}))
    _set_session_cookie(resp, token)
    return resp


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Login with username + optional password. Sets session cookie."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password", "")

    if not username:
        return jsonify({"error": "username is required"}), 400

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM user_profiles WHERE username = ? AND is_active = 1",
        (username,),
    ).fetchone()

    if not row:
        return jsonify({"error": "No account found with that username."}), 404

    # Verify password if one is set
    if row["password_hash"]:
        if not password or not _verify_password(password, row["password_hash"]):
            return jsonify({"error": "Incorrect password. Please try again."}), 401
    # If no password set, allow login without one

    token = _create_session(row["id"], ip_address=request.remote_addr or "")
    resp = make_response(jsonify({"ok": True, "profile": _profile_dict(row)}))
    _set_session_cookie(resp, token)
    return resp


@auth_bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Logout — delete session from DB, clear cookie."""
    session_token = request.cookies.get("biged_session", "")
    if session_token:
        import db as _db

        def _do():
            with _db.get_conn() as conn:
                conn.execute(
                    "DELETE FROM user_sessions WHERE token = ?", (session_token,)
                )

        try:
            _db._retry_write(_do)
        except Exception:
            log.warning("Failed to delete session", exc_info=True)

    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie("biged_session", "", expires=0, path="/")
    return resp


@auth_bp.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    """Return current user from session cookie, or guest."""
    session_token = request.cookies.get("biged_session", "")
    if session_token:
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT u.* FROM user_sessions s "
                "JOIN user_profiles u ON s.user_id = u.id "
                "WHERE s.token = ? AND s.expires_at > datetime('now') AND u.is_active = 1",
                (session_token,),
            ).fetchone()
            if row:
                return jsonify(_profile_dict(row))
        except Exception:
            log.warning("Session lookup failed", exc_info=True)

    return jsonify({"guest": True, "role": "viewer"})


@auth_bp.route("/api/auth/profiles", methods=["POST"])
@_require_role("admin")
def api_auth_create_profile():
    """Admin: create a new profile."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    display_name = (data.get("display_name") or "").strip()
    role = data.get("role", "operator")
    password = data.get("password", "")
    avatar_color = data.get("avatar_color", "#3b82f6")

    if not username or not display_name:
        return jsonify({"error": "username and display_name are required"}), 400

    if role not in ("admin", "operator", "viewer"):
        return jsonify({"error": "role must be admin, operator, or viewer"}), 400

    password_hash = _hash_password(password) if password else None

    import db as _db

    new_id = None

    def _do():
        nonlocal new_id
        with _db.get_conn() as c:
            cur = c.execute(
                "INSERT INTO user_profiles (username, display_name, role, password_hash, avatar_color) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, display_name, role, password_hash, avatar_color),
            )
            new_id = cur.lastrowid

    try:
        _db._retry_write(_do)
    except Exception as exc:
        if "UNIQUE" in str(exc):
            return jsonify({"error": "That username is already taken. Please choose another."}), 409
        log.warning("Failed to create profile", exc_info=True)
        return jsonify({"error": "Failed to create profile"}), 500

    profile = get_conn().execute(
        "SELECT * FROM user_profiles WHERE id = ?", (new_id,)
    ).fetchone()
    return jsonify({"ok": True, "profile": _profile_dict(profile)}), 201


@auth_bp.route("/api/auth/profiles/<int:profile_id>", methods=["PUT"])
def api_auth_update_profile(profile_id):
    """Update profile. Admin can update any; users can update self."""
    # Check who is making this request
    session_token = request.cookies.get("biged_session", "")
    current_user_id = None
    current_role = "viewer"

    if session_token:
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT u.id, u.role FROM user_sessions s "
                "JOIN user_profiles u ON s.user_id = u.id "
                "WHERE s.token = ? AND s.expires_at > datetime('now') AND u.is_active = 1",
                (session_token,),
            ).fetchone()
            if row:
                current_user_id = row["id"]
                current_role = row["role"]
        except Exception:
            pass

    is_self = current_user_id == profile_id
    if current_role != "admin" and not is_self:
        return jsonify({"error": "insufficient permissions"}), 403

    data = request.get_json(silent=True) or {}
    updates = []
    params = []

    if "display_name" in data:
        updates.append("display_name = ?")
        params.append(data["display_name"].strip())

    if "avatar_color" in data:
        updates.append("avatar_color = ?")
        params.append(data["avatar_color"])

    if "auto_start" in data:
        updates.append("auto_start = ?")
        params.append(1 if data["auto_start"] else 0)

    if "password" in data:
        pw = data["password"]
        if pw:
            updates.append("password_hash = ?")
            params.append(_hash_password(pw))
        else:
            updates.append("password_hash = NULL")

    # Only admin can change roles
    if "role" in data and current_role == "admin":
        role = data["role"]
        if role in ("admin", "operator", "viewer"):
            updates.append("role = ?")
            params.append(role)

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    import db as _db

    params.append(profile_id)

    def _do():
        with _db.get_conn() as c:
            c.execute(
                f"UPDATE user_profiles SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    try:
        _db._retry_write(_do)
    except Exception:
        log.warning("Failed to update profile %d", profile_id, exc_info=True)
        return jsonify({"error": "Failed to update profile"}), 500

    profile = get_conn().execute(
        "SELECT * FROM user_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    if not profile:
        return jsonify({"error": "Profile not found"}), 404

    return jsonify({"ok": True, "profile": _profile_dict(profile)})


@auth_bp.route("/api/auth/profiles/<int:profile_id>", methods=["DELETE"])
@_require_role("admin")
def api_auth_delete_profile(profile_id):
    """Admin: deactivate a profile."""
    import db as _db

    def _do():
        with _db.get_conn() as c:
            c.execute(
                "UPDATE user_profiles SET is_active = 0 WHERE id = ?",
                (profile_id,),
            )
            # Also delete their sessions
            c.execute(
                "DELETE FROM user_sessions WHERE user_id = ?",
                (profile_id,),
            )

    try:
        _db._retry_write(_do)
    except Exception:
        log.warning("Failed to deactivate profile %d", profile_id, exc_info=True)
        return jsonify({"error": "Failed to deactivate profile"}), 500

    return jsonify({"ok": True})
