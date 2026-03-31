"""Security tests for geo fleet inter-fleet auth."""
import sys
sys.path.insert(0, "fleet")
import inspect


def test_route_to_region_has_auth():
    from geo_fleet import route_to_region
    source = inspect.getsource(route_to_region)
    assert "Authorization" in source or "Bearer" in source, \
        "route_to_region must include auth header"

def test_apply_auto_scale_has_auth():
    from geo_fleet import apply_auto_scale
    source = inspect.getsource(apply_auto_scale)
    assert "Authorization" in source or "Bearer" in source

def test_sync_skills_has_auth():
    from geo_fleet import sync_skills_to_cdn
    source = inspect.getsource(sync_skills_to_cdn)
    assert "Authorization" in source or "Bearer" in source

def test_get_federation_token_exists():
    from geo_fleet import _get_federation_token
    result = _get_federation_token()
    assert isinstance(result, str)
