# BigEd + Claude Integration — Architecture Draft

**Status:** Draft for review  
**Date:** March 2026  
**Purpose:** Define a ToS-compliant hybrid system that gives BigEd users the best of both worlds — unattended API automation for routine audits and on-demand Claude Code sessions for deep human-guided work.

---

## The core idea

BigEd integrates Claude across two strict lanes, each with its own authentication and rules. No lane crossing. No grey areas.

| Lane | Auth method | What it does | Who triggers it |
|------|------------|--------------|-----------------|
| **Lane 1 — Claude Code (VS Code)** | OAuth via Pro/Max/Enterprise subscription | Interactive sessions for training file reviews, complex edits, deep analysis | Human only — always |
| **Lane 2 — API audit system** | Anthropic API key (Console) | Scheduled background audits, prompt queue processing, recurring analysis | Automated (cron/scheduler) |

The API agent can *prepare work* for a Claude Code session. It can *request* a human review. It **cannot** launch or interact with Claude Code directly. That boundary is non-negotiable and architecturally enforced.

---

## Why this is ToS-compliant

Anthropic's Consumer Terms (Section 3) prohibit automated access to subscription services "through a bot, script, or otherwise" — with two exceptions:

1. **Via an Anthropic API key** — no automation restrictions at all
2. **Where Anthropic otherwise explicitly permits it** — Claude Code CLI is their official product, built for scripted and piped usage

Our design maps cleanly:

- **Lane 1** uses the official Claude Code extension/CLI. BigEd only opens VS Code and writes context files to disk. The human does everything else. No tokens are extracted, proxied, or spoofed.
- **Lane 2** uses an API key from the Anthropic Console under Commercial Terms. Fully automated, fully permitted, zero ambiguity.
- **The handoff** between lanes is a notification + a button click. The API agent writes files; the human reads the notification and decides whether to open Claude Code. The agent never touches OAuth.

**Prohibited patterns we explicitly avoid:**
- Extracting OAuth tokens from Claude Code for API use
- Auto-triggering Claude Code sessions from schedulers or agents
- Spoofing the Claude Code client identity
- Routing subscription traffic through proxies or gateways
- Piping prompts into Claude Code without a user present

---

## Lane 1: Human-managed Claude Code sessions

### How it works

BigEd's UI has a button: **"Open in Claude Code."** When clicked, it:

1. Writes/updates structured `.md` context files in the workspace (see file templates below)
2. Launches VS Code via system URI or `code` CLI command
3. The user takes over — Claude Code reads the `.md` files automatically at session start

BigEd's job ends at step 2. It never sends prompts, never reads responses, never touches the session.

### What BigEd pre-stages (the .md file ecosystem)

Claude Code has a layered memory system. Everything is plain markdown, read automatically at session start. BigEd can write all of these before the user clicks launch:

#### 1. `CLAUDE.md` — Project constitution (static, version-controlled)

```markdown
# BigEd Training Management System

## Project context
BigEd is a training file management and compliance platform. Claude Code sessions
in this workspace focus on reviewing training materials, generating task lists,
and ensuring compliance with organizational training standards.

## Review standards
- All training files must include learning objectives, assessment criteria,
  and revision dates
- Flag any file missing required metadata fields
- Compliance checks follow the standards defined in .claude/rules/compliance.md
- Use @docs/training-standards.md as the reference for acceptable formats

## Commands
- Build: npm run build
- Test: npm run test
- Lint: npm run lint

## Conventions
- Write findings as structured markdown task lists
- Use ISO 8601 dates in all output
- Reference training files by their full path relative to /training-files/
- When generating task lists, categorize by: Critical, Review Needed, Info Only
```

#### 2. `.claude/rules/` — Path-scoped modular rules (auto-loaded)

These only activate when Claude works with matching files, saving context space.

**`.claude/rules/compliance.md`**
```markdown
---
paths:
  - "training-files/**/*.md"
  - "training-files/**/*.pdf"
  - "training-files/**/*.docx"
---
# Training file compliance rules

- Every training file MUST contain: title, version, effective date, review date,
  author, and learning objectives
- Flag files where review date is in the past as "OVERDUE REVIEW"
- Flag files missing any required metadata as "INCOMPLETE METADATA"
- Check that all external links are formatted correctly
- Verify assessment criteria match stated learning objectives
- Output findings in this format:

  ## [filename]
  - **Status:** Compliant / Non-compliant / Needs review
  - **Issues:** [list specific problems]
  - **Suggested actions:** [concrete next steps]
```

**`.claude/rules/task-generation.md`**
```markdown
---
paths:
  - "tasks/**/*"
  - "audit-results/**/*"
---
# Task list generation rules

- Read audit-results.md for the latest API audit findings
- Generate actionable task items from each finding
- Categorize: Critical (blocking), Review Needed (human judgment), Info Only (FYI)
- Include estimated effort: Quick fix (<15 min), Standard (15-60 min), Deep review (1+ hr)
- Assign priority based on: overdue dates > missing metadata > formatting issues
- Output as a checklist the user can work through in order
```

#### 3. `task-briefing.md` — Dynamic context (written by API agent before each launch)

```markdown
# Current review briefing
Generated: 2026-03-20T08:30:00Z by BigEd API audit system

## What happened
The scheduled audit (every 3 days) ran at 06:00 UTC today and reviewed
47 training files across 3 departments.

## Summary
- **12 files** flagged for issues (see details below)
- **4 files** have overdue review dates (Critical)
- **5 files** are missing required metadata fields (Review Needed)
- **3 files** have minor formatting inconsistencies (Info Only)

## Critical — overdue review dates
1. `/training-files/safety/fire-evacuation-v2.md` — review date 2025-11-01
2. `/training-files/safety/ppe-requirements.md` — review date 2025-09-15
3. `/training-files/onboarding/day-one-checklist.md` — review date 2026-01-01
4. `/training-files/compliance/data-handling.md` — review date 2025-12-01

## Review Needed — missing metadata
1. `/training-files/hr/benefits-overview.md` — missing: learning objectives, assessment criteria
2. `/training-files/it/password-policy.md` — missing: version number
3. `/training-files/safety/chemical-storage.md` — missing: author, effective date
4. `/training-files/onboarding/remote-setup.md` — missing: review date
5. `/training-files/compliance/gdpr-basics.md` — missing: assessment criteria

## Info Only — formatting
1. `/training-files/hr/leave-policy.md` — inconsistent heading levels
2. `/training-files/it/vpn-setup.md` — broken internal link on line 47
3. `/training-files/onboarding/team-intro.md` — date format not ISO 8601

## Suggested approach
Start with the 4 Critical overdue files — these are blocking compliance.
Then review the 5 metadata-incomplete files. The formatting issues can
be batched as quick fixes at the end.
```

#### 4. `audit-results.md` — Full API audit output (written by Lane 2)

```markdown
# API audit results
Run ID: audit-2026-03-20-0600
Model: claude-sonnet-4-6
Tokens used: 38,247 input / 12,891 output
Cost: $0.31

## Per-file results

### /training-files/safety/fire-evacuation-v2.md
- **Status:** Non-compliant
- **Issues:**
  - Review date overdue by 140 days (was 2025-11-01)
  - Section 3.2 references outdated building codes
  - Emergency contact list has 2 disconnected numbers
- **Confidence:** High (structured metadata check + content analysis)

### /training-files/hr/benefits-overview.md
- **Status:** Needs review
- **Issues:**
  - Missing learning objectives (file appears to be informational, not training)
  - Missing assessment criteria
- **Confidence:** Medium (may be intentionally informational — human should confirm
  whether this file should be reclassified or updated)
- **Note for reviewer:** This is why the API agent flagged this for Claude Code
  review rather than auto-categorizing it.

[... additional files ...]
```

#### 5. `.claude/skills/` — Reusable workflow templates (on-demand)

**`.claude/skills/training-review/SKILL.md`**
```markdown
---
name: training-review
description: Comprehensive review of a training file against BigEd standards.
  Invoke when reviewing individual training documents for compliance,
  quality, and completeness.
---

# Training file review skill

## Steps
1. Read the target training file completely
2. Check all required metadata fields against @.claude/rules/compliance.md
3. Assess content quality: clarity, accuracy, completeness of learning objectives
4. Verify assessment criteria align with stated objectives
5. Check all links and cross-references
6. Generate a structured review report

## Output format
Use this template:

### Review: [filename]
**Reviewer:** Claude Code (human-supervised)
**Date:** [today]
**Overall status:** Compliant / Non-compliant / Needs revision

#### Metadata check
| Field | Present | Valid | Notes |
|-------|---------|-------|-------|
| Title | ✓/✗ | ✓/✗ | ... |
| Version | ✓/✗ | ✓/✗ | ... |
[... all required fields ...]

#### Content quality
- Learning objectives: [assessment]
- Assessment criteria: [assessment]
- Clarity and readability: [assessment]

#### Issues found
1. [issue + suggested fix]

#### Recommended actions
- [ ] [action item 1]
- [ ] [action item 2]
```

#### 6. `.claude/agents/` — Specialized subagent personas

**`.claude/agents/compliance-reviewer.md`**
```markdown
---
name: compliance-reviewer
description: Strict compliance-focused reviewer for training files.
  Prioritizes regulatory requirements and organizational standards.
model: sonnet
color: red
---

You are a compliance reviewer specializing in training documentation.
Your priority is identifying regulatory and policy gaps.

When reviewing files:
- Apply the strictest reasonable interpretation of requirements
- Flag anything ambiguous as "needs clarification" rather than passing it
- Cross-reference against the standards in @docs/training-standards.md
- Note any files that may need legal or HR review
- Be specific about which standard or requirement each issue relates to
```

---

## Lane 2: Unattended API audit system

### Components

**Audit engine:** Takes training files + a prompt from the queue, calls the Anthropic API (`/v1/messages`) with an API key, stores structured results in BigEd's database.

**Prompt queue:** User-configured list of prompts the engine processes. Each entry has:

| Field | What it controls |
|-------|-----------------|
| `prompt_id` | Unique name for this template |
| `prompt_text` | The actual prompt (supports `{file}`, `{date}` variables) |
| `model` | Haiku (quick checks) / Sonnet (standard) / Opus (deep analysis) |
| `repeat` | Run once, or repeat every scheduled execution |
| `priority` | Execution order within a batch |
| `max_tokens` | Token budget cap for this prompt |

**Scheduler:** Two modes:

1. **Recurring interval** — user picks a cadence (every 1/2/3/5/7/14/30 days). System runs the full queue against pending files at that interval.
2. **Single window block** — user schedules a specific date/time window for a one-time batch. Good for large reviews or end-of-cycle audits.

### Scheduler UI settings

| Setting | Details |
|---------|---------|
| Audit frequency | Dropdown: every N days, or custom cron |
| Single window block | Date picker + time range. Runs once. |
| Prompt queue | Drag-and-drop ordered list. Toggle repeat per prompt. |
| File scope | Which directories/files to include. Supports glob patterns. |
| Model per prompt | Haiku / Sonnet / Opus with estimated cost shown |
| Token budget | Per-prompt and per-run caps with warnings |
| Notifications | Email + in-app alert when audit completes |

---

## The handoff: "Manual Claude Code review requested"

This is the feature that bridges the two lanes while keeping them strictly separate.

### Flow

1. **API audit runs** (Lane 2, automated, API key) and finds something that needs human judgment — ambiguous categorization, content that might be intentionally non-standard, or a finding the model has medium/low confidence on.

2. **Agent writes briefing files** to the workspace:
   - Updates `task-briefing.md` with the specific findings and why human review is needed
   - Updates `audit-results.md` with the full audit output
   - Optionally updates `.claude/rules/` if the review needs specific focus areas

3. **Agent sends notification** through BigEd's UI:
   - In-app banner: "Claude Code review requested — 4 critical items, 5 needing human judgment"
   - Optional email with summary
   - Notification includes a one-line reason: "3 files may need reclassification — API audit confidence was below threshold"

4. **User reviews the notification** and decides whether to act. They can:
   - Click **"Open in Claude Code"** — BigEd launches VS Code with all context pre-loaded
   - Click **"View findings"** — see the audit results without launching Claude Code
   - Click **"Dismiss"** — acknowledge and move on
   - Click **"Schedule for later"** — add to their task queue with a reminder

5. **If they launch Claude Code**, the session starts with full context already loaded. Claude has read the briefing, the audit results, the compliance rules, and the task-generation rules. The user can immediately say "let's start with the critical files" and Claude knows exactly what they mean.

### What the agent CANNOT do

- Launch VS Code or Claude Code
- Send prompts into an active Claude Code session
- Auto-approve its own recommendations
- Modify scheduler settings without human confirmation
- Escalate its own notification priority

---

## HITL approval gate

Any change that increases API consumption requires explicit human approval.

| Action | What triggers it | Approval flow |
|--------|-----------------|---------------|
| Increase audit frequency | User changes to shorter cadence | Confirmation dialog with cost delta |
| Add new prompt | User creates a template | Dry run preview + approve |
| Enable prompt repeat | User toggles repeat on | Projected monthly cost + confirm |
| Upgrade model tier | Haiku → Sonnet → Opus | Side-by-side cost comparison |
| Expand file scope | More files added to audit | Token estimate for new files |
| Accept system recommendation | System suggests optimization | Review recommendation + explicit approve |

### System recommendations (never auto-applied)

The system can suggest optimizations, but every suggestion requires a human click:

- "Your review prompt hasn't changed in 30 days. Consider updating criteria." → **Approve** / Dismiss
- "60% of files pass clean. Reduce frequency for passing files to save ~$X/month." → **Approve** / Dismiss
- "Prompt #3 consistently uses 20% of its token budget. Switch Opus → Sonnet for 75% cost savings." → **Approve** / Dismiss

No recommendation auto-expires into approval. No default actions. Every change is a deliberate human choice.

---

## Cost model

Based on training file review every 3 days, ~10 files per batch, Sonnet model:

| Metric | Per run | Monthly (~10 runs) |
|--------|---------|-------------------|
| Input tokens | ~40K | ~400K |
| Output tokens | ~20K | ~200K |
| Estimated API cost | ~$0.20–$0.40 | ~$2–$4 |
| Claude Code subscription | Included in plan | $20–$200 (plan-dependent) |

The API audit layer is remarkably cheap at this cadence. The HITL gate prevents cost surprises.

---

## Implementation phases

### Phase 1: API audit system
- Prompt queue management UI in BigEd settings
- Scheduler with recurring + single window block support
- Audit engine processing queue against training files
- Results viewer with task list generation
- Token/cost tracking dashboard

### Phase 2: VS Code launch integration
- "Open in Claude Code" button in training review UI
- CLAUDE.md + rules + briefing file writer
- Cross-platform VS Code launch (macOS/Windows/Linux)
- Workspace configuration for Claude Code extension

### Phase 3: HITL governance + handoff
- Recommendation engine analyzing audit patterns
- Approval workflow UI with cost projections
- "Manual Claude Code review requested" notification system
- Audit log for all configuration changes
- Anomalous usage alerting

---

## Open questions for review

1. **Enterprise vs Pro/Max:** Should we target Enterprise plan from day one (Commercial Terms, no data training, Compliance API) or support Pro/Max with upgrade path?

2. **CLAUDE.md ownership:** Should BigEd fully own the CLAUDE.md and regenerate it, or write a base template that users can extend? (Recommendation: BigEd owns the dynamic files like `task-briefing.md` and `audit-results.md`, but the user owns `CLAUDE.md` and `.claude/rules/`.)

3. **Notification channels:** Email + in-app is the baseline. Should we add Slack/Teams webhooks for the "review requested" notifications?

4. **Multi-user workspaces:** If multiple users share a BigEd workspace, how do we handle concurrent Claude Code sessions? (Each user gets their own `CLAUDE.local.md` with personal preferences, shared rules stay in `.claude/rules/`.)

5. **Audit result retention:** How long do we keep API audit results? Suggestion: 90 days in-app, exportable anytime, with the last 3 runs always available as `.md` files in the workspace.

---

*This document is a working draft for internal review. Share with Cowork for collaborative feedback and task generation.*
