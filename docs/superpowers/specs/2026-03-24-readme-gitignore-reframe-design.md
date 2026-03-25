# BigEd README Rewrite & .gitignore Documentation Privatization

**Date:** 2026-03-24
**Status:** Approved
**Scope:** README.md rewrite, .gitignore overhaul, git cache cleanup

---

## 1. Goal

Reframe BigEd's public-facing documentation from "personal vibe-coding experiment" to "AI agent orchestration platform built through AI-assisted development with human-directed architecture." Simultaneously privatize all internal working documentation so only product-facing .md files are visible on GitHub.

## 2. README Rewrite

### 2.1 Opening

Replace current self-deprecating opening with:

> BigEd CC is a centralized AI agent orchestration platform for managing local and cloud LLMs from a single interface. It coordinates a fleet of 130+ specialized AI agents across code review, security auditing, research, ML training, and knowledge management — with enterprise-grade security, multi-tenant support, and hardware-aware scaling.
>
> Built through AI-assisted development, BigEd CC is both a functional platform and a case study in directing complex software architecture through iterative AI collaboration. The human role was architectural direction, quality control, debugging, and system design — the kind of work that doesn't show up in lines-of-code metrics but determines whether a system works.

### 2.2 Contribution Table

Replace percentage-based table with role-based table:

| Role | Contributor | What This Means |
|------|------------|----------------|
| **Architecture & System Design** | Max (human) | All architectural decisions, system scope, feature prioritization, quality standards, and debugging direction |
| **Code Generation** | Claude Code (Opus 4.6) | Primary code author under human direction — wrote implementations from architectural specs and prompt-driven requirements |
| **Review & Iteration** | Claude (Sonnet 4.6) | Code review, audits, skill generation, iterative improvements |
| **Independent Audit** | Gemini Pro (2.5/3.1) | Architecture audits, second opinions, cross-validation |
| **Quality Assurance** | Max (human) | Testing, debugging, catch-and-correct for AI-introduced bugs, documentation maintenance |

### 2.3 Section Renames

- "Extra Stuff That's In There" → "Enterprise & Security Features"
- Remove self-deprecating language ("Because the models kept building...", "Take them for what they are")

### 2.4 Sections Unchanged

- Quick Start (Windows + From Source)
- Architecture tree
- Model Support table
- MCP Server Config section
- Contributing section
- License section
- Ko-fi support badge
- Repository Structure table

### 2.5 "How It Was Built" Section Heading

Rename to "Development Approach" to match the professional reframing.

## 3. .gitignore Update

### 3.1 Strategy

Wildcard-ignore all .md files, then whitelist product files:

```gitignore
# ── Documentation — private by default, whitelist product files ──────────────
*.md
!README.md
!CONTRIBUTING.md
!SETUP.md
!autoresearch/README.md
!.github/PULL_REQUEST_TEMPLATE.md
!docs/flowcharts/README.md
!docs/screenshots/README.md
!.claude/skills/**/*.md
!fleet/templates/**/*.md
```

**Why `!.claude/skills/**/*.md`:** The existing `.claude/` ignore rule prevents git from traversing into `.claude/` at all. The `!.claude/skills/**` negation only works for already-tracked files. Adding the explicit `*.md` negation ensures new SKILL.md files will still be tracked.

**Why `!fleet/templates/**/*.md`:** Template and rule .md files in `fleet/templates/` are functional source files consumed by the fleet system at runtime (e.g., `CLAUDE_TEMPLATE.md`, `rules/skill-authoring.md`). Removing them from git would break fresh clones.

### 3.2 Product Files (PUBLIC — whitelisted)

| File | Reason |
|------|--------|
| `README.md` | Main project overview |
| `CONTRIBUTING.md` | Contributor guide |
| `SETUP.md` | Install walkthrough |
| `autoresearch/README.md` | ML pipeline overview (portfolio piece) |
| `.github/PULL_REQUEST_TEMPLATE.md` | Functional tooling |
| `docs/flowcharts/README.md` | Directory placeholder |
| `docs/screenshots/README.md` | Directory placeholder |
| `.claude/skills/**/*.md` | Skill definitions (functional tooling) |
| `fleet/templates/**/*.md` | Fleet templates and rules (runtime source files) |

### 3.3 Files to Stop Tracking (git rm --cached)

Use a wildcard command to catch all non-whitelisted .md files:

```bash
git ls-files '*.md' | grep -v -E '^(README|CONTRIBUTING|SETUP)\.md$' | \
  grep -v -E '^(autoresearch/README|\.github/PULL_REQUEST_TEMPLATE|docs/flowcharts/README|docs/screenshots/README)\.md$' | \
  grep -v '^\.claude/skills/' | \
  grep -v '^fleet/templates/' | \
  xargs git rm --cached
```

This is safer than listing individual files because it catches everything the gitignore would match, including files like `fleet/VSCODE_README.md`, `improvement/current-task.md`, and other .md files not explicitly enumerated.

**Key files being removed from tracking:**
- Root: CLAUDE.md, AUDIT_TRACKER.md, ROADMAP.md, FRAMEWORK_BLUEPRINT.md, OPERATIONS.md, CROSS_PLATFORM.md, GEMINI_DOC_CLEANUP.md, SESSION_HANDOFF.md
- BigEd/compliance/: DPIA.md, MODEL_CARDS.md, ROPA.md
- docs/: WHAT_IS_BIGED.md, archive/*.md, specs/*.md, superpowers/**/*.md (including this spec)
- autoresearch/: CLAUDE.md, program.md
- fleet/: VSCODE_README.md, STATUS.md, audit-results.md, task-briefing.md

**Note:** This spec file itself (`docs/superpowers/specs/2026-03-24-readme-gitignore-reframe-design.md`) will also be removed from tracking. This is intentional — design specs are internal working documents.

**Note:** `docs/WHAT_IS_BIGED.md` and `BigEd/compliance/*.md` are deliberately privatized. While they demonstrate capability, they represent internal architecture knowledge and compliance implementation details that are more valuable kept private.

### 3.4 Cleanup

Remove now-redundant individual .md ignore rules from .gitignore:
- `how_we_roll.md`
- `SESSION_HANDOFF.md`
- `fleet/STATUS.md`
- `fleet/audit-results.md`
- `fleet/task-briefing.md`
- `BigEd/launcher/ARCHITECTURE_RESEARCH.md`
- Various `CLAUDE.*.md`, `GEMINI.md`, `MACHINE_PROFILE.md` entries
- `BigEd/research/` directory rule (covered by *.md wildcard)
- Individual BigEd/*.md entries

## 4. Git Operations

Execute in order:

1. Update `.gitignore` with wildcard + whitelist rules, clean up redundant individual rules
2. Rewrite `README.md` with Knowledge Base framing
3. `git rm --cached` all non-whitelisted .md files (using wildcard command from Section 3.3)
4. Stage all changes
5. Single commit: "Reframe README and privatize internal documentation"
6. Push (after user review of diff)

**Safety note:** `git rm --cached` only removes files from the git index — local files are preserved. However, anyone who pulls this commit on another machine will have those files deleted from their working directory. This is expected — the files are internal documentation not needed for the project to function.

## 5. Verification

After implementation:
- `git ls-files '*.md'` shows only: README.md, CONTRIBUTING.md, SETUP.md, autoresearch/README.md, .github/PULL_REQUEST_TEMPLATE.md, docs/flowcharts/README.md, docs/screenshots/README.md, .claude/skills/**/*.md, fleet/templates/**/*.md
- All other .md files exist locally but don't appear on GitHub
- New .md files created in the future are private by default

## 6. Rollback

If needed, undo the entire operation:

```bash
git reset HEAD~1
git checkout -- .gitignore README.md
```

This restores both files and re-stages all the previously-tracked .md files.
