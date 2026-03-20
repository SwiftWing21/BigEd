# BigEd CC — Enterprise Compliance Tiers

**Status:** Decision captured
**Date:** March 2026

## Compliance Levels

Users select their compliance tier at setup. Enterprise installs force Enterprise tier.

| Tier | Who | Auth | Compliance | Federation | Data Training |
|------|-----|------|------------|------------|---------------|
| **Basic** | Pro/Max individuals | OAuth Device Flow | Self-managed | None | Opt-out available |
| **Standard** | Pro/Max teams | OAuth + team workspace | Audit logging, DLP | Optional | Opt-out enforced |
| **Enterprise** | Enterprise customers | SSO/SAML + API key | Full RBAC, audit, retention | Required | Disabled (Commercial Terms) |

## Key Rules

1. **Enterprise installs force Enterprise tier** — no downgrade, compliance is non-negotiable
2. **Pro/Max users get Enterprise features as opt-in** — they can enable audit logging, DLP, federation without upgrading their Anthropic plan
3. **Compliance level selector at setup** — dropdown in walkthrough, persisted in fleet.toml
4. **Multi-user support** — CLAUDE.local.md per user, shared rules in .claude/rules/
5. **Federation** — required for Enterprise, optional for Standard, disabled for Basic

## fleet.toml Config

```toml
[compliance]
tier = "basic"              # basic | standard | enterprise
enforce_audit_log = false   # forced true on enterprise
enforce_dlp = false         # forced true on enterprise
enforce_rbac = false        # forced true on enterprise
data_training_opt_out = true
retention_days = 90         # enterprise may require longer

[federation]
enabled = false             # forced true on enterprise
peer_discovery = false
task_overflow_routing = false
```

## Enterprise Install Detection

```python
# Enterprise installs set this at install time (cannot be changed)
BIGED_ENTERPRISE = os.environ.get("BIGED_ENTERPRISE", "0") == "1"

if BIGED_ENTERPRISE:
    # Force enterprise compliance — override any fleet.toml settings
    config["compliance"]["tier"] = "enterprise"
    config["compliance"]["enforce_audit_log"] = True
    config["compliance"]["enforce_dlp"] = True
    config["compliance"]["enforce_rbac"] = True
    config["federation"]["enabled"] = True
```

## Open Items

- SSO/SAML provider integration (Azure AD, Okta, Google Workspace)
- Per-tenant DB isolation (separate fleet.db per organization)
- Compliance reporting endpoints for enterprise audit requirements
- License key validation for enterprise features
