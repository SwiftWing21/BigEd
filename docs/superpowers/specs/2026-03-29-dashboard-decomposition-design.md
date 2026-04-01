# Dashboard.py Decomposition — Design Spec

**Date:** 2026-03-29
**Status:** Approved
**Goal:** Split 5,433-line dashboard.py monolith into focused blueprints while preserving all route URLs and behavior.

## Approach

Hybrid: big domains get their own blueprint, small related domains are grouped by theme. Dashboard.py becomes a thin orchestrator (~600-700 lines).

## dashboard_utils.py (~400-500 lines)

Shared infrastructure imported by all blueprints:

- `get_conn()`, `query()` — DB connection pooling
- `_load_config()` — TOML config with caching
- `_check_rate_limit()` — in-memory request throttling
- `_log_api_attribution()` — endpoint usage logging
- `_add_security_headers()` — CORS, HSTS, CSP
- `_require_role()`, `_get_request_role()` — role enforcement
- `_broadcast_sse()`, `_sse_broadcaster()`, `_alert_monitor()` — SSE system
- `_add_alert()`, `_is_recent()` — alert helpers
- `_cpu_sampler()` — background CPU sampling thread

## dashboard.py (orchestrator, ~600-700 lines)

What stays:

- Flask app init, CORS, TLS config
- Blueprint registration (existing 15 + 11 new)
- `@app.before_request` / `@app.after_response` hooks
- `/` index, `/api/status`, `/api/boot/status`, `/api/stream` (SSE)
- Small routes that don't justify a file: `/api/csrf`, `/api/comms`, `/api/dashboard`, `/api/data_stats`, `/api/code_stats`, `/api/skills`, `/api/ollama/ps`, `/api/training`, `/api/timeline`, `/v1/chat/completions`, walkthrough endpoints (~390 lines total)

## Standalone Blueprints (big domains)

| Blueprint | Prefix | Routes | ~Lines | Content |
|-----------|--------|--------|--------|---------|
| `factorio_blueprint.py` | `/api/factorio` | 12 | 230 | Game control, bridge status, spectator, training proxy |
| `federation_blueprint.py` | `/api/federation` | 11 | 214 | Peers, certs, HITL, cross-fleet routing |
| `tasks_blueprint.py` | `/api/tasks` | 9 | 307 | Dispatch, priority, human-in-loop, requeue |
| `activity_blueprint.py` | `/api/activity` | 3 | 434 | Live feed, lanes, activity history |
| `deploy_blueprint.py` | `/api/deploy` | 9 | 148 | Prepare, push, approve/reject, rollback |
| `mode_blueprint.py` | `/api/mode` | 2 + helpers | 350 | Mode switch + detect/state/detail/modifier helpers |
| `settings_blueprint.py` | `/api/settings` | 5 | 150 | Settings CRUD, theme, JSON schema |

## Grouped Blueprints (small domains by theme)

| Blueprint | Bundles | Prefixes | Routes | ~Lines |
|-----------|---------|----------|--------|--------|
| `monitoring_blueprint.py` | alerts, logs, thermal, health, cluster, integrity | `/api/alerts`, `/api/logs`, `/api/thermal`, `/api/health`, `/api/cluster`, `/api/integrity` | 16 | ~440 |
| `metering_blueprint.py` | billing, usage/cost, gate/traffic, scaling | `/api/billing`, `/api/usage`, `/api/gate`, `/api/scaling` | 18 | ~330 |
| `ops_blueprint.py` | cache, audit, GDPR, filesystem audit, SLA, triggers | `/api/cache`, `/api/audit`, `/api/gdpr`, `/api/filesystem`, `/api/sla`, `/api/trigger` | 12 | ~280 |
| `knowledge_blueprint.py` | RAG, recommendations, knowledge, discussions, reviews, evolution, experiments | `/api/rag`, `/api/recommendations`, `/api/knowledge`, `/api/discussions`, `/api/reviews`, `/api/evolution`, `/api/experiments` | 14 | ~410 |

## Existing Blueprints (unchanged)

- `ingest_blueprint.py` (384 lines, 12 routes)
- `views_blueprint.py` (500+ lines, 14+ routes)
- `modules_blueprint.py` (629 lines, 19 routes)
- Plus 12 other registered blueprints (fleet_bp, health_bp, geo_bp, a2a_bp, etc.)

## Migration Rules

1. Extract one blueprint at a time, run smoke tests after each
2. All blueprints import shared helpers from `dashboard_utils.py`
3. No route URL changes — purely internal refactor
4. Blueprint registration order preserved in dashboard.py
5. Each new blueprint follows existing pattern: `Blueprint()` constructor, `@bp.route()` decorators, lazy imports, try/except error handling
6. SSE broadcast calls use `dashboard_utils._broadcast_sse()` — no direct queue access

## Migration Order (recommended)

1. `dashboard_utils.py` — extract shared helpers first (everything else depends on this)
2. `factorio_blueprint.py` — self-contained, no cross-dependencies
3. `federation_blueprint.py` — self-contained
4. `tasks_blueprint.py` — self-contained
5. `activity_blueprint.py` — uses SSE helpers from utils
6. `deploy_blueprint.py` — self-contained
7. `mode_blueprint.py` — has large helper functions, extract together
8. `settings_blueprint.py` — self-contained
9. `monitoring_blueprint.py` — grouped, extract together
10. `metering_blueprint.py` — grouped
11. `ops_blueprint.py` — grouped
12. `knowledge_blueprint.py` — grouped, finish with this

## Final State

- **dashboard.py**: ~600-700 lines (orchestrator + small routes)
- **dashboard_utils.py**: ~400-500 lines (shared infrastructure)
- **11 new blueprints**: 150-440 lines each
- **3 existing blueprints**: unchanged
- **Total files**: 15 blueprint files + dashboard.py + dashboard_utils.py
