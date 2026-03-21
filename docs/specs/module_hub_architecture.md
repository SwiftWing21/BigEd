# BigEd CC — Module Hub Architecture

**Status:** Phase 1 backend complete — UX (Phase 2) pending
**Repo:** https://github.com/SwiftWing21/BigEd-ModuleHub
**Implementation:** `BigEd/launcher/modules/hub.py`
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
2. BigEd fetches `registry.json` from the hub repo (cached)
3. Verifies: checksum (SHA-256), min version, SOC 2 flag, enterprise gate
4. Downloads module `.py` file(s) to `BigEd/launcher/modules/`
5. Updates local `manifest.json` with new module entry
6. <!-- TODO: verify --> Adds to `fleet.toml [launcher.tabs]` — **not yet implemented in hub.py**; requires manual edit + restart
7. Tab appears after launcher restart (lazy-load on first click is Phase 2)

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
- [ ] Restructure BigEd-ModuleHub repo with registry.json (external repo — not yet done)
- [ ] Move current modules from BigEd main repo to hub (external repo — not yet done)
- [x] Module download/install function in BigEd launcher (`hub.py:install_module()`)
- [x] Checksum verification on download (SHA-256, `hub.py:96-100`)
- [x] Local manifest tracking (`hub.py:_update_local_manifest()`)
- [x] Version comparison: installed vs hub (`hub.py:get_update_available()`)
- [x] Uninstall support (`hub.py:uninstall_module()`)
- [ ] Module Hub section in Settings → Modules panel (Phase 2)
- [ ] Auto-add to fleet.toml [launcher.tabs] on install (not yet wired)

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
hub_url              = "https://github.com/SwiftWing21/BigEd-ModuleHub"
enterprise_hub_url   = ""      # Private org repo (empty = use public hub)
verify_checksums     = true    # SHA-256 verification on download (hub.py:96-100)

# Planned (not yet read by hub.py):
# auto_update          = false   # Check for module updates on boot
# check_interval_hours = 24      # How often to check for updates
# allow_community      = true    # Allow non-core modules (enterprise may disable)
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
       → [Update manifest.json]
       → [Manual: add to fleet.toml tabs + restart]  ← Phase 1 gap
       → [Lazy-load on first tab click]               ← Phase 2
       → [on_refresh() polling] → [on_close() cleanup]
```

### Enterprise Module Flow

```
[Enterprise Hub] → [Federation selects modules] → [Auto-download]
       → [FileSystemGuard validates zones] → [SOC 2 audit check]
       → [Install with enterprise flags] → [Audit logged]
```

---

## Implementation vs Spec Divergences (hub.py audit — v0.053.00b)

| Spec item | Implementation status | Notes |
|-----------|----------------------|-------|
| `install_module()` adds to `fleet.toml [launcher.tabs]` | ✗ Not implemented | Only updates `manifest.json`. Tab registration requires manual fleet.toml edit + launcher restart. |
| `get_update_available()` returns only newer-version modules | ✗ Partial — also returns uninstalled modules | Line 69: `updates.append(mod)` for any module not in installed dict. Rename to `get_available_or_updates()` or split into two methods. |
| `auto_update`, `check_interval_hours`, `allow_community` config keys | ✗ Not read by hub.py | Keys are in the spec's fleet.toml section but hub.py only reads `hub_url`, `enterprise_hub_url`, `verify_checksums`. |
| Install flow step 7: "Tab appears immediately (lazy-loaded)" | ✗ Not implemented | Phase 2 — requires Module Hub Settings UX panel. |
| Checksum: `actual.startswith(expected)` comparison | ⚠ Asymmetric check | `hub.py:99-100` uses `startswith` in both directions. Full hex equality preferred. <!-- TODO: verify --> |
