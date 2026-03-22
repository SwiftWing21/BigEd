# BigEd CC — Beta (0.170.05b)

## Quick Start
```bash
python fleet/dependency_check.py          # pre-flight check (11 deps)
python fleet/smoke_test.py --fast         # 22/22 smoke tests
python BigEd/launcher/launcher.py         # launch GUI (boots fleet automatically)
python fleet/lead_client.py status        # CLI fleet status
python fleet/lead_client.py task "your instruction here"  # dispatch a task
```

## MCP Server
- **Use the MCP server aggressively** for context, file operations, and tool access
- Dev reference docs are stored on the MCP server, NOT in this repo
- Project agent .md files (CLAUDE.md, fleet/CLAUDE.md) stay in-repo as instructions

## Docs (kept as separate files — too large to inline)
- `AUDIT_TRACKER.md` — grading rubric (12 dimensions), scoreboard, resolved issues
- `ROADMAP.md` — active plan, version history, audit coverage check
- `FRAMEWORK_BLUEPRINT.md` — full architecture spec, data schema, 58 endpoints
- `OPERATIONS.md` — runbook, CLI reference, troubleshooting, backup/recovery
- `CROSS_PLATFORM.md` — platform matrix, FleetBridge ABC, migration priorities
- `CONTRIBUTING.md` — contributor guide, skill authoring, code standards
- `SETUP.md` — first-time install walkthrough (Windows/Linux/macOS)

## Roadmap & Blueprint Standards

All roadmap items must reference grading logic from `audit_tracker.md`. Format:
```
### [Item Title]
- **Goal:** What this accomplishes
- **Grading Alignment:** <criterion> → impact: +X pts / weight: Y%
- **Dependencies:** Blocks / blocked by
- **Est. Tokens:** ~Xk (XS=1-2k | S=3-5k | M=8-15k | L=20-40k | XL=50k+)
- **Status:** [ ] Not started / [x] Done
```
End every roadmap with an Audit Coverage Check section.

## Version Scheme
- Alpha: `0.XX.00` milestones + `0.XX.YY` patches
- Milestones: S-Tier infrastructure | Patches: UX, agent quality, bug fixes
- Roadmap: `ROADMAP.md`

## Structure
- `fleet/` — 85-skill AI worker fleet (Ollama + Claude/Gemini)
- `BigEd/` — launcher GUI + compliance docs
- `autoresearch/` — ML training pipeline
- `fleet/backup_manager.py` — auto-save backup system
- `fleet/cpu_temp.py` — cross-platform CPU temperature
- `fleet/filesystem_guard.py` — SOC 2 file access control
- `docs/specs/` — enterprise integration specs

## Fleet Status
- Skills: 85 (added billing_ocr, token_optimizer, screenshot, packet_optimizer, regression_detector, clinical_review, + more) | Dashboard: 58 endpoints | Smoke: 22/22
- Dynamic agent scaling: 4 core + demand-based | Dr. Ders: event-driven wake-up timer
- Security: P0-P2 hardened (XSS, SQL injection, thread safety, zombie cleanup)
- Backup: auto-save every 20min, configurable depth/location
- v0.050.00b-0.170.05b: installer overhaul, model recovery, startup perf, autoresearch integration, deferred items sweep, feedback loop

## Gotchas
- **Ollama PATH**: not on Git Bash PATH on Windows — supervisor auto-finds via `%LOCALAPPDATA%\Programs\Ollama`
- **Window flash**: all subprocess.Popen calls must use `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)`
- **fleet.toml is config center**: all runtime config lives here, never hardcode URLs/ports in skills
- **DB access**: always through `data_access.py` (FleetDB) or `rag.py` — never raw sqlite3
- **Skills never auto-deploy**: drafts go to `knowledge/code_drafts/`, operator reviews before promotion
- **No `uv run` on Windows**: use native `python` — `uv run` is WSL only
- **Idle evolution flood**: skill_test removed from idle rotation — was 96% of tasks
- **Zombie Ollama**: close handler unloads all models (keep_alive=0) — Ollama stays running
- **Dr. Ders offline**: supervisor now spawns + respawns hw_supervisor.py

## Local Machine — CLAUDE.USER.md

`CLAUDE.USER.md` holds machine-specific config (gitignored — never committed).
Auto-generate:
```bash
python -c "import sys; sys.path.insert(0,'fleet'); from system_info import generate_user_md; open('CLAUDE.USER.md','w').write(generate_user_md())"
```

### RAM-based worker scaling
| RAM | max_workers | memory_limit_mb | Tier |
|-----|-------------|-----------------|------|
| <8GB | 3 | 256 | minimal |
| 8-16GB | 6 | 384 | basic |
| 16-32GB | 10 | 512 | standard |
| 32-64GB | 13 | 512 | high |
| 64GB+ | 16 | 768 | server |

### First-run setup
1. Auto-generate `CLAUDE.USER.md` (command above)
2. `python fleet/smoke_test.py` — validates Ollama, DB, skills
3. `python BigEd/launcher/launcher.py` — walkthrough auto-detects hardware
4. `fleet.db` + `rag.db` auto-created on first use

### Data layer
- **FleetDB** (`fleet/data_access.py`): unified DAL — agent counts, tasks, token speeds, HITL
- **RAG** (`fleet/rag.py` + `rag.db`): vector store — `rag_index` writes, `rag_query` reads
- **Config** (`fleet/config.py`): TOML loader — `load_config()`, `is_offline()`, `is_air_gap()`
- **MCP** (`fleet/mcp_manager.py`): server registry — `.mcp.json`, probes, skill routing
- **System** (`fleet/system_info.py`): hardware detection — `detect_system()`, `generate_user_md()`
- **GPU** (`fleet/gpu.py`): vendor-agnostic — NVIDIA/AMD/Intel/Null
- **Deps** (`fleet/dependency_check.py`): pre-flight — `check_all()`, `--json` for CI

## Fleet
- Dual-supervisor: `supervisor.py` + Dr. Ders (`hw_supervisor.py`)
- Config: `fleet/CLAUDE.md` | Status: `lead_client.py status`
- Process control: REST API (`/api/fleet/*`) | psutil-based (no pkill/pgrep)
- Boot: 7-stage sequence with adaptive timeouts

## Agent Work Distribution
- **Default: worktree multi-agent** — `isolation: "worktree"`, agents per batch set in CLAUDE.USER.md
- Split by feature, not file. Git merge handles overlaps.
- Clean up: `rm -rf .claude/worktrees; git worktree prune`
- **Team size**: configured per-environment in CLAUDE.USER.md (default: 0 for GitHub/CI, dev machines set their own)
- Use `run_in_background` for independent tasks, foreground for sequential dependencies

## API
- Throttle 20% of rate limits, 300ms min between requests
- HA fallback: Claude → Gemini → Local (circuit breaker, 3 failures/5min)

## Model Tiers (API + Local)
- **Haiku** (`claude-haiku-4-5`): Sub-agents, high-volume routing
- **Sonnet** (`claude-sonnet-4-6`): Default — code review, analysis, generation
- **Opus** (`claude-opus-4-6`): Architecture, multi-step reasoning, planning
- **Local Ollama**: qwen3:8b (default GPU) | qwen3:4b (conductor CPU) | qwen3:0.6b (failsafe CPU)
  - Routing: `providers.py LOCAL_COMPLEXITY_ROUTING` + `fleet.toml [models.tiers]`
- **MiniMax** (planned): M2.5 as mid-tier provider

## Dev Mode
- `DEV_MODE = True` during alpha (shows BUILD, debug, idle controls)
- Production: `BIGED_PRODUCTION=1` env var or `build.py --production`
- Beta: `0.XXX.YYb` format, `b` suffix until 1.000.00 graduation
