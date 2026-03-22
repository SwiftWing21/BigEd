# Open-Source Project Reviewer — Design Spec

**Date:** 2026-03-22
**Version:** 0.1
**Status:** Draft

---

## Overview

Two fleet skills + one Claude Code plugin for discovering, rating, and reviewing open-source projects.

| Component | Purpose | Token Cost |
|-----------|---------|------------|
| `oss_review` | Lightweight: pre-rate + single-agent review | Low |
| `oss_review_swarm` | Heavy: 4+1 agent swarm audit, regression tracking, watchlist | High |
| `.claude/skills/oss-review.md` | Interactive Claude Code plugin (uses oss_review core) | Low |

---

## Skill 1: oss_review (Lightweight)

### Contract

```python
SKILL_NAME = "oss_review"
DESCRIPTION = "Discover, pre-rate, and review open-source projects (single agent)"
COMPLEXITY = "medium"
REQUIRES_NETWORK = True
```

### Actions

| Action | Input | Output | LLM Cost |
|--------|-------|--------|----------|
| `discover` | `query` (topic string) | Ranked project list with pre-ratings | None (API only) |
| `pre_rate` | `url` (GitHub/registry URL) | Traffic light + metrics JSON | None (API only) |
| `review` | `url` + optional `focus` | Report card + findings markdown | 1 LLM call |
| `report` | `url` | Report card only (grades, no details) | 1 LLM call (small) |

### Pre-Rating (Zero LLM Cost)

Data sources:

| Source | Data | Endpoint |
|--------|------|----------|
| GitHub API | Stars, forks, open issues, last commit, contributors, license | `api.github.com/repos/{owner}/{repo}` |
| PyPI | Downloads/month, version count, latest release | `pypistats.org/api/packages/{name}/recent` |
| npm | Weekly downloads, version count | `api.npmjs.org/downloads/point/last-month/{name}` |
| OSV.dev | Known CVEs/advisories | `api.osv.dev/v1/query` (free, no key) |

Traffic light scoring:

| Grade | Criteria |
|-------|----------|
| GREEN | >1000 stars OR >10k downloads/month, 0 critical CVEs, commit <30 days |
| YELLOW | 100-1000 stars, <=2 non-critical CVEs, commit <90 days |
| RED | <100 stars AND <1k downloads, any critical CVE, commit >180 days |

### Single-Agent Review

One LLM call with a structured prompt covering:
- Architecture overview (from README + file tree)
- Code quality indicators (from sampled files)
- Security surface (dependencies, auth patterns, input handling)
- Maintenance health (issue response time, PR merge rate, docs quality)

Output: `knowledge/oss_reviews/{project}_review_{date}.md`

```markdown
# OSS Review: {project_name}
**URL:** {url}
**Pre-Rating:** GREEN | Date: 2026-03-22

## Report Card
| Dimension | Grade | Notes |
|-----------|-------|-------|
| Security | B+ | No critical CVEs, 2 medium dependency issues |
| Performance | A- | Clean async patterns, no blocking I/O |
| Architecture | B | Good modularity, test coverage at 72% |
| Compliance | A | MIT license, clean SBOM |
| **Overall** | **B+** | |

## Pre-Rating Metrics
- Stars: 4,521 | Forks: 312 | Downloads: 45k/month
- Open Issues: 23 | CVEs: 0 critical, 2 medium
- Last Commit: 3 days ago | Contributors: 18

## Key Findings
1. [MEDIUM] Dependency X has known CVE-2026-1234 (non-critical)
2. [LOW] No rate limiting on public API endpoints
3. [NOTE] Good error handling patterns, consistent across modules

## Known Issues (from project tracker)
- #142: Memory leak on large file processing (open, 2 weeks)
- #98: Auth bypass on admin endpoint (closed, fixed in v2.1)
```

### Payload Examples

```python
# Discover projects by topic
{"action": "discover", "query": "python async task queue", "limit": 5}

# Pre-rate a specific project
{"action": "pre_rate", "url": "https://github.com/user/repo"}

# Full review
{"action": "review", "url": "https://github.com/user/repo"}
{"action": "review", "url": "https://github.com/user/repo", "focus": "security"}
```

---

## Skill 2: oss_review_swarm (Heavy)

### Contract

```python
SKILL_NAME = "oss_review_swarm"
DESCRIPTION = "Multi-agent swarm audit of open-source projects with regression tracking"
COMPLEXITY = "complex"
REQUIRES_NETWORK = True
```

### Actions

All `oss_review` actions plus:

| Action | Input | Output | LLM Cost |
|--------|-------|--------|----------|
| `review` | `url` | Swarm audit (4+1 agents) | 5 LLM calls |
| `watchlist_add` | `url`, `frequency` | Confirm + schedule | None |
| `watchlist_remove` | `url` | Confirm | None |
| `compare` | `url` | Diff vs baseline | 1 LLM call |

### Swarm Architecture (Hybrid D)

4 specialized review agents run in parallel, 1 synthesis agent merges:

| Agent | Lens | Focus Areas |
|-------|------|-------------|
| Agent 1 | Security | CVEs, dependency vulns, injection risks, auth, secrets, SSRF |
| Agent 2 | Performance | Algorithmic complexity, memory patterns, I/O blocking, caching |
| Agent 3 | Architecture | Module coupling, test coverage, API surface, error handling, docs |
| Agent 4 | Compliance | License compatibility, SBOM, data handling, supply chain |

**Synthesis Agent:**
- Receives all 4 sets of findings
- Duplicate findings across lenses = high confidence (score boosted)
- Unique findings = included with single-lens confidence tag
- Disagreements = flagged for HITL review
- Produces final letter grades (weighted average)
- Confidence score per finding (0-100, threshold 70 for inclusion)

### HITL Checkpoints

| Checkpoint | When | User Action |
|------------|------|-------------|
| Pre-rate gate | After pre_rate, before full review | "Project rated YELLOW. Proceed?" |
| Review mode | User provides URL | "Quick review or swarm audit?" |
| Disagreement | Synthesis finds agent disagreement | "Agents disagree on X. Your call?" |
| Watchlist | Adding to watchlist | "Track {project} weekly?" |

### Regression Tracking

**Database table (fleet.db):**

```sql
CREATE TABLE IF NOT EXISTS oss_watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_url TEXT NOT NULL UNIQUE,
    project_name TEXT,
    last_review_at TEXT,
    last_grade TEXT,
    review_frequency TEXT DEFAULT 'weekly',
    baseline_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
```

**Compare action output:**

```markdown
## Regression Report: {project}
**Previous Review:** 2026-03-15 (Grade: B+)
**Current Review:** 2026-03-22 (Grade: B)

### Grade Changes
| Dimension | Previous | Current | Delta |
|-----------|----------|---------|-------|
| Security | B+ | B- | -2 (new CVE found) |
| Performance | A- | A- | = |

### New Issues (3)
1. [HIGH] CVE-2026-5678 in dependency Y (disclosed 2026-03-18)

### Fixed Issues (1)
1. [MEDIUM] Auth bypass — fixed in v2.2

### CVE Delta
- New: CVE-2026-5678 (HIGH)
- Resolved: CVE-2026-1234 (patched in v2.1.1)
```

**Scheduled re-reviews:**
- Uses existing `event_triggers.py` scheduled tasks system
- Frequency options: daily, weekly, monthly
- On re-review: auto-runs `compare` and stores new baseline
- Alerts via dashboard if grade drops

### Evolution

The skill evolves by:
- Learning which findings were approved/rejected via human feedback (reinforcement.py)
- Adjusting confidence thresholds based on false positive rate
- Tracking which lenses produce the most valuable findings per project type

---

## Claude Code Plugin

### Location

`.claude/skills/oss-review.md`

### Invocation

```
/oss-review https://github.com/user/repo
/oss-review search "python async task queue"
```

### Behavior

1. If URL provided: run `pre_rate` → show traffic light → ask "Proceed with review?"
2. If search query: run `discover` → show ranked list → user picks one → review
3. Review uses single-agent mode (Claude Code IS the agent)
4. Output: report card inline in conversation
5. Option to save to `knowledge/oss_reviews/`

### Plugin Format

```markdown
---
name: oss-review
description: Review open-source projects for quality, security, and maintainability
---

# OSS Project Reviewer

When the user asks to review an open-source project or search for projects:

1. If a URL is provided, fetch the repo metadata from GitHub API
2. Pre-rate the project (stars, downloads, CVEs, last commit)
3. Show the traffic light rating and ask if user wants full review
4. If yes, analyze: README, file structure, key source files, dependencies
5. Output a report card with letter grades + key findings
```

---

## File Layout

```
fleet/skills/oss_review.py           # Lightweight skill
fleet/skills/oss_review_swarm.py     # Heavy swarm skill
fleet/skills/_oss_core.py            # Shared core (pre-rate, GitHub API, registry)
.claude/skills/oss-review.md         # Claude Code plugin
knowledge/oss_reviews/               # Output directory
```

---

## Dependencies

| Dependency | Status | Used For |
|------------|--------|----------|
| web_search.py | Exists | Project discovery |
| browser_crawl.py | Exists | README/docs extraction |
| code_review.py | Exists | Review prompt patterns |
| security_review.py | Exists | Security check patterns |
| evaluate.py | Exists | Post-review grading |
| regression_detector.py | Exists | Regression patterns |
| event_triggers.py | Exists | Watchlist scheduling |
| reinforcement.py | Exists | Feedback-driven evolution |
| swarm_consensus.py | Exists | Multi-agent voting |

No new external dependencies required. All HTTP calls use stdlib urllib/httpx with explicit timeouts. SSRF protection applied to all URLs.

---

## Compliance

- REQUIRES_NETWORK = True (enforced by worker.py)
- All URLs validated against SSRF blocklist
- GitHub API rate limiting respected (60/hr unauthenticated, 5000/hr with token)
- OSV.dev queries are free and unlimited
- No credentials stored — uses existing ~/.secrets pattern
- Outputs go through PHI filter if DITL enabled
- All findings logged to audit trail

---

## Success Criteria

1. `oss_review pre_rate` returns traffic light in <3 seconds (API-only)
2. `oss_review review` produces report card in <60 seconds
3. `oss_review_swarm review` produces full audit in <5 minutes
4. Regression `compare` correctly identifies new/fixed issues
5. Watchlist scheduled re-reviews fire via event_triggers
6. Claude Code plugin works standalone (no fleet dependency)
