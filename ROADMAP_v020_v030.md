# BigEd CC Roadmap: v0.20 → v0.30

> **Goal of v0.30:** Production-ready modular platform. Tabs are deployable service modules.
> VS Code developer workflow. Safe deprecation path. Customer-configurable deployments.

## Architecture Shift: Tabs → Modules

Currently tabs are inline methods in a 5700+ line launcher.py. By v0.30 they become:
- **Core modules** (always loaded): Command Center, Agents
- **Service modules** (bolt-on): CRM, Customers, Accounts, Onboarding, Ingestion, Outputs
- Each module: self-contained Python file, manifest, enable/disable at deploy time
- Customers get only the modules they need — no dead UI for unused features

```
BigEd/launcher/
├── launcher.py              # Core app shell (~2000 lines target)
├── modules/
│   ├── __init__.py          # Module loader + registry
│   ├── manifest.json        # Module metadata (name, version, deps, default_enabled)
│   ├── mod_crm.py           # CRM tab + business logic
│   ├── mod_onboarding.py    # Onboarding tab + checklists
│   ├── mod_customers.py     # Customer deployments tab
│   ├── mod_accounts.py      # Service accounts tab
│   ├── mod_ingestion.py     # File ingestion tab
│   └── mod_outputs.py       # Knowledge browser tab
└── data/                    # Per-module persistent storage
```

### Module Interface

Every module implements:
```python
class Module:
    NAME = "crm"                    # matches fleet.toml key
    LABEL = "CRM"                   # tab display name
    DEFAULT_ENABLED = False         # off unless configured
    DEPENDS_ON = []                 # other module names required

    def __init__(self, app):        # receives main app reference
        self.app = app

    def build_tab(self, parent):    # construct UI in tab frame
        ...

    def on_refresh(self):           # called every 4s refresh cycle
        ...

    def on_close(self):             # cleanup on app exit
        ...

    def get_settings(self) -> dict: # export module settings
        ...

    def apply_settings(self, cfg):  # import module settings
        ...
```

### Deprecation Protocol

Safe path to remove/replace a module:
1. Set `deprecated = true` in manifest.json
2. Module shows banner: "This module is deprecated. Data export available."
3. `deprecated_after = "v0.28"` — auto-disable after that version
4. Data export function preserves user data before removal
5. Module file can be deleted — loader skips missing modules gracefully

---

## v0.21 — VS Code Developer Workflow

**Goal:** Quick test/debug cycle for fleet + launcher development.

### 21.1 VS Code launch configurations
```
.vscode/
├── launch.json    # Debug configs for supervisor, worker, smoke_test, launcher
├── tasks.json     # Build tasks: smoke test, lint, format, pyinstaller
└── settings.json  # Python path, formatter, linter
```

Launch configs:
- **Smoke Test** — `uv run python fleet/smoke_test.py`
- **Single Worker** — `uv run python fleet/worker.py --role researcher`
- **Supervisor** — `uv run python fleet/supervisor.py`
- **HW Supervisor** — `uv run python fleet/hw_supervisor.py`
- **Launcher (debug)** — `python BigEd/launcher/launcher.py`
- **Lead Client** — `uv run python fleet/lead_client.py status`

### 21.2 Developer smoke test (fast mode)
- `smoke_test.py --fast` — skip Ollama/RAG checks, run only DB + config + imports (~2s)
- `smoke_test.py --full` — all 10 checks (~10s)
- Pre-commit hook option: run `--fast` before commit

### 21.3 Test isolation
- `FLEET_TEST_DB=:memory:` env var → use in-memory SQLite for tests
- No side effects on production fleet.db during development
- `smoke_test.py` auto-uses memory DB when `--fast` flag set

**Files:** `.vscode/launch.json`, `.vscode/tasks.json`, `fleet/smoke_test.py`

---

## v0.22 — Module Extraction (Phase 1: CRM + Accounts)

**Goal:** Extract first 2 business tabs into standalone module files.

### 22.1 Module loader (`BigEd/launcher/modules/__init__.py`)
- Scan `modules/` for `mod_*.py` files
- Import each, check for `Module` class
- Cross-check against `fleet.toml [launcher.tabs]`
- Load enabled modules, skip disabled, warn on missing

### 22.2 Extract CRM module
- Move `_build_tab_crm()` + CRM DB methods → `mod_crm.py`
- CRM gets its own SQLite table (already has one in tools.db)
- Module-local settings: default industry list, lead stages, etc.

### 22.3 Extract Accounts module
- Move `_build_tab_accounts()` + account tracking → `mod_accounts.py`
- Service account DB queries stay local to module

### 22.4 Manifest
```json
{
  "modules": [
    {"name": "crm", "file": "mod_crm.py", "version": "0.22", "default_enabled": false, "deprecated": false},
    {"name": "accounts", "file": "mod_accounts.py", "version": "0.22", "default_enabled": false, "deprecated": false}
  ]
}
```

**Files:** `BigEd/launcher/modules/__init__.py`, `BigEd/launcher/modules/mod_crm.py`, `BigEd/launcher/modules/mod_accounts.py`, `BigEd/launcher/modules/manifest.json`

---

## v0.23 — Module Extraction (Phase 2: Remaining Tabs)

**Goal:** Complete module extraction for all non-core tabs.

### 23.1 Extract Onboarding module → `mod_onboarding.py`
### 23.2 Extract Customers module → `mod_customers.py`
### 23.3 Extract Ingestion module → `mod_ingestion.py`
### 23.4 Extract Outputs module → `mod_outputs.py`

### 23.5 Launcher.py reduction
Target: launcher.py drops from ~5800 lines to ~2500:
- Core app shell, theme, sidebar, header, timers
- Command Center tab (always on)
- Agents tab (always on)
- Console classes (Claude/Gemini/Local)
- Settings dialog
- Module loader integration

### 23.6 Module enable/disable in Settings
- Settings dialog shows all discovered modules
- Toggle switch per module (writes to fleet.toml)
- Restart required indicator for newly enabled modules

**Files:** `BigEd/launcher/modules/mod_*.py`, `BigEd/launcher/launcher.py`

---

## v0.24 — Module Deployment & Packaging

**Goal:** Modules can be independently deployed, versioned, and distributed.

### 24.1 Module packaging
- Each module: single .py file + optional data/ subfolder
- Version tracked in manifest.json
- Module can declare minimum BigEd CC version required

### 24.2 Module discovery
- On startup: scan modules/ dir for new .py files not in manifest
- Prompt user: "New module detected: mod_foo.py — Enable?"
- Auto-add to manifest with `default_enabled = false`

### 24.3 Customer deployment profiles
```toml
# fleet.toml [deployment]
profile = "consulting"  # or "research", "full", "minimal"

[deployment.profiles.minimal]
modules = ["ingestion", "outputs"]

[deployment.profiles.consulting]
modules = ["crm", "onboarding", "customers", "accounts", "ingestion", "outputs"]

[deployment.profiles.research]
modules = ["ingestion", "outputs"]

[deployment.profiles.full]
modules = ["crm", "onboarding", "customers", "accounts", "ingestion", "outputs"]
```

### 24.4 Module dependency resolution
- `mod_onboarding.py` declares `DEPENDS_ON = ["crm"]`
- Loader auto-enables dependencies when a module is enabled
- Warns if disabling a module that others depend on

**Files:** `BigEd/launcher/modules/__init__.py`, `fleet/fleet.toml`

---

## v0.25 — Data Pipeline Maturity

**Goal:** Every module produces queryable, validated, deduplicated data.

### 25.1 Module data contracts
- Each module declares its data schema (fields, types, required)
- Validation at write time — malformed data → error log, not silent corruption
- Data export per module (JSON/CSV) for customer portability

### 25.2 Cross-module data flow
- CRM contacts → Onboarding checklists (customer lookup)
- Onboarding completion → Customers deployment tracking
- Ingestion → RAG index → Outputs browser

### 25.3 Data retention policies
- Per-module configurable retention (days)
- Auto-archive old records to JSONL
- Dashboard: data health metrics per module

**Files:** `BigEd/launcher/modules/mod_*.py`, `fleet/db.py`

---

## v0.26 — Safe Deprecation Framework

**Goal:** Any module or feature can be safely deprecated without data loss.

### 26.1 Deprecation lifecycle
```
ACTIVE → DEPRECATED → SUNSET → REMOVED
  │          │           │         │
  │      banner shown   auto-     file deleted,
  │      in UI          disabled  data archived
  │                     after N
  │                     versions
  └─────────────────────────────────┘
              data always exportable
```

### 26.2 Module deprecation metadata
```json
{
  "name": "crm",
  "deprecated": true,
  "deprecated_since": "v0.26",
  "sunset_version": "v0.30",
  "replacement": null,
  "migration_notes": "Export CRM data before v0.30. No replacement planned."
}
```

### 26.3 Deprecation enforcement
- Deprecated modules show warning banner in tab header
- Sunset modules auto-disable on version bump
- Data export prompted before auto-disable
- Removed modules: loader logs info, skips gracefully

### 26.4 Feature flags (non-module)
- `fleet.toml [features]` section for non-tab features
- `discord_bot = true`, `dashboard = true`, `openclaw = false`
- Same deprecation lifecycle applies

**Files:** `BigEd/launcher/modules/__init__.py`, `BigEd/launcher/modules/manifest.json`

---

## v0.27 — Fleet Dashboard v2

**Goal:** Dashboard becomes the monitoring hub for 24/7 operation.

### 27.1 New endpoints
- `/api/thermal` — live GPU/CPU temps, fan speed, power, ambient estimate
- `/api/training` — lock status, active run, results history
- `/api/modules` — enabled modules, versions, deprecation status
- `/api/data_stats` — per-module data size, growth, gaps

### 27.2 WebSocket live updates
- Replace 30s polling with WebSocket push
- Real-time agent status, task completion, thermal alerts

### 27.3 Alerts
- Thermal warning (>75°C sustained)
- Worker crash notification
- Training lock timeout warning
- Disk space low warning

**Files:** `fleet/dashboard.py`, `fleet/hw_supervisor.py`

---

## v0.28 — Training Pipeline v2

**Goal:** Skill training produces actionable discoveries, not just scores.

### 28.1 Discovery logging
- Every training iteration logs: what was tried, what changed, outcome
- Discoveries saved to `knowledge/skill_training/discoveries/` as markdown
- Even score-neutral or negative results are logged (negative results have value)

### 28.2 Training profiles
- `fleet.toml [training.profiles]` — named configs (aggressive, conservative, exploratory)
- Aggressive: 10 iterations, wider LLM temperature
- Conservative: 3 iterations, tight constraints
- Exploratory: try fundamentally different approaches

### 28.3 Cross-skill learning
- Planner reviews training discoveries weekly
- Successful patterns from one skill applied to others
- "Waterfall pattern worked for web_search" → try in lead_research

**Files:** `fleet/skills/skill_train.py`, `fleet/skills/plan_workload.py`

---

## v0.29 — Integration Testing & Hardening

**Goal:** Extended test suite validates 24/7 + module system.

### 29.1 Module integration tests
- Each module: test build, refresh, settings export/import, disable/re-enable
- Module with dependencies: test cascade enable/disable

### 29.2 Soak test framework
- `fleet/soak_test.py` — 1-hour automated test:
  - Submit 100 mixed tasks, verify all complete
  - Enable/disable modules mid-run
  - Kill random workers, verify recovery
  - Monitor thermal readings throughout

### 29.3 Deprecation test
- Test full lifecycle: active → deprecated → sunset → removed
- Verify data export at each stage
- Verify graceful handling of missing module files

**Files:** `fleet/soak_test.py`, `fleet/smoke_test.py`

---

## v0.30 — Production Platform Release

**Goal:** Stable, modular, customer-deployable platform.

### 30.1 Release checklist
- [ ] All modules extracted and tested
- [ ] Deprecation framework operational
- [ ] VS Code dev workflow documented
- [ ] Deployment profiles working (minimal/consulting/research/full)
- [ ] 24/7 soak test passes (v0.20 thermal + v0.29 modules)
- [ ] Customer config export/import preserves all module data
- [ ] Dashboard v2 with WebSocket + alerts

### 30.2 Deployment package
- PyInstaller bundle includes all modules
- `manifest.json` determines which modules activate
- Customer receives: executable + fleet.toml template + deployment profile

### 30.3 Documentation
- Module developer guide (how to create new modules)
- Deployment guide (profiles, config, thermal tuning)
- Deprecation guide (how to sunset features safely)

---

## Version Summary

| Version | Theme | Key Deliverable |
|---------|-------|-----------------|
| v0.21 | Dev workflow | VS Code configs, fast smoke test, test isolation |
| v0.22 | Module extraction 1 | CRM + Accounts extracted, module loader |
| v0.23 | Module extraction 2 | All tabs modularized, launcher.py < 2500 lines |
| v0.24 | Deployment | Module packaging, customer profiles, dependency resolution |
| v0.25 | Data maturity | Data contracts, cross-module flow, retention policies |
| v0.26 | Deprecation | Safe lifecycle (active→deprecated→sunset→removed) |
| v0.27 | Dashboard v2 | WebSocket, thermal/training/module endpoints, alerts |
| v0.28 | Training v2 | Discovery logging, training profiles, cross-skill learning |
| v0.29 | Integration testing | Soak test, module tests, deprecation tests |
| v0.30 | Production release | Stable modular platform, customer-deployable |
