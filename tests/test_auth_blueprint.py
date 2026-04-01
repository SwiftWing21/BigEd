"""Unit tests for fleet/auth_blueprint.py — local user profiles auth."""
import sys
import os

import pytest

# Put fleet/ on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

from flask import Flask


def _ensure_auth_tables(db_module):
    """Create user_profiles + user_sessions tables for testing."""
    with db_module.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            password_hash TEXT,
            avatar_color TEXT DEFAULT '#3b82f6',
            created_at TEXT DEFAULT (datetime('now')),
            last_login TEXT,
            is_active INTEGER DEFAULT 1,
            sso_user_id TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES user_profiles(id),
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            ip_address TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions(expires_at)")


def _make_app(tmp_db):
    """Create a Flask test app with auth_bp registered and a fresh DB."""
    import db as _db
    import auth_blueprint as ab

    _ensure_auth_tables(_db)

    app = Flask(__name__)
    app.register_blueprint(ab.auth_bp)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(tmp_db):
    app = _make_app(tmp_db)
    with app.test_client() as c:
        yield c


# ── 1. Setup (first profile creation) ──────────────────────────────────────


def test_setup_creates_admin(client):
    resp = client.post("/api/auth/setup", json={
        "username": "admin",
        "display_name": "Admin User",
        "password": "secret123",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["profile"]["role"] == "admin"
    assert data["profile"]["username"] == "admin"
    assert data["profile"]["has_password"] is True
    # Session cookie should be set
    cookie = client.get_cookie("biged_session")
    assert cookie is not None


def test_setup_fails_when_profiles_exist(client):
    # First setup
    client.post("/api/auth/setup", json={
        "username": "admin",
        "display_name": "Admin",
    })
    # Second setup should fail
    resp = client.post("/api/auth/setup", json={
        "username": "admin2",
        "display_name": "Admin 2",
    })
    assert resp.status_code == 400
    assert "already completed" in resp.get_json()["error"]


def test_setup_requires_fields(client):
    resp = client.post("/api/auth/setup", json={"username": "", "display_name": ""})
    assert resp.status_code == 400


# ── 2. Login with/without password ─────────────────────────────────────────


def test_login_no_password(client):
    # Create profile without password
    client.post("/api/auth/setup", json={
        "username": "nopw",
        "display_name": "No Password",
    })
    # Clear cookies
    client.delete_cookie("biged_session")

    resp = client.post("/api/auth/login", json={"username": "nopw"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_login_with_password(client):
    client.post("/api/auth/setup", json={
        "username": "pwuser",
        "display_name": "PW User",
        "password": "mypass",
    })
    client.delete_cookie("biged_session")

    # Wrong password
    resp = client.post("/api/auth/login", json={
        "username": "pwuser",
        "password": "wrong",
    })
    assert resp.status_code == 401

    # No password when one is required
    resp = client.post("/api/auth/login", json={"username": "pwuser"})
    assert resp.status_code == 401

    # Correct password
    resp = client.post("/api/auth/login", json={
        "username": "pwuser",
        "password": "mypass",
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_login_unknown_user(client):
    resp = client.post("/api/auth/login", json={"username": "nobody"})
    assert resp.status_code == 404


# ── 3. Session cookie set/validate ─────────────────────────────────────────


def test_session_cookie_set_on_login(client):
    client.post("/api/auth/setup", json={
        "username": "sess",
        "display_name": "Session User",
    })
    cookie = client.get_cookie("biged_session")
    assert cookie is not None
    assert len(cookie.value) > 20  # token_urlsafe(32) is ~43 chars


# ── 4. /api/auth/me returns correct role ───────────────────────────────────


def test_me_returns_profile(client):
    client.post("/api/auth/setup", json={
        "username": "meuser",
        "display_name": "Me User",
    })
    resp = client.get("/api/auth/me")
    data = resp.get_json()
    assert data["username"] == "meuser"
    assert data["role"] == "admin"


def test_me_returns_guest_without_session(client):
    resp = client.get("/api/auth/me")
    data = resp.get_json()
    assert data["guest"] is True
    assert data["role"] == "viewer"


# ── 5. Admin can create profiles, viewer cannot ────────────────────────────


def test_admin_creates_profile(client):
    # Setup admin
    client.post("/api/auth/setup", json={
        "username": "boss",
        "display_name": "Boss",
    })
    # Create another profile
    resp = client.post("/api/auth/profiles", json={
        "username": "worker",
        "display_name": "Worker Bee",
        "role": "operator",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["profile"]["role"] == "operator"


def test_viewer_cannot_create_profile(client):
    # No session = viewer role
    resp = client.post("/api/auth/profiles", json={
        "username": "hacker",
        "display_name": "Hacker",
    })
    assert resp.status_code == 403


def test_list_profiles(client):
    client.post("/api/auth/setup", json={
        "username": "lister",
        "display_name": "Lister",
    })
    resp = client.get("/api/auth/profiles")
    data = resp.get_json()
    assert len(data) >= 1
    assert data[0]["username"] == "lister"
    # No password_hash in response
    assert "password_hash" not in data[0]


# ── 6. Logout clears session ──────────────────────────────────────────────


def test_logout_clears_session(client):
    client.post("/api/auth/setup", json={
        "username": "logme",
        "display_name": "Log Me",
    })
    # Confirm we have a session
    resp = client.get("/api/auth/me")
    assert "guest" not in resp.get_json()

    # Logout
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    # Cookie should be cleared — me should return guest
    resp = client.get("/api/auth/me")
    assert resp.get_json().get("guest") is True


# ── 7. Update and delete profiles ──────────────────────────────────────────


def test_admin_deactivates_profile(client):
    client.post("/api/auth/setup", json={
        "username": "admin",
        "display_name": "Admin",
    })
    client.post("/api/auth/profiles", json={
        "username": "target",
        "display_name": "Target",
        "role": "viewer",
    })
    # Get the target profile ID
    profiles = client.get("/api/auth/profiles").get_json()
    target_id = [p for p in profiles if p["username"] == "target"][0]["id"]

    resp = client.delete(f"/api/auth/profiles/{target_id}")
    assert resp.status_code == 200

    # Profile should no longer appear in list
    profiles = client.get("/api/auth/profiles").get_json()
    usernames = [p["username"] for p in profiles]
    assert "target" not in usernames
