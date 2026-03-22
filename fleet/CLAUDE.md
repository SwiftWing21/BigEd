# Fleet

## Workers
| Name | Role |
|------|------|
| researcher | Papers, arxiv, web search |
| coder_1/2/3/N | Code review (architect / critic / perf). Count via `fleet.toml [workers] coder_count` |
| archivist | Flashcards, knowledge org |
| analyst | autoresearch results.tsv analysis |
| sales | SMB lead research + outreach |
| onboarding | Client onboarding checklists |
| implementation | Local AI deployment specs |
| security | Security audits, pen tests, advisories |
| planner | Workload planning — queues 5-500 tasks by fleet state |
| legal | Legal drafts + compliance |
| account_manager | Account reviews |

`disabled_agents` in fleet.toml `[fleet]` excludes roles from boot (default: sales, onboarding, implementation, legal, account_manager).

## Quick Reference
- Status: `python lead_client.py status`
- Start: `python supervisor.py` (native Windows) or `nohup uv run python supervisor.py >> logs/supervisor.log 2>&1 &` (Linux/WSL)
- Smoke: `python smoke_test.py --fast` (22/22)
- Deps: `python dependency_check.py` (11 checks)
- Export: `python lead_client.py export` | Import: `python lead_client.py import <file>`
- Skills: 85 registered | Dashboard: 58 endpoints
- Security advisories: `knowledge/security/pending/advisory_<id>.md`
- Process control: REST API (`/api/fleet/*`)

## Coder Skill Outputs
| Skill | Output |
|-------|--------|
| `code_discuss` | `knowledge/code_discussion/` + messages table |
| `code_index` | `knowledge/code_index.jsonl` |
| `code_review` | `knowledge/code_reviews/<file>_review_<date>_<agent>.md` |
| `fma_review` | `knowledge/fma_reviews/<file>_review_<date>_<agent>.md` + discussion |
| `skill_draft` | `knowledge/code_drafts/<name>_draft_<date>_<agent>.py` |
| `security_review` | `knowledge/security/reviews/security_review_<date>.md` |
| `code_quality` | `knowledge/quality/reviews/quality_review_<date>.md` |
| `deploy_skill` | Deploys reviewed drafts from `code_drafts/` to `skills/` with rollback |
| `marathon_log` | `knowledge/marathon/marathon_log_<date>.jsonl` |
| `evaluate` | `knowledge/evaluations/eval_<date>_<agent>.md` |
| `code_refactor` | `knowledge/refactors/<file>_refactor_<date>_<agent>.md` |
| `stability_report` | `knowledge/stability/stability_<date>.md` |
| `github_sync` | Syncs task state with GitHub project board |

Drafts are **never auto-deployed** — review before copying to `skills/`.

## Messaging Bridges
| Bridge | Config flag | Status |
|--------|------------|--------|
| Discord (`discord_bot.py`) | `discord_bot_enabled` | Active — routes `biged-fleetchat` to fleet |
| OpenClaw gateway | `openclaw_enabled` | Installed, disabled by default |
| FleetBridge (`fleet_bridge.py`) | `fleet_bridge_enabled` | SSE reactive comms |

## Dual Supervisor Architecture
- `supervisor.py` — Process lifecycle (Ollama adopt/start, worker respawn, disabled agents, training detection, Discord/OpenClaw, idle evolution)
- `hw_supervisor.py` (Dr. Ders) — Model health (keepalive ~240s, conductor ~60s, VRAM/thermal scaling, model tiers, HA fallback)
- `hw_state.json` — Written by Dr. Ders every 5s. Read by supervisor, workers, dashboard, launcher.
- Cost tracking (CT-1/2/3/4) — Token budgets, per-skill attribution, alerts, dashboard panels

## Key Modules
- `mcp_manager.py` — MCP server registry, probes, skill routing (reads `.mcp.json`)
- `system_info.py` — Unified RAM/CPU/GPU detection, worker limits, `generate_user_md()`
- `dependency_check.py` — Pre-flight checker (core/hardware/data/optional/mcp)
- `data_access.py` — FleetDB DAL (all DB queries go through here)
- `providers.py` — Multi-backend ABC (Ollama, llama.cpp, llamafile) + HuggingFace search
- `idle_evolution.py` — Weighted random skill selection, per-agent cooldown, cross-worker dedup

## Offline / Air-Gap Modes
- `offline_mode = true`: external API rejected, local Ollama works, Discord/OpenClaw skipped
- `air_gap_mode = true`: implies offline + dashboard disabled, secrets not loaded, deny-by-default whitelist
- Skills declare `REQUIRES_NETWORK = True` — worker checks before dispatch.

## Files
- `fleet.db` — SQLite (tasks, agents, messages, usage, idle_runs)
- `fleet.toml` — all runtime config (models, thermal, workers, security, mcp, federation)
- `config.py` — TOML loader + `is_offline()`, `is_air_gap()`
- `.mcp.json` — MCP server definitions (project root, gitignored)
- `knowledge/` — worker artifacts | `logs/` — per-worker logs
