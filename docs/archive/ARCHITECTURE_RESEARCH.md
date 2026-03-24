# Architecture Research: Gemini Console Upgrade (Dev Note: Kept for reference docs/historical)

This document outlines research and a proposed architectural refactor for the Gemini console to improve automation, security, and integration.

## 1. Alternative Approaches

This section summarizes various approaches for automating and integrating AI model interactions within the development environment.

| Summary | Approach            | Launches IDE? | Fully Automated? | Best For                      |
| :------ | :------------------ | :-----------: | :--------------: | :---------------------------- |
|         | `code` CLI          |       ✅       |     Partial      | Opening files for human review|
|         | `claude -p headless`|       ❌       |        ✅        | Batch scripting               |
|         | Claude Code SDK     |       ❌       |        ✅        | Programmatic pipelines        |
|         | VS Code Tasks API   |       ✅       |        ✅        | IDE-integrated workflows      |

## 2. Proposed Architecture: VS Code Task Automation

Given the goal of creating a robust, automated, and IDE-integrated workflow for the Gemini console, and addressing the current sign-in/authentication challenges, the recommended approach is to leverage the **VS Code Tasks API**.

### Rationale

1.  **Integrated Experience**: The VS Code Tasks API allows for seamless integration into the developer's existing environment. This avoids context switching and allows for a smoother workflow. The Gemini console can be a VS Code task that developers can run directly from the command palette or via a keybinding.

2.  **Automation**: Tasks are fully automatable and can be chained together. This is ideal for multi-step processes like the ones involving Gemini and Claude. For example, a task could first call the Gemini API, then process the output, and then feed it into a headless Claude script.

3.  **Authentication Management**: The "signing in" issue can be robustly addressed. Instead of custom credential management, we can leverage VS Code's `SecretStorage` API within a small, dedicated extension that exposes commands to the tasks. This is the standard and secure way to store sensitive information like API keys within VS Code. The extension can securely store and retrieve API keys, which can then be passed to the scripts as environment variables.

4.  **Flexibility**: This approach combines the best of both worlds. We can have fully automated workflows (`"fully automated": true`) while also having the option to launch the IDE (`"launches IDE": true`) for inspection when needed. Tasks can run in the background, and their output can be displayed in the integrated terminal.

### Implementation Sketch

1.  **Create a simple VS Code extension**:
    *   This extension will have one primary function: to manage and expose API keys for Gemini and other services.
    *   It will use `vscode.SecretStorage` to securely store API keys.
    *   It will expose a command, e.g., `gemini.getApiKey`, that can be called from `tasks.json`.

2.  **Define `tasks.json`**:
    *   Create a `tasks.json` file in the `.vscode` directory of the project.
    *   Define a `gemini-console` task of type `shell`.
    *   The task will call the main script (e.g., a Python script).
    *   The API key will be passed as an environment variable, fetched using the new command.

    **Example `tasks.json` entry:**

    ```json
    {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "Run Gemini Console",
                "type": "shell",
                "command": "python ${workspaceFolder}/BigEd/launcher/launcher.py",
                "options": {
                    "env": {
                        "GEMINI_API_KEY": "${input:geminiApiKey}"
                    }
                }
            }
        ],
        "inputs": [
            {
                "id": "geminiApiKey",
                "type": "command",
                "command": "gemini.getApiKey"
            }
        ]
    }
    ```

3.  **Refactor Console Script**:
    *   The Gemini console script (`launcher.py` or similar) will be refactored to read the API key from the `GEMINI_API_KEY` environment variable. This removes any file-based or hardcoded key management and solves the sign-in issue in a secure and standard way.

This approach provides a scalable and secure foundation for the Gemini console and its integration with other services like the headless Claude solution.

# Architecture Research: Autonomous Local Model Fleet

> **Generated:** 2026-03-19 | **System:** BigEd CC v1.0 | **Hardware:** RTX 3080 Ti, 12GB VRAM
> **Scope:** Local model automation, task flow generation, self-healing cleanup, structured report outputs

---

## 1. Local Model Automation

### 1.1 VRAM-Aware Model Lifecycle

The fleet operates a **4-tier model cascade** managed by `hw_supervisor.py`, scaling models up/down based on real-time VRAM pressure:

| Tier | Model | Trigger | VRAM |
|------|-------|---------|------|
| Default | `qwen3:8b` | Normal operation | ~6.9GB |
| Mid | `qwen3:4b` | VRAM > 75% | ~3.5GB |
| Low | `qwen3:1.7b` | Sustained pressure | ~1.5GB |
| Critical | `qwen3:0.6b` | VRAM > 90% (emergency) | ~0.5GB |

**Thermal boundaries** drive transitions automatically:

| Parameter | Value | Effect |
|-----------|-------|--------|
| `gpu_max_sustained_c` | 75 | Workers pause GPU claims |
| `gpu_max_burst_c` | 78 | Hard ceiling, skip GPU tasks |
| `cooldown_target_c` | 72 | Resume after sustained cooldown (60s window) |
| `vram.emergency` | 90% | Immediate eviction to critical tier |
| `vram.high` | 75% | Downscale to mid tier |
| `vram.restore` | 60% | Restore default tier |

**Key automation functions:**

- `warmup_model(name)` — Sends empty prompt with `keep_alive=5m` to preload into VRAM
- `unload_all_models()` — Evicts all loaded models via `keep_alive=0` (pre-training safety)
- `set_local_model(target)` — Atomically updates `fleet.toml` config with regex substitution
- `evict_models_for_training(host)` — Pre-flight VRAM clearance before PyTorch allocation

**Hardware state broadcast** (`hw_state.json`, written every ~5s):

```json
{
  "status": "ok | throttling | transitioning",
  "model": "qwen3:8b",
  "thermal": { "gpu_temp_c": 68, "gpu_power_w": 120, "vram_used_mb": 4521 },
  "models_loaded": [{ "name": "qwen3:8b", "size_gb": 4.7, "device": "cuda" }],
  "conductor": "loaded | unloaded | warming"
}
```

### 1.2 Training Exclusivity

When `train.py` is detected (via `pgrep -f [t]rain\.py`):

1. All Ollama models evicted from GPU
2. Workers switched to CPU-only mode
3. Ollama continues serving on CPU: `CUDA_VISIBLE_DEVICES=-1 ollama serve &`
4. VRAM budget: 6GB reserved for training, remainder for fleet at tier_low
5. Exclusive lock in DB (`locks` table, 7200s timeout)

### 1.3 HA Provider Fallback

Three-provider cascade with automatic failover:

```
Primary (claude-sonnet-4-6) → Gemini → Local Ollama
```

- Offline mode: chain collapses to `["local"]` only
- Fallback completion tagged in UI: "done (fallback: local)"
- API throttle: 300ms min between requests, exponential backoff on 429s (4 retries)
- Batch API available for 50% cost reduction on non-real-time bulk work

---

## 2. Task Generation & Continuous Flow

### 2.1 Task State Machine

```
PENDING ─── claim ──→ RUNNING ─── pass ──→ REVIEW ─── approve ──→ DONE
   ↑                     │                    │
   │                     │ fail               │ reject (retry)
   │                     ↓                    ↓
   │                   FAILED              PENDING (with _review_critique)
   │                     │
   └── promote ── WAITING (dependencies)
                     │
                   WAITING_HUMAN (operator input needed)
```

### 2.2 Task Creation APIs

**Simple dispatch:**
```python
db.post_task("web_search", {"query": "local AI deployment"}, priority=5)
```

**DAG pipeline (sequential chain with auto-dependencies):**
```python
db.post_task_chain([
    {"type": "skill_draft", "payload": {...}},
    {"type": "skill_test",  "payload": {...}},
    {"type": "skill_evolve","payload": {...}},
], priority=7)
# task[1].depends_on=[task[0].id], task[2].depends_on=[task[1].id]
```

**Parent-child subtasks:**
```python
db.post_task("research", payload, parent_id=parent_task_id)
```

### 2.3 Atomic Claiming & Race Protection

Workers claim tasks via atomic UPDATE + verify pattern:

1. `SELECT ... WHERE status='PENDING' ORDER BY priority DESC LIMIT 1`
2. `UPDATE ... SET status='RUNNING', assigned_to=? WHERE id=? AND status='PENDING'`
3. Verify: `SELECT assigned_to WHERE id=?` — if mismatch, lost race, retry

SQLite pragmas ensure concurrent access safety:
- `journal_mode=WAL` (concurrent readers + single writer)
- `busy_timeout=30000` (30s internal retry)
- `_retry_write()` with jittered exponential backoff (8 retries)

### 2.4 Role-Based Affinity Routing

Each worker role has preferred skills (configured in `fleet.toml [affinity]`):

| Role | Primary Skills |
|------|---------------|
| researcher | web_search, summarize, unifi_manage, home_assistant, browser_crawl |
| coder | code_write, code_review, code_quality, benchmark |
| archivist | rag_store, rag_query, flashcard_generate |

Claim priority: affinity match first, then any highest-priority PENDING task.

### 2.5 Idle Evolution (Self-Improvement When Idle)

When no tasks are pending for 30s (6 polls at 5s):

1. `db.get_least_evolved_skill()` — finds skill oldest/never tested
2. Posts low-priority task (priority=1): `skill_test` or `skill_evolve`
3. Logs to `idle_runs` table for tracking
4. Budget-aware: respects daily USD limits per skill
5. Any real task immediately preempts idle work

**Idle curricula** (per-role TOML files in `fleet/idle_curricula/`):
- Structured self-improvement sequences loaded once per worker startup
- Round-robin rotation through skill inventory

### 2.6 Dependency Promotion & Cascade Failure

**On task completion:**
```python
_promote_waiting_tasks():
    for task WHERE status='WAITING':
        if ALL dependencies in DONE: → set PENDING
```

**On task failure:**
```python
_cascade_fail_dependents():
    for task WHERE failed_id IN depends_on:
        → set FAILED with propagated error
```

### 2.7 Adversarial Review Pipeline

High-stakes skills pass through evaluator-optimizer loop before finalization:

**HIGH_STAKES_SKILLS:** code_write, code_write_review, legal_draft, security_audit, security_apply, pen_test, skill_draft, skill_evolve, branch_manager, product_release

- Review provider: Claude API, Gemini, or local Ollama (with /think)
- Max 2 rounds (configurable), review failure auto-passes (don't block work)
- Critique appended to payload as `_review_critique` for worker context on retry

---

## 3. Self-Healing Cleanup

### 3.1 Watchdog Monitoring Cycles

| Cycle | Interval | Checks |
|-------|----------|--------|
| Light | 60s | Failure streaks, stuck reviews, recent task result scrubbing |
| Full | 10min | + knowledge file scan (recursive *.md/*.json/*.jsonl/*.txt) |

### 3.2 Failure Quarantine

**Detection:** 3+ consecutive failures by same agent → auto-quarantine

```python
get_failure_streaks(threshold=3):
    # Window of last threshold*20 failed tasks, grouped by agent
    # Returns: [{"agent": "coder-1", "fail_count": 5, "last_error": "..."}]

quarantine_agent(name, reason):
    # agents.status = 'QUARANTINED'
    # Worker pauses claims until manually cleared
```

**Recovery:** `diagnostics.clear_quarantine(name)` — operator action via CLI or dashboard.

### 3.3 Stuck Review Auto-Pass

Tasks in REVIEW status > 30 minutes are auto-completed:
```python
db.complete_task(t['id'], {"auto_passed": True, "reason": "Review timeout (>30min)"})
```

### 3.4 Stale Task Recovery

Tasks stuck in RUNNING > 15 minutes (worker presumed crashed):
- Supervisor detects via heartbeat gap
- Requeued to PENDING for re-claim by another worker
- 15s cooldown before worker respawn

### 3.5 DLP Secret Redaction

**Patterns scanned:**

| Provider | Pattern |
|----------|---------|
| Anthropic/OpenAI | `sk-[a-zA-Z0-9_-]{20,}` |
| Google | `AIza[a-zA-Z0-9_-]{30,}` |
| GitHub PAT | `ghp_[a-zA-Z0-9]{30,}` |
| GitHub OAuth | `gho_[a-zA-Z0-9]{30,}` |
| Slack | `xoxb-[a-zA-Z0-9-]{10,}` |
| AWS | `AKIA[A-Z0-9]{16}` |
| Tavily | `tvly-[a-zA-Z0-9_-]{15,}` |

**Additional vectors:**
- Base64-encoded secrets: decoded and pattern-matched
- Env var exact-match: variables containing KEY/TOKEN/SECRET/PAT/PASSWORD

**Scrubbing targets:** Last 50 completed task results + all `knowledge/` files recursively.

### 3.6 Zombie Process Cleanup

- Workers create process groups (`os.setpgrp()`) on startup
- Signal handlers (SIGTERM/SIGINT) kill entire process group
- Catches orphaned child processes (Playwright browsers, nmap scans)

### 3.7 Supervisor Process Respawn

```
Worker dies → 15s cooldown → respawn
                    ↑
        Supervisor monitors via poll()
```

---

## 4. Structured Report Outputs

### 4.1 Report Generation Pipeline

**Stability Reports** (`skills/stability_report.py`):

Input: `data/resolutions.jsonl` — JSONL resolution records
```json
{"component": "auth", "severity": "P1", "status": "shipped", "resolved_at": "2026-03-19T..."}
```

Analysis: Aggregate by component, severity, status, platform → Counter-based breakdown

Output: `knowledge/reports/stability_report_YYYYMMDD.md`
```markdown
# Stability Report — 2026-03-19
**Total resolutions:** 42 | **Shipped:** 35 | **Pending:** 5
## Top Failure Components (table)
## Severity Breakdown (table)
```

### 4.2 Report Storage Locations

| Category | Directory | Format |
|----------|-----------|--------|
| Stability reports | `knowledge/reports/` | Markdown, timestamped |
| Code reviews | `knowledge/code_reviews/` | Markdown per review |
| Quality analysis | `knowledge/quality/reviews/` | Markdown with metrics |
| Security audits | `knowledge/security/reviews/` | Markdown + advisory |
| Refactoring plans | `knowledge/refactors/` | Markdown blueprints |
| Marathon ML logs | `knowledge/marathon/` | JSONL per session |
| Debug reports | `data/reports/` | JSON, auto-generated on crash |
| Resolution tracking | `data/resolutions.jsonl` | JSONL, per-fix records |

### 4.3 Debug Report Auto-Generation

On unhandled exception in `app.mainloop()`:

```python
generate_debug_report():
    # Captures: platform, hardware, fleet state, _log_ring (last 200 entries),
    # error traceback. Sanitizes secrets. Writes to data/reports/debug_TIMESTAMP.json
```

### 4.4 Dashboard API for Reports

| Endpoint | Data |
|----------|------|
| `/api/status` | Fleet agents, task counts, Ollama status |
| `/api/activity` | Recent tasks (claimed, done, failed) |
| `/api/thermal` | GPU/CPU temps, VRAM, model tier |
| `/api/cost` | Usage summary by skill/model/agent |
| `/api/usage/delta` | Per-skill cost comparison between date ranges |
| `/api/usage/budgets` | Budget status with pct_used |
| `/api/resolutions` | Last 50 resolution records |
| `/api/alerts` | Watchdog alerts + DLP warnings |
| `/api/comms` | Per-channel message/note counts |
| `/api/fleet/marathon` | Training session status |

---

## 5. Data Access Layer (DAL) Architecture

### 5.1 Database Schema (fleet.db — SQLite + WAL)

```sql
agents    (name UNIQUE, role, status, current_task_id, last_heartbeat, pid)
tasks     (id, created_at, assigned_to, status, priority, type, payload_json,
           result_json, error, parent_id, depends_on, review_rounds)
messages  (id, from_agent, to_agent, created_at, read_at, body_json, channel)
notes     (id, channel, from_agent, created_at, body_json)
locks     (name PK, holder, acquired_at)
usage     (id, created_at, skill, model, input_tokens, output_tokens,
           cache_read_tokens, cache_create_tokens, cost_usd, task_id, agent)
idle_runs (id, created_at, agent, skill, result, cost_usd)
```

### 5.2 Extracted DAL Modules (Feature Isolation FI-1/2/3)

| Module | Source | Responsibility |
|--------|--------|---------------|
| `cost_tracking.py` | db.py | log_usage, get_usage_summary, get_usage_delta, check_budget |
| `idle_evolution.py` | db.py | log_idle_run, get_idle_stats, get_least_evolved_skill |
| `comms.py` | db.py | post_message, get_messages, broadcast, post_note, get_notes |
| `diagnostics.py` | db.py | quarantine_agent, clear_quarantine, get_failure_streaks, get_stuck_reviews |
| `providers.py` | _models.py | HA fallback cascade, PRICING dict, calculate_cost |
| `process_control.py` | dashboard.py | Flask Blueprint with /api/fleet/* endpoints |
| `marathon.py` | supervisor.py | is_training_running, checkpoint monitoring, VRAM eviction |

All extracted modules use **lazy imports** to break circular dependencies:
```python
def _get_conn():
    import db  # lazy import inside function
    return db.get_conn()
```

Re-exported via `db` module for backward compatibility.

### 5.3 Launcher DAL (tools.db)

`BigEd/launcher/data_access.py` — Thread-safe SQLite wrapper for the GUI application:
- `ensure_table(name, columns)` — idempotent DDL
- `insert(table, data)` / `query(table, where)` / `update(table, data, where)` / `delete(table, where)`
- Manages CRM, accounts, onboarding, customer modules

### 5.4 Communication Channels (Layered)

| Layer | Channel | Participants | Use |
|-------|---------|-------------|-----|
| 1 | `sup` | Supervisors only | Training state, model transitions, recovery notes |
| 2 | `agent` | Workers only | Skill discussion, peer reviews |
| 3 | `fleet` | All agents | Commands, announcements, broadcast |
| 4 | `pool` | Supervisor → workers | Pause/resume, scaling directives |

Messages are read-once (inbox model). Notes are append-only (persistent scratchpad).

---

## 6. Architectural Patterns Summary

### Pattern 1: Dual-Supervisor

| Supervisor | Loop | Responsibility |
|-----------|------|---------------|
| `supervisor.py` | 30s | Task distribution, worker lifecycle, idle evolution, training detection, stale task recovery |
| `hw_supervisor.py` | 5s | VRAM monitoring, thermal scaling, model tier transitions, Ollama keepalive |

### Pattern 2: Evaluator-Optimizer

```
Worker executes skill → Review gate (high-stakes?) → Adversarial review
    → PASS: complete task
    → FAIL: append critique, requeue for retry (max 2 rounds)
    → ERROR: auto-pass (don't block work on infra failure)
```

### Pattern 3: Event-Driven State File

`hw_state.json` serves as a lightweight IPC mechanism:
- Written by hw_supervisor (5s interval)
- Read by supervisor, workers, dashboard, launcher
- No locking needed (atomic write + read tolerance for stale data)

### Pattern 4: Budget-Aware Execution

```
Pre-execution:  check_budget(skill) → warn if exceeded (never blocks)
Post-execution: log_usage(skill, tokens, cost) → track per-skill attribution
Dashboard:      /api/usage/budgets → pct_used visualization
Regression:     /api/usage/regression → flag >20% token increase vs prior week
```

### Pattern 5: Graceful Degradation

- Offline mode: external APIs rejected, local Ollama works, Discord/OpenClaw skipped
- Air-gap mode: deny-by-default skill whitelist (14 approved), dashboard disabled, secrets not loaded
- Provider fallback: Claude → Gemini → Local (automatic, transparent)
- Review failure: auto-pass (infrastructure errors don't block productive work)
- DB write failure: jittered exponential backoff (8 retries, 30s busy_timeout)

---

## 7. Production Configuration Reference

| Parameter | Value | Source |
|-----------|-------|--------|
| Default model | qwen3:8b | fleet.toml [models] |
| Complex provider | claude-sonnet-4-6 | fleet.toml [models] |
| VRAM safe ceiling | 10GB | MACHINE_PROFILE.md |
| Sweet spot DEPTH | 6 (~26M params, 6.9GB) | autoresearch |
| Thermal poll | 5s | fleet.toml [thermal] |
| Supervisor cycle | 30s | supervisor.py |
| Watchdog light | 60s | _watchdog.py |
| Watchdog full | 600s | _watchdog.py |
| Stale task timeout | 900s (15min) | supervisor.py |
| Worker poll | 5s | worker.py |
| Idle threshold | 30s (6 polls) | worker.py |
| Keepalive ping | 240s | hw_supervisor.py |
| API throttle | 300ms min gap | providers.py |
| API retries | 4 (exponential backoff) | providers.py |
| Review max rounds | 2 | fleet.toml [review] |
| Quarantine threshold | 3 consecutive failures | _watchdog.py |
| Stuck review timeout | 30min | diagnostics.py |
| DB busy timeout | 30s | db.py |
| DB write retries | 8 (jittered backoff) | db.py |
| Training lock timeout | 7200s (2h) | fleet.toml [training] |
| Skill execution timeout | 600s (10min) | worker.py |
| Log ring buffer | 200 entries | launcher.py |

---

## 8. Security Posture Assessment

### 8.1 Authentication & Authorization

**Implemented:**
- API keys sourced from `~/.secrets` (sourced into env by supervisor.py)
- `BIGED_OWNER_KEY` env var gates owner module (`mod_owner_core.py`)
- Air-gap mode blocks secret loading entirely
- Role-based skill affinity for task claiming preference

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| No dashboard auth | CRITICAL | 31 endpoints accessible to any local process without bearer/HMAC |
| No RBAC | HIGH | Binary owner key only; no granular permissions across fleet ops |
| Plaintext secrets | HIGH | `~/.secrets` readable by any same-privilege process |
| No key rotation | MEDIUM | No mechanism to rotate API keys without manual edit |
| No API call attribution | MEDIUM | Dashboard endpoints don't log originating caller |

### 8.2 Network Security

**Implemented:**
- Dashboard binds `127.0.0.1:5555` (localhost only)
- Ollama binds `localhost:11434`
- `pen_test.py` validates localhost binding for both services
- WSL2 NAT detection warns on 172.x.x.x gateway
- High-risk port scanning: RDP, SMB, MySQL, PostgreSQL, Redis, MongoDB, Elasticsearch
- `network_hardening_enabled` config flag

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| Advisory only | HIGH | pen_test detects misconfigs but doesn't prevent them |
| No TLS | MEDIUM | Dashboard serves HTTP only (acceptable for localhost, risk if binding changes) |
| No firewall integration | MEDIUM | No iptables/nftables rules enforced programmatically |
| CDN dependency | LOW | `chart.js@4` loaded from jsdelivr CDN in dashboard HTML |

### 8.3 DLP & Secret Scanning

**Implemented:**
- 7 regex patterns: `sk-*`, `AIza*`, `ghp_*`, `gho_*`, `xoxb-*`, `AKIA*`, `tvly-*`
- Base64-encoded secret detection (decode + pattern match)
- PII scanning: email, SSN, credit card, US phone patterns
- Exact-value detection from loaded env vars
- Dual-pass: input-side scan (before LLM) + output-side scan (after completion)
- Knowledge file scrubbing: recursive `*.md/*.json/*.jsonl/*.txt` every 10min
- Automatic `[REDACTED]` replacement in task results and files

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| Late-stage input scan | MEDIUM | Scans before LLM dispatch but doesn't block malicious payloads |
| Limited file coverage | MEDIUM | `.db`, `.pyc`, binary formats excluded from scan |
| Only last 50 tasks | MEDIUM | Older completed tasks never re-scanned |
| No redaction audit log | LOW | DLP events in supervisor.log but not separately auditable |
| Base64 heuristic weak | LOW | 20+ char threshold has false positive/negative rate |

### 8.4 Sandbox & Process Isolation

**Implemented:**
- `sandbox_enabled` flag + `sandbox_skills` whitelist in fleet.toml
- Docker availability detection before skill dispatch
- Process group creation (`os.setpgrp()`) for child cleanup
- Signal handlers (SIGTERM/SIGINT) kill entire process group
- Air-gap deny-by-default whitelist (14 approved skills)
- Offline mode blocks `REQUIRES_NETWORK` skills (16 network-capable skills controlled)

**Air-gap approved skills (14):**
```
code_review, code_discuss, code_index, code_quality, summarize,
discuss, flashcard, analyze_results, rag_index, rag_query,
benchmark, ingest, security_review, security_audit
```

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| No sandbox execution | CRITICAL | Docker detected but skills run natively ("deferred to future") |
| No resource limits | HIGH | No CPU/memory caps per worker or skill |
| Thread timeout weak | MEDIUM | Daemonic threads can't be forcefully killed; may leak resources |
| No seccomp/AppArmor | MEDIUM | No kernel-level confinement |
| No privilege dropping | LOW | Runs as invoking user; no setuid/setgid |

### 8.5 Input Validation & Injection Protection

**Implemented:**
- Parameterized SQL queries throughout (`?` placeholders in all db.py operations)
- JSON schema validation on `post_task()` payloads
- Cost tracking whitelist for `group_by` parameter (only "skill", "model", "agent")
- Flask `jsonify()` for safe output serialization
- Input watchdog scans payloads before LLM dispatch

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| No request size limit | MEDIUM | Flask routes accept unlimited payload size |
| No rate limiting | MEDIUM | Dashboard endpoints have no throttle (API-level throttle exists on providers) |
| No CSRF protection | MEDIUM | State-changing POST endpoints unprotected |
| Path traversal risk | MEDIUM | Knowledge browsing uses `iterdir()` without universal traversal guards |
| No type validation | LOW | `request.args`/`request.json` parameters unchecked beyond group_by whitelist |

### 8.6 Review Pipeline Security

**Implemented:**
- Adversarial review for 10 HIGH_STAKES_SKILLS
- 3 review providers: Claude API, Gemini, local Ollama (with /think)
- Max 2 rounds to prevent infinite loops
- Critique appended to payload for retry context
- Review auto-pass on infrastructure failure (availability > correctness)

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| Disabled by default | HIGH | `[review] enabled = false` — opt-in, not mandatory |
| Auto-pass on failure | HIGH | Infra errors bypass review entirely |
| 30min timeout bypass | MEDIUM | Stuck reviews auto-complete via watchdog |
| No escalation | MEDIUM | FAIL verdict requeues but doesn't alert operator |
| Generic reviewer prompt | LOW | No skill-specific attack vector context |

### 8.7 Data at Rest

**Implemented:**
- SQLite WAL mode (concurrent access safety)
- `PRAGMA synchronous=NORMAL` (fsync balance)
- `PRAGMA foreign_keys=ON` (referential integrity)
- Backup script: `scripts/backup.sh` (fleet.db, rag.db, tools.db, knowledge/)

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| No encryption at rest | HIGH | fleet.db plaintext, readable with sqlite3 CLI |
| Permissive file perms | MEDIUM | knowledge/, logs/, hw_state.json world-readable (OS umask dependent) |
| No WAL checkpointing | LOW | No `wal_autocheckpoint` configured; WAL can grow |

### 8.8 Logging & Audit Trail

**Implemented:**
- Per-worker file logging (`fleet/logs/{role}.log`)
- Supervisor lifecycle logging
- Watchdog event logging (quarantines, DLP, stuck reviews)
- Cost tracking with timestamp/skill/model/tokens/cost
- Agent heartbeat tracking (5s interval)
- Debug report auto-generation on crash (`data/reports/debug_TIMESTAMP.json`)
- Resolution tracking (`data/resolutions.jsonl`)

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| No tamper evidence | MEDIUM | Plain text logs, no HMAC/signing |
| Fragmented audit trail | MEDIUM | Events split across log files, DB tables, JSONL files |
| No log rotation | LOW | Logs accumulate indefinitely |
| No API call logging | LOW | Dashboard doesn't log access (no auth layer to attribute) |

### 8.9 Dependency Security

**Implemented:**
- `security_audit` skill runs `pip check` (broken deps) + `pip-audit` (known CVEs)
- `dependency_scan_enabled` config flag
- Findings saved to `knowledge/security/pending/` for human review
- Human-in-the-loop: operator approves before `security_apply` executes

**Gaps:**

| Gap | Severity | Detail |
|-----|----------|--------|
| Advisory only | MEDIUM | Vulnerable packages can still execute |
| Transitive deps uncovered | LOW | Only direct dependencies scanned |
| pip-audit optional | LOW | Gracefully degrades if not installed |

---

## 9. Architecture Report Card (1st pass — superseded by Section 15)

### Overall Grade: B+ (1st pass) → B- (final, see Section 15)

A mature, well-structured autonomous fleet with strong operational patterns but security hardening gaps appropriate for a local-only v1.0 deployment.

### Category Grades

| Category | Grade | Rationale |
|----------|-------|-----------|
| **Local Model Automation** | A | 4-tier VRAM cascade, thermal monitoring, training exclusivity, HA fallback — production-grade |
| **Task Flow & Generation** | A | DAG dependencies, atomic claiming, idle evolution, adversarial review — comprehensive lifecycle |
| **Self-Healing & Cleanup** | A- | Watchdog cycles, quarantine, DLP, stale recovery, zombie cleanup — minor gaps in scan coverage |
| **Report & Observability** | A- | 31 API endpoints, SSE reactive UI, stability reports, debug auto-gen — lacks centralized audit |
| **Data Access Layer** | A | 7 extracted modules, lazy imports, WAL pragmas, retry logic — clean separation of concerns |
| **Architectural Patterns** | A | Dual-supervisor, evaluator-optimizer, event-driven IPC, budget-aware — well-composed |
| **Authentication & AuthZ** | D | No dashboard auth, no RBAC, plaintext secrets — critical gaps for any multi-user scenario |
| **Network Security** | B- | Localhost binding enforced, pen_test detection — but advisory-only, no TLS |
| **DLP & Secret Scanning** | B+ | 7 patterns + base64 + PII + env-match — good coverage, minor scan gaps |
| **Sandbox & Isolation** | D+ | Detection exists but execution deferred; no resource limits, no kernel confinement |
| **Input Validation** | B | Parameterized SQL, JSON validation — but no rate limiting, CSRF, or payload size limits |
| **Review Pipeline** | C+ | Comprehensive when enabled — but disabled by default, auto-pass on failure |
| **Data at Rest** | C | WAL + backup script — but no encryption, permissive permissions |
| **Logging & Audit** | B- | Per-worker + watchdog + cost tracking — but fragmented, unsigned, no rotation |
| **Dependency Security** | B- | pip-audit integration exists — advisory only, no blocking |

### Risk-Adjusted Summary

```
                    ┌─────────────────────────────────────┐
  STRENGTHS         │  Local Model Automation         [A] │
                    │  Task Flow & DAG Engine         [A] │
                    │  Self-Healing Watchdog          [A-]│
                    │  Observability & Reports        [A-]│
                    │  DAL Architecture               [A] │
                    │  DLP Secret Scanning            [B+]│
                    ├─────────────────────────────────────┤
  ACCEPTABLE        │  Network Security (localhost)   [B-]│
                    │  Input Validation               [B] │
                    │  Logging & Audit                [B-]│
                    │  Dependency Scanning            [B-]│
                    ├─────────────────────────────────────┤
  NEEDS WORK        │  Review Pipeline (disabled)     [C+]│
                    │  Data at Rest (unencrypted)     [C] │
                    ├─────────────────────────────────────┤
  CRITICAL GAPS     │  Authentication & AuthZ         [D] │
                    │  Sandbox Execution              [D+]│
                    └─────────────────────────────────────┘
```

### Priority Remediation Roadmap

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| P0 | Dashboard bearer token auth (Flask middleware) | 2h | Closes biggest single gap |
| P0 | Enable `[review] enabled = true` as default | 5min | Free safety improvement |
| P1 | Docker sandbox execution for code_write/pen_test | 1-2d | True isolation for dangerous skills |
| P1 | Per-worker resource limits (cgroups/cpulimit) | 4h | Prevents CPU/memory exhaustion |
| P2 | SQLCipher encryption for fleet.db | 4h | Data at rest protection |
| P2 | Flask-Limiter rate limiting on dashboard | 1h | DoS prevention |
| P2 | CSRF tokens on state-changing endpoints | 2h | Cross-site request protection |
| P3 | Centralized structured audit log (JSON + HMAC) | 1d | Tamper-evident event trail |
| P3 | Log rotation (logrotate or Python handler) | 1h | Prevents disk exhaustion |
| P3 | TLS for dashboard (self-signed cert) | 2h | Encrypted local transport |

### Context Note

These grades reflect a **local-only, single-operator deployment** on a personal workstation. Many "critical" gaps (no auth, no TLS, no encryption at rest) are **acceptable trade-offs** for this threat model. The grades shift significantly if the fleet moves to multi-user, remote access, or cloud deployment (roadmap 2.0+).

---

## 10. 3rd Pass Audit (Gemini Architecture Review)
*Conducted: 2026-03-19 | Verified: 2026-03-19 (Claude 2nd pass)*

This 3rd pass audit focuses on emergent architectural realities, identifying blind spots in the v1.0 documentation versus the actual codebase implementation.

**Verification score: 1 confirmed, 1 partial, 3 inaccurate (40% accuracy)**

### 10.1 The SSE Shift (Undocumented IPC Paradigm) — CONFIRMED

- **Finding:** Section 6 (Architectural Patterns) heavily emphasizes file-based IPC (`hw_state.json`, `STATUS.md`). However, `launcher.py` recently integrated `ui.sse_client` to consume `/api/stream`.
- **Impact:** The architecture has fundamentally shifted from Proactive Polling (Disk I/O) to Reactive Streaming (Network Sockets). The documentation lags behind this reality.
- **Action:** Formalize SSE as **Pattern 6: Reactive Streaming IPC**. Once fully stable, legacy file-polling loops (`parse_status()`) should be entirely deprecated to eliminate unnecessary SQLite WAL reads from the GUI.

**Verification:** `ui/sse_client.py` exists (40+ lines, SSEClient class). Launcher imports `create_tk_sse_bridge` at line 594. Dashboard implements `/api/stream` (lines 855-882) with queue-based SSE broadcast. `parse_status()` retained as fallback when SSE disconnects.

### 10.2 ML Pipeline Data Silos — PARTIALLY TRUE

- **Finding:** The `autoresearch` directory operates entirely outside the Fleet's Data Access Layer (DAL). It writes to `run.log` and `results.tsv`, while the fleet expects `knowledge/marathon/*.jsonl` (as noted in Section 4.2).
- **Impact:** Agents cannot natively query, index, or visualize the results of their own ML training loops using the standard SQLite/DAL tools. The data is effectively siloed in TSV format.
- **Action:** Introduce an `ml_bridge` skill or refactor `train_profile.py` to post results directly back to `fleet.db` via the REST API, eliminating the `.tsv` silo and enabling the dashboard to render ML progress natively.

**Verification:** Data silo is real — `autoresearch/train_profile.py` (line 188) writes `run.log` and `results.tsv` with no fleet.db or REST API integration. However, marathon_log skill outputs **markdown** files to `knowledge/marathon/{session_id}.md`, not JSONL as claimed. The format mismatch is minor but the silo finding is valid.

### 10.3 Cross-Platform Isolation Asymmetry — INACCURATE

- **Original claim:** WSL2 acts as a de-facto hardware-backed hypervisor boundary for worker processes.
- **Action:** Docker/containerization (Priority 1 Remediation) is not just a "nice to have" for isolation—it must be treated as a strict deployment prerequisite for safe native macOS/Linux execution.

**Verification: INACCURATE.** Workers spawn via native `subprocess.Popen(cmd, cwd=str(FLEET_DIR))` (supervisor.py:187) directly on the host OS. No WSL wrapping, no `wsl.exe` invocation, no container boundary. On this Windows machine, Python workers run as Windows processes, not inside WSL. The WSL isolation premise is architecturally inapplicable. The Docker remediation recommendation remains valid regardless — but the reasoning is wrong.

### 10.4 SQLite WAL "Thundering Herd" Risk — INACCURATE (ALREADY MITIGATED)

- **Original claim:** `_promote_waiting_tasks` and `_cascade_fail_dependents` execute broad UPDATE statements risking a thundering herd.

**Verification: INACCURATE.** Both functions use **targeted per-row UPDATEs** (`UPDATE tasks SET status=? WHERE id=?`), not broad sweeps. Additionally, v0.08.00 introduced an async `dag_queue` (db.py:240-262) that offloads promotion/cascade-fail out of the atomic `complete_task()` cycle. The fix this claim recommends was already implemented. The actual worker count is configurable (`max_workers=4` in fleet.toml, up to 13 roles), but contention is managed by `_retry_write()` with jittered exponential backoff (8 retries).

### 10.5 Incomplete "God Object" Decoupling — INACCURATE (ALREADY RESOLVED)

- **Original claim:** `launcher.py` contains hardcoded raw SQL `_db_init()` spanning >100 lines.

**Verification: INACCURATE.** `_db_init()` in launcher.py is **8 lines** that cleanly delegate to `data_access.py`:
```python
def _db_init(self):
    from data_access import DataAccess
    dal = DataAccess(self._db_path)
    dal.init_launcher_db()
```
Schema creation is centralized in `data_access.py:init_launcher_db()` (50 lines, using `ensure_table()` abstraction). No raw SQL in launcher.py for schema creation. The decoupling this claim recommends was already completed.

---

## 11. Exploitable Security Vulnerabilities

*4th pass — line-by-line vulnerability scan*

### 11.1 Critical (Exploitable Now)

| ID | Type | File:Line | Exploitability | Description |
|----|------|-----------|----------------|-------------|
| V1 | Path Traversal | code_review.py:50-59 | **Trivial** | `_pick_file()` accepts absolute paths without bounds checking. Payload `{"file": "/etc/shadow"}` reads any file the fleet user can access. |
| V2 | SSRF | home_assistant.py:92 | **Trivial** | `entity_id` flows directly into API URL: `f"{api}/states/{entity_id}"`. Payload `{"entity_id": "../../admin/secret"}` crafts arbitrary HA API requests. |
| V3 | SSRF | browser_crawl.py:54 | **Trivial** | User-supplied URL fetched without filtering. `{"url": "http://169.254.169.254/latest/meta-data/"}` hits cloud metadata. `{"url": "http://127.0.0.1:5555/api/status"}` enumerates internal fleet. |
| V4 | Command Injection | pen_test.py:108 | **Moderate** | nmap target from payload: `["nmap"] + args + [target]`. List form prevents shell injection, but nmap flags injectable: `{"target": "@/etc/passwd"}` reads host file as target list. |

**V1 Proof-of-Concept:**
```json
{"type": "code_review", "payload": {"file": "/root/.ssh/id_rsa"}}
```
Result: Private key content returned in task result_json, visible via `/api/activity`.

**V2 Proof-of-Concept:**
```json
{"type": "home_assistant", "payload": {"action": "get_entity", "entity_id": "../../../../../../etc/passwd"}}
```

**V3 Proof-of-Concept:**
```json
{"type": "browser_crawl", "payload": {"url": "http://127.0.0.1:5555/api/fleet/health"}}
```
Result: Internal fleet health data (agent PIDs, thermal limits, training status) returned as crawl output.

### 11.2 High

| ID | Type | File:Line | Exploitability | Description |
|----|------|-----------|----------------|-------------|
| V5 | Info Disclosure | dashboard.py:742+ | **Trivial** | Unfiltered `str(e)` in error responses exposes file paths, Python internals, FLEET_DIR location. |
| V6 | File Write | pen_test.py:348 | **Moderate** | Output filename derived from `payload.label` — path separators in label could write outside knowledge/. Mitigated by Path normalization on most OSes. |
| V7 | MQTT Wildcard | mqtt_inspect.py:52 | **Moderate** | User-controlled topic subscription: `client.subscribe(t)`. Payload `{"topics": ["#"]}` subscribes to ALL MQTT topics including sensitive device telemetry. |
| V8 | Dynamic Import | worker.py:174 | **Moderate** | `importlib.import_module(f"skills.{skill_name}")` — skill_name from task payload. Constrained to skills.* namespace but no whitelist validation. |

### 11.3 Medium

| ID | Type | File:Line | Description |
|----|------|-----------|-------------|
| V9 | Race Condition | db.py:177-217 | Task claiming SELECT+UPDATE not fully atomic. Double-execution possible under burst load. |
| V10 | OOM | dashboard.py:635-644 | `/api/knowledge` recursively lists all knowledge/ files. Millions of files could exhaust memory. |
| V11 | HA Service Injection | home_assistant.py:96-104 | Unvalidated `domain`/`service` params could invoke HA shell_command scripts if configured. |

### 11.4 Immediate Fixes Required

```python
# V1 Fix — code_review.py: Add path bounds checking
def _pick_file(requested: str) -> Path | None:
    if requested:
        p = Path(requested).resolve()
        if not str(p).startswith(str(FLEET_DIR)):
            return None  # BLOCK path traversal
        if p.exists():
            return p

# V2 Fix — home_assistant.py: Validate entity_id format
import re
entity_id = payload.get("entity_id", "")
if not re.match(r'^[a-z_][a-z0-9_]*\.[a-z0-9_]+$', entity_id):
    return {"error": "Invalid entity_id format"}

# V3 Fix — browser_crawl.py: Block internal URLs
from urllib.parse import urlparse
parsed = urlparse(url)
if parsed.hostname in ('127.0.0.1', 'localhost', '169.254.169.254', '::1'):
    return {"error": "Internal URLs blocked"}

# V4 Fix — pen_test.py: Validate nmap target
if target != "auto" and not re.match(r'^[\d\./:a-fA-F-]+$', target):
    return {"error": "Invalid nmap target format"}
```

---

## 12. Tech Debt & Bug Audit

### 12.1 Critical Data Integrity Bugs (P0)

| ID | File:Line | Bug | Impact |
|----|-----------|-----|--------|
| B1 | cost_tracking.py:110,146,165,184 | `_get_conn()` without context manager in 4 functions | SQLite connection leak; pool exhaustion under dashboard load |
| B2 | a2a.py:95,99 | Two separate `db.get_conn()` calls — commit on different connection than execute | A2A callback URLs silently fail to persist; external agents never notified |
| B3 | hw_supervisor.py:111 | `write_text(json.dumps(state))` — non-atomic write to hw_state.json | Process crash mid-write = truncated JSON; workers read corrupt state, ignore thermal data |
| B4 | hw_supervisor.py:133-136 | Regex `re.sub()` on fleet.toml (TECH_DEBT 4.6 claims fixed, but regex still present) | TOML corruption if regex fails to match or power fails mid-write |

**B2 Detail — Wrong Connection Bug:**
```python
db.get_conn().execute(...)  # Connection A
db.get_conn().commit()      # Connection B ← NOT the same connection!
```

**B3 Fix — Atomic write pattern:**
```python
import tempfile
with tempfile.NamedTemporaryFile(mode='w', dir=FLEET_DIR, suffix='.json', delete=False) as f:
    json.dump(state, f)
    tmp = f.name
Path(tmp).replace(HW_STATE_FILE)
```

### 12.2 High Severity Bugs (P1)

| ID | File:Line | Bug | Impact |
|----|-----------|-----|--------|
| B5 | dashboard.py:607,655 | DB connection leak in `/api/db_stats` and message count endpoints | Dashboard timeouts after ~100 refreshes |
| B6 | a2a.py:91-101 | `except Exception: pass` swallows all A2A callback errors | Silent failure; no audit trail |
| B7 | marathon.py:14 | `pgrep` doesn't exist on native Windows | Training never detected on Windows; VRAM not freed before training |
| B8 | hw_supervisor.py:162-170 | `warmup_model()` silent failure + no verification | Model transition reports success even when model not loaded; workers fail |
| B9 | worker.py:179-186 | Thread timeout doesn't kill daemon thread | Resource leak; zombie threads accumulate on long-running skill timeouts |
| B10 | supervisor.py:78-81 | Ollama stderr discarded (`subprocess.DEVNULL`) | Ollama startup failures undiagnosable |

### 12.3 Swallowed Exceptions Audit

**158 instances** of `except Exception: pass` or `except Exception` with minimal handling across the fleet. Critical locations:

| File | Count | Worst Case |
|------|-------|-----------|
| hw_supervisor.py | 23 | Silent thermal data loss; workers proceed with stale VRAM info |
| worker.py | 18 | Task inbox messages silently dropped; config_reload/pause commands missed |
| dashboard.py | 31 | API endpoints return stale data without error indication |
| supervisor.py | 15 | Ollama crash undetected; service restart failures invisible |
| skills/*.py | 71 | Skill execution failures appear as empty results, not errors |

### 12.4 Documentation Drift

| Document | Claims | Reality | Drift |
|----------|--------|---------|-------|
| CLAUDE.md | 31 endpoints | 39 endpoints (verified) | Stale |
| CLAUDE.md | 3492 lines launcher.py | ~3591 lines | Stale |
| CLAUDE.md | 55 skills | 63 .py files in skills/ | Stale |
| TECH_DEBT.md | All debt resolved (4.1-4.8) | B4: regex TOML still present (4.6 claims fixed) | Inaccurate |
| FLEET_BLUEPRINTS.md | Evaluator-Optimizer "not started" | v0.35 REVIEW status fully implemented | Stale |
| ARCHITECTURE_COMPARISON.md | Frozen at v0.01.02 | Codebase at v0.47/1.0 | Severely stale |
| FRAMEWORK_BLUEPRINT.md | Version history ends at v0.41 | v0.42-v0.48 + 1.0 undocumented | Stale |

### 12.5 Missing Test Coverage

| Critical Path | Test Status | Risk |
|--------------|-------------|------|
| Full task roundtrip (dispatch→claim→execute→complete) | Manual only | HIGH |
| Concurrent worker failure + recovery | None | HIGH |
| hw_supervisor model transitions under load | None | HIGH |
| A2A callback URL persistence | None | HIGH |
| Security: DLP redaction verification | None | CRITICAL |
| Security: path traversal blocked | None | CRITICAL |
| Security: SSRF blocked | None | CRITICAL |
| Platform-specific CI (Win/Linux/macOS) | Documented not active | MEDIUM |
| Ollama malformed response handling | None | MEDIUM |

---

## 13. Model / Supervisor / Agent Verification

### 13.1 Model Lifecycle — Verified with Gaps

| Function | Status | Finding |
|----------|--------|---------|
| Tier escalation (default→mid→low→critical) | CORRECT | Asymmetric: escalation immediate, de-escalation requires sustained cooldown |
| Tier de-escalation + hysteresis | CORRECT | `below_target_since` timer prevents oscillation (60s sustained cooldown) |
| `warmup_model()` | **FLAWED** | Silent failure (except: pass). No verification model appeared in /api/ps after warmup |
| `unload_all_models()` | CORRECT | Queries /api/ps then sends keep_alive=0 to each |
| `evict_models_for_training()` | CORRECT | Non-blocking daemon thread; graceful |
| Conductor model management | **GAP** | No backoff on conductor warmup failure. Failed every 60s without delay |
| Vision model rotation | CORRECT | hw_state.json `vision_request` flag triggers on-demand loading |
| `AmbientEstimator` | CORRECT | Tracks cooldown curves, estimates ambient from 10min window |
| Ollama response validation | **MISSING** | No HTTP status check; malformed JSON (500/503) causes silent failure |
| Model existence check | **MISSING** | Non-existent model returns 200 with error JSON; not detected |

### 13.2 Supervisor Lifecycle — Verified with Gaps

| Phase | Status | Finding |
|-------|--------|---------|
| Startup sequence (8 steps) | CORRECT | Sequential: DB init → secrets → Ollama → workers → services |
| Worker death detection (5s poll) | CORRECT | 20s max latency (5s detect + 15s cooldown) |
| Worker respawn | CORRECT | Per-role cooldown prevents thrashing |
| Training detection | **BROKEN ON WINDOWS** | `pgrep -f [t]rain\.py` — pgrep doesn't exist natively |
| Graceful shutdown | CORRECT | SIGTERM → 5s wait → SIGKILL → stop Ollama |
| Stale task recovery | **FRAGILE** | Workers don't heartbeat during skill execution; 10-min skills appear stale after 15min |
| hw_supervisor coordination | **RACE** | hw_supervisor started separately; may boot before Ollama is ready |
| Sup-channel reads | CORRECT | Every 30s, reads directives from other supervisors |

### 13.3 Agent Worker Lifecycle — Verified with Gaps

| Phase | Status | Finding |
|-------|--------|---------|
| Boot: register + heartbeat | CORRECT | Clean sequence with Ollama wait (30s timeout) |
| Task claim (atomic) | CORRECT | Optimistic verify + jittered backoff (8 retries, 30s busy_timeout) |
| Skill execution (thread + timeout) | **LEAK** | Daemon threads not killed on timeout; accumulate |
| Affinity routing | CORRECT | Config-driven; affinity match first, then any task |
| Quarantine check | CORRECT | Workers pause claims when quarantined |
| Review rejection → retry | CORRECT | Critique appended to payload; worker sees `_review_critique` |
| Idle evolution | CORRECT | Budget-aware, low-priority, self-assigned; prevents infinite loops |
| Transient failure backoff | CORRECT | Requeue + 10s sleep on timeout/429/5xx; permanent fail on other errors |
| Inbox processing | **FRAGILE** | Malformed messages silently ignored (except: body = {}) |

---

## 14. Industry Comparison Matrix

### 14.1 Pattern Comparison

| Pattern | BigEd CC | CrewAI | AutoGen | LangGraph | OpenAI Agents SDK | Google ADK |
|---------|----------|--------|---------|-----------|------------------|-----------|
| **Orchestration** | Dual-supervisor (process + hw) | Sequential dispatch | GroupChat | State machine DAG | Handoff routing | Agentic loop |
| **Hardware Awareness** | 4-tier VRAM cascade + thermal | None | None | None | None | None |
| **Communication** | 4-layer channels (sup/agent/fleet/pool) | Task output only | Message passing | State context | Tool output | Context passing |
| **Task DAG** | depends_on + conditional edges + cycle detection | Linear sequences | Via code | Graph-based | Handoff chains | Workflow graph |
| **Model Fallback** | 3-provider cascade (Claude→Gemini→Local) | Single provider | Via code | Single provider | Single provider | Single provider |
| **Cost Tracking** | Per-skill/model/agent + cache + budgets | Basic token count | In code | None | Token usage API | Per-operation |
| **Safety** | DLP + evaluator + watchdog + HitL + air-gap | Minimal | Code sandbox | Minimal | Guardrails | None |
| **Retry Logic** | Circuit breaker + jittered backoff | Via tools | Via code | Minimal | Built-in | Built-in |
| **Hot Reload** | Config reload via message channel | Static | Via agents | Static | Static | Static |
| **Desktop UI** | CustomTkinter + Flask dashboard | CLI only | CLI only | CLI only | API only | Web only |

### 14.2 Where BigEd Leads

1. **Hardware-Aware Scaling** — No industry framework dynamically adjusts model tiers based on VRAM/thermal pressure
2. **Dual-Supervisor Architecture** — K8s-style separation of process vs hardware concerns in single-machine fleet
3. **4-Layer Communication** — Multi-channel architecture with message versioning; industry uses flat message passing
4. **Cost-Aware Scheduling** — Per-skill budget enforcement with cache awareness; no framework integrates this
5. **Desktop GUI** — Only fleet with native desktop + web dashboard dual interface

### 14.3 Where BigEd Trails

1. **Formal DAG Visualization** — CrewAI and LangGraph offer graph rendering; BigEd has DAG endpoint but no UI
2. **Input-Side Guardrails** — NeMo Guardrails provides structured allow/deny/warn; BigEd logs but doesn't block
3. **Declarative Workflow DSL** — CrewAI YAML, LangGraph Python graph, Google ADK YAML; BigEd uses JSON payloads only
4. **A2A Protocol Standards** — Industry moving to OpenAPI-based agent-to-agent; BigEd uses custom comms.py
5. **Code Execution Sandbox** — AutoGen has Docker sandbox; BigEd's sandbox execution is deferred

### 14.4 Where BigEd is Aligned

- Task DAG dependencies (equivalent to LangGraph/Google ADK)
- Token budget tracking (equivalent to Anthropic SDK usage)
- Skill execution timeout (equivalent to AutoGen/LangGraph)
- Stale task recovery (most thorough implementation across frameworks)

---

## 15. Comprehensive Report Card (Post-Remediation Recheck)

### Overall Grade: B+

Upgraded from B- after remediation pass. 78% of security fixes verified, 80% of tech debt resolved. Three residual items prevent A-grade: web_crawl.py SSRF, warmup_model() silent failure, Ollama `--host` bind flag.

### Category Grades (Post-Remediation)

| Category | Pre-Fix | Post-Fix | Delta | Verified Fix |
|----------|---------|----------|-------|--------------|
| **Local Model Automation** | A- | A- | = | warmup_model() still silent failure; Ollama response validation still missing |
| **Task Flow & Generation** | A | A | = | DAG conditional edges, atomic claiming, cost forecasting — excellent |
| **Self-Healing & Cleanup** | B+ | A- | +1 | Stale task recovery fixed (heartbeat-aware); exception handlers documented |
| **Report & Observability** | A | A | = | 39 endpoints, SSE, alert monitor, DAG viz, HMAC audit log added |
| **Data Access Layer** | B+ | A | +1 | Connection leaks fixed (B1/B2/B5); atomic writes (B3); tomlkit (B4) |
| **Architectural Patterns** | A | A | = | Dual-supervisor, evaluator-optimizer, event-driven IPC, budget-aware |
| **Security: Vulnerabilities** | F | C+ | +5 | 8/11 vulns fixed; web_crawl SSRF + pen_test label + MQTT return type remain |
| **Security: Auth & AuthZ** | D | B- | +3 | Dashboard bearer auth added; review enabled; RBAC still missing |
| **Security: Network** | C | C+ | +1 | Pen test validates binding; Ollama `--host` flag still not passed |
| **Security: DLP** | C | B+ | +3 | Blocking on injection; expanded patterns (14); expanded extensions (12 types) |
| **Security: Sandbox** | D | B | +4 | Docker sandbox IMPLEMENTED with --network=none, --memory=512m, --cpus=1 |
| **Security: Input Validation** | D+ | B+ | +4 | Path traversal fixed; skill whitelist; prompt injection blocked; SSRF blocked (browser_crawl) |
| **Security: Review Pipeline** | C- | B- | +2 | Enabled by default; confidence threshold NOT yet enforced |
| **Security: Data at Rest** | C | C | = | No SQLCipher; WAL + backup unchanged |
| **Logging & Audit** | B- | A- | +3 | HMAC-signed audit.jsonl; 365-day retention; rotation at 50MB |
| **Tech Debt** | B- | B+ | +2 | 8/10 bugs fixed; warmup (B8) + Ollama stderr (B10) remain |
| **Test Coverage** | C | B | +2 | Security tests added (path traversal, SSRF, DLP in smoke_test.py) |
| **Documentation** | C+ | C+ | = | CLAUDE.md partially updated (66 skills, 39+ endpoints) |
| **Cross-Platform** | B- | B | +1 | pgrep fixed (psutil fallback); hardcoded /mnt paths still present |
| **Industry Alignment** | B+ | A- | +1 | Docker sandbox + circuit breaker + DAG viz close industry gaps |

### Composite Security Grade: B (was D+)

```
  EXPLOITABLE VULNS    [C+] ←── 8/11 fixed; 3 residual (1 high, 2 medium)
  AUTH & AUTHZ         [B-] ←── Dashboard bearer auth + review enabled
  INPUT VALIDATION     [B+] ←── Path traversal + skill whitelist + injection blocking
  SANDBOX              [B]  ←── Docker sandbox with resource limits LIVE
  DLP SCANNING         [B+] ←── Blocking + 14 patterns + 12 file extensions
  NETWORK              [C+] ←── Ollama --host flag still missing
  REVIEW PIPELINE      [B-] ←── Enabled; confidence threshold not enforced
  DATA AT REST         [C]  ←── No SQLCipher
  ─────────────────────────
  COMPOSITE            [B]  ←── Target: A+ (3 items remaining for A)
```

### Risk-Adjusted Summary (Post-Remediation)

```
                    ┌──────────────────────────────────────────┐
  EXCELLENT         │  Architectural Patterns             [A]  │
                    │  Task Flow & DAG Engine             [A]  │
                    │  Observability & Reports (39 ep)    [A]  │
                    │  DAL Architecture                   [A]  │
                    │  Industry Alignment                [A-]  │
                    │  Logging & Audit (HMAC signed)     [A-]  │
                    │  Self-Healing Watchdog             [A-]  │
                    ├──────────────────────────────────────────┤
  GOOD              │  Local Model Automation            [A-]  │
                    │  Input Validation (fixed)          [B+]  │
                    │  DLP Scanning (blocking)           [B+]  │
                    │  Tech Debt                         [B+]  │
                    │  Sandbox (Docker live)             [B]   │
                    │  Cross-Platform (psutil)           [B]   │
                    │  Test Coverage (security tests)    [B]   │
                    ├──────────────────────────────────────────┤
  ACCEPTABLE        │  Auth & AuthZ (bearer token)      [B-]  │
                    │  Review Pipeline (enabled)        [B-]  │
                    │  Exploitable Vulns (3 residual)   [C+]  │
                    │  Network Security                 [C+]  │
                    │  Documentation                    [C+]  │
                    ├──────────────────────────────────────────┤
  NEEDS WORK        │  Data at Rest (unencrypted)       [C]   │
                    └──────────────────────────────────────────┘
```

---

## 16. Remediation Roadmap to A+ (Updated Post-Recheck)

### Phase 1: Emergency — VERIFIED COMPLETE (10/12)

| # | Action | Status | Evidence |
|---|--------|--------|----------|
| 1 | Path bounds checking in code_review.py | DONE | `startswith(base_dir.resolve())` bounds check |
| 2 | SSRF blocklist in browser_crawl.py | DONE | `_BLOCKED_HOSTS` + private network check |
| 2b | SSRF blocklist in web_crawl.py | **NOT DONE** | No `_check_ssrf()` — still exploitable |
| 3 | Entity ID regex validation in home_assistant.py | DONE | Strict `^[a-z_][a-z0-9_]*\.[a-z0-9_]+$` |
| 4 | nmap target format validation in pen_test.py | DONE | `^[\d\./:a-fA-F\-]+$` regex |
| 5 | Sanitize error responses in dashboard.py | DONE | `_safe_error()` strips paths |
| 6 | Make input DLP scan blocking | DONE | `db.fail_task()` on injection; `continue` |
| 7 | Pass `--host 127.0.0.1` to `ollama serve` | **NOT DONE** | supervisor.py:79 still no flag |
| 8 | Enable `[review] enabled = true` | DONE | fleet.toml confirmed |
| 9 | Dashboard bearer token auth middleware | DONE | `_check_auth()` on all /api/* |
| 10 | Context managers on all `get_conn()` calls | DONE | cost_tracking, a2a, dashboard fixed |
| 11 | Atomic write for hw_state.json | DONE | tempfile + `os.replace()` |
| 12 | Review confidence threshold | **PARTIAL** | Confidence computed but not used to reject |

### Phase 2: Hardening — VERIFIED COMPLETE (11/13)

| # | Action | Status | Evidence |
|---|--------|--------|----------|
| 13 | Expand secret patterns | DONE | 14 patterns (Azure, GCP, private keys, DB URIs) |
| 14 | Expand knowledge scrub extensions | DONE | 12 types (.yaml, .sh, .py, .log, .env, .cfg, .ini) |
| 15 | Multiline-aware redaction | PARTIAL | Pattern-based, not DOTALL-safe |
| 16 | MQTT topic validation | PARTIAL | Logic correct but `json.dumps()` return type bug |
| 17 | Skill name whitelist | DONE | `_is_valid_skill()` checks skills/ directory |
| 18 | Rate limiting on dashboard | DONE | Custom: 60 req/min per endpoint |
| 19 | CSRF tokens on POST endpoints | DONE | Single-use tokens, API clients exempted |
| 20 | Replace regex TOML with tomlkit | DONE | `tomlkit.parse()` + atomic write |
| 21 | Windows training detection | DONE | psutil cross-platform + pgrep fallback |
| 22 | Warmup model verification | **NOT DONE** | No /api/ps check after warmup |
| 23 | Ollama response validation | **NOT DONE** | No HTTP status or error field check |
| 24 | Docker sandbox with resource limits | DONE | `--network=none --memory=512m --cpus=1` |
| 25 | SQLCipher encryption | NOT DONE | Standard sqlite3, no encryption |

### Phase 3: Excellence — VERIFIED COMPLETE (7/9)

| # | Action | Status | Evidence |
|---|--------|--------|----------|
| 26 | Docker sandbox execution | DONE | `_run_in_docker()` with fallback to native |
| 27 | Network firewall enforcement | NOT DONE | No iptables rules |
| 28 | Centralized audit log (HMAC) | DONE | `audit_log.py` with HMAC signing + rotation |
| 29 | TLS for dashboard | NOT DONE | No SSL context |
| 30 | Security test suite | DONE | smoke_test.py: path traversal + SSRF tests |
| 31 | Log rotation | DONE | audit_log.py: 50MB max, 365-day retention |
| 32 | Replace bare except handlers | PARTIAL | Documented with comments; not all specific |
| 33 | Integration tests | PARTIAL | Security tests added; full roundtrip not automated |
| 34 | Circuit breaker for Ollama | DONE | providers.py: full implementation |

### Phase 4: A+ Polish — Remaining Items

| # | Action | Effort | Priority |
|---|--------|--------|----------|
| R1 | SSRF blocklist in web_crawl.py (copy from browser_crawl.py) | 15min | **P0** |
| R2 | Pass `--host 127.0.0.1` to `ollama serve` in supervisor.py | 5min | **P0** |
| R3 | Sanitize pen_test.py `label` parameter (sanitize_filename) | 10min | **P0** |
| R4 | Fix mqtt_inspect.py return type (dict not json.dumps string) | 5min | P1 |
| R5 | Add confidence threshold to worker.py review verdict | 30min | P1 |
| R6 | warmup_model() — verify via /api/ps + retry on failure | 1h | P1 |
| R7 | get_loaded_models() — check HTTP status + error field | 30min | P1 |
| R8 | Conductor warmup exponential backoff | 1h | P2 |
| R9 | SQLCipher encryption for fleet.db | 4h | P2 |
| R10 | TLS for dashboard (self-signed cert) | 2h | P2 |
| R11 | ThreadPoolExecutor for skill timeout (replace daemon threads) | 4h | P2 |
| R12 | Ollama stderr capture + logging in supervisor.py | 30min | P2 |
| R13 | Data retention policy in fleet.toml `[retention]` section | 30min | P2 |
| R14 | Full documentation update (counts, line numbers) | 2h | P3 |

**3 items (R1-R3) are P0 at ~30min total effort. Completing them moves Security to A-.**

### Grade Projection (Updated)

| Phase | Security | Overall | Status |
|-------|----------|---------|--------|
| Pre-remediation | D+ | B- | Complete |
| Phase 1-3 (implemented) | B | B+ | **Current** |
| + R1-R3 (30min) | A- | A- | Next |
| + R4-R7 (2.5h) | A | A- | Sprint |
| + R8-R14 (14h) | A+ | A | Target |

**Distance to A+: ~17 hours of focused work.** R1-R3 (30 minutes) achieves the single biggest grade jump.

---

## 17. Future Architectural Considerations & Blind Spots (Gemini Analysis)

While the current architecture is robust for a v1.0 single-machine deployment, this analysis identifies several blind spots and future architectural frontiers that must be considered for the v2.0+ multi-fleet and SaaS roadmaps.

### 17.1 Distributed Systems & Federation (v2.0+)
- **The Gap:** The current architecture relies on a single SQLite database for state management. This is a single point of failure and does not scale beyond one machine. The v2.0 "Multi-Fleet" goal requires a fundamental shift.
- **Architectural Path:**
  - **State Management:** Transition from SQLite to a distributed key-value store (like etcd or Consul) or a database designed for clustering (like CockroachDB/TiDB) for managing fleet state, locks, and tasks.
  - **Communication:** Replace direct HTTP calls and the local SSE bus with a proper message broker (e.g., RabbitMQ, NATS, or Kafka) to handle inter-fleet and inter-agent communication reliably across network boundaries.
  - **Consensus:** For leader election (e.g., a single `planner` for a federated fleet), a consensus algorithm like Raft will be necessary.

### 17.2 Data Provenance & Lineage
- **The Gap:** An agent can generate a report, but it's difficult to trace that report back through the entire DAG of tasks, sub-tasks, web searches, and RAG queries that produced it. This "explainability" is critical for debugging and auditing autonomous systems.
- **Architectural Path:**
  - **Trace IDs:** Implement a universal `trace_id` that is generated for every top-level task and propagated down through all child tasks, API calls, and database records.
  - **Knowledge Graph:** Instead of storing knowledge as flat markdown files, transition to a graph database (like Neo4j or ArangoDB). Each piece of information (a summary, a code block, a fact) becomes a node, linked by edges representing the agent and task that created it (e.g., `(summary)-[:GENERATED_BY {task_id:123}]->(researcher)`).

### 17.3 Economic Modeling & Task Valuation
- **The Gap:** The fleet operates on a cost-budgeting model (CT-4) but lacks an economic one. It doesn't have a way to value its own compute time or to decide if a task's potential ROI is worth the token cost.
- **Architectural Path:**
  - **Task Bidding:** Before claiming a task, agents could "bid" on it, estimating the token cost and time required. The supervisor could then award the task to the most efficient agent.
  - **Value Function:** The `planner` agent needs a "value function" skill (`estimate_task_value`) that uses an LLM to assign a qualitative or quantitative business value to a proposed task, allowing for true cost-benefit analysis before dispatch.

### 17.4 Advanced AI Safety & Ethics
- **The Gap:** The current safety model is focused on technical security (sandboxing, DLP). It lacks a framework for ethical or philosophical guardrails. What prevents the fleet from autonomously drafting a malicious social media campaign or generating harmful content, even if it's technically "correct"?
- **Architectural Path:**
  - **Constitutional AI:** Integrate a "constitution" (a set of principles, similar to Anthropic's model) into the `_review.py` skill. The adversarial reviewer must check not only for correctness but also for alignment with the fleet's ethical principles.
  - **Red Teaming Curriculum:** Add a dedicated `red_teaming` skill and idle curriculum where a specialized agent actively tries to find ways to make other agents violate the constitution, surfacing ethical blind spots before they become real-world problems.

---

## 18. The Path to "S-Tier" (Post-1.0 / SaaS Scale)

Achieving an **A+** grade means the system is secure, functional, well-tested, and documented for its current threat model. Elevating the architecture to the mythical **"S-Tier" (State of the Art)** requires transitioning from "secure by configuration" to "provably secure by design."

### 18.1 Cryptographic Task Provenance (Zero-Trust)
- **Current State:** Agents inherently trust any row present in the SQLite `tasks` table. If an attacker (or a hallucinating agent with SQL access) injects a malicious payload into the DB, a worker will blindly execute it.
- **S-Tier Standard:** E2E Cryptographic Signatures. Every task dispatched by the `planner` or GUI is signed (e.g., Ed25519) using an ephemeral fleet session key. Workers mathematically verify the signature before claiming a task, ensuring zero unverified code execution even if the database layer is compromised.

### 18.2 Formal DAG Verification
- **Current State:** Deadlocks and infinite cycles are prevented by basic runtime checks and max retry rounds.
- **S-Tier Standard:** Deterministic Graph Analysis. The DAG engine utilizes a formal acyclic verification pass *before* committing a multi-step task chain to the database. The system mathematically proves a task chain can reach terminal states based on dependency types.

### 18.3 Hardware-Level MicroVM Isolation
- **Current State:** Docker (planned A+ baseline) utilizes namespaces and cgroups, which still share the host kernel (vulnerable to kernel privilege escalation).
- **S-Tier Standard:** Firecracker MicroVMs or WebAssembly (Wasm). Untrusted code (`code_write`, `browser_crawl` JS rendering) runs inside hypervisor-backed micro-virtual machines that boot in <10ms, completely severing the shared kernel attack vector.

### 18.4 Zero-Downtime Hot-Reloading
- **Current State:** Upgrading the orchestration layer or modifying schemas requires a graceful stop, a `pkill` sweep, and a fleet restart.
- **S-Tier Standard:** State-detachment. The `supervisor.py` logic can be hot-swapped while workers are mid-task. The internal state machine serializes into the SSE stream, replaces its own binary, and resumes orchestration without dropping a single active SQLite lock.
