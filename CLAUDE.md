# Education Project — v0.47 (approaching 1.0)

## Structure
- `fleet/` — 8-agent AI worker fleet (Ollama/qwen3:8b + Sonnet)
- `BigEd/` — reference docs, command sheets, notes

## Fleet Status
- Smoke: 15/15 | Skills: 55 | Dashboard: 31 endpoints
- `launcher.py`: 3492 lines
- All TECH_DEBT resolved (4.1–4.8)
- All parallel tracks complete: PT (perf tuning), DT (debug tools), CT (cost tracking), CM (comms), GR (GitHub roadmap)
- New capabilities: HA fallback, omni-box search, SSE reactive UI, token budgets, GitHub sync, auto-boot, idle evolution, marathon ML

## Machine (RTX 3080 Ti, 12GB VRAM)
- VRAM safe: 10GB. Sweet spot: DEPTH=6, ~26M params, 6.9GB
- DEPTH=7+ OOMs. No Ollama on GPU during train.py
- Ollama safe during training: `CUDA_VISIBLE_DEVICES=-1 ollama serve &`
- Python: `uv run` not `python`. Details: `MACHINE_PROFILE.md`
- Arch history/VRAM opts: `GEMINI.md`

## Fleet
- Dual-supervisor: `supervisor.py` (task distribution, auto-boot, idle evolution) + `hw_supervisor.py` (VRAM monitor, auto-scales models under pressure, HA fallback)
- Config: `fleet/CLAUDE.md` | Status: `uv run python lead_client.py status`
- Commands: `BigEd/fleet_commands.md` | Eco mode default: CPU-only, ~40% CPU, 0 VRAM
- Process control: REST API for fleet lifecycle

## API
- Throttle 20% of rate limits, 300ms min between requests, exponential backoff on 429s
- Models: `claude-sonnet-4-6` default, `claude-haiku-4-5` high-volume, `claude-opus-4-6` complex

## Claude Code Agent Teams
- Enable: set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `settings.json` under `env`
- **Lead = creator**: whichever session you ask to create the team becomes the lead. No special command — just prompt it (e.g., "Create an agent team to refactor these modules")
- Lead spawns teammates, assigns tasks, and synthesizes results. Teammates are full independent Claude Code sessions with the same project context
- Control entirely via natural language to the lead. `Shift+Down` to message individual teammates
- **No same-file edits**: two teammates must not edit the same file simultaneously (overwrites). Structure tasks so each teammate owns distinct files
- **No nested teams**: teammates can't spawn sub-teams. Only the lead manages the group
- **Fixed leadership**: can't transfer lead or promote a teammate
- **Cleanup**: always run "Clean up the team" from the lead, never from a teammate
- `/resume` and `/rewind` do not restore in-process teammates
- 3–5 teammates is a good default. Token usage scales linearly per teammate
- Team config: `~/.claude/teams/{team-name}/config.json` | Tasks: `~/.claude/tasks/{team-name}/`
