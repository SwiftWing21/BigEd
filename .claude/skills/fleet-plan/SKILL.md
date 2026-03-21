---
name: fleet-plan
description: Plan fleet implementation work — structured specs with agent batches, file ownership, grading alignment, and verification commands. Use when the user wants to plan a feature or refactor.
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash
---

# Fleet Implementation Planner

Plan the implementation of: $ARGUMENTS

## Step 1: Understand Scope

1. **Read ROADMAP.md** — is this already planned? What version?
2. **Read AUDIT_TRACKER.md** — which grading criteria does this affect?
3. **Read TECH_DEBT.md** — any P0/P1 debt that intersects this work?
4. **Check fleet.toml** — does this need config changes?
5. **Grep for related code**: find existing patterns this builds on

## Step 2: Write the Spec

Use this format for every planned item:

```
### [Item Title]
- **Goal:** What this accomplishes
- **Grading Alignment:** <criterion from audit_tracker.md> → impact
- **Files:** List every file that will be created or modified
- **Dependencies:** What must be done first
- **Est. Tokens:** ~Xk (XS=1-2k | S=3-5k | M=8-15k | L=20-40k | XL=50k+)
- **Status:** [ ] Not started / [~] In progress / [x] Done
```

## Step 3: Plan Agent Batches

For multi-file work, split into parallel agent batches:

| Principle | Rule |
|-----------|------|
| **No file conflicts** | Two agents must NOT modify the same file |
| **Feature, not file** | Split by feature/concern, not by individual file |
| **Batch after merge** | Dependent work goes in the next batch |
| **Worktree isolation** | Always use `isolation: "worktree"` |
| **5-10 agents max** | Per batch, to avoid context explosion |

Template:
```
### Batch 1 (N agents, parallel)
| Agent | Items | Files | Conflict Risk |
|-------|-------|-------|---------------|
| name  | what  | which | None/Low/Med  |
```

## Step 4: Define Verification

Every plan must end with verification commands:

```bash
# Syntax check all modified files
python -c "import py_compile; py_compile.compile('file.py', doraise=True)"

# Smoke tests (22/22 required for any merge)
python fleet/smoke_test.py --fast

# Soak tests (13/13 required for stability-gate / security changes)
python fleet/smoke_test.py

# Dependencies intact
python fleet/dependency_check.py

# Specific feature verification
curl http://localhost:5555/api/...
```

## Fleet-Specific Planning Rules

- **fleet.toml changes**: always add with comments, never remove existing keys; use `tomlkit` for any programmatic writes to preserve formatting
- **New skills**: draft to knowledge/code_drafts/, never auto-deploy
- **Dashboard endpoints**: add before the `if __name__` block
- **Settings panels**: create as mixin in ui/settings/<name>.py
- **Dialogs**: create in ui/dialogs/<name>.py, re-export from __init__.py
- **Config reads**: always via config.py load_config(), never direct TOML parse
- **DB access**: always via data_access.py FleetDB, never raw sqlite3
- **MCP servers**: register in mcp_manager.py, route via fleet.toml [mcp.routing]
- **Windows-native required**: core features must not require WSL; test on bare Windows
- **subprocess.Popen**: always pass `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` to avoid console flash on Windows
- **Process management**: use `psutil` — never `pkill`, `pgrep`, or shell kill commands
