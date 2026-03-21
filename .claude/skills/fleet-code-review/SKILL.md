---
name: fleet-code-review
description: Review fleet Python code using the fleet's structured review format with severity levels, perspectives, and line-level findings. Use when the user asks to review fleet code.
disable-model-invocation: true
allowed-tools: Read, Glob, Grep
---

# Fleet Code Review

Review the fleet code at: $ARGUMENTS

## Review Perspectives

Choose the most appropriate perspective (or ask the user):

| Perspective | Focus Areas |
|-------------|-------------|
| **Software Architect** | Module structure, interface design, coupling, extensibility |
| **Code Critic** | Bugs, error handling, edge cases, security, code clarity |
| **Performance Optimizer** | Query efficiency, I/O patterns, timeouts, caching opportunities |

## Output Format

Produce a structured review with these sections:

### Summary
One paragraph — what this file does and your overall impression.

### Findings
List each finding as:
- **[SEVERITY]** Line ~N: description of issue and suggested fix

Severity levels: `CRITICAL` | `HIGH` | `MEDIUM` | `LOW` | `NOTE`

Limit to 8 most important findings. Be specific — reference actual variable names, function names, and line numbers.

### Top Recommendation
The single most impactful change to make first.

## Quality Checks (from fleet's code_quality skill)

When reviewing, specifically check for:
- Bare except clauses (should catch specific exceptions)
- Mutable default arguments (`def foo(x=[])`)
- Star imports (`from x import *`)
- Functions >80 lines (suggest splitting)
- Nesting depth >3 (suggest early returns)
- `print()` in skill code (should use logging)
- Missing `run()` docstring
- Inconsistent return types (mixed None/dict)
- Unused imports
- TODO/FIXME/HACK markers

### Windows-Specific Checks

- **No shell process commands** (`pkill`, `pgrep`, `kill`) — must use `psutil` for process management
- **TOML writes** — must use `tomlkit` (not `toml` or raw string writes) to preserve comments and formatting
- **subprocess.Popen** — must include `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` to suppress console flash
- **No `uv run`** — use native `python` on Windows; `uv run` is WSL-only

## Fleet-Specific Checks

- Does `run()` validate all payload keys?
- Are file paths checked for traversal?
- Does it handle `call_complex()` failures gracefully?
- Is output saved to the correct `knowledge/` subdirectory?
- Does it return a proper dict with status/error keys?

## Architecture-Specific Checks

### Mixin Pattern (ui/settings/)
- Settings panels use mixin classes in `BigEd/launcher/ui/settings/` (10 modules)
- Check for proper MRO (Method Resolution Order) — mixin should not define `__init__` unless calling `super()`
- No circular imports between mixin modules
- Public API is `ui.settings.__init__.py` — panels should import from `ui.settings`, not from `ui.settings.general` etc.

### Dialog Extraction (ui/dialogs/)
- Dialogs should live in `BigEd/launcher/ui/dialogs/`, not inline in `launcher.py`
- Each dialog module (thermal.py, review.py, model_selector.py, walkthrough.py) should be self-contained
- If you find dialog code still in launcher.py, flag it as a `[MEDIUM]` extraction candidate

### MCP Handler Pattern
- MCP handlers use lazy `sys.path.insert` — verify the path depth is correct for the module's location in the tree
- Check that `mcp_manager.get_mcp_url()` / `is_mcp_available()` is used before attempting MCP calls
- Fallback chain should be: MCP server -> local library -> HTTP fallback

### Theme Constants
- New theme constants must be used: `GLASS_*`, `FONT_BOLD`, `FONT_TITLE`, etc.
- Flag hardcoded color values (hex strings, RGB tuples) that should use theme constants instead
- Check for consistency with the existing theme system

### Dependency Validation
- `fleet/dependency_check.py` can be used to validate runtime prerequisites after code changes
- If reviewing code that adds new imports or dependencies, note that `python dependency_check.py --json` should pass

## After Review

Save the review to `fleet/knowledge/code_reviews/<filename>_review_<date>_<agent>.md` using the format above (include the agent name suffix per fleet/CLAUDE.md convention), so the fleet's `skill_evolve` can consume it later.
