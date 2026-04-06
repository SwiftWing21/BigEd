# BigEd CC — Beta (0.400.00b)

## Quick Start
```bash
python fleet/dependency_check.py          # pre-flight check (11 deps)
python fleet/smoke_test.py --fast         # 51/51 smoke tests
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
- `FRAMEWORK_BLUEPRINT.md` — full architecture spec, data schema, 190+ endpoints
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
- Beta: `0.XXX.00b` milestones + `0.XXX.YYb` patches (b suffix until 1.000.00 graduation)
- Milestones: S-Tier infrastructure | Patches: UX, agent quality, bug fixes
- Major: `0.X00.00b` (0.100=Multi-Fleet, 0.200=Intelligent Orchestration, 0.300=Enterprise, 0.400=SaaS)
- Roadmap: `ROADMAP.md`

## Structure
- `fleet/` — 130+ skill AI worker fleet (Ollama + Claude/Gemini)
- `BigEd/` — launcher GUI + compliance docs
- `autoresearch/` — ML training pipeline
- `deploy/` — Kubernetes Helm chart for enterprise deployment
- `docs/` — design specs, plans, WHAT_IS_BIGED reference
- `education-context/` — training context files for Playwright MCP workspace
- `scripts/` — setup scripts (setup.ps1, setup.sh) and backup utilities
- `fleet/backup_manager.py` — auto-save backup system
- `fleet/cpu_temp.py` — cross-platform CPU temperature
- `fleet/filesystem_guard.py` — SOC 2 file access control

## Fleet Status
- Skills: 129 | Dashboard: 256+ endpoints (across dashboard.py + 19 blueprints) | Smoke: 51/52 | Tables: 34 | Tests: 852
- Dynamic agent scaling: 4 core + demand-based | Dr. Ders: event-driven wake-up timer
- Security: P0-P2 hardened (XSS, SQL injection, thread safety, zombie cleanup)
- Backup: auto-save every 20min, configurable depth/location
- v0.050.00b-0.400.00b: installer overhaul, model recovery, startup perf, autoresearch integration, deferred items sweep, feedback loop, federation, enterprise multi-tenant, SaaS platform

## Gotchas
- **Ollama PATH**: not on Git Bash PATH on Windows — supervisor auto-finds via `%LOCALAPPDATA%\Programs\Ollama`
- **Window flash**: all `subprocess.Popen` calls must use `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)`
- **fleet.toml is config center**: all runtime config lives here, never hardcode URLs/ports in skills
- **DB access**: always through `db.py` (fleet.db) or `data_access.py` (FleetDB for launcher) or `rag.py` — never raw `sqlite3.connect()` in skills
- **DB writes**: use `db._retry_write(fn)` for all INSERT/UPDATE — handles WAL busy timeouts with jittered backoff
- **Skills never auto-deploy**: drafts go to `knowledge/code_drafts/`, operator reviews before promotion
- **No `uv run` on Windows**: use native `python` — `uv run` is WSL only
- **Idle evolution flood**: skill_test removed from idle rotation — was 96% of tasks
- **Zombie Ollama**: close handler unloads all models (keep_alive=0) — Ollama stays running
- **Dr. Ders offline**: supervisor now spawns + respawns hw_supervisor.py
- **Error handling**: always `except Exception:` (never bare `except:`). Log with `log.warning()` at minimum — never silently swallow
- **HTTP timeouts**: every `urllib.request.urlopen()` and `httpx` call MUST have `timeout=` parameter (10-30s typical)
- **Lazy imports in skills**: use `import db` inside functions, not at module level — prevents circular imports
- **Icon system**: `icon_1024.png` is the master source, `brick.ico` is derived. Never regenerate icons during build — `generate_icon.py` was deleted
- **Cross-platform**: guard `import winreg` with `sys.platform == "win32"`, use `_open_path()` instead of `os.startfile()`

## Common Tasks — Do This, Not That

### Adding a new skill
```python
# DO: place in fleet/skills/<name>.py with the standard contract
SKILL_NAME = "my_skill"
DESCRIPTION = "What this skill does"
REQUIRES_NETWORK = False  # True if it calls external APIs

def run(task: dict, context: dict) -> dict:
    import db  # lazy import — prevents circular imports
    return {"status": "ok", "result": "..."}

# DON'T: import db at module level, skip SKILL_NAME/DESCRIPTION, or use raw sqlite3
```

### Writing to the database
```python
# DO: use db._retry_write() for any INSERT/UPDATE
import db
def save_result(data):
    def _do():
        with db.get_conn() as conn:
            conn.execute("INSERT INTO tasks ...", (data,))
    db._retry_write(_do)

# DON'T: use raw sqlite3.connect("fleet/fleet.db") — bypasses WAL retry and connection pooling
```

### Spawning subprocesses (Windows-safe)
```python
# DO: suppress the console window on Windows
import subprocess
proc = subprocess.Popen(
    ["python", "script.py"],
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

# DON'T: bare Popen without creationflags — causes window flash on Windows
```

### Making HTTP requests
```python
# DO: always set a timeout
import urllib.request
resp = urllib.request.urlopen(url, timeout=15)

# DON'T: urlopen(url) without timeout — can hang indefinitely on network issues
```

### Reading config values
```python
# DO: use fleet/config.py
from config import load_config
cfg = load_config()
port = cfg.get("dashboard", {}).get("port", 5555)

# DON'T: hardcode ports, URLs, or model names — they change per-environment
```

### Platform-specific imports
```python
# DO: guard Windows-only imports
import sys
if sys.platform == "win32":
    import winreg

# DON'T: bare `import winreg` at module level — crashes on Linux/macOS
```

### Error handling
```python
# DO: catch Exception, log a warning, return a safe default
try:
    result = do_work()
except Exception:
    log.warning("do_work failed", exc_info=True)
    result = fallback_value

# DON'T: use bare `except:` (catches SystemExit/KeyboardInterrupt) or silently pass
```

## Key File Paths
| File | Purpose | When to touch |
|------|---------|---------------|
| `fleet/fleet.toml` | All runtime config | Adding config keys, changing ports/models/thresholds |
| `fleet/db.py` | Database schema + DAL | Adding tables, new queries, schema migrations |
| `fleet/supervisor.py` | Process lifecycle | Worker scaling, boot sequence, respawn logic |
| `fleet/hw_supervisor.py` | Model health (Dr. Ders) | VRAM/thermal thresholds, keepalive timing |
| `fleet/dashboard.py` | Web UI + REST API | New endpoints, UI panels, SSE events |
| `fleet/skills/*.py` | Skill implementations | Adding/modifying agent capabilities |
| `fleet/providers.py` | Multi-backend LLM routing | Adding providers, changing fallback chain |
| `fleet/config.py` | TOML config loader | Config schema changes, new top-level sections |
| `fleet/reinforcement.py` | Human feedback loop | IQ scoring adjustments, age-out policy |
| `fleet/self_healing.py` | Auto-recovery + circuit breakers | Agent stuck detection, task retry, skill rollback |
| `fleet/ml_router.py` | ML-based task routing | sklearn agent-to-skill model, retrain |
| `fleet/federation_router.py` | Cross-fleet task routing | Peer overflow, capacity aggregation |
| `fleet/tenant_admin.py` | Multi-tenant management | Tenant CRUD, isolation, quotas |
| `fleet/compliance.py` | Compliance reporting | SOC 2, audit summary, SLA reports |
| `fleet/marketplace.py` | Skill marketplace | Package publish, review, install |
| `fleet/geo_fleet.py` | Geo-distributed fleets | Region management, auto-scaling |
| `fleet/sso.py` | SSO / OIDC / SAML | Enterprise identity federation |
| `fleet/billing.py` | Usage-based billing | Per-tenant metering, quotas, invoices |
| `fleet/control_plane.py` | SaaS control plane | Fleet provisioning, health aggregation |
| `fleet/experiment.py` | ML experiment framework | Propose/run/eval/deploy lifecycle, autonomy dial |
| `fleet/view_registry.py` | Hybrid ViewPort data sources | Register graph-renderable modules |
| `fleet/views_blueprint.py` | ViewPort + experiment REST API | 16 endpoints: views, configs, experiments |
| `fleet/token_bridge.py` | Design token sync | JSON → CSS custom properties |
| `BigEd/launcher/launcher.py` | Desktop GUI entry point | UI layout, boot flow, settings panels |

## Local Machine — CLAUDE.USER.md

`CLAUDE.USER.md` holds machine-specific config (gitignored — never committed).
Auto-generate:
```bash
python -c "import sys; sys.path.insert(0,'fleet'); from system_info import generate_user_md; open('CLAUDE.USER.md','w').write(generate_user_md())"
```

### RAM-based worker scaling
| RAM | max_workers | memory_limit_mb | Tier |
|-----|-------------|-----------------|------|
| <8GB | 4 | 256 | minimal |
| 8-16GB | 8 | 384 | basic |
| 16-32GB | 14 | 512 | standard |
| 32-64GB | 20 | 512 | high |
| 64GB+ | 28 | 768 | server |

RAM ceiling: 95% by default (`ram_ceiling_pct` in fleet.toml). Scale-up blocked when system RAM exceeds this.

### First-run setup
1. Auto-generate `CLAUDE.USER.md` (command above)
2. `python fleet/smoke_test.py` — validates Ollama, DB, skills
3. `python BigEd/launcher/launcher.py` — walkthrough auto-detects hardware
4. `fleet.db` + `rag.db` auto-created on first use

### Data layer
- **FleetDB** (`BigEd/launcher/data_access.py`): unified DAL — agent counts, tasks, token speeds, HITL
- **RAG** (`fleet/rag.py` + `rag.db`): BM25/FTS5 + optional vector search — `search()`, `hybrid_search()`, `rerank()`
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
- HA fallback: Claude → Gemini → MiniMax → Local (circuit breaker, 3 failures/5min)

## Model Tiers (API + Local)
- **Haiku** (`claude-haiku-4-5`): Sub-agents, high-volume routing
- **Sonnet** (`claude-sonnet-4-6`): Default — code review, analysis, generation
- **Opus** (`claude-opus-4-6`): Architecture, multi-step reasoning, planning
- **Local Ollama**: qwen3:8b (default GPU) | qwen3:4b (conductor CPU) | qwen3:0.6b (failsafe CPU)
  - Routing: `providers.py LOCAL_COMPLEXITY_ROUTING` + `fleet.toml [models.tiers]`
- **MiniMax** (planned): M2.5 as mid-tier provider

## Session Handoff
- **Read `SESSION_HANDOFF.md` at the start of every session** for context on recent work
- **Update it before ending** with what you did, next priorities, and current metrics
- Run `doc_freshness` skill periodically (`python fleet/skills/doc_freshness.py`) to catch stale doc values
- Keep `GEMINI_DOC_CLEANUP.md` as a reference for known doc debt

## Dev Mode
- `DEV_MODE = True` during alpha (shows BUILD, debug, idle controls)
- Production: `BIGED_PRODUCTION=1` env var or `build.py --production`
- Beta: `0.XXX.YYb` format, `b` suffix until 1.000.00 graduation
