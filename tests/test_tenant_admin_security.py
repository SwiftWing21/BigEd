"""Security tests for tenant admin path traversal prevention."""
import pytest
import sys
sys.path.insert(0, "fleet")


def test_deploy_rejects_path_traversal():
    from tenant_admin import deploy_skill_to_tenant
    with pytest.raises(ValueError, match="[Pp]ath traversal|[Ii]nvalid"):
        deploy_skill_to_tenant("test-tenant", "../../etc/passwd")


def test_deploy_rejects_absolute_outside_fleet():
    from tenant_admin import deploy_skill_to_tenant
    with pytest.raises(ValueError, match="[Oo]utside|[Ii]nvalid|[Pp]ath|not found"):
        deploy_skill_to_tenant("test-tenant", "/etc/passwd")


def test_remove_rejects_path_traversal():
    from tenant_admin import remove_tenant_skill
    with pytest.raises(ValueError, match="[Pp]ath traversal|[Ii]nvalid"):
        remove_tenant_skill("test-tenant", "../../sso")


def test_remove_rejects_slash():
    from tenant_admin import remove_tenant_skill
    with pytest.raises(ValueError, match="[Pp]ath|[Ii]nvalid"):
        remove_tenant_skill("test-tenant", "subdir/evil")
