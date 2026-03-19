# BigEd CC — Alpha

## MCP Server
- **Use the MCP server aggressively** for context, file operations, and tool access
- Dev reference docs are stored on the MCP server, NOT in this repo
- Project agent .md files (CLAUDE.md, fleet/CLAUDE.md) stay in-repo as instructions


## Roadmap & Blueprint Standards

All future blueprints, roadmaps, and implementation plans must:

1. **Reference grading logic from `audit_tracker.md`**
   - Before writing any roadmap item, read `audit_tracker.md` and extract the 
     relevant grading criteria, weights, and thresholds that apply to that item.
   - Each roadmap item must include a `Grading Alignment` field showing which 
     audit criteria it satisfies and what score impact it carries.
   - If a roadmap item does not map to any grading criteria, flag it explicitly 
     as `[UNGRADED]` so it can be triaged.

2. **Roadmap item format**
   Use this structure for every item:
```
   ### [Item Title]
   - **Goal:** What this accomplishes
   - **Grading Alignment:** <criterion from audit_tracker.md> → impact: +X pts / weight: Y%
   - **Dependencies:** List any items this blocks or is blocked by
   - **Est. Tokens:** ~Xk (see scale below)
   - **Status:** [ ] Not started / [ ] In progress / [ ] Done
```

3. **Token estimation scale (lightweight)**
   Use this rough heuristic per item — no deep analysis needed:

   | Complexity | Description                              | Est. Tokens |
   |------------|------------------------------------------|-------------|
   | XS         | Config change, minor copy, 1-file edit   | ~1–2k       |
   | S          | Single component or small feature        | ~3–5k       |
   | M          | Multi-file feature, new module           | ~8–15k      |
   | L          | Cross-cutting concern, refactor, new API | ~20–40k     |
   | XL         | Architecture change, major integration   | ~50k+       |

   Label each item with its size tier and token range. No need for exact counts.

4. **Audit drift check**
   At the end of every roadmap, include a brief section:
```
   ## Audit Coverage Check
   - Criteria fully covered: [list]
   - Criteria partially covered: [list]
   - Criteria not addressed this cycle: [list]
```

Always re-read `audit_tracker.md` if it has been updated before generating a new roadmap.

## Version Scheme
- Alpha: `0.XX.00` milestones + `0.XX.YY` patches
- Milestones: S-Tier infrastructure (reliability, observability, intelligence, security)
- Patches: UX polish, agent quality, console flows, bug fixes
- Roadmap: `ROADMAP.md`

## Structure
- `fleet/` — 72-skill AI worker fleet (Ollama + Claude/Gemini)
- `BigEd/` — launcher GUI + compliance docs
- `autoresearch/` — ML training pipeline

## Fleet Status
- Skills: 73 | Dashboard: 40+ endpoints | Smoke: 22/22
- All TECH_DEBT resolved | All parallel tracks complete
- Swarm intelligence: 3 tiers (evolution, research, specialization)
- Boot: native Windows (no WSL for fleet processes)
- Security: OWASP B+, 26 controls, GDPR B

## Machine (RTX 3080 Ti, 12GB VRAM)
- VRAM safe: 10GB. Default: qwen3:8b (~6.9GB)
- CPU models: qwen3:4b (conductor), qwen3:0.6b (maintainer)
- Python: `uv run` in WSL, native `python` on Windows
- max_workers: 10 (RAM-based scaling to 13)

## Fleet
- Dual-supervisor: `supervisor.py` + Dr. Ders (`hw_supervisor.py`) (native Windows)
- Config: `fleet/CLAUDE.md` | Status: `lead_client.py status`
- Process control: REST API (`/api/fleet/*`) | psutil-based (no pkill/pgrep)
- Boot: auto-start on launch, 7-stage sequence with adaptive timeouts

## Agent Work Distribution
- **Default: worktree multi-agent** — `isolation: "worktree"`, 5-10 agents per batch
- Split by feature, not file. Git merge handles overlaps.
- Clean up: `rm -rf .claude/worktrees; git worktree prune`

## API
- Throttle 20% of rate limits, 300ms min between requests
- HA fallback: Claude → Gemini → Local (circuit breaker, 3 failures/5min)

## Model Tiers (API + Local)
- **Haiku** (`claude-haiku-4-5`): Sub-agents, simple/repetitive tasks, high-volume routing
  - Fleet: flashcards, RAG queries, summaries, indexing, status reports
  - Multi-agent: grunt work subtasks orchestrated by Sonnet/Opus
- **Sonnet** (`claude-sonnet-4-6`): Default workhorse — code review, analysis, generation
  - Fleet: code_review, discuss, security_audit, dataset_synthesize, skill_train
- **Opus** (`claude-opus-4-6`): Hardest problems — architecture, multi-step reasoning, planning
  - Fleet: plan_workload, lead_research, skill_evolve, code_write, legal_draft
- **Local Ollama** (per-skill routing):
  - Simple skills → `qwen3:4b` (fast, ~89 tok/s) | Medium/Complex → `qwen3:8b` (~45 tok/s)
  - Routing via `providers.py LOCAL_COMPLEXITY_ROUTING` + `fleet.toml [models.tiers]`
  - Token speed tracked per-call in `usage` table (tok/s, eval_duration_ms)

## Claude Code Integration
- CLI: `claude -p "prompt"` for headless code analysis
- Skill: `fleet/skills/claude_code.py` wraps CLI for fleet tasks
- VS Code: `code /path` for interactive sessions

## Dev Mode
- `DEV_MODE = True` during alpha (shows BUILD, debug, idle controls)
- Production: `BIGED_PRODUCTION=1` env var or `build.py --production`
- Dev reference files on MCP server, not in repo
