# Two-Brain Audit System Design

**Date:** 2026-04-01
**Status:** Approved
**Approach:** B (core module + thin skill + blueprint)

## Overview

A dual-layer audit system that combines automated quantitative scoring (left brain) with manual qualitative grading + user feedback (right brain). The two halves share a common dimension framework and reconcile automatically, with divergences blocking smoke test green status until resolved.

Phase 1 implements this for BigEd. Phase 2 extracts the generic engine into a standalone repo (`two-brain-audit`).

---

## Phase 1: BigEd Integration

### Data Model

**`audit_scores` table:**

```sql
CREATE TABLE IF NOT EXISTS audit_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
    tier         TEXT    NOT NULL,  -- 'light' | 'medium' | 'daily' | 'weekly'
    dimension    TEXT    NOT NULL,  -- one of the 12 dimensions
    auto_score   REAL,             -- 0.0-1.0 normalized
    auto_detail  TEXT,             -- JSON: what was counted, what passed/failed
    manual_grade TEXT,             -- S/A+/A/A-/B+/B/B-/C+/C/D/F (snapshot from sidecar)
    divergence   INTEGER DEFAULT 0, -- 0=aligned, 1=flagged
    acknowledged INTEGER DEFAULT 0  -- 0=unresolved, 1=user dismissed
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_scores(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_dim ON audit_scores(dimension);
```

**`user_feedback` table:**

```sql
CREATE TABLE IF NOT EXISTS user_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now')),
    score      REAL    NOT NULL,  -- 0.0-1.0 (stars map: 0.2/0.4/0.6/0.8/1.0, slider: fine-grained)
    scope      TEXT    NOT NULL,  -- 'overall' | 'session'
    session_id TEXT,              -- null if 'overall'; uses dashboard browser session cookie
    text       TEXT,              -- free text, nullable
    inferred   TEXT,              -- JSON array of {dimension, confidence}
    actor      TEXT               -- user identity if available
);
CREATE INDEX IF NOT EXISTS idx_feedback_scope_ts ON user_feedback(scope, timestamp);
```

**Retention:** No automatic pruning. At ~4,400 rows/year (audit_scores) and moderate feedback volume, SQLite handles this indefinitely. Revisit if row count exceeds 100k.


**Grade-to-score mapping:**

| Grade | Score | Meaning |
|-------|-------|---------|
| S | 1.00 | Production-grade, zero known gaps |
| A+ | 0.95 | Exceptional, trivial gaps only |
| A | 0.90 | Excellent |
| A- | 0.85 | Minor issues |
| B+ | 0.80 | Good, some gaps tracked |
| B | 0.75 | Adequate |
| B- | 0.70 | Functional, needs attention |
| C+ | 0.65 | Notable gaps |
| C | 0.60 | Significant gaps |
| D | 0.50 | Below expectations |
| F | 0.30 | Broken or missing (0.0 if completely absent) |

Divergence threshold: `abs(auto_score - manual_numeric) > 0.15` AND `auto_confidence >= 0.5`.

### Manual Grade Sidecar

**`fleet/audit_baseline.json`:**

```json
{
  "version": "0.900.00b",
  "dimensions": {
    "architecture": {
      "grade": "A",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "dashboard refactor 4/5 phases done"
    },
    "security": {
      "grade": "A",
      "updated": "2026-03-31",
      "source": "oauth_review",
      "model": "claude-sonnet-4-6",
      "token_cost": 12400,
      "findings": ["JWT bypass fixed", "2 P2s remaining"],
      "confidence": 0.92
    },
    "usability_ux": {
      "grade": "B+",
      "updated": "2026-04-01",
      "source": "human",
      "notes": "Factorio FPM agent spinbox added",
      "user_feedback": {
        "avg_score": 0.76,
        "sample_size": 23,
        "last_7d_trend": -0.04,
        "recent_complaints": ["dark mode contrast", "graph load time"]
      }
    }
  }
}
```

Three source types: `human` (JSON edit), `oauth_review` (external model), `user_feedback` (aggregated from feedback table).

### Tiered Triggering

| Tier | Trigger | What it checks | Cost |
|------|---------|----------------|------|
| **Light** | Every smoke test | Counts: skills, endpoints, test pass rate, file existence | ~2s |
| **Medium** | On-demand (skill or dashboard button) | Light + health probes, dep check, git diff since last grade, compliance freshness | ~10s |
| **Daily** | Dr. Ders, 3:00 AM | Medium + reconciliation against manual grades, stale grade detection, ratchet check | ~20s |
| **Weekly** | Dr. Ders, Sunday 3:30 AM | Daily + GitHub API, semgrep, PyPI/Ollama version drift, federation peers, MCP probes | ~45s |

### Scoring Engine: 12 Dimensions

**Auto-confidence per dimension:**

```python
AUTO_CONFIDENCE = {
    "testing":           0.95,
    "module_plugin":     0.90,
    "documentation":     0.85,
    "performance":       0.85,
    "reliability":       0.85,
    "observability":     0.80,
    "architecture":      0.75,
    "code_quality":      0.75,
    "security":          0.60,
    "dynamic_abilities": 0.60,
    "data_hitl":         0.55,
    "usability_ux":      0.30,  # scales up with feedback volume
}
```

UX confidence scales with feedback: `min(0.75, 0.30 + (feedback_count / 100) * 0.45)`.

**Light tier (deterministic counts):**

| Dimension | Formula | Source |
|-----------|---------|--------|
| Testing | `passing_tests / total_tests` (pytest + smoke combined) | smoke_test.py results, pytest exit code |
| Module/Plugin | `registered_modules / expected_modules` | ModuleHub DB query |
| Documentation | `(total_refs - stale_refs) / total_refs` | doc_freshness skill output |
| Performance | `endpoints_responding_under_threshold / total_endpoints` | health_api.py latency data |

**Medium tier (adds probes):**

| Dimension | Formula | Source |
|-----------|---------|--------|
| Reliability | `(healthy_agents + breakers_closed) / (total_agents + total_breakers)` | health_monitor.py |
| Observability | SSE broadcaster alive + audit_log writable + /api/health returns 200 | health probes |
| Architecture | `files_under_loc_threshold / total_scored_files` | LOC scan on key files |
| Code Quality | Ruff clean + no raw sqlite3 + no bare excepts | ruff + grep |

**Daily tier (adds reconciliation):**

| Dimension | Formula | Source |
|-----------|---------|--------|
| Security | Ruff security rules + path traversal smoke + RBAC populated. Full picture via OAuth review | smoke_test + manual |
| Usability/UX | Pages load without error + user feedback aggregate | health probes + feedback table |
| Dynamic Abilities | ML router + experiment framework + billing tables exist with recent rows | DB query |
| Data/HITL | HF ingest sources reachable + HITL table has recent entries | DB + health probes |

### External Checks (Weekly Tier)

**Security scanning (semgrep):**

- Scans: `fleet/skills/*.py`, `fleet/*.py`, `BigEd/**/*.py`
- Rules: `p/python`, `p/owasp-top-ten`, `p/sql-injection`
- Score: `1.0 - (errors * 0.15 + warnings * 0.05)`, clamped to [0.0, 1.0]
- Feeds: security dimension
- Installed once, rules auto-update. No API key. Optional dependency (warns if missing).

**Dependency version drift:**

- Python packages: compare installed vs PyPI latest (`GET https://pypi.org/pypi/{package}/json`)
  - Major version behind = 0.0, minor = 0.5, patch behind = 1.0 per package
  - Score: average across tracked packages
- Ollama models: compare fleet.toml tags vs available (maintained list in audit_baseline.json)
- Timeout: 10s per PyPI call, 5s Ollama. Network failures = "unknown" not "stale"
- Feeds: reliability dimension

**GitHub + CI state:**

- Uses existing `git_suite.py` helpers + `GITHUB_TOKEN`
- CI status: latest Actions run passed = 1.0, failed = 0.0. Feeds: testing
- Open bugs: `1.0 - (open_bugs * 0.05)`, clamped. Feeds: code_quality
- Stale PRs (>14 days): `1.0 - (stale_prs * 0.1)`, clamped. Feeds: architecture
- Rate limit aware: skips if < 20 remaining

**Federation + MCP probes:**

- Federation: `reachable_peers / configured_peers`. Feeds: reliability
- MCP servers: `responsive_servers / configured_servers`. Feeds: module_plugin
- Ollama: `/api/tags` responds + >= 1 model loaded. Binary 1.0/0.0. Feeds: performance

**Weekly score merge with daily:**

```python
def merged_score(dimension):
    daily = latest_daily_score(dimension)
    weekly = latest_weekly_score(dimension)
    if weekly is None:
        return daily
    age_days = (now - weekly.timestamp).days
    weekly_weight = max(0.1, 0.4 - (age_days * 0.05))  # 0.4 fresh, decays to 0.1
    return daily * (1 - weekly_weight) + weekly * weekly_weight
```

### User Feedback System

**UI widget** (bottom of audit panel):

- Star rating (1-5, maps to 0.2/0.4/0.6/0.8/1.0) OR slider (1-100%, maps to 0.01-1.0)
- Both rendered; whichever user interacts with first populates the score
- Scope toggle: Overall (long-term UX dimension) vs Session (tagged with session_id)
- Free text box — no category selector

**LLM classification of free text:**

- Routed through local model (qwen3:4b) as a fleet task
- Input: raw feedback text
- Output: `[{dimension, confidence}, ...]` — one or more of the 12 dimensions
- No category dropdown, no user friction

**Feedback aggregation into sidecar:**

- `user_feedback` field added to dimension entries in `audit_baseline.json`
- Contains: avg_score, sample_size, last_7d_trend, recent_complaints
- Updated by daily tier reconciliation run

### Reconciliation Flow

```
Dr. Ders daily trigger (3:00 AM)
        |
        v
  Run daily tier scoring (all 12 dimensions)
        |
        v
  Load audit_baseline.json (manual grades + feedback)
        |
        v
  For each dimension:
  +-- Compare auto_score vs manual_numeric
  +-- Check auto_confidence threshold
  +-- If divergent + high confidence -> set divergence=1
  +-- Write row to audit_scores table
        |
        v
  Any divergence=1 AND acknowledged=0?
  +-- Yes -> SSE push: "Audit: {dimension} diverged (auto {X} vs manual {Y})"
  |          Smoke test audit check returns [WARN]
  +-- No  -> SSE push: "Audit: all dimensions aligned"
             Smoke test audit check returns [PASS]
```

**Three resolution paths:**

1. **Update manual grade** — edit `audit_baseline.json`. Divergence clears next run.
2. **Acknowledge** — POST `/api/audit/acknowledge/{dim}`. Sets `acknowledged=1`. Stops blocking but stays visible (dimmed). Resets if gap widens.
3. **OAuth review** — POST `/api/audit/oauth-review/{dim}`. Deep external model review. Updates sidecar with findings. Manual trigger only (token cost control).

**OAuth review mechanism:**

The OAuth review endpoint dispatches a fleet task that:
1. Gathers context for the target dimension — relevant source files, recent smoke test results, health data, git diff since last review
2. Sends a structured prompt to the external model (via `providers.py` HA chain — Claude preferred, Gemini fallback):
   ```
   You are auditing the "{dimension}" dimension of a software system.
   Grade on a scale of S/A+/A/A-/B+/B/B-/C+/C/D/F.
   Return JSON: {grade, confidence (0.0-1.0), findings: [strings], recommendations: [strings]}
   ```
3. Parses the JSON response. If parsing fails, returns error (does not update sidecar).
4. Writes to `audit_baseline.json`: sets `source: "oauth_review"`, `model`, `token_cost` (from API response usage), `findings`, `confidence`, `updated` timestamp
5. Token cost tracked via existing `usage` table in fleet.db (same path as normal API calls)

The review is a single API call per dimension — not a multi-turn conversation. Typical cost: 5k-15k tokens depending on dimension context size.

**Smoke test integration:**

New test in `smoke_test.py`: `test_audit_health()`. Checks for unresolved high-confidence divergences. Returns `WARN` (not `FAIL`) — overall result shows `51/51 passed (1 warning)` instead of clean green.

**Status symbols:**

| Symbol | Condition | Smoke test impact |
|--------|-----------|-------------------|
| check | `abs(auto - manual) <= 0.15` OR confidence < 0.5 | None |
| warning (yellow) | `abs(auto - manual) > 0.15` AND confidence >= 0.5 | `[WARN]` |
| warning (soft) | `abs(auto - manual) > 0.15` AND confidence < 0.5 | None ("review suggested") |
| fail (red) | Auto score <= 0.50 (D or below) regardless of manual | `[FAIL]` |

**Note:** D (0.50) is a failing grade. The `<=` boundary means D triggers `[FAIL]`, not just `[WARN]`. Only C (0.60) and above are non-failing.

### Dashboard Panel

Location: bottom of dashboard, full-width, collapsible (default expanded).

**Layout:**

```
+--------------------------------------------------------------+
|  SYSTEM AUDIT                                   Last: 2m ago |
|                                                              |
|  Dimension         Auto   Manual  Status    Confidence       |
|  -----------------------------------------------------------+
|  Testing           0.93   A (0.90)  ok      95%             |
|  Security          0.82   A (0.90)  warn    60%             |
|  Architecture      0.87   A (0.90)  ok      75%             |
|  ...                                                         |
|                                                              |
|  Overall: 0.86 (A-)  |  Manual: A  |  Divergences: 1 active |
|                                                              |
|  +-- User Feedback ----------------------------------------+ |
|  | stars/slider    [Overall v] [Session]                    | |
|  | [text box                                             ]  | |
|  |                                          [Submit]        | |
|  +----------------------------------------------------------+|
|                                                              |
|  [Run Medium]  [Trigger OAuth Review]  [Acknowledge All]     |
+--------------------------------------------------------------+
```

### REST Endpoints

New blueprint: `fleet/audit_blueprint.py`

```
GET  /api/audit/scores              -- latest score per dimension
GET  /api/audit/scores/history      -- time series (last 30 days default)
GET  /api/audit/divergences         -- active unacknowledged divergences
POST /api/audit/acknowledge/{dim}   -- mark divergence acknowledged
POST /api/audit/trigger/{tier}      -- run medium/daily/weekly on demand
POST /api/audit/oauth-review/{dim}  -- trigger OAuth review for dimension
GET  /api/audit/baseline            -- current manual grades from sidecar
POST /api/audit/feedback            -- submit user feedback
GET  /api/audit/feedback/summary    -- aggregated feedback per dimension
```

### File Layout

**New files:**

| File | Purpose | ~LOC |
|------|---------|------|
| `fleet/audit_scorer.py` | Core module: tier logic, scoring, reconciliation, DB writes | ~600 |
| `fleet/audit_blueprint.py` | REST endpoints (Flask blueprint) | ~200 |
| `fleet/skills/audit_score.py` | Thin skill wrapper for fleet task invocation | ~60 |
| `fleet/audit_baseline.json` | Manual grades sidecar | ~80 |
| `fleet/templates/components/_audit_panel.html` | Dashboard panel HTML + JS | ~150 |

**Modified files:**

| File | Change |
|------|--------|
| `fleet/db.py` | Add `audit_scores` + `user_feedback` tables |
| `fleet/smoke_test.py` | Add `test_audit_health()` |
| `fleet/hw_supervisor.py` | Add daily (3:00 AM) + weekly (Sunday 3:30 AM) triggers |
| `fleet/dashboard.py` | Register audit_blueprint, include panel |
| `fleet/dependency_check.py` | Add semgrep as optional dependency |

### Dr. Ders Scheduling

```python
AUDIT_SCHEDULE = {
    "daily":  {"hour": 3, "minute": 0},
    "weekly": {"weekday": 6, "hour": 3, "minute": 30},  # Sunday
}
```

---

## Phase 2: Standalone `two-brain-audit` Repo (Roadmap)

**Extract after 30+ days of BigEd production use.**

### Architecture

The generic engine separates into:

```
two-brain-audit/
  src/
    engine.py          -- DimensionRegistry, Scorer, Reconciler
    tiers.py           -- TierScheduler (light/medium/daily/weekly)
    sidecar.py         -- JSON sidecar read/write
    feedback.py        -- Feedback collection + LLM classification interface
    db.py              -- SQLite storage (audit_scores, user_feedback)
    dashboard/         -- Optional web panel (Flask blueprint, portable)
    exporters/         -- JSON, CSV, Markdown report generators
  presets/
    python_project.py  -- Preset dimensions for generic Python repos
    api_service.py     -- Preset dimensions for REST API services
    database.py        -- Preset dimensions for database auditing
    infrastructure.py  -- Preset dimensions for infra/DevOps
  integrations/
    github.py          -- GitHub API checks (CI, issues, PRs)
    semgrep.py         -- Security scanning
    pypi.py            -- Dependency version drift
    ollama.py          -- Model freshness
  examples/
    biged/             -- BigEd's 12 dimensions as a reference implementation
  README.md
  pyproject.toml
```

### Core API

```python
from two_brain_audit import AuditEngine, Dimension

engine = AuditEngine(db_path="audit.db", baseline_path="audit_baseline.json")

# Register dimensions with check functions
engine.register(Dimension(
    name="test_coverage",
    check=lambda: run_pytest_cov(),
    confidence=0.95,
    tier="light",
))

# Run a tier
results = engine.run_tier("daily")

# Get reconciliation status
divergences = engine.get_divergences()

# Record user feedback
engine.record_feedback(score=0.8, scope="overall", text="UI feels snappy")
```

### Suggested Targets / Use Cases

**1. Python project audit:**
- Dimensions: test coverage, type coverage (mypy), lint score (ruff), dep freshness (PyPI), doc coverage, security (semgrep), code complexity (radon), import hygiene
- Trigger: CI pipeline + weekly scheduled
- Value: replaces ad-hoc "are we maintaining quality?" discussions with numbers

**2. REST API service audit:**
- Dimensions: endpoint health, response time p95, error rate, auth coverage, schema validation (OpenAPI), rate limiting, CORS config, TLS cert expiry
- Trigger: continuous (light), hourly (medium), daily (full)
- Value: SLA compliance evidence generation

**3. Database audit:**
- Dimensions: schema completeness, index coverage, query performance (slow query log), backup freshness, replication lag, connection pool utilization, migration currency
- Trigger: daily + weekly
- Value: catches "database works fine until it doesn't" drift

**4. Infrastructure audit:**
- Dimensions: uptime, cert expiry, resource utilization, config drift (Terraform state), secret rotation age, DNS propagation, CDN cache hit rate
- Trigger: hourly (light), daily (full)
- Value: compliance evidence + early warning for infra rot

**5. ML pipeline audit:**
- Dimensions: model freshness, training data drift, inference latency, prediction accuracy (holdout), feature store currency, GPU utilization, experiment tracking completeness
- Trigger: daily + per-training-run
- Value: model decay detection before production impact

**6. Multi-service platform audit:**
- Dimensions: per-service health + cross-service contract tests + dependency graph currency + shared schema compatibility
- Trigger: per-deploy (light), daily (full), weekly (ecosystem)
- Value: microservice sprawl visibility

### Maintenance Model

**Self-sustaining checks:**
- Drift detection on the drift detector: if checker configs (semgrep rules, dependency list) haven't been updated in 6 months, flag for review
- Self-pruning: if a weekly check hasn't produced an actionable finding in 90 days, flag for review (either working perfectly or checking the wrong thing)
- Grade expiry: manual grades older than N days in a flagged dimension display as `--` (removed, not stale)

**Ratchet rule:**
- Ratchet targets stored in `audit_baseline.json` under a `ratchets` key: `{"testing": "A", "security": "A-", ...}`
- Default: no ratchet until explicitly set. Operator adds a ratchet by editing the sidecar.
- Once set, if the auto score drops below the ratchet grade's numeric value, smoke test returns `[WARN]` for that dimension
- To downgrade a ratchet: edit `audit_baseline.json`, lower the ratchet value, and add a `ratchet_note` explaining why. The daily reconciliation logs the change with the git commit SHA that introduced it (via `git log -1 --format=%H -- fleet/audit_baseline.json`)
- Ratchets are advisory in Phase 1 (WARN not FAIL) — promoted to FAIL in Phase 2 after tuning

**Near-zero risk stack (6 layers):**

| Layer | What it does | What it catches |
|-------|-------------|-----------------|
| 1. Functional test scoring | Score from tests, not file existence | Inflation |
| 2. Grade expiry | Manual grades expire after N days if flagged | Stale optimism |
| 3. Cross-validation | Divergence flagged when manual exceeds auto by > 0.15 on high-confidence dimensions | Optimistic prose |
| 4. Git diff detection | Flags unreviewed changes since last manual grade | Silent drift |
| 5. External scanner | Independent signal (semgrep, PyPI, GitHub) | Blind spots |
| 6. Ratchet rule | Score can't drop below ratchet target without explicit sidecar edit | Backsliding |

Residual risk: all 6 layers agree on a wrong answer simultaneously. Requires functional test to pass + manual reviewer to agree + git to show no changes + external scanner to miss it. Near-zero probability.

### README Outline (for standalone repo)

```markdown
# Two-Brain Audit

A dual-layer audit system that combines automated scoring (left brain) with
manual qualitative grading (right brain) and reconciles them automatically.

## Why Two Brains?

Neither automated scoring nor manual review alone is sufficient.
[Table: scenarios where each catches what the other misses]

## Quick Start

    pip install two-brain-audit
    two-brain-audit init          # creates audit_baseline.json + SQLite DB
    two-brain-audit register --preset python_project
    two-brain-audit run light     # first scan
    two-brain-audit dashboard     # optional web panel

## Concepts

- **Dimensions**: what you're measuring (test coverage, security, etc.)
- **Tiers**: how deep to check (light/medium/daily/weekly)
- **Sidecar**: JSON file for manual grades (human + OAuth model reviews)
- **Reconciliation**: auto vs manual comparison with divergence detection
- **Feedback**: user-facing rating widget with LLM-classified free text
- **Ratchet**: prevents silent score regression

## Presets

- `python_project` — 8 dimensions for any Python repo
- `api_service` — 8 dimensions for REST APIs
- `database` — 7 dimensions for database health
- `infrastructure` — 8 dimensions for infra/DevOps
- `ml_pipeline` — 7 dimensions for ML workflows
- Custom — register your own dimensions with check functions

## Integrations

GitHub, semgrep, PyPI, Ollama, federation peers, MCP servers.
Pluggable — add your own via the integration interface.

## Dashboard

Optional Flask blueprint. Drop into any existing Flask app or run standalone.

## Maintenance

Self-sustaining: drift detection on checkers, self-pruning stale checks,
grade expiry, ratchet rules. See MAINTENANCE.md for operational guide.

## Origin

Extracted from BigEd CC (github.com/[user]/Education) after 30+ days of
production use. Battle-tested on a 125-skill AI fleet with 12 audit dimensions.
```

---

## Estimated Effort

**Phase 1 (BigEd):**
- ~1,100 LOC across 5 new files + 5 modified files
- Est. tokens: ~25k (L)
- Dependencies: semgrep (optional, pip install)
- Timeline: single implementation session

**Phase 2 (standalone extraction):**
- Separate spec after 30+ days Phase 1 production
- Est. tokens: ~40k (L-XL)
- Includes: packaging, presets, CLI, docs, tests
