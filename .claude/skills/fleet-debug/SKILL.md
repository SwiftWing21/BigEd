---
name: fleet-debug
description: Systematic debugging for fleet issues — task failures, worker crashes, model errors, boot problems. Use when the user reports a bug, test failure, or unexpected behavior.
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash
---

# Fleet Systematic Debugging

Debug the issue: $ARGUMENTS

## Step 1: Reproduce & Gather Evidence

Before guessing, collect facts:

1. **Check worker logs**: `fleet/logs/<worker_name>.log` — look for the most recent ERROR/WARNING
2. **Check supervisor log**: `fleet/logs/supervisor.log` — boot failures, respawn loops, Ollama issues
3. **Check hw_state.json**: `fleet/hw_state.json` — is Dr. Ders reporting degraded/transitioning?
4. **Check task queue**: Query fleet.db for recent FAILED tasks (read-only diagnostic — do not modify the DB directly; use `data_access.py` for writes):
   ```bash
   python -c "import sqlite3; c=sqlite3.connect('fleet/fleet.db'); [print(dict(r)) for r in c.execute('SELECT id,type,status,error,assigned_to FROM tasks WHERE status=\"FAILED\" ORDER BY id DESC LIMIT 5')]"
   ```
   Run from the project root so `fleet/fleet.db` resolves correctly.
5. **Check dependency state**: `python fleet/dependency_check.py --json`
6. **Check fleet.toml**: Has config been modified recently? `git diff fleet/fleet.toml`

## Step 2: Isolate the Layer

Fleet bugs live in one of these layers — identify which:

| Layer | Symptoms | Key Files |
|-------|----------|-----------|
| **Config** | Wrong model, missing section, bad value | fleet.toml, config.py |
| **Ollama/Model** | Timeout, OOM, model not found | hw_supervisor.py, providers.py, system_info.py |
| **Supervisor** | Workers not starting, respawn loops, disabled agents still running | supervisor.py |
| **Worker** | Task claims but fails, skill import error | worker.py, skills/<name>.py |
| **Dashboard** | API returns 500, missing endpoint | dashboard.py, security.py, templates/ |
| **Launcher** | GUI crash, settings not saving, boot stuck | launcher.py, ui/settings/, ui/dialogs/, ui/boot.py |
| **MCP** | Server unreachable, routing miss | mcp_manager.py, .mcp.json |
| **Database** | Schema mismatch, locked, corrupt | db.py, data_access.py, fleet.db |

## Step 3: Root Cause Trace

1. Start at the error message — what function threw it?
2. Read that function — what are its inputs?
3. Trace the inputs back one level — where did they come from?
4. Repeat until you find the mismatch between expected and actual state

**Common fleet root causes:**
- Ollama not on PATH (Windows) → supervisor._find_ollama() fallback
- fleet.toml section missing → config.py returns empty dict → skill gets None
- Stale worker process from previous session → psutil kill + restart
- DB schema change without migration → db.init_db() needs update
- MCP server URL changed → .mcp.json not updated → skill probe fails

## Step 4: Fix & Verify

1. Make the minimal fix
2. Run: `python fleet/smoke_test.py --fast` — all 22 must pass
3. Run: `python fleet/smoke_test.py` (soak) — all 13 soak tests must pass for stability-gate changes
4. If fleet was running: restart supervisor to pick up changes
5. Check the specific failure scenario again
6. Run: `python fleet/dependency_check.py` — all checks should pass

## Anti-Patterns (don't do these)

- Don't guess — read the logs first
- Don't restart everything — isolate the failing component
- Don't modify fleet.db directly — use db.py functions
- Don't hardcode paths — use FLEET_DIR / Path constants
- Don't suppress exceptions with bare except — catch specific errors
