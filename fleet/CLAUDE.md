# Fleet

## Workers
| Name | Role |
|------|------|
| researcher | Papers, arxiv, web search |
| coder_1 | Code review — software architect perspective |
| coder_2 | Code review — critic / reviewer perspective |
| coder_3 | Code review — performance optimizer perspective |
| coder_N | Additional instances via `fleet.toml [workers] coder_count` |
| archivist | Flashcards, knowledge org |
| analyst | autoresearch results.tsv analysis |
| sales | SMB lead research + outreach |
| onboarding | Client onboarding checklists |
| implementation | Local AI deployment specs |
| security | Security audits, pen tests, advisories |
| planner | Workload planning — queues 5–500 tasks based on fleet state |

## Quick Reference
- Full command reference: `../Max Stuff/fleet_commands.md`
- Live status: `uv run python lead_client.py status`
- Start fleet: `nohup uv run python supervisor.py >> logs/supervisor.log 2>&1 &`
- Security advisories: `knowledge/security/pending/advisory_<id>.md`

## Coder skill outputs
| Skill | Output location |
|-------|----------------|
| `code_discuss` | `knowledge/code_discussion/` + messages table |
| `code_index` | `knowledge/code_index.jsonl` |
| `code_review` | `knowledge/code_reviews/<file>_review_<date>_<agent>.md` |
| `fma_review` | `knowledge/fma_reviews/<file>_review_<date>_<agent>.md` + discussion logs |
| `skill_draft` | `knowledge/code_drafts/<name>_draft_<date>_<agent>.py` |

Drafts are **never auto-deployed** — review before copying to `skills/`.

## Messaging Bridges
| Bridge | Config flag | Status |
|--------|------------|--------|
| Discord bot (`discord_bot.py`) | `discord_bot_enabled` | Active — routes `biged-fleetchat` channel to fleet |
| OpenClaw gateway | `openclaw_enabled` | Installed, disabled by default — multi-channel (WhatsApp/Telegram/Slack/etc) |

Discord commands: `/aider`, `/claude`, `/gemini`, `/local`, `/status`, `/task`, `/result`, `/help`

## Files
- `fleet.db` — SQLite store (tasks, agents, messages)
- `fleet.toml` — config (eco_mode, model, timeouts)
- `knowledge/` — worker artifacts
- `logs/` — per-worker logs
