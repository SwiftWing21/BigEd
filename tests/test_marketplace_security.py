"""Security tests for marketplace."""
import pytest
import sys
sys.path.insert(0, "fleet")
import inspect


def test_validate_skill_name_rejects_traversal():
    from marketplace import _validate_skill_name
    assert _validate_skill_name("valid_skill") is True
    with pytest.raises(ValueError):
        _validate_skill_name("../../sso")
    with pytest.raises(ValueError):
        _validate_skill_name("sub/evil")


def test_install_endpoint_has_auth():
    from marketplace import api_marketplace_install
    source = inspect.getsource(api_marketplace_install)
    assert "_require_role" in source, "install endpoint must have auth check"


def test_review_endpoint_has_auth():
    from marketplace import api_marketplace_submit_review
    source = inspect.getsource(api_marketplace_submit_review)
    assert "_require_role" in source, "review endpoint must have auth check"
