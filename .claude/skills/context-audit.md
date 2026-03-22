---
name: context-audit
description: Audit project context files for quality, completeness, and effectiveness
---

# Context Quality Auditor

When the user asks to audit their project's context files, check CLAUDE.md quality, or bootstrap context for a new project:

## Audit Mode (default)

1. Find all context files: CLAUDE.md, .claude/rules/*.md, AGENTS.md, GEMINI.md, CONTRIBUTING.md
2. Grade each dimension:
   - **Completeness**: Does CLAUDE.md cover conventions, gotchas, structure, workflows?
   - **Consistency**: Do docs agree with each other and the code?
   - **Actionability**: Are instructions specific enough? ("use _retry_write" > "handle errors well")
   - **Coverage**: What % of the codebase is mentioned in context files?
   - **Freshness**: When was CLAUDE.md last updated vs recent commits?
3. Show report card with letter grades (A-F) per dimension
4. Highlight gaps: what's missing, what's stale, what's vague
5. Offer to draft improvements for the lowest-scoring dimensions

## Bootstrap Mode (/context-audit bootstrap)

For projects with NO context files:
1. Analyze: language, framework, directory structure, package.json/requirements.txt
2. Detect conventions from code patterns (naming, error handling, imports)
3. Find common commands (build, test, lint) from config files
4. Generate a starter CLAUDE.md with:
   - Project description (from README or package.json)
   - Detected conventions
   - Directory structure overview
   - Common gotchas (inferred from error patterns)
   - Build/test/run commands
5. Optionally generate 2-3 rule files for the most impactful patterns

## Gaps Mode (/context-audit gaps)

Show only the gaps — where context exists but isn't effective:
- "Your CLAUDE.md has security rules, but 3 recent commits introduced security issues"
- "Error handling conventions are documented but vague — 'handle errors' should be 'use try/except Exception, never bare except'"

## Output format:

| Dimension | Grade | Key Issue |
|-----------|-------|-----------|
| Completeness | B | Missing gotchas section |
| Consistency | A | All docs agree |
| Actionability | C+ | 4 vague instructions found |
| Coverage | B+ | fleet/ and BigEd/ covered, autoresearch/ not mentioned |
| Freshness | A | Updated 2 days ago |
| **Overall** | **B** | |

## Important:
- Be specific about what's missing — "add a Gotchas section covering X, Y, Z"
- Don't suggest adding context that already exists
- Prefer concrete examples over abstract advice
- If the project has no CLAUDE.md, offer bootstrap mode immediately
