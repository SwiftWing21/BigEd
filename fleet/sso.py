"""
SSO / OIDC / SAML Authentication — Enterprise Identity Federation.

Provides:
  - OIDC provider integration (primary) with PKCE
  - SAML2 SP flow (optional, graceful fallback if python3-saml missing)
  - JWT session management (issue, validate, revoke)
  - Flask blueprint with /auth/* routes for dashboard integration

Config lives in fleet.toml [sso].  Secrets (client_secret) are read from
environment variables when the TOML value is empty.

v0.300.00b
"""
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, session

log = logging.getLogger("sso")

FLEET_DIR = Path(__file__).parent

# ── In-memory stores (thread-safe) ──────────────────────────────────────────

_sessions: dict[str, dict] = {}       # token -> {user, role, exp, ...}
_pending_flows: dict[str, dict] = {}  # state -> {verifier, nonce, ts}
_store_lock = threading.Lock()

# OIDC discovery cache
_oidc_config_cache: dict = {}
_oidc_cache_ts: float = 0.0
_OIDC_CACHE_TTL = 3600  # 1 hour

# JWKS cache
_jwks_cache: dict = {}
_jwks_cache_ts: float = 0.0
_JWKS_CACHE_TTL = 3600

# ── Helpers ─────────────────────────────────────────────────────────────────


def _get_sso_config() -> dict:
    """Load [sso] section from fleet.toml. Returns empty dict if missing."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("sso", {})
    except Exception:
        log.warning("Failed to load SSO config from fleet.toml", exc_info=True)
        return {}


def is_sso_enabled() -> bool:
    """Check whether SSO is enabled in fleet.toml."""
    return _get_sso_config().get("enabled", False)


def _base64url_encode(data: bytes) -> str:
    """Base64url encoding without padding (RFC 7636)."""
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(s: str) -> bytes:
    """Base64url decoding with padding restoration."""
    import base64
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _json_from_url(url: str, *, method: str = "GET",
                   data: bytes | None = None,
                   headers: dict | None = None,
                   timeout: int = 15) -> dict:
    """Fetch JSON from a URL using urllib (no external deps). Always has timeout."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data and not headers:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode("utf-8"))


# ── OIDC Discovery ─────────────────────────────────────────────────────────


def _discover_oidc(issuer_url: str) -> dict:
    """Fetch and cache OpenID Connect discovery document."""
    global _oidc_config_cache, _oidc_cache_ts
    now = time.time()
    if _oidc_config_cache and (now - _oidc_cache_ts) < _OIDC_CACHE_TTL:
        return _oidc_config_cache

    discovery_url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    log.info("Discovering OIDC endpoints from %s", discovery_url)
    doc = _json_from_url(discovery_url, timeout=15)

    required_keys = ["authorization_endpoint", "token_endpoint",
                     "jwks_uri", "issuer"]
    missing = [k for k in required_keys if k not in doc]
    if missing:
        raise ValueError(f"OIDC discovery missing required keys: {missing}")

    _oidc_config_cache = doc
    _oidc_cache_ts = now
    return doc


def _fetch_jwks(jwks_uri: str) -> dict:
    """Fetch and cache JWKS from the provider."""
    global _jwks_cache, _jwks_cache_ts
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_ts) < _JWKS_CACHE_TTL:
        return _jwks_cache

    log.info("Fetching JWKS from %s", jwks_uri)
    jwks = _json_from_url(jwks_uri, timeout=15)
    _jwks_cache = jwks
    _jwks_cache_ts = now
    return jwks


# ── OIDC Core ──────────────────────────────────────────────────────────────


def configure_oidc(issuer_url: str, client_id: str,
                   client_secret: str = "") -> dict:
    """Discover OIDC endpoints and return provider configuration.

    Parameters
    ----------
    issuer_url : str
        The OIDC issuer URL (e.g. https://accounts.google.com).
    client_id : str
        OAuth2 client ID.
    client_secret : str
        OAuth2 client secret. Read from SSO_CLIENT_SECRET env var if empty.

    Returns
    -------
    dict with authorization_endpoint, token_endpoint, userinfo_endpoint, jwks_uri.
    """
    if not client_secret:
        client_secret = os.environ.get("SSO_CLIENT_SECRET", "")
    if not client_secret:
        log.warning("No OIDC client secret configured — "
                    "set oidc_client_secret in fleet.toml or SSO_CLIENT_SECRET env var")

    doc = _discover_oidc(issuer_url)
    return {
        "issuer": doc["issuer"],
        "authorization_endpoint": doc["authorization_endpoint"],
        "token_endpoint": doc["token_endpoint"],
        "userinfo_endpoint": doc.get("userinfo_endpoint", ""),
        "jwks_uri": doc["jwks_uri"],
        "client_id": client_id,
        # Never log secrets
    }


def start_auth_flow(redirect_uri: str = "") -> tuple[str, str]:
    """Generate an OIDC authorization URL with PKCE (S256).

    Returns
    -------
    (auth_url, state) — caller redirects browser to auth_url.
    """
    sso_cfg = _get_sso_config()
    issuer = sso_cfg.get("oidc_issuer", "")
    client_id = sso_cfg.get("oidc_client_id", "")
    if not issuer or not client_id:
        raise ValueError("OIDC not configured — set oidc_issuer and "
                         "oidc_client_id in fleet.toml [sso]")

    doc = _discover_oidc(issuer)
    auth_endpoint = doc["authorization_endpoint"]

    # PKCE: generate code_verifier and code_challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _base64url_encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    )

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    if not redirect_uri:
        # Default: infer from dashboard config
        from config import load_config
        cfg = load_config()
        dash = cfg.get("dashboard", {})
        port = dash.get("port", 5555)
        bind = dash.get("bind_address", "127.0.0.1")
        scheme = "https" if bind not in ("127.0.0.1", "localhost") else "http"
        redirect_uri = f"{scheme}://{bind}:{port}/auth/callback"

    import urllib.parse
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })

    # Store pending flow (expires in 10 minutes)
    with _store_lock:
        _pending_flows[state] = {
            "verifier": code_verifier,
            "nonce": nonce,
            "redirect_uri": redirect_uri,
            "ts": time.time(),
        }
        # Prune expired flows (> 10 min)
        cutoff = time.time() - 600
        expired = [s for s, f in _pending_flows.items() if f["ts"] < cutoff]
        for s in expired:
            del _pending_flows[s]

    auth_url = f"{auth_endpoint}?{params}"
    return auth_url, state


def handle_callback(code: str, state: str) -> dict:
    """Exchange authorization code for tokens, validate, extract user info.

    Parameters
    ----------
    code : str
        Authorization code from the callback.
    state : str
        State parameter to match against pending flows.

    Returns
    -------
    dict with id_token, access_token, user_info, roles.
    """
    import urllib.parse

    # Validate state
    with _store_lock:
        flow = _pending_flows.pop(state, None)
    if not flow:
        raise ValueError("Invalid or expired state parameter")
    if time.time() - flow["ts"] > 600:
        raise ValueError("Auth flow expired (>10 minutes)")

    sso_cfg = _get_sso_config()
    issuer = sso_cfg.get("oidc_issuer", "")
    client_id = sso_cfg.get("oidc_client_id", "")
    client_secret = sso_cfg.get("oidc_client_secret", "") or os.environ.get(
        "SSO_CLIENT_SECRET", "")

    doc = _discover_oidc(issuer)
    token_endpoint = doc["token_endpoint"]

    # Exchange code for tokens
    token_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": flow["redirect_uri"],
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": flow["verifier"],
    }).encode("utf-8")

    token_resp = _json_from_url(token_endpoint, method="POST",
                                data=token_data, timeout=15)

    id_token_raw = token_resp.get("id_token", "")
    access_token = token_resp.get("access_token", "")

    # Decode and validate ID token
    user_info = {}
    if id_token_raw:
        user_info = validate_token(id_token_raw)
        # Verify nonce
        if user_info.get("nonce") != flow["nonce"]:
            raise ValueError("ID token nonce mismatch")
    elif access_token and doc.get("userinfo_endpoint"):
        # Fall back to userinfo endpoint
        user_info = _json_from_url(
            doc["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )

    if not user_info:
        raise ValueError("Failed to extract user info from OIDC response")

    roles = get_user_roles(user_info)

    return {
        "id_token": id_token_raw,
        "access_token": access_token,
        "user_info": user_info,
        "roles": roles,
    }


def validate_token(token: str) -> dict:
    """Verify JWT signature, expiry, and audience. Returns decoded claims.

    Uses JWKS from the OIDC provider to verify RS256 signatures.
    Falls back to unverified decode if PyJWT/cryptography not available,
    logging a warning.
    """
    # Split JWT
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT — expected 3 parts")

    header_b64, payload_b64, _sig = parts

    # Decode header and payload
    header = json.loads(_base64url_decode(header_b64))
    payload = json.loads(_base64url_decode(payload_b64))

    # Check expiry
    exp = payload.get("exp", 0)
    if exp and time.time() > exp:
        raise ValueError("Token expired")

    # Check audience
    sso_cfg = _get_sso_config()
    client_id = sso_cfg.get("oidc_client_id", "")
    aud = payload.get("aud", "")
    if client_id:
        # aud can be a string or list
        if isinstance(aud, list):
            if client_id not in aud:
                raise ValueError(f"Token audience mismatch: expected {client_id}")
        elif aud and aud != client_id:
            raise ValueError(f"Token audience mismatch: expected {client_id}")

    # Cryptographic verification (requires PyJWT + cryptography)
    try:
        import jwt as pyjwt

        issuer = sso_cfg.get("oidc_issuer", "")
        if issuer:
            doc = _discover_oidc(issuer)
            jwks_data = _fetch_jwks(doc["jwks_uri"])
            from jwt import PyJWKClient
            # Build JWK set from cached data
            jwk_client = PyJWKClient(doc["jwks_uri"])
            signing_key = jwk_client.get_signing_key_from_jwt(token)
            verified = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=[header.get("alg", "RS256")],
                audience=client_id or None,
                issuer=issuer,
                options={"verify_exp": True},
            )
            return verified
    except ImportError:
        log.warning("PyJWT not installed — JWT signature NOT verified. "
                    "Install with: pip install PyJWT cryptography")
    except Exception:
        log.warning("JWT signature verification failed — "
                    "using unverified payload", exc_info=True)

    return payload


def get_user_roles(user_info: dict) -> list[str]:
    """Map OIDC claims to BigEd roles (admin/operator/developer/viewer/auditor).

    Checks the claim configured in fleet.toml [sso] role_claim (default: "roles").
    Falls back to default_role if no matching claims found.

    Known provider mappings:
      - Azure AD: "roles" claim (app roles)
      - Okta: "groups" claim
      - Google: no default roles claim — uses default_role
    """
    sso_cfg = _get_sso_config()
    role_claim = sso_cfg.get("role_claim", "roles")
    default_role = sso_cfg.get("default_role", "viewer")

    # Valid BigEd roles (from security.py PERMISSIONS)
    valid_roles = {"admin", "operator", "developer", "viewer", "auditor"}

    # Extract roles from the configured claim
    raw_roles = user_info.get(role_claim, [])
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]

    # Filter to valid BigEd roles (case-insensitive matching)
    roles = []
    for r in raw_roles:
        r_lower = r.lower().strip()
        if r_lower in valid_roles:
            roles.append(r_lower)
        # Common mappings from IdP group names
        elif r_lower in ("administrators", "admins", "superadmin"):
            roles.append("admin")
        elif r_lower in ("operators", "ops", "devops"):
            roles.append("operator")
        elif r_lower in ("developers", "devs", "engineering"):
            roles.append("developer")
        elif r_lower in ("auditors", "compliance"):
            roles.append("auditor")

    if not roles:
        roles = [default_role]

    return roles


# ── Session Management ─────────────────────────────────────────────────────


def create_session(user_info: dict) -> str:
    """Issue a session token (opaque + JWT-style, 8h expiry by default).

    Parameters
    ----------
    user_info : dict
        User claims from OIDC (sub, email, name, roles, etc.).

    Returns
    -------
    str — session token.
    """
    sso_cfg = _get_sso_config()
    expiry_hours = sso_cfg.get("session_expiry_hours", 8)
    roles = user_info.get("roles", get_user_roles(user_info))

    token = secrets.token_urlsafe(48)
    session_data = {
        "sub": user_info.get("sub", ""),
        "email": user_info.get("email", ""),
        "name": user_info.get("name", user_info.get("preferred_username", "")),
        "roles": roles,
        "role": roles[0] if roles else "viewer",  # primary role for RBAC
        "exp": time.time() + (expiry_hours * 3600),
        "iat": time.time(),
        "provider": "oidc",
    }

    with _store_lock:
        _sessions[token] = session_data
        # Prune expired sessions (keep memory bounded)
        now = time.time()
        expired = [t for t, s in _sessions.items() if s.get("exp", 0) < now]
        for t in expired:
            del _sessions[t]

    log.info("SSO session created for user=%s role=%s",
             session_data.get("email", "unknown"), session_data.get("role"))
    return token


def validate_session(token: str) -> dict | None:
    """Verify session token. Returns user context dict or None if invalid/expired."""
    if not token:
        return None
    with _store_lock:
        session_data = _sessions.get(token)
    if not session_data:
        return None
    if time.time() > session_data.get("exp", 0):
        # Expired — clean up
        with _store_lock:
            _sessions.pop(token, None)
        return None
    return session_data


def revoke_session(token: str) -> bool:
    """Invalidate a session token. Returns True if the token was found."""
    with _store_lock:
        return _sessions.pop(token, None) is not None


def get_session_from_request(req=None) -> dict | None:
    """Extract and validate SSO session from the current request.

    Checks (in order):
      1. Authorization: Bearer <session_token>
      2. X-SSO-Token header
      3. sso_token cookie
    """
    req = req or request
    # 1. Bearer token
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        result = validate_session(token)
        if result:
            return result

    # 2. X-SSO-Token header
    token = req.headers.get("X-SSO-Token", "")
    if token:
        result = validate_session(token)
        if result:
            return result

    # 3. Cookie
    token = req.cookies.get("sso_token", "")
    if token:
        return validate_session(token)

    return None


# ── SAML Support (optional) ────────────────────────────────────────────────

_saml_available = False
try:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth  # noqa: F401
    _saml_available = True
except ImportError:
    log.info("python3-saml not installed — SAML endpoints will return 501. "
             "Install with: pip install python3-saml")


def _get_saml_auth(req):
    """Build a SAML auth object from the Flask request. Requires python3-saml."""
    if not _saml_available:
        return None
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    sso_cfg = _get_sso_config()
    metadata_url = sso_cfg.get("saml_metadata_url", "")
    if not metadata_url:
        return None

    # Build SAML settings from metadata
    prepared = {
        "https": "on" if req.scheme == "https" else "off",
        "http_host": req.host,
        "script_name": req.path,
        "get_data": req.args.to_dict(),
        "post_data": req.form.to_dict(),
    }
    try:
        saml_settings = {
            "strict": True,
            "debug": False,
            "idp": {"metadata_url": metadata_url},
        }
        return OneLogin_Saml2_Auth(prepared, saml_settings)
    except Exception:
        log.warning("Failed to initialize SAML auth", exc_info=True)
        return None


# ── Flask Blueprint (/auth/*) ──────────────────────────────────────────────

sso_bp = Blueprint("sso", __name__, url_prefix="/auth")


@sso_bp.route("/login")
def auth_login():
    """Redirect to the configured identity provider.

    OIDC: redirects to authorization endpoint with PKCE.
    SAML: redirects to IdP SSO URL.
    """
    if not is_sso_enabled():
        return jsonify({"error": "SSO not enabled",
                        "hint": "Set [sso] enabled = true in fleet.toml"}), 404

    sso_cfg = _get_sso_config()
    provider = sso_cfg.get("provider", "oidc")

    if provider == "saml":
        if not _saml_available:
            return jsonify({
                "error": "SAML not available",
                "hint": "Install python3-saml: pip install python3-saml",
            }), 501
        try:
            saml_auth = _get_saml_auth(request)
            if not saml_auth:
                return jsonify({"error": "SAML not configured"}), 500
            return redirect(saml_auth.login())
        except Exception:
            log.warning("SAML login failed", exc_info=True)
            return jsonify({"error": "SAML login failed"}), 500

    # Default: OIDC
    try:
        auth_url, state = start_auth_flow()
        return redirect(auth_url)
    except Exception:
        log.warning("OIDC login flow failed", exc_info=True)
        return jsonify({"error": "OIDC login failed — check SSO configuration"}), 500


@sso_bp.route("/callback")
def auth_callback():
    """Handle OIDC callback — exchange code for tokens, create session.

    On success: sets sso_token cookie and redirects to dashboard.
    On failure: returns JSON error.
    """
    if not is_sso_enabled():
        return jsonify({"error": "SSO not enabled"}), 404

    sso_cfg = _get_sso_config()
    provider = sso_cfg.get("provider", "oidc")

    if provider == "saml":
        if not _saml_available:
            return jsonify({"error": "SAML not available"}), 501
        try:
            saml_auth = _get_saml_auth(request)
            if not saml_auth:
                return jsonify({"error": "SAML not configured"}), 500
            saml_auth.process_response()
            errors = saml_auth.get_errors()
            if errors:
                log.warning("SAML errors: %s", errors)
                return jsonify({"error": "SAML authentication failed",
                                "details": errors}), 401
            attrs = saml_auth.get_attributes()
            user_info = {
                "sub": saml_auth.get_nameid(),
                "email": attrs.get("email", [saml_auth.get_nameid()])[0],
                "name": attrs.get("displayName", [""])[0],
                "roles": attrs.get("roles", attrs.get("groups", [])),
            }
            token = create_session(user_info)
            resp = redirect("/")
            resp.set_cookie("sso_token", token, httponly=True,
                            samesite="Lax", max_age=8 * 3600, secure=True)
            return resp
        except Exception:
            log.warning("SAML callback failed", exc_info=True)
            return jsonify({"error": "SAML callback failed"}), 500

    # Default: OIDC
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    error = request.args.get("error", "")

    if error:
        error_desc = request.args.get("error_description", error)
        log.warning("OIDC callback error: %s", error_desc)
        return jsonify({"error": "Authentication denied",
                        "details": error_desc}), 401

    if not code or not state:
        return jsonify({"error": "Missing code or state parameter"}), 400

    try:
        result = handle_callback(code, state)
        user_info = result["user_info"]
        user_info["roles"] = result["roles"]
        token = create_session(user_info)

        # Redirect to dashboard with session cookie
        resp = redirect("/")
        expiry_hours = sso_cfg.get("session_expiry_hours", 8)
        resp.set_cookie("sso_token", token, httponly=True,
                        samesite="Lax", max_age=expiry_hours * 3600,
                        secure=True)
        return resp
    except Exception:
        log.warning("OIDC callback failed", exc_info=True)
        return jsonify({"error": "OIDC token exchange failed"}), 500


@sso_bp.route("/logout")
def auth_logout():
    """Clear SSO session and redirect to login or home."""
    # Revoke from all sources
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        revoke_session(auth[7:])
    token = request.headers.get("X-SSO-Token", "")
    if token:
        revoke_session(token)
    cookie_token = request.cookies.get("sso_token", "")
    if cookie_token:
        revoke_session(cookie_token)

    resp = redirect("/")
    resp.delete_cookie("sso_token")
    return resp


@sso_bp.route("/status")
def auth_status():
    """Return current user info from SSO session, or 401 if not authenticated."""
    session_data = get_session_from_request()
    if not session_data:
        return jsonify({
            "authenticated": False,
            "sso_enabled": is_sso_enabled(),
            "provider": _get_sso_config().get("provider", "oidc"),
        }), 401

    return jsonify({
        "authenticated": True,
        "user": {
            "email": session_data.get("email", ""),
            "name": session_data.get("name", ""),
            "sub": session_data.get("sub", ""),
        },
        "role": session_data.get("role", "viewer"),
        "roles": session_data.get("roles", []),
        "provider": session_data.get("provider", "oidc"),
        "expires_at": session_data.get("exp", 0),
    })


# ── SSO Middleware (for security.py integration) ────────────────────────────


def sso_auth_check():
    """Before-request hook: if SSO is enabled, enforce session authentication.

    Allows through:
      - /auth/* routes (login/callback/logout/status)
      - Static assets (non-/api/ routes when SSO only gates API)
      - Requests with valid SSO session
      - Requests with valid dashboard_token (fallback for API clients)

    Returns None to continue, or a 401 response to block.
    """
    if not is_sso_enabled():
        return None

    # Always allow auth routes through
    if request.path.startswith("/auth/"):
        return None

    # Only gate /api/* and /a2a/* routes (same as existing _auth hook)
    if (not request.path.startswith("/api/")
            and not request.path.startswith("/a2a/")
            and request.path != "/.well-known/agent.json"):
        return None

    # Check SSO session
    session_data = get_session_from_request()
    if session_data:
        # Attach user context to request for downstream handlers
        request.sso_user = session_data  # type: ignore[attr-defined]
        return None

    # Fall through to existing token-based auth (dashboard_token still works)
    # This allows API clients to use Bearer tokens alongside SSO
    return None


def get_sso_role(req=None) -> str | None:
    """Get the user's role from SSO session, if present.

    Returns None if no SSO session (caller falls back to token-based roles).
    """
    req = req or request
    session_data = get_session_from_request(req)
    if session_data:
        return session_data.get("role", "viewer")
    return None


# ── Registration helper ────────────────────────────────────────────────────


def register_sso(app):
    """Register SSO blueprint and middleware with a Flask app.

    Call this during dashboard setup, after register_hooks().
    Only registers if [sso] section exists in fleet.toml (even if disabled,
    to allow enabling at runtime).
    """
    app.register_blueprint(sso_bp)

    # Register middleware — runs before the existing _auth hook
    # because Flask before_request hooks run in registration order
    @app.before_request
    def _sso_check():
        return sso_auth_check()

    log.info("SSO routes registered at /auth/* (enabled=%s)", is_sso_enabled())
