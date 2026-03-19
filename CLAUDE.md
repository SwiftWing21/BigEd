# Education Project — v0.47 (approaching 1.0)

## Structure
- `fleet/` — 8-agent AI worker fleet (Ollama/qwen3:8b + Sonnet)
- `BigEd/` — reference docs, command sheets, notes
- `education-context/` — root-level project docs mirrored for MCP/container access (read-only copies of *.md from project root)

## Version Scheme
- Pre-1.0: `v0.XX` (v0.31 through v0.48)
- Post-1.0: `0.XX.YY` format (0.01.01 through current)
- Roadmap: `ROADMAP_v030_v040.md` — add new items in chronological order using 0.XX.YY format

## Fleet Status
- Smoke: 17/17 | Skills: 66 | Dashboard: 39+ endpoints
- `launcher.py`: 3492 lines
- TECH_DEBT nearly clear (4.6 partial: hw_supervisor.py regex TOML remains)
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

## MCP Servers
- **Playwright** (containerized): `docker-compose up -d` — browser automation via `http://localhost:8931`
  - Config: `.mcp.json` (HTTP transport) | Container: `playwright-mcp`
  - `education-context/` mounted read-only at `/workspace/education-context/` inside container
  - Includes Chromium, Firefox, WebKit. Restarts automatically with Docker Desktop.
  - To refresh context files: copy root *.md → `education-context/` (container sees changes live via bind mount)

## Agent Work Distribution (PREFERRED: Worktree Multi-Agent)
- **Default method**: Use `isolation: "worktree"` on Agent tool calls — each agent gets its own git branch in an isolated worktree
- **Scale**: 5-10 agents per batch is optimal. Up to 15 for large audits. Merge sequentially after completion.
- **Split by feature, not file**: each agent implements a complete vertical slice. Git merge handles any overlapping edits.
- **Merge pattern**: commit in worktree → `git merge worktree-agent-XXXX --no-edit` → resolve conflicts → push
- **Cleanup**: `rm -rf .claude/worktrees; git worktree prune; git branch | grep worktree | xargs git branch -D`
- **When to use static file exclusion instead**: only when agents must edit the same function/block (rare)

## Claude Code Agent Teams (alternative)
- Enable: set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `settings.json` under `env`
- **Lead = creator**: whichever session you ask to create the team becomes the lead
- Lead spawns teammates, assigns tasks, and synthesizes results
- Control via natural language. `Shift+Down` to message individual teammates
- **No same-file edits**: two teammates must not edit the same file simultaneously
- **No nested teams**: teammates can't spawn sub-teams
- 3-5 teammates per team. Token usage scales linearly per teammate
