---
name: fleet-skill-evolve
description: Improve an existing fleet skill using review findings and quality analysis. Use when the user wants to evolve, fix, or improve a fleet skill.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# Fleet Skill Evolver

Evolve/improve the fleet skill: $ARGUMENTS

## Workflow

1. **Read the current skill source**:
   - Find it in `fleet/skills/<skill_name>.py`
   - Understand its `run()` interface, payload schema, and output format

2. **Gather review findings** (check all of these):
   - `fleet/knowledge/code_reviews/` — look for `*<skill_name>*_review_*.md`
   - `fleet/knowledge/fma_reviews/` — look for `*<skill_name>*_review_*.md`
   - `fleet/knowledge/quality/reviews/` — check recent quality reports for findings on this skill
   - If no reviews exist, analyze the skill yourself for: error handling, input validation, efficiency, code clarity

3. **Generate improved version**:
   - Preserve the same `run(payload, config) -> dict` interface
   - Keep all working functionality
   - Apply review findings as improvements
   - Don't change the payload schema unless adding new OPTIONAL fields
   - Add improvements incrementally — don't rewrite from scratch

4. **Validate after evolution**:
   - Run `python fleet/dependency_check.py --json` (from project root) to verify no broken imports or missing dependencies
   - Ensure the evolved skill still passes basic contract checks (SKILL_NAME, DESCRIPTION, run() present)

5. **Save to drafts**: `fleet/knowledge/code_drafts/<skill_name>_evolved_<date>.py`
   - NEVER overwrite the live skill in `fleet/skills/` directly

6. **Report changes**: Show a diff-style summary of what changed and why

## Evolution Rules

- **Interface stability**: `run(payload, config) -> dict` must not change
- **Backward compatible**: Existing payload keys must still work
- **Incremental**: Improve, don't rewrite. Preserve working logic.
- **Documented**: Add a comment block at the top listing changes made
- **Testable**: Changes should be verifiable via `skill_test`

## Settings Mixin Note

If evolving a settings panel, it is now a mixin class in `BigEd/launcher/ui/settings/<panel>.py`.
The settings system uses 10 mixin modules composed via MRO. When evolving:
- Do not break the mixin chain — ensure `super()` calls are correct
- Import from `ui.settings` (the public API), not from individual submodules
- Test that the mixin composes correctly with the main settings window

## Common Improvements

| Category | What to Look For |
|----------|-----------------|
| Error handling | Bare excepts, missing validation, unhelpful error messages |
| Input validation | Untrusted payload fields not checked |
| Path safety | Path traversal not guarded (use `_security.safe_path()`) |
| Performance | Redundant I/O, loading full files when streaming would work |
| Timeouts | Network calls without timeout parameters |
| Return consistency | Mixed None/dict returns — always return dict |
| Logging | print() instead of structured logging |
| Complexity declaration | Missing `COMPLEXITY = "simple"\|"medium"\|"complex"` on LLM-heavy skills — add to improve model routing |
