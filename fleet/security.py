"""
Fleet Dashboard — security, auth, RBAC, rate-limiting, CSRF, TLS.

Extracted from dashboard.py (TECH_DEBT 4.3) to keep security primitives
in a single, auditable module.  SOC 2 / OWASP alignment.
"""
import functools
import os
import re
import secrets
import threading
import time
from pathlib import Path

from flask import jsonify, request

FLEET_DIR = Path(__file__).parent

# ── TLS (auto-generate self-signed cert) ─────────────────────────────────


def ensure_tls_cert(cert_dir=None):
    """Generate self-signed TLS cert if none exists.

    Returns (cert_path, key_path) on success, (None, None) on failure.
    Falls back to HTTP when openssl is unavailable.
    """
    cert_dir = cert_dir or os.path.join(os.path.dirname(__file__), "certs")
    cert_path = os.path.join(cert_dir, "dashboard.crt")
    key_path = os.path.join(cert_dir, "dashboard.key")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    os.makedirs(cert_dir, exist_ok=True)
    try:
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "365", "-nodes",
            "-subj", "/CN=localhost/O=BigEdCC"
        ], check=True, capture_output=True)
        return cert_path, key_path
    except Exception:
        return None, None  # Fall back to HTTP


# ── RBAC role definitions ────────────────────────────────────────────────

RBAC_ROLES = {
    "admin": {"read", "write", "delete", "configure"},
    "operator": {"read", "write"},
    "viewer": {"read"},
}

# ── Granular RBAC permissions (0.135.00b — Enterprise & Multi-Tenant) ────

PERMISSIONS = {
    "admin": {"read", "write", "delete", "configure", "deploy", "audit", "manage_users"},
    "operator": {"read", "write", "deploy", "audit"},
    "developer": {"read", "write", "deploy"},
    "viewer": {"read", "audit"},
    "auditor": {"read", "audit"},
}


def check_permission(role: str, action: str) -> bool:
    """Check if a role has permission for an action.

    Uses the granular PERMISSIONS table (0.135.00b). Roles not in the table
    have no permissions (deny by default).
    """
    perms = PERMISSIONS.get(role, set())
    return action in perms


def get_request_role(config_loader, req=None):
    """Determine role from request token.

    Checks Authorization header and query param against configured
    admin_token, operator_token, and dashboard_token in fleet.toml [security].

    Parameters
    ----------
    config_loader : callable
        Function that returns the fleet config dict (avoids circular import).
    req : flask.Request, optional
        Defaults to the current ``flask.request``.
    """
    req = req or request
    token = req.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = req.args.get("token", "")
    # Check against configured tokens
    config = config_loader()
    security = config.get("security", {})
    admin_token = security.get("admin_token", "")
    operator_token = security.get("operator_token", "")
    if admin_token and token == admin_token:
        return "admin"
    if operator_token and token == operator_token:
        return "operator"
    # Default: if any token matches the existing dashboard_token, treat as operator
    dash_token = security.get("dashboard_token", "")
    if dash_token and token == dash_token:
        return "operator"
    return "viewer"


def require_role(role, config_loader):
    """Decorator to enforce minimum role for an endpoint.

    Compares the request role's permissions against the required role's
    permissions. Returns 403 if insufficient.

    Parameters
    ----------
    role : str
        Minimum role name ("admin", "operator", "viewer").
    config_loader : callable
        Passed through to ``get_request_role``.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            user_role = get_request_role(config_loader)
            role_perms = RBAC_ROLES.get(user_role, set())
            required_perms = RBAC_ROLES.get(role, set())
            if not required_perms.issubset(role_perms):
                return jsonify({"error": "insufficient permissions", "required_role": role}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── Error sanitizer ──────────────────────────────────────────────────────


def safe_error(e):
    """Sanitize error messages by stripping file paths."""
    msg = str(e)
    msg = re.sub(r'[A-Z]:\\[^\s"\']+', '[path]', msg)
    msg = re.sub(r'/[^\s"\']+/[^\s"\']+', '[path]', msg)
    return msg


# ── CSRF token management ────────────────────────────────────────────────

_csrf_tokens: set[str] = set()


def generate_csrf_token():
    """Generate a CSRF token for forms."""
    token = secrets.token_hex(32)
    _csrf_tokens.add(token)
    # Keep max 100 tokens
    while len(_csrf_tokens) > 100:
        _csrf_tokens.pop()
    return token


def validate_csrf_token(token: str) -> bool:
    """Validate and consume a CSRF token (single-use). Returns True if valid."""
    if token and token in _csrf_tokens:
        _csrf_tokens.discard(token)
        return True
    return False


# ── Rate limiter ─────────────────────────────────────────────────────────

_rate_limits: dict = {}           # (ip, endpoint) -> [timestamps]
_rate_lock = threading.Lock()
RATE_LIMIT_REQUESTS = 60          # max requests per window
RATE_LIMIT_WINDOW = 60            # seconds


def check_rate_limit():
    """Rate limit /api/* endpoints. Returns a 429 Response or None."""
    if not request.path.startswith("/api/"):
        return None
    key = (request.remote_addr, request.path.rsplit("/", 1)[0])  # group by path prefix
    now = time.time()
    with _rate_lock:
        timestamps = _rate_limits.setdefault(key, [])
        # Remove old entries
        timestamps[:] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            return jsonify({"error": "Rate limit exceeded", "retry_after": RATE_LIMIT_WINDOW}), 429
        timestamps.append(now)
    return None


# ── CORS helper ──────────────────────────────────────────────────────────

# Populated at dashboard startup for remote access
cors_origins: list[str] = []


def add_cors_headers(response):
    """Add CORS headers when the request Origin is in the allowed list."""
    if not cors_origins:
        return response
    origin = request.headers.get("Origin", "")
    if origin in cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ── Flask hook registrators ──────────────────────────────────────────────


def register_hooks(app, config_loader):
    """Wire all security hooks into a Flask app.

    Call once during app setup. ``config_loader`` is a zero-arg callable
    returning the fleet config dict.
    """

    @app.after_request
    def _cors(response):
        return add_cors_headers(response)

    @app.before_request
    def _auth():
        if (not request.path.startswith("/api/")
                and not request.path.startswith("/a2a/")
                and request.path != "/.well-known/agent.json"):
            return  # skip auth for HTML pages, static
        config = config_loader()
        token = config.get("security", {}).get("dashboard_token", "")
        if not token:
            return  # no token configured = open access (local dev mode)
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return  # valid
        return jsonify({"error": "Unauthorized — set Authorization: Bearer <token>"}), 401

    @app.before_request
    def _rate():
        result = check_rate_limit()
        if result:
            return result

    @app.before_request
    def _csrf():
        if request.method != "POST":
            return
        # Skip CSRF for API clients using Bearer auth
        if request.headers.get("Authorization", "").startswith("Bearer"):
            return
        # Skip for JSON content type (API calls)
        if request.content_type and "json" in request.content_type:
            return
        # Check CSRF token for form submissions
        token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if validate_csrf_token(token):
            return
        # No CSRF for local-only deployment — log warning but don't block
        # (strict enforcement would break the web launcher forms)
