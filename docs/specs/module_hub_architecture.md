# BigEd CC — Module Hub Architecture

**Status:** Planning
**Repo:** https://github.com/SwiftWing21/BigEd-ModuleHub
**Purpose:** Community and enterprise plugin repository for BigEd CC modules

---

## Why GitHub as Module Hub

GitHub is the right choice because:
1. **Version control** — modules are versioned, diffable, auditable
2. **Security** — PRs require review, code is inspectable before install
3. **Discovery** — GitHub search, topics, stars, README previews
4. **Enterprise** — organizations can fork and run private module repos
5. **CI/CD** — GitHub Actions can lint, test, and validate modules on push
6. **No infrastructure** — no server to maintain, no database, no CDN
7. **Precedent** — VS Code marketplace, Homebrew, Terraform Registry all use GitHub

---

## Module Hub Repository Structure

```
BigEd-ModuleHub/
├── README.md                    # Hub landing page
├── CONTRIBUTING.md              # How to publish a module
├── SECURITY.md                  # Security requirements for modules
├── registry.json                # Module catalog (machine-readable)
├── modules/
│   ├── crm/
│   │   ├── manifest.json        # Module metadata
│   │   ├── mod_crm.py           # Module code
│   │   ├── README.md            # Module documentation
│   │   └── screenshots/         # Module screenshots
│   ├── accounts/
│   ├── onboarding/
│   ├── customers/
│   ├── intelligence/
│   ├── ingestion/
│   ├── outputs/
│   └── owner_core/              # Enterprise-only (gated)
└── enterprise/
    ├── ENTERPRISE.md            # Enterprise module guidelines
    ├── compliance/              # SOC 2 compliance templates
    └── federation/              # Federation module configs
```

## registry.json — Module Catalog

```json
{
  "version": "1.0",
  "modules": [
    {
      "name": "intelligence",
      "version": "0.051",
      "description": "System transparency, model controls, prompt queue, evaluation display",
      "author": "BigEd Core",
      "license": "Apache-2.0",
      "min_biged_version": "0.051.00b",
      "default_enabled": true,
      "enterprise_only": false,
      "soc2_compliant": true,
      "dependencies": [],
      "size_kb": 15,
      "download_url": "modules/intelligence/mod_intelligence.py",
      "checksum_sha256": "...",
      "tags": ["transparency", "cost-tracking", "evaluation"]
    }
  ]
}
```

## Module Manifest (per module)

```json
{
  "name": "intelligence",
  "label": "Intelligence",
  "version": "0.051",
  "description": "System transparency, model controls, prompt queue",
  "author": "BigEd Core",
  "license": "Apache-2.0",
  "min_biged_version": "0.051.00b",
  "default_enabled": true,
  "enterprise_only": false,
  "soc2_compliant": true,
  "requires_network": false,
  "requires_api_keys": false,
  "filesystem_zones": ["knowledge"],
  "dependencies": [],
  "icon": "🧠"
}
```

---

## UX: Module Hub in BigEd CC Launcher

### Module Hub Tab (new module)

The launcher gets a **Module Hub** tab (or section in Settings → Modules):

```
┌─────────────────────────────────────────────────────┐
│ 🏪  Module Hub                          [Refresh]   │
├─────────────────────────────────────────────────────┤
│                                                     │
│ INSTALLED (3)                                       │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │
│ │ 🧠 Intel    │ │ 📥 Ingest   │ │ 📤 Outputs  │   │
│ │ v0.051      │ │ v0.023      │ │ v0.023      │   │
│ │ ✓ Enabled   │ │ ✓ Enabled   │ │ ✓ Enabled   │   │
│ │ [Disable]   │ │ [Disable]   │ │ [Disable]   │   │
│ └─────────────┘ └─────────────┘ └─────────────┘   │
│                                                     │
│ AVAILABLE (5)                                       │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │
│ │ 🤝 CRM      │ │ 👥 Custom   │ │ 📋 Accounts │   │
│ │ v0.022      │ │ v0.023      │ │ v0.022      │   │
│ │ ☐ Disabled  │ │ ☐ Disabled  │ │ ☐ Disabled  │   │
│ │ [Install]   │ │ [Install]   │ │ [Install]   │   │
│ └─────────────┘ └─────────────┘ └─────────────┘   │
│                                                     │
│ Hub: github.com/SwiftWing21/BigEd-ModuleHub         │
│ Enterprise: [Configure Private Hub]                  │
└─────────────────────────────────────────────────────┘
```

### Install Flow

1. User clicks **[Install]** on a module card
2. BigEd downloads `manifest.json` from the hub repo
3. Verifies: checksum, min version, SOC 2 flag, enterprise gate
4. Downloads module `.py` file(s) to `BigEd/launcher/modules/`
5. Updates local `manifest.json` with new module entry
6. Adds to `fleet.toml [launcher.tabs]`
7. Tab appears immediately (lazy-loaded on first click)

### Enterprise Flow

1. Enterprise installs set `BIGED_ENTERPRISE=1`
2. On boot, federation auto-selects modules from configured hub
3. Enterprise hub URL in `fleet.toml`:
   ```toml
   [modules]
   hub_url = "https://github.com/SwiftWing21/BigEd-ModuleHub"
   enterprise_hub_url = ""  # Private org repo URL
   auto_update = false      # Auto-download module updates
   ```
4. Enterprise-only modules require `enterprise_only: true` flag
5. Custom modules: enterprise orgs push to their own hub repo
6. Agent-generated module recommendations logged for operator review

---

## Security Requirements

### For All Modules (SOC 2 baseline)

- [ ] No hardcoded credentials or API keys
- [ ] No network calls without `requires_network: true` declaration
- [ ] No file access outside declared `filesystem_zones`
- [ ] No subprocess execution without sandbox flag
- [ ] Code must pass `py_compile` + basic lint
- [ ] SHA-256 checksum verified on download
- [ ] Module code is inspectable (source only, no compiled/obfuscated)

### For Enterprise Modules (additional)

- [ ] Must declare all filesystem zones accessed
- [ ] Must pass FileSystemGuard validation
- [ ] Audit logging for all data operations
- [ ] DLP scanning on module outputs
- [ ] Code review approval required before deployment
- [ ] Signed commits (GPG) from verified authors

### SOC 2 Compliance Skill Reference

BigEd CC maintains SOC 2 compliance via:
- `fleet/skills/security_audit.py` — automated security scanning
- `fleet/skills/security_review.py` — code review for security issues
- `fleet/filesystem_guard.py` — file access control enforcement
- `fleet/skills/_watchdog.py` — DLP secret detection + quarantine
- `fleet/security.py` — RBAC, TLS, CSRF, rate limiting

---

## Implementation Phases

### Phase 1: Core Module Hub (0.053.00b)
- [ ] Restructure BigEd-ModuleHub repo with registry.json
- [ ] Move current modules from BigEd main repo to hub
- [ ] Module download/install function in BigEd launcher
- [ ] Module Hub section in Settings → Modules panel
- [ ] Checksum verification on download

### Phase 2: Module Hub UX (0.053.01b)
- [ ] Dedicated Module Hub tab or Settings panel
- [ ] Module cards with install/enable/disable/update
- [ ] Version checking (installed vs available)
- [ ] Module search/filter by tags
- [ ] Module README preview

### Phase 3: Enterprise Features (0.053.02b)
- [ ] Private hub URL configuration
- [ ] Federation auto-select from enterprise hub
- [ ] Enterprise-only module gating
- [ ] Agent-generated module recommendations
- [ ] Custom module publishing workflow

### Phase 4: Community (0.054.00b)
- [ ] Module submission PR template
- [ ] Automated CI validation (lint, compile, security scan)
- [ ] Module ratings/downloads tracking
- [ ] Module dependency resolution
- [ ] Module versioning (semver within hub)

---

## fleet.toml Configuration

```toml
[modules]
hub_url = "https://github.com/SwiftWing21/BigEd-ModuleHub"
enterprise_hub_url = ""           # Private org repo (empty = use public)
auto_update = false               # Check for module updates on boot
check_interval_hours = 24         # How often to check for updates
verify_checksums = true           # SHA-256 verification on download
allow_community = true            # Allow non-core modules (enterprise may disable)
```

---

## Framework Blueprint Update

### Module System Architecture

```
BigEd CC (main repo)
    │
    ├── BigEd/launcher/modules/     ← Installed modules live here
    │   ├── __init__.py             ← Module loader + registry
    │   ├── manifest.json           ← Local installed module catalog
    │   ├── mod_intelligence.py     ← Core module (ships with BigEd)
    │   ├── mod_ingestion.py        ← Core module
    │   └── mod_crm.py              ← Downloaded from Module Hub
    │
    ├── Module Hub (GitHub repo)
    │   ├── registry.json           ← Available module catalog
    │   └── modules/                ← Module source + manifests
    │
    └── Enterprise Private Hub (optional)
        ├── registry.json           ← Org-specific modules
        └── modules/                ← Custom/proprietary modules
```

### Module Lifecycle

```
[Hub Registry] → [Download] → [Verify Checksum] → [Install to modules/]
       → [Update manifest.json] → [Add to fleet.toml tabs]
       → [Lazy-load on first tab click] → [on_refresh() polling]
       → [on_close() cleanup]
```

### Enterprise Module Flow

```
[Enterprise Hub] → [Federation selects modules] → [Auto-download]
       → [FileSystemGuard validates zones] → [SOC 2 audit check]
       → [Install with enterprise flags] → [Audit logged]
```
