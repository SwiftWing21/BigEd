# BigEd CC — Alpha (0.30.01a)

## MCP Server
- **Use the MCP server aggressively** for context, file operations, and tool access
- Dev reference docs are stored on the MCP server, NOT in this repo
- Project agent .md files (CLAUDE.md, fleet/CLAUDE.md) stay in-repo as instructions

## Docs (kept as separate files — too large to inline)
- `AUDIT_TRACKER.md` — grading rubric (12 dimensions), scoreboard, resolved issues
- `ROADMAP.md` — active plan, version history, audit coverage check
- `FRAMEWORK_BLUEPRINT.md` — full architecture spec, data schema, 45+ endpoints
- `OPERATIONS.md` — runbook, CLI reference, troubleshooting, backup/recovery
- `CROSS_PLATFORM.md` — platform matrix, FleetBridge ABC, migration priorities
- `CONTRIBUTING.md` — contributor guide, skill authoring, code standards
- `SETUP.md` — first-time install walkthrough (Windows/Linux/macOS)

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
- Skills: 74 | Dashboard: 45+ endpoints | Smoke: 22/22
- All TECH_DEBT resolved (4.1-4.8) | All parallel tracks complete | Audit: S
- S-Tiers complete: S1 Reliability, S2 Observability, S3 Auto-Intelligence, S4 Security, S5 Multi-Backend
- Swarm intelligence: 3 tiers (evolution, research, specialization)
- Boot: native Windows (no WSL for fleet processes)
- Security: OWASP B+, 26 controls, GDPR B
- v0.30.00: remote dashboard, A2A federation, export/import, containerization
- v0.30.01a: disabled agents, HITL evolution toggle, topic diversity fix

## Local Machine — CLAUDE.USER.md

`CLAUDE.USER.md` holds machine-specific config (gitignored — never committed).
If the file is missing, auto-generate it:
```bash
python -c "import sys; sys.path.insert(0,'fleet'); from system_info import generate_user_md; open('CLAUDE.USER.md','w').write(generate_user_md())"
```

Or create manually from this template:

```markdown
# User & Environment — [Machine Name]

## Hardware
- **GPU:** [model, VRAM] or "None (CPU-only)"
- **RAM:** [total]GB — max_workers: [N] ([tier])
- **CPU:** [physical] cores ([logical] logical)
- **Platform:** [OS] — shell: [bash/zsh/powershell]

## Environment
- Python: [version]
- Ollama: [host URL] (running|not detected)
- Keys: HF_TOKEN, ANTHROPIC_API_KEY, VRAM_LIMIT_GB

## MCP Servers
| Server | Transport | URL/Command | Status |
|--------|-----------|-------------|--------|
| playwright | http | http://localhost:8931 | active |

## Model Routing
- **Local default:** [model] (~[VRAM]GB, ~[tok/s] tok/s)
- **CPU conductor:** [model]
- **API fallback:** Claude → Gemini → Local

## Worker Limits (auto-detected)
- RAM tier: [minimal|basic|standard|high|server] ([total]GB)
- Recommended max_workers: [N]
- Memory limit per worker: [M]MB
```

### RAM-based worker scaling
`fleet/system_info.py` → `get_worker_limits()` auto-detects RAM and recommends:

| RAM | max_workers | memory_limit_mb | Tier |
|-----|-------------|-----------------|------|
| <8GB | 3 | 256 | minimal |
| 8-16GB | 6 | 384 | basic |
| 16-32GB | 10 | 512 | standard |
| 32-64GB | 13 | 512 | high |
| 64GB+ | 16 | 768 | server |

### First-run setup checklist
If `CLAUDE.USER.md` is missing or fields are blank, the operator should:
1. Run `python -c "..."` (above) to auto-generate CLAUDE.USER.md from detected hardware
2. Run `python fleet/smoke_test.py` — validates Ollama, DB, skills
3. Run `python BigEd/launcher/launcher.py` — walkthrough auto-detects hardware
4. Check `fleet/fleet.db` exists — if not, any fleet script auto-creates via `db.init_db()`
5. Check `fleet/rag.db` exists — if not, `rag_index` skill creates on first ingest
6. Verify DAL: `fleet/data_access.py` (FleetDB) and `fleet/rag.py` (RAG store) are the unified data layer — all DB access goes through these, never raw sqlite3

### Data layer reference
- **FleetDB** (`fleet/data_access.py`): unified DAL for fleet.db — agent counts, task queries, token speeds, HITL status
- **RAG** (`fleet/rag.py` + `fleet/rag.db`): vector store for knowledge ingestion — `rag_index` writes, `rag_query` reads
- **Config** (`fleet/config.py`): TOML loader — `load_config()`, `is_offline()`, `is_air_gap()`
- **MCP** (`fleet/mcp_manager.py`): MCP server registry — reads `.mcp.json`, probes servers, skill routing
- **System** (`fleet/system_info.py`): unified hardware detection — `detect_system()`, `get_memory()`, `get_worker_limits()`, `generate_user_md()`
- **GPU** (`fleet/gpu.py`): vendor-agnostic GPU backend — NVIDIA/AMD/Intel/Null, used by system_info + hw_supervisor

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

## Consolidated Notes (from deleted .md files)

### Tech Debt (was TECH_DEBT.md) — ALL RESOLVED
4.1 launcher god-object → extracted consoles/settings/boot/modules (-37% lines)
4.2 aggressive polling → SSE client + 8s fallback
4.3 string process control → REST endpoints (process_control.py)
4.4 decentralized DB → data_access.py DAL
4.5 WSL dependency → NativeWindowsBridge + detect_cli()
4.6 regex TOML → tomlkit + atomic writes
4.7 skills bypassing routing → all 12 refactored to call_complex()
4.8 OS-specific commands → cross-platform branching

### Consolidated History
- Gemini 3-pass review → dual-supervisor split, crash backoff [15,30,60,120,300]s
- Beta 1.0 from Alpha 0.30.01a — all 12 audit dimensions A or S, 30+ HITL QA scenarios
- v0.14-v0.20: thermal, training lock, modular tabs, idle policy, hardening
- v0.21-v0.30: S1-S5 milestones, module extraction, multi-backend, federation
- Version map: v0.31→1.0 (pre-1.0) → 0.01→0.20 (post-1.0) → 0.21→0.30 (alpha)
