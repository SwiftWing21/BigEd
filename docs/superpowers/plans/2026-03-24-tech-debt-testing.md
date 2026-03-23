# Tech Debt Review + Function Testing Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the 30+ new modules shipped in the 2026-03-23 marathon session. Verify imports, endpoints, DB schemas, config, and cross-module integration.

**Architecture:** Systematic sweep — config cleanup → import verification → endpoint testing → integration testing → smoke test expansion.

**Tech Stack:** Python unittest/pytest, existing smoke_test.py, curl/httpx for endpoints.

---

## Phase 1: Config & Merge Cleanup (Agent 1)

fleet.toml likely has duplicate sections from additive merge conflict resolution. Dashboard.py may have duplicate blueprint registrations.

- [x] Read `fleet/fleet.toml` end-to-end — no duplicate config sections found (clean)
- [x] Verify every `[section]` appears exactly once — confirmed
- [x] Read `fleet/dashboard.py` — removed duplicate `/api/settings/theme` route (in-memory version superseded by fleet.toml-backed version)
- [x] Fixed duplicate `api_recommendations` function name collision — renamed to `api_system_recommendations` and `api_skill_recommendations`
- [x] Check `.gitignore` includes `fleet/certs/`, `fleet/data/*.pkl`, `fleet/data/tenant_keys.db` — all covered (`fleet/certs/` at line 47, `fleet/data/` at line 144)
- [x] Verify `fleet/requirements.txt` has no duplicate entries — confirmed (9 unique packages)
- [ ] Commit: `fix: clean fleet.toml duplicates and dashboard blueprint registration from merge`

## Phase 2: Import Verification (Agent 2)

Every new module must import cleanly without side effects.

- [ ] Run `python -c "import <module>"` for each new module:
  ```
  discovery, federation_router, fleet_tls, remote_deploy,
  federation_hitl, federation_data, ml_router, self_healing,
  health_api, skill_recommender, ab_testing, dag_builder,
  predictive_scaler, mcp_server, dispatch_bridge, intent,
  sso, tenant_crypto, tenant_crypto_api, billing, compliance,
  tenant_admin, control_plane, self_service, payments,
  marketplace, geo_fleet, geo_api
  ```
- [ ] Fix any ImportError, SyntaxError, or circular import issues
- [ ] Verify lazy imports work (modules that import `db` inside functions)
- [ ] Run `python -m py_compile fleet/<module>.py` for all new files
- [ ] Commit: `fix: resolve import errors across new v0.100-v0.400 modules`

## Phase 3: DB Schema Validation (Agent 3)

Multiple modules add tables to fleet.db. Verify no conflicts.

- [ ] List all `CREATE TABLE` statements across new modules:
  - `db.py`: tasks (FORWARDED status added), deployments, experiments, experiment_results
  - `billing.py`: tenant_usage, tenant_quotas
  - `tenant_admin.py`: tenants
  - `self_service.py`: api_keys, onboarding
  - `payments.py`: payment_records, payment_customers
  - `marketplace.py`: marketplace_packages, marketplace_reviews, marketplace_publishers, marketplace_installs
  - `geo_fleet.py`: fleet_regions, scaling_config, scaling_events, cdn_endpoints
  - `compliance.py`: compliance_reports
  - `tenant_crypto.py`: tenant_keys (separate DB)
- [ ] Run `python -c "import db; db.init_db()"` — verify no schema conflicts
- [ ] Verify each module's `ensure_*_tables()` / `_init_*()` is idempotent
- [ ] Check for column name collisions across tables
- [ ] Commit: `fix: resolve DB schema conflicts and ensure idempotent table creation`

## Phase 4: Endpoint Testing (Agent 4)

Test every new REST endpoint returns valid JSON and correct status codes.

- [ ] Start dashboard: `python fleet/dashboard.py` (or use existing running instance)
- [ ] Test each endpoint group with curl/httpx:

  **Federation (0.100):**
  ```
  GET /api/federation/discovered
  GET /api/federation/capacity
  GET /api/federation/routing-stats
  GET /api/federation/cert-status
  GET /api/federation/hitl
  GET /api/cluster/agents
  GET /api/cluster/tasks
  GET /api/cluster/metrics
  ```

  **Health (0.200):**
  ```
  GET /api/health/agents
  GET /api/health/skills
  GET /api/health/circuit-breakers
  GET /api/health/rollback-candidates
  GET /api/health/recovery-log
  ```

  **Routing (0.200):**
  ```
  GET /api/routing/model-status
  GET /api/recommendations/popular
  GET /api/experiments
  GET /api/scaling/prediction
  ```

  **DAG (0.200):**
  ```
  POST /api/dag/create {"description": "review code then summarize"}
  ```

  **Billing (0.300):**
  ```
  GET /api/billing/pricing
  GET /api/billing/overview
  ```

  **Compliance (0.300):**
  ```
  GET /api/compliance/status
  GET /api/compliance/reports
  ```

  **Tenant (0.300):**
  ```
  GET /api/tenants
  ```

  **Platform (0.400):**
  ```
  GET /api/platform/fleets
  GET /api/platform/health
  GET /api/platform/metrics
  GET /api/plans
  GET /api/marketplace/packages
  GET /api/regions
  ```

- [ ] Document any 500 errors, missing routes, or import failures
- [ ] Fix broken endpoints
- [ ] Commit: `fix: resolve endpoint errors found during function testing`

## Phase 5: Integration Testing (Agent 5)

Test cross-module interactions.

- [ ] **Intent → Task → Result:** `python fleet/dispatch_bridge.py submit "review worker code"` → verify task created in DB
- [ ] **MCP Server loads:** `python -c "from mcp_server import mcp; print(mcp.name)"` → "BigEd Fleet"
- [ ] **Skill catalog:** `python fleet/dispatch_bridge.py catalog` → lists 92+ skills
- [ ] **Discovery module:** `python -c "from discovery import get_discovered_peers; print(get_discovered_peers())"` → empty list (no peers on local)
- [ ] **Self-healing sweep:** `python -c "from self_healing import run_health_sweep; run_health_sweep()"` → no crash
- [ ] **DAG builder:** `python -c "from dag_builder import build_dag_from_description; print(build_dag_from_description('review code then summarize'))"` → valid DAG
- [ ] **Billing tables:** `python -c "from billing import ensure_billing_tables; ensure_billing_tables()"` → no error
- [ ] **Marketplace tables:** `python -c "from marketplace import _ensure_tables; _ensure_tables()"` → no error
- [ ] **Compliance status:** `python -c "from compliance import get_compliance_status; print(get_compliance_status())"` → green/yellow/red
- [ ] Commit: `fix: resolve integration issues found during cross-module testing`

## Phase 6: Smoke Test Expansion

- [ ] Add new smoke checks to `fleet/smoke_test.py`:
  - `check_new_module_imports()` — import all 28 new modules
  - `check_fastmcp_available()` — FastMCP importable
  - `check_db_schema_complete()` — all expected tables exist
  - `check_fleet_toml_valid()` — TOML parses without error, no duplicate sections
  - `check_dashboard_blueprints()` — all blueprints registered
- [ ] Run `python fleet/smoke_test.py --fast` — target 27/27 (was 22/22)
- [ ] Commit: `test: expand smoke tests to cover v0.100-v0.400 modules`

## Phase 7: Version Bump + Roadmap Update

- [x] Update version strings — CLAUDE.md header, docs/WHAT_IS_BIGED.md, fleet/CLAUDE.md (fleet.toml has no version field; launcher reads from git tags dynamically)
- [x] Verify `ROADMAP.md` — 0.085/0.110/0.135/0.160 milestones (the actual section headers for 0.100-0.400 themes) already marked [DONE] with dates
- [x] Update `CLAUDE.md` — added 11 new modules to Key File Paths table, updated skill count (86), endpoint count (190+), version to 0.400.00b
- [ ] Commit: `chore: version bump to v0.400.00b, update roadmap and docs`

---

## Agent Assignment (5 parallel agents)

| Agent | Phases | Files |
|---|---|---|
| 1 | Phase 1 (config cleanup) + Phase 7 (version bump) | fleet.toml, dashboard.py, ROADMAP.md, CLAUDE.md |
| 2 | Phase 2 (import verification) | All new .py modules |
| 3 | Phase 3 (DB schema) | db.py, billing.py, marketplace.py, geo_fleet.py, etc. |
| 4 | Phase 4 (endpoint testing) | dashboard.py, all API endpoints |
| 5 | Phase 5 (integration) + Phase 6 (smoke tests) | dispatch_bridge.py, smoke_test.py |
