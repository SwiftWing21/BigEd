---
name: fleet-conventions
description: Fleet architecture knowledge — skill contract, worker roles, file layout, model routing, and security patterns. Applied automatically when working on fleet code.
user-invocable: false
---

# Fleet Conventions

## Skill Contract

Every skill in `fleet/skills/` exports:
- `SKILL_NAME` — string identifier for dispatch
- `DESCRIPTION` — human-readable purpose
- `REQUIRES_NETWORK` — optional bool, default False
- `run(payload: dict, config: dict) -> dict` — single entry point

Payload is untrusted (from task queue). Config comes from `fleet.toml`.

## Worker Roles & Affinity

| Role | Skills |
|------|--------|
| coder | code_write, code_review, code_discuss, skill_draft, skill_test, skill_evolve |
| researcher | web_search, arxiv_fetch, summarize, web_crawl, rag_query |
| archivist | flashcard, rag_index, rag_query, ingest |
| analyst | analyze_results, benchmark, synthesize |
| security | security_audit, pen_test, security_review, security_apply |
| planner | plan_workload (queues 5-500 tasks by fleet state) |
| sales | lead_research, marketing |
| onboarding | client onboarding checklists |
| implementation | local AI deployment specs |
| legal | legal_draft, compliance |
| account_manager | account_review |

**Disabled by default** (`disabled_agents` in `fleet.toml [fleet]`): sales, onboarding, implementation, legal, account_manager.

## Model Routing

- **Simple** (Haiku/qwen3:4b): flashcard, rag_query, summarize, ingest
- **Medium** (Sonnet/qwen3:8b): code_review, discuss, security_audit, analyze_results
- **Complex** (Opus/qwen3:8b): plan_workload, lead_research, skill_evolve, legal_draft
- **Failsafe** (qwen3:0.6b, CPU-only): last resort when GPU/conductor unavailable

Routing is centralised in `providers.py` — `SKILL_COMPLEXITY` dict maps skill names to tiers;
`COMPLEXITY_ROUTING` / `LOCAL_COMPLEXITY_ROUTING` map tiers to model IDs. Skills do **not**
declare their own complexity tier.

For LLM calls in skills: `from skills._models import call_complex`

The `model_manager.py` skill provides Ollama model inventory — list, pull, delete, and
inspect models. Use it as a reference for a well-structured skill with multiple actions.

## File Layout

### Core Fleet

- `fleet/skills/` — skill modules (80 public skills as of v0.053; check `ls fleet/skills/*.py | grep -v "^_\|__init__"` for current count)
- `fleet/skills/_models.py` — shared model routing, budget checking
- `fleet/skills/_security.py` — path traversal prevention, sanitization
- `fleet/skills/_watchdog.py` — health monitoring, DLP scrubbing
- `fleet/skills/_review.py` — adversarial review for high-stakes skills
- `fleet/knowledge/` — all skill outputs (reviews, drafts, indexes)
- `fleet/fleet.toml` — fleet config (models, workers, budgets, security, disabled_agents)
- `fleet/config.py` — TOML loader, offline/air-gap detection
- `fleet/security.py` — authoritative security module (TLS, RBAC, rate-limit, CSRF)
- `fleet/mcp_manager.py` — MCP server discovery, reads `.mcp.json`, provides `get_mcp_url()` and `is_mcp_available()`
- `fleet/system_info.py` — `detect_system()`, `get_worker_limits()`, `generate_user_md()` for hardware-aware behavior
- `fleet/dependency_check.py` — validates runtime prerequisites, run with `--json` for machine-readable output
- `fleet/templates/dashboard.html` — extracted dashboard HTML template (was inline in dashboard.py)
- `fleet/providers.py` — multi-backend ABC (Ollama, llama.cpp, llamafile) + `SKILL_COMPLEXITY` / `COMPLEXITY_ROUTING` / `LOCAL_COMPLEXITY_ROUTING` dicts
- `fleet/db.py` — schema init and low-level DB helpers; use `data_access.py` for queries
- `fleet/idle_evolution.py` — weighted random skill selection, per-agent cooldown, cross-worker dedup
- `fleet/discord_bot.py` — Discord bridge (`discord_bot_enabled` in fleet.toml) — routes `biged-fleetchat` to fleet
- `fleet/fleet_bridge.py` — SSE reactive comms bridge (`fleet_bridge_enabled` in fleet.toml)

### Launcher GUI (BigEd)

- `BigEd/launcher/launcher.py` — main launcher (4237 lines, dialogs extracted to ui/dialogs/)
- `BigEd/launcher/dashboard.py` — web dashboard (1330 lines, HTML extracted to templates/, security to security.py)
- `BigEd/launcher/ui/dialogs/` — extracted dialog modules:
  - `thermal.py` — thermal monitoring dialog
  - `review.py` — code review dialog
  - `model_selector.py` — Ollama model selection dialog
  - `walkthrough.py` — first-run walkthrough dialog
- `BigEd/launcher/ui/settings/` — settings panels (10 mixin modules, mixin pattern):
  - Each panel is a mixin class; the main settings window composes them via MRO
  - Public API is through `__init__.py` — import from `ui.settings`, not individual submodules

## MCP Routing Pattern

Skills that need external services should use the 3-tier fallback chain:

1. **MCP server** — `from mcp_manager import get_mcp_url, is_mcp_available`; check if an MCP server provides the capability
2. **Local library** — fall back to a local Python library if available
3. **HTTP fallback** — direct HTTP request as last resort

See `browser_crawl.py` as the reference implementation of this pattern.

`mcp_manager.py` reads `.mcp.json` for server configuration. MCP handlers use lazy
`sys.path.insert` — verify path depth is correct when adding new handlers.

## Disabled Agents

`fleet.toml` supports a `disabled_agents` list. The supervisor filters these at boot time,
so disabled agents never receive tasks. Check `disabled_agents` before assuming a skill
is available in the running fleet.

## Security Patterns

- Path traversal: always validate against `FLEET_DIR` or use `_security.safe_path()`
- Secrets: never log or return API keys — `_watchdog.py` scrubs 17 patterns
- High-stakes skills get adversarial review: code_write, legal_draft, security_audit, pen_test, deploy_skill
- Drafts NEVER auto-deploy — must go through `skill_promote` -> `deploy_skill`
- TLS, RBAC, rate-limit, and CSRF are handled centrally in `fleet/security.py`

## Offline / Air-Gap Modes

Controlled by `fleet.toml [fleet]`:

- **`offline_mode = true`**: external API calls rejected; local Ollama works; Discord/OpenClaw bridges skipped; skills with `REQUIRES_NETWORK = True` will not be dispatched.
- **`air_gap_mode = true`**: implies offline + dashboard disabled, secrets not loaded, deny-by-default whitelist enforced. Use `config.is_air_gap()` to gate any network behaviour.

Always check `config.is_offline()` / `config.is_air_gap()` before making external calls.

## Draft Workflow

1. `skill_draft` generates to `knowledge/code_drafts/`
2. Human or fleet reviews the draft
3. `skill_promote` validates and stages
4. `deploy_skill` copies to `skills/` with rollback capability
