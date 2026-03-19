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

## Quick Reference
- Commands: `../BigEd/fleet_commands.md`
- Status: `uv run python lead_client.py status`
- Start: `nohup uv run python supervisor.py >> logs/supervisor.log 2>&1 &`
- Security advisories: `knowledge/security/pending/advisory_<id>.md`

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

Drafts are **never auto-deployed** — review before copying to `skills/`.

## Messaging Bridges
| Bridge | Config flag | Status |
|--------|------------|--------|
| Discord (`discord_bot.py`) | `discord_bot_enabled` | Active — routes `biged-fleetchat` to fleet |
| OpenClaw gateway | `openclaw_enabled` | Installed, disabled by default |

Discord: `/aider`, `/claude`, `/gemini`, `/local`, `/status`, `/task`, `/result`, `/help`

## Dual Supervisor Architecture
- `supervisor.py` — Process lifecycle (Ollama start/stop, worker respawn, training detection, Discord/OpenClaw)
- `hw_supervisor.py` — Model health (keepalive every ~240s, conductor check every ~60s, VRAM/thermal scaling, model tier transitions)
- `hw_state.json` — Written by hw_supervisor every 5s. Contains: status, model, thermal, models_loaded, conductor status. Read by supervisor, workers, dashboard, and launcher.

## Offline / Air-Gap Modes
- `offline_mode = true` in fleet.toml: external API skills rejected, local Ollama works, Discord/OpenClaw skipped
- `air_gap_mode = true`: implies offline + dashboard disabled, secrets not loaded, deny-by-default skill whitelist
- Skills declare `REQUIRES_NETWORK = True` if they need internet. Worker checks before dispatch.

## Files
- `fleet.db` — SQLite (tasks, agents, messages)
- `fleet.toml` — config (eco_mode, model, timeouts, offline_mode, air_gap_mode)
- `config.py` — TOML loader + `is_offline()`, `is_air_gap()`, `AIR_GAP_SKILLS`
- `knowledge/` — worker artifacts | `logs/` — per-worker logs
