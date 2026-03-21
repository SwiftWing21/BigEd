---
name: fleet-simplify
description: Post-implementation review — check new code for reuse, quality, efficiency, theme compliance, and fleet patterns, then fix issues found. Use after completing a feature or refactor.
disable-model-invocation: true
allowed-tools: Read, Edit, Glob, Grep, Bash
---

# Fleet Simplify

Review and simplify recent changes: $ARGUMENTS

## Step 1: Identify What Changed

```bash
# Recent changes (uncommitted)
git diff --stat HEAD

# Or last N commits
git log --oneline -5
git diff --stat HEAD~3..HEAD
```

## Step 2: Check for Reuse Violations

For each changed file, check if it duplicates existing patterns:

| Pattern | Correct Source | Common Violation |
|---------|---------------|-----------------|
| Theme colors | `BigEd/launcher/ui/theme.py` (`BG`, `BG2`, `BG3`, `ACCENT`, `ACCENT_H`, `GOLD`, `TEXT`, `DIM`, `GREEN`, `ORANGE`, `RED`, `BLUE`, `CYAN`, `YELLOW`) | Hardcoded hex strings like `"#1a1a1a"` |
| Glass palette | `theme.py` (`GLASS_BG`, `GLASS_NAV`, `GLASS_PANEL`, `GLASS_HOVER`, `GLASS_SEL`, `GLASS_BORDER`) | Hardcoded `"#0f0f0f"` or `"#181818"` |
| Button backgrounds | `theme.py` (`BG_START`, `BG_START_H`, `BG_DASH`, `BG_DASH_H`, `BG_DANGER`, `BG_DANGER_H`) | Hardcoded `"#1e3a1e"` etc. |
| Counter colors | `theme.py` `COUNTER_COLORS` dict | Hardcoded color-by-status mappings |
| Font constants | `theme.py` (`FONT_XS`, `FONT_SM`, `FONT_STAT`, `FONT_MONO`, `FONT_BOLD`, `FONT_TITLE`, `FONT_H`, `MONO`, `FONT`) | Hardcoded `("Segoe UI", 10)` or `("Consolas", 11)` |
| Dimension constants | `theme.py` (`CARD_RADIUS`, `CARD_PAD`, `BTN_HEIGHT`, `BTN_HEIGHT_SM`, `TAB_HEIGHT`, `HEADER_HEIGHT`) | Hardcoded `8`, `28`, etc. |
| Config reads | `config.py` `load_config()` | Direct TOML parsing or hardcoded values |
| DB access | `db.py` (canonical DAL — `DB_PATH`, `get_conn()`, query helpers) | Raw `sqlite3.connect("fleet.db")` in skills or new modules |
| MCP server URLs | `mcp_manager.py` `get_mcp_url()` | Hardcoded `localhost:8931` |
| Subprocess flags | `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` | Missing flag causes console window flash on Windows |
| Ollama host | `config["models"]["ollama_host"]` via `load_config()` | Hardcoded `http://localhost:11434` |
| Path construction | `FLEET_DIR / "subdir"` (from `Path(__file__).parent`) | String concatenation or hardcoded paths |
| Error returns | `return {"error": "msg"}` from `run()` | Raising exceptions from skill `run()` |

## Step 3: Quality Checks

| Check | How | Fix |
|-------|-----|-----|
| Hardcoded colors | `grep -rn '"#[0-9a-fA-F]' <file>` | Replace with `theme.py` constant |
| Bare excepts | `grep -n 'except:' <file>` | Catch specific exception |
| Missing type hints | Scan function signatures | Add return type at minimum |
| Functions >60 lines | Count lines between `def` markers | Extract helper |
| Import bloat | Compare imports to actual usage | Remove unused |
| Dead code markers | `grep -n 'TODO\|FIXME\|HACK\|XXX' <file>` | Resolve or remove |
| Print debugging | `grep -n 'print(' <file>` (in fleet code) | Use `logging` module |
| Magic numbers | Scan for unnamed numeric literals | Extract to named constant |
| Duplicate logic | Compare with existing utils in same module | Extract shared helper |

## Step 4: Fleet Architecture Compliance

| Rule | Check |
|------|-------|
| Skills in `fleet/skills/` export `SKILL_NAME`, `DESCRIPTION`, `run(payload, config)` | Grep for missing exports |
| Skills optionally export `REQUIRES_NETWORK = True` | Check if skill makes HTTP calls without declaring it |
| Skills optionally export `COMPLEXITY = "simple"\|"medium"\|"complex"` | Check if complex/LLM-heavy skills declare their complexity tier (affects model routing) |
| Settings panels are mixins in `BigEd/launcher/ui/settings/<name>.py` | Not inline in `__init__.py` |
| Dialogs are in `BigEd/launcher/ui/dialogs/` | Not inline in `launcher.py` |
| Security primitives are in `fleet/security.py` | Not duplicated in dashboard or skills |
| Dashboard HTML is in `fleet/templates/dashboard.html` | Not inline strings in Python |
| DB queries go through `fleet/db.py` | Not raw `sqlite3.connect()` in new modules |
| Config reads go through `fleet/config.py` `load_config()` | Not direct `toml.load()` or hardcoded values |
| MCP lookups go through `fleet/mcp_manager.py` | Not hardcoded URLs |
| Worker skill outputs go to `fleet/knowledge/<category>/` | Not dumped in fleet root or ad-hoc paths |

## Step 5: Apply Fixes

For each issue found:
1. Show the issue (file, line, what's wrong)
2. Show the fix (what it should be)
3. Apply with Edit tool
4. Verify syntax: `python -c "import py_compile; py_compile.compile('file.py', doraise=True)"`

## Step 6: Verify

```bash
# All syntax clean (Windows-safe — avoids find/exec incompatibilities)
python -m compileall -q fleet/ BigEd/ -x "\.venv|__pycache__"

# Smoke tests pass
python fleet/smoke_test.py --fast

# Dependencies intact
python fleet/dependency_check.py
```

## Output

Summarize as:
```
## Simplify Report
- Files reviewed: N
- Issues found: N (X fixed, Y deferred)
- Reuse violations: [list]
- Quality issues: [list]
- Architecture compliance: [pass/fail details]
```
