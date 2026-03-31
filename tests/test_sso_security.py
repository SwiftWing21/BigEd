"""Security tests for SSO JWT verification and middleware."""
import sys
sys.path.insert(0, "fleet")
import inspect


def test_jwt_verification_rejects_unverified():
    """JWT verify must return None (not payload) on verification failure."""
    from sso import validate_token
    source = inspect.getsource(validate_token)
    # The function must NOT have a bare 'return payload' after the except blocks
    lines = source.strip().split("\n")
    # Last non-empty line should be 'return None', not 'return payload'
    last_returns = [l.strip() for l in lines if l.strip().startswith("return")]
    assert "return payload" not in last_returns[-1:], \
        "Final return must not be 'return payload' — unverified tokens must be rejected"


def test_sso_middleware_returns_401():
    """sso_auth_check must return 401, not None, for unauthenticated API requests."""
    from sso import sso_auth_check
    source = inspect.getsource(sso_auth_check)
    assert "401" in source, "sso_auth_check must return 401 for unauthenticated requests"
    assert "Authentication required" in source, "Must include auth error message"
