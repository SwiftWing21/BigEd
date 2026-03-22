# Quality Flywheel — Design Spec

**Date:** 2026-03-22
**Version:** 0.1
**Status:** Draft

---

## Overview

A self-reinforcing quality system that audits project context files, grades their effectiveness, proposes improvements, and learns from human feedback. Each cycle compounds quality — the skill improves the files that make the skill work better.

Two interfaces:
| Component | Purpose | User |
|-----------|---------|------|
| `quality_flywheel` | Fleet skill — agents audit and improve their own context | BigEd agents |
| `.claude/skills/context-audit.md` | Claude Code plugin — any developer bootstraps quality context | Any Claude user |

**Prior art:** Context Development Lifecycle, rubric-based LLM evaluation, persistent context engineering. No existing tool packages the full loop as a reusable skill.

---

## Core Concept

```
Grade context files → Measure output quality → Find gaps
    ↓                                              ↓
Draft improvements ← Gap analysis reveals what's broken
    ↓
Human approves/rejects
    ↓
Apply approved changes → Re-grade to verify
    ↓
Reinforcement learns from approval/rejection patterns
    ↓
Next cycle produces better suggestions (flywheel spins)
```

---

## Skill: quality_flywheel

### Contract

```python
SKILL_NAME = "quality_flywheel"
DESCRIPTION = "Audit project context files, grade quality, propose improvements, learn from feedback"
COMPLEXITY = "complex"
REQUIRES_NETWORK = False
```

### Actions

| Action | Input | Output | LLM Cost |
|--------|-------|--------|----------|
| `audit` | optional `scope` | Graded report card (context + output quality) | 1-2 calls |
| `gaps` | none | Gap analysis: where context exists but doesn't work | 1 call |
| `draft` | `dimension` or auto | Proposed context file improvements | 1 call |
| `apply` | `draft_id` | Apply approved draft, re-grade | 1 call |
| `history` | optional `days` | Score trend over time | None |
| `calibrate` | none | Re-calibrate rubric weights from feedback history | 1 call |

---

## Grading Rubric (10 dimensions)

### Part A: Context Quality (grade the docs)

| Dimension | What it measures | How |
|-----------|-----------------|-----|
| **Completeness** | Does CLAUDE.md cover conventions, gotchas, structure, workflows? | Check for required sections vs template |
| **Consistency** | Do docs agree with each other and the actual code? | Cross-reference claims vs codebase |
| **Actionability** | Are instructions specific enough for an AI to follow? | Score vagueness: "write good code" (F) vs "use _retry_write for all DB ops" (A) |
| **Coverage** | What % of the codebase has relevant context? | Map context files to code directories |
| **Freshness** | Are docs stale vs recent commits? | Compare doc dates vs git log |

### Part B: Output Quality (grade what the AI produces)

| Dimension | What it measures | How |
|-----------|-----------------|-----|
| **Accuracy** | Does the AI follow stated conventions? | Sample recent task outputs, check compliance |
| **First-attempt rate** | How often does AI get it right without correction? | Analyze task DONE vs FAILED ratio per skill |
| **Regression rate** | Does quality degrade over sessions? | Compare IQ scores across time windows |
| **Context utilization** | Does the AI actually reference the docs? | Check if outputs mention patterns from CLAUDE.md |
| **Feedback incorporation** | Do corrections stick across sessions? | Check if rejected patterns recur |

### Scoring

Each dimension: **A** (excellent) through **F** (missing/broken)

| Grade | Score | Meaning |
|-------|-------|---------|
| A | 90-100 | Excellent — actively compounding quality |
| B | 75-89 | Good — minor gaps, not degrading |
| C | 60-74 | Adequate — noticeable gaps, some staleness |
| D | 40-59 | Poor — significant gaps, AI frequently ignores context |
| F | 0-39 | Missing or broken — no effective context |

**Overall score** = weighted average. Weights calibrated from feedback history.

Default weights:
- Completeness: 15%
- Consistency: 15%
- Actionability: 20% (most impactful on output quality)
- Coverage: 10%
- Freshness: 10%
- Accuracy: 10%
- First-attempt rate: 8%
- Regression rate: 5%
- Context utilization: 4%
- Feedback incorporation: 3%

---

## Gap Analysis

The gap between Part A and Part B reveals actionable insights:

| Context Grade | Output Grade | Diagnosis |
|--------------|-------------|-----------|
| A | A | Flywheel spinning — maintain |
| A | C | Context exists but isn't effective — needs rewording |
| C | A | AI compensating for gaps — context should catch up |
| F | F | No context, no quality — bootstrap needed |
| B | D declining | Regression — recent changes broke something |

---

## Draft Improvements

When the skill finds gaps, it generates concrete drafts:

### What it drafts:

1. **CLAUDE.md additions** — New sections for uncovered areas
2. **Rule files** — `.claude/rules/*.md` for specific patterns
3. **Gotcha entries** — Common mistakes from FAILED task analysis
4. **Convention updates** — Patterns that emerged from code but aren't documented
5. **Rubric adjustments** — Weight changes based on which dimensions matter most

### Draft format:

```markdown
## Draft #42 — Add error handling conventions
**Dimension:** Actionability (currently C, target B+)
**Confidence:** 78%
**Evidence:** 12 tasks in the last week had error handling rejected by reviewers

### Proposed addition to CLAUDE.md:

> ## Error Handling
> - Always use `try/except Exception` (never bare `except:`)
> - DB operations: wrap in `_retry_write()` with jittered backoff
> - HTTP calls: explicit `timeout` parameter, always
> - Never swallow exceptions silently — at minimum `log.warning()`

### Expected impact:
- First-attempt rate: +8% (based on similar corrections in feedback history)
- Actionability grade: C → B+
```

---

## HITL Checkpoints

| Checkpoint | When | User Action |
|------------|------|-------------|
| Draft review | After generating improvement | Approve / Reject / Edit |
| Apply confirmation | Before modifying CLAUDE.md or rules | "Apply this change?" |
| Re-grade review | After applying, showing before/after | "Score improved B→A. Keep?" |
| Calibration review | After weight adjustment | "Adjust actionability weight 20%→25%?" |

---

## Reinforcement Learning

Uses existing `reinforcement.py` infrastructure:

- **Approved drafts** → boost confidence for that pattern type
- **Rejected drafts** → reduce confidence, learn what not to suggest
- **Edited drafts** → learn the human's preferred phrasing style
- **Score improvements after apply** → validate the improvement was real

### Feedback storage:

```sql
-- Uses existing output_feedback table
-- output_path = "flywheel_draft_42"
-- verdict = "approved" / "rejected"
-- feedback_text = human's edit or rejection reason
```

### Evolution over time:

1. Early cycles: low confidence, many suggestions, human reviews all
2. Middle cycles: learned patterns, fewer suggestions, higher accuracy
3. Mature cycles: mostly maintenance — catches regressions, suggests updates when code changes diverge from context

---

## Score History & Regression Tracking

```sql
CREATE TABLE IF NOT EXISTS flywheel_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL,
    dimension TEXT NOT NULL,
    grade TEXT NOT NULL,
    score REAL NOT NULL,
    details_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
CREATE INDEX IF NOT EXISTS idx_flywheel_project ON flywheel_scores(project_path, created_at)
```

- `history` action returns score trend chart data
- Dashboard panel shows flywheel health (overall grade + trend)
- Alerts if score drops >1 letter grade between audits

---

## Claude Code Plugin

### Location

`.claude/skills/context-audit.md`

### Invocation

```
/context-audit              — Full audit of current project
/context-audit gaps         — Show gaps only
/context-audit bootstrap    — Generate starter CLAUDE.md for projects without one
```

### Behavior

1. Scan project for context files (CLAUDE.md, .claude/rules/, AGENTS.md, GEMINI.md)
2. Grade each dimension (simplified — no output quality measurement without fleet)
3. Show report card inline
4. Offer to draft improvements
5. On approval, write changes directly

### Bootstrap mode

For projects with NO context files:
1. Analyze the codebase (language, framework, structure, conventions)
2. Generate a starter CLAUDE.md with:
   - Project description
   - Key conventions detected from code
   - Common gotchas inferred from error patterns
   - Build/test commands detected from config files
3. Generate 2-3 rule files for the most impactful patterns

---

## File Layout

```
fleet/skills/quality_flywheel.py      # Fleet skill
fleet/skills/_flywheel_core.py        # Shared grading engine
.claude/skills/context-audit.md       # Claude Code plugin
knowledge/flywheel/                   # Audit reports + drafts
knowledge/flywheel/drafts/            # Proposed improvements
knowledge/flywheel/history/           # Score snapshots
```

---

## Dependencies

| Dependency | Status | Used For |
|------------|--------|----------|
| code_quality.py | Exists | Convention detection patterns |
| evaluate.py | Exists | Output grading patterns |
| intelligence.py | Exists | IQ score history for output quality |
| reinforcement.py | Exists | Feedback-driven learning |
| regression_detector.py | Exists | Score regression detection |
| data_access.py | Exists | DB queries for task/feedback history |

No new external dependencies. All analysis is local.

---

## Compliance

- REQUIRES_NETWORK = False (all analysis is local)
- Never modifies files without HITL approval
- Drafts stored as proposals, not auto-applied
- PHI filter applied if DITL enabled
- All suggestions logged to audit trail
- Score history queryable for SOC 2 evidence

---

## Success Criteria

1. `audit` produces graded report card in <30 seconds
2. `gaps` correctly identifies where context doesn't match output quality
3. `draft` improvements are approved >60% of the time (learning curve)
4. Approved drafts measurably improve the target dimension's score
5. Rejection rate decreases over time (reinforcement learning working)
6. Plugin `bootstrap` generates a usable CLAUDE.md for any project in <15 seconds
7. Score history shows upward trend over 10+ audit cycles
