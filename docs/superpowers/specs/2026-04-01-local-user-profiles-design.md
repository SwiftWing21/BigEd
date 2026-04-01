# Local User Profiles — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Option B (local profiles) with Option C (SSO/OIDC) roadmapped

## Problem

The dashboard has zero auth — all API calls default to `viewer` role. This means:
- Role-gated endpoints (operator/admin) are either broken or downgraded
- No audit trail of who approved/rejected outputs
- No way to distinguish between users on a shared machine
- Showing the app to others gives them full access with no accountability

## Design

### DB Schema

New table `user_profiles` in fleet.db:

```sql
CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',  -- admin | operator | viewer
    password_hash TEXT,                      -- bcrypt hash, NULL = no password
    avatar_color TEXT DEFAULT '#3b82f6',     -- profile color for UI
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT,
    is_active INTEGER DEFAULT 1
);
```

Migration: `fleet/migrations/011_user_profiles.py`

### First-Run Setup

On first launch (no profiles exist):
1. Dashboard shows a "Create Your Profile" screen instead of the login screen
2. Fields: display name, username, optional password
3. First profile is always `admin` role
4. After creation, auto-logged in with session cookie

### Login Flow

1. Dashboard loads → checks for session cookie (`biged_session`)
2. No cookie or expired → show login screen (profile picker + password if set)
3. Valid cookie → proceed to dashboard with role from profile
4. Session stored in `user_sessions` table (not in-memory — survives restart)

### Session Management

New table `user_sessions`:

```sql
CREATE TABLE IF NOT EXISTS user_sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id),
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    ip_address TEXT
);
```

- Session token: `secrets.token_urlsafe(32)`
- Cookie: `biged_session=<token>; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800`
- Expiry: 7 days (configurable in fleet.toml `[auth] session_ttl_days = 7`)
- Login refreshes expiry

### API Endpoints

New `fleet/auth_blueprint.py`:

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/auth/profiles` | GET | none | List profiles (username, display_name, avatar_color, role — no password info) |
| `/api/auth/login` | POST | none | `{username, password?}` → set session cookie, return profile |
| `/api/auth/logout` | POST | any | Clear session cookie + DB row |
| `/api/auth/me` | GET | any | Return current user profile (or `{guest: true, role: "viewer"}` if no session) |
| `/api/auth/setup` | POST | none | First-run: create initial admin profile |
| `/api/auth/profiles` | POST | admin | Create new profile |
| `/api/auth/profiles/<id>` | PUT | admin | Update profile (name, role, password) |
| `/api/auth/profiles/<id>` | DELETE | admin | Deactivate profile |

### Security Integration

Modify `fleet/security.py` `get_request_role()`:

```python
def get_request_role(config_loader, req=None):
    # 1. Check biged_session cookie → lookup user_sessions → get role
    # 2. Existing SSO session check (unchanged)
    # 3. Existing token check (unchanged)
    # 4. Fall back to "viewer" (guest)
```

This means ALL existing `@_require_role()` decorators work automatically — no changes needed to any blueprint.

### Login UI

When no valid session exists, the dashboard shows a login overlay:

- Full-screen overlay on top of dashboard (dashboard still loads underneath)
- Profile cards in a grid (avatar color circle + display name + role badge)
- Click a profile → if password set, show password input; if not, log in immediately
- First-run: "Welcome to BigEd CC" → create profile form
- After login: overlay fades away, dashboard is revealed

### Feedback Audit Trail

Modify `db.submit_feedback()` to accept optional `reviewer` parameter:
```python
def submit_feedback(output_path, verdict, feedback_text="", agent_name="", skill_type="", reviewer=""):
```

The outputs blueprint passes the current user's display_name from the session.

### Profile Management UI

New section in Settings page → "User Profiles" tab:
- List all profiles with role badges
- Admin can: create new profiles, change roles, set/reset passwords, deactivate
- Any user can: change their own display name and password

### Password Handling

- `hashlib.pbkdf2_hmac('sha256', password, salt, 100000)` — no external dependency needed
- Salt: `os.urandom(16)`, stored as hex prefix in password_hash (`salt_hex$hash_hex`)
- Empty password = no password required (click to login)
- Password optional for all profiles (convenience for single-user setups)

### Roadmap: Option C (SSO/OIDC)

Already partially built in `fleet/sso.py`:
- OIDC discovery, authorization flow, token validation
- `sso_sessions` table exists
- `handle_callback()` creates sessions

To activate for multi-tenant/enterprise:
1. Add SSO provider config to Settings UI (provider URL, client ID, client secret)
2. Add "Sign in with SSO" button to login screen alongside local profiles
3. SSO login creates a local profile automatically (linked via `sso_user_id` column, add to schema)
4. Role mapping: SSO claims → local roles (configurable)
5. Session management shared between local and SSO (same `user_sessions` table)

This is NOT built now — just the schema extension (`sso_user_id` column on `user_profiles`) to avoid future migration pain.

## Files

| File | Action | Purpose |
|------|--------|---------|
| `fleet/migrations/011_user_profiles.py` | Create | Schema for user_profiles + user_sessions |
| `fleet/auth_blueprint.py` | Create | Auth API (login, logout, profiles, setup) |
| `fleet/security.py` | Modify | Add session cookie check to `get_request_role()` |
| `fleet/outputs_blueprint.py` | Modify | Pass reviewer to submit_feedback |
| `fleet/db.py` / `fleet/db_feedback.py` | Modify | Add reviewer param to submit_feedback |
| `fleet/templates/components/_scripts.html` | Modify | Login overlay + profile management |
| `fleet/templates/components/_styles.html` | Modify | Login screen CSS |
| `fleet/templates/components/_settings.html` | Modify | Profile management tab |
| `fleet/dashboard.py` | Modify | Register auth_blueprint |
| `tests/test_auth_blueprint.py` | Create | Auth endpoint tests |

## Not in scope

- OAuth/OIDC integration (roadmapped as Option C)
- Email verification
- Password recovery
- Two-factor authentication
- Rate limiting on login attempts (add later if needed)
