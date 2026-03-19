# BigEd CC — Audit Tracker

> **Purpose:** Ongoing audit log for Sonnet to maintain S-tier or near-S-tier standards across all dimensions.
> Run at each milestone (`0.XX.00`) and major patch (`0.XX.YY`). Grade each dimension, log findings, track resolution.
> **Roadmap ref:** `ROADMAP.md` | **GitHub:** https://github.com/users/SwiftWing21/projects/2/views/1

---

## How to Use This Tracker

1. **At each milestone:** Read this file + run `lead_client.py status` + check smoke tests.
2. **Grade each dimension** using the rubric below. Update the scoreboard.
3. **Log new findings** in the relevant section with severity and file:line.
4. **Mark resolved** items with `[DONE vX.X.XX]` — do not delete, for history.
5. **Opus audit** (this doc type): Deep structural review, refactor candidates, bug sweep. Do quarterly or pre-milestone.
6. **Sonnet quick audit**: Scoreboard + open issues only. Do at each patch.

---

## Grading Rubric

| Grade | Meaning |
|-------|---------|
| **S** | Exceptional — production-grade, zero known gaps |
| **A** | Excellent — minor issues only, no blockers |
| **B+** | Good — some gaps, actively tracked |
| **B** | Adequate — notable gaps, needs attention |
| **C** | Needs work — blockers present |

---

## Scoreboard

> Last updated: **v0.21.00** | Audited by: Opus (2026-03-19)

| Dimension | Grade | Trend | Key Gap |
|-----------|-------|-------|---------|
| **Architecture / SoC** | A | → | launcher.py still 4,561 LOC; TECH_DEBT 4.3/4.4 open |
| **Code Quality** | A | ↑ | All P1 resolved; deferred imports in providers remain |
| **Testing** | A- | → | 22/22 smoke; no per-skill unit tests; dashboard untested |
| **Security** | B+ | ↑ | OWASP B+, 26 controls; prod config not hardened by default |
| **Reliability / S1** | A | ↑ | S1 complete: SSE race, conn leaks, _alive guards, escalating backoff |
| **Observability / S2** | B | → | JSON logging not unified; no `/api/health` aggregate |
| **Usability / UX** | A | ↑ | IQ on cards, timestamps, Fleet Comm modernized |
| **Dynamic Abilities** | A | ↑ | Escalating backoff, Ollama degradation cascade added |
| **Module / Plugin Support** | B+ | → | 6 modules; no formal manifest/discovery registry |
| **Data Processing + HITL** | A | ↑ | Intelligence scoring live (0.21.03); HITL model recs active |
| **Performance** | A | ↑ | Health probe no longer burns tokens (P1-02 fixed) |
| **Documentation** | A | → | CLAUDE.md thorough; compliance docs complete |
| **Overall** | **A** | ↑ | S1 done. S2-S4 milestones remain for S-tier |

---

## Open Issues

### CRITICAL (P0) — Block release

*None at v0.21.01.*

---

### HIGH (P1) — Fix before next milestone

#### P1-01 — Double budget DB query per inference call [DONE v0.21.04]
**File:** `fleet/skills/_models.py:61,94`
**Detail:** `check_budget()` is called twice in `call_complex()` — once for enforcement check (line 61), once for cost pre-estimation (line 94). Each call hits `db.get_usage_summary()`. High-frequency skills (flashcard, rag_query) run this 2x per task.
**Fix:** Cached result from first call, reused in second check. Single DB round-trip.

#### P1-02 — Claude health probe burns API tokens [DONE v0.21.04]
**File:** `fleet/providers.py:268-273`
**Detail:** `probe_provider_health("claude")` sends a real 1-token inference request to validate auth. At scale (Dr. Ders keepalive ~240s), this adds real cost and quota consumption.
**Fix:** Replaced `client.messages.create()` with `client.models.list(limit=1)` — auth-only, zero inference cost.

#### P1-03 — Budget throttle blocks worker thread [DONE v0.21.00]
**File:** `fleet/skills/_models.py:73`
**Detail:** `time.sleep(5)` in `throttle` mode executes on the worker thread, blocking it from picking up other tasks during the delay.
**Fix:** Replaced with immediate `[BUDGET THROTTLED]` return message. Worker thread no longer blocked.

---

### MEDIUM (P2) — Fix within 2 milestones

#### P2-01 — Redundant import inside function body [DONE v0.21.04]
**File:** `fleet/skills/_models.py:89`
**Detail:** `from providers import PRICING` on line 89 is already imported at the module top (line 14). Redundant in-function import — harmless but misleading.
**Fix:** Removed the in-function import. Already available at module scope.

#### P2-02 — Token estimation is word-count-based (inaccurate for code)
**File:** `fleet/skills/_models.py:87-88`
**Detail:** Pre-execution cost estimate uses `len(text.split()) * 1.3` as a word→token approximation. For code, JSON, or markdown, actual token counts can be 2-5x word count. Causes under-estimation of costs for code_write/code_review skills.
**Fix:** Use `anthropic.Anthropic().count_tokens()` or `tiktoken` for a real token count, or at minimum apply a higher multiplier (2.0) for code-heavy skills.
**Target:** 0.22.00

#### P2-03 — Theme constants duplicated across UI modules
**File:** `BigEd/launcher/ui/settings.py:21-36`, `BigEd/launcher/launcher.py` (source)
**Detail:** Color/font constants (BG, BG2, ACCENT, GOLD, TEXT, MONO, FONT, etc.) are copy-pasted into `settings.py` with comment "copied from launcher.py — dialogs are standalone." A brand color change requires updating N files.
**Fix:** Extract to `BigEd/launcher/ui/theme.py`. All UI modules import from there.
**Target:** 0.22.00 or FI-4

#### P2-04 — TECH_DEBT 4.3: REST API helpers still in launcher.py
**File:** `BigEd/launcher/launcher.py` (~4,561 LOC)
**Detail:** REST helper functions for fleet API calls remain embedded in the launcher god-object. Documented as TECH_DEBT 4.3 (unresolved).
**Fix:** Extract to `BigEd/launcher/fleet_api.py`. Reduces launcher.py by ~200-400 LOC.
**Target:** 0.22.00

#### P2-05 — TECH_DEBT 4.4: Data access layer incomplete
**File:** `BigEd/launcher/data_access.py` (224 LOC) + `launcher.py`
**Detail:** `data_access.py` was extracted as part of TECH_DEBT 4.4 but the extraction is partial — some DB calls remain in launcher.py.
**Fix:** Complete extraction. All launcher DB access routes through `data_access.py`.
**Target:** 0.22.00

#### P2-06 — SSE client race condition (TECH_DEBT 4.2) [DONE v0.21.00]
**File:** `BigEd/launcher/ui/sse_client.py`
**Detail:** SSE client list race (noted in stability guide MEDIUM issues). If multiple SSE streams are opened/closed rapidly, the listener list can corrupt.
**Fix:** `threading.Lock` on `_callbacks` dict. Snapshot-under-lock in `_dispatch()` prevents iteration errors.

#### P2-07 — Connection leaks in dashboard [DONE v0.21.00]
**File:** `fleet/dashboard.py`
**Detail:** Stability guide MEDIUM issue. Dashboard HTTP connections not always closed on endpoint exceptions.
**Fix:** All DB connection sites wrapped with `try/finally` + `conn.close()`. Covers api_data_stats, api_comms, api_rag, tools DB.

#### P2-08 — `_alive` flag missing from timer chains [DONE v0.21.00]
**File:** `BigEd/launcher/launcher.py`, `BigEd/launcher/ui/boot.py`
**Detail:** Timer chains (`self.after()`) can fire after window destroy, causing `TclError`. Listed as S1 Reliability gap.
**Fix:** `_safe_after()` method guards 43 calls in launcher.py + 13 in boot.py. `_alive` flag set False in `_on_close()`.

#### P2-09 — settings.py needs splitting (1,301 LOC)
**File:** `BigEd/launcher/ui/settings.py`
**Detail:** All 6 settings tabs (General, Models, Hardware, API Keys, Review, Operations) in one 1,301 LOC file. Each tab is self-contained enough to be its own file.
**Fix:** Split into `settings_general.py`, `settings_models.py`, `settings_hardware.py`, `settings_keys.py`, `settings_review.py`, `settings_ops.py`. `settings.py` becomes a thin orchestrator.
**Target:** 0.23.00

---

### LOW (P3) — Track, fix when passing

#### P3-01 — `_call_local` timeout hardcoded to 120s
**File:** `fleet/providers.py:326`
**Detail:** `urllib.urlopen(req, timeout=120)` — not configurable via fleet.toml. Long-running vision or large-context local calls may need more time.
**Fix:** Read from `config.get("fleet", {}).get("local_timeout", 120)`.

#### P3-02 — Deferred imports inside function bodies
**File:** `fleet/providers.py` (multiple), `fleet/skills/_models.py`
**Detail:** `import anthropic`, `import google.generativeai`, `import db`, `import sys` scattered inside function bodies. Intentional (avoid loading unused deps) but reduces readability and hides missing-dependency errors to runtime.
**Fix:** Document the pattern explicitly in a module docstring. Consider conditional top-level imports with `try/except ImportError`.

#### P3-03 — No per-skill unit tests
**File:** `fleet/skills/*.py`
**Detail:** 73 skills tested only via smoke_test.py end-to-end. Individual skill logic (output parsing, error handling) is untested in isolation.
**Fix:** Add `fleet/tests/` with pytest fixtures that mock `call_complex()` and validate skill output format.

#### P3-04 — Dashboard.py (1,529 LOC) untested
**File:** `fleet/dashboard.py`
**Detail:** No dedicated test for the 40+ API endpoints. Endpoint contract changes can silently break UI.
**Fix:** Add `flask.testing.FlaskClient` tests for critical endpoints (`/api/fleet/status`, `/api/tasks`, `/api/usage`).

#### P3-05 — Auto-start fires during first-run walkthrough
**File:** `BigEd/launcher/ui/boot.py` or `launcher.py`
**Detail:** Stability guide MEDIUM: auto-boot can trigger before walkthrough completion on first run.
**Fix:** Check `first_run_complete` flag from fleet.toml before initiating auto-start.

#### P3-06 — `get_optimal_model()` override logic is partial
**File:** `fleet/providers.py:106-117`
**Detail:** `get_optimal_model()` only downgrade-protects simple tasks. The config `complex` model override path is confusing and doesn't handle the `medium` complexity tier explicitly.
**Fix:** Simplify: `return COMPLEXITY_ROUTING.get(complexity, "claude-sonnet-4-6")` — always use complexity table, ignore config override (config's `complex` key is for provider selection, not complexity routing).

#### P3-07 — `check_complex_batch` error message is generic
**File:** `fleet/skills/_models.py:213`
**Detail:** Failed batch items return `{"error": "Request failed"}` with no detail from the API response. Debugging batch failures is hard.
**Fix:** Include `item.result.error.type` and `item.result.error.message` in the error dict.

---

## Dimension Deep-Dives

### 1. Separation of Concerns (SoC)

**Current Grade: A**

| Component | SoC Quality | Notes |
|-----------|-------------|-------|
| `fleet/providers.py` | ✓ Clean | Model routing only. No business logic. |
| `fleet/skills/_models.py` | ✓ Clean | Inference dispatch + budget. Imports clean. |
| `fleet/db.py` | ✓ Clean | DAL only. No fleet logic. |
| `fleet/supervisor.py` | ✓ Good | Process lifecycle. Some config coupling. |
| `fleet/hw_supervisor.py` | ✓ Good | GPU/thermal only. Well-bounded. |
| `fleet/dashboard.py` | ⚠ Acceptable | 40+ endpoints in one file. Consider Blueprint split. |
| `BigEd/launcher/launcher.py` | ⚠ God-object | 4,561 LOC. TECH_DEBT 4.3/4.4 unresolved. |
| `BigEd/launcher/ui/settings.py` | ⚠ Large | 1,301 LOC. Split by tab is natural next step. |
| `BigEd/launcher/ui/theme.py` | ✗ Missing | Theme constants duplicated across UI files. |

**S-tier path:** Extract `theme.py` (P2-03), complete TECH_DEBT 4.3 (P2-04) and 4.4 (P2-05), split settings.py (P2-09).

---

### 2. Usability / UX

**Current Grade: A-**

| Feature | Status | Notes |
|---------|--------|-------|
| HITL inline actions | ✓ Done (v0.21.01) | View/respond to agent requests in status tab |
| Security advisory flow | ✓ Done | View/dismiss in same panel |
| Omni-box (Ctrl+K) | ✓ Done (v0.45) | Skill auto-complete + agent ping |
| First-run walkthrough | ✓ Done (v0.34) | 6-step, skip-all, re-trigger |
| Model performance panel | ✓ Done (v0.21.01) | Tok/s live comparison |
| Settings dialog | ✓ Good | 820x580, 6 tabs, sidebar nav |
| Console persistence | Planned (0.21.03) | Chat history across rebuilds |
| Dashboard auto-open | Planned (0.22.01) | On boot complete |
| Intelligence scoring | ✓ Done (v0.21.03) | 0.0-1.0 per task, Tier 1 mechanical |

**Next UX wins:** Console history persistence (0.21.03), dashboard auto-open (0.22.01), model comparison benchmarks (0.21.02).

---

### 3. Dynamic Abilities

**Current Grade: A**

| Capability | Status | Grade |
|-----------|--------|-------|
| HA fallback (Claude→Gemini→Local) | ✓ Active | A |
| Circuit breaker (3 failures/5min → 60s cooldown) | ✓ Active | A |
| VRAM/thermal scaling (eco↔full↔emergency) | ✓ Active | A |
| Complexity-based model routing | ✓ Active | A |
| Per-skill local model routing | ✓ Active (v0.21.01) | A |
| Budget enforcement (warn/throttle/block) | ✓ Active (CT-4) | A- |
| Offline/air-gap mode | ✓ Active (v0.33) | A |
| Idle skill evolution | ✓ Active (v0.42) | A |
| Worker respawn + crash backoff | Partial | B+ |
| Escalating backoff (15→30→60→300s) | Planned (0.21.00) | — |
| DB connection pool | Planned (0.21.00) | — |

**S-tier path:** Escalating backoff + DB pool (0.21.00 S1 milestone).

---

### 4. Module / Plugin Support

**Current Grade: B+**

| Feature | Status | Notes |
|---------|--------|-------|
| 6 active modules (CRM, Ingestion, Accounts, Onboarding, OwnerCore, Walkthrough) | ✓ Active | |
| Zero-bloat baseline (no modules by default) | ✓ Active (v0.46) | |
| Module loader (`__init__.py`, 334 LOC) | ✓ Working | |
| Plugin manifest / capability registry | ✗ Missing | No formal discovery mechanism |
| Hot-reload of modules without restart | ✗ Missing | Restart required |
| Community skill install (git repo pull) | Planned (2.0) | Plugin marketplace |
| Module dependency declaration | ✗ Missing | Modules can't declare deps |

**S-tier path:** Add plugin manifest (`module.toml` per module with name/version/deps/entrypoint), then hot-reload capability.

---

### 5. Data Processing + HITL

**Current Grade: A-**

| Feature | Status | Notes |
|---------|--------|-------|
| Task queue (SQLite DAG) | ✓ Active | parent_id, depends_on, WAITING, cascade |
| Conditional DAG edges | ✓ Active (0.01.02) | |
| WAITING_HUMAN status + Fleet Comm tab | ✓ Active (v0.37, v0.21.01) | |
| Inline HITL action panel | ✓ Active (v0.21.01) | Status tab |
| Security advisory approve/dismiss | ✓ Active | |
| Evaluator-optimizer loop (REVIEW status) | ✓ Active (v0.35, 2 rounds max) | |
| Semantic watchdog (QUARANTINED) | ✓ Active (v0.36) | |
| DLP secret scrubbing | ✓ Active (v0.36 + 0.11.00) | |
| Base64 secret detection | ✓ Active (GR-4) | |
| Message Batches API (50% savings) | ✓ Active (`call_complex_batch`) | |
| Quality/intelligence scoring per task | ✓ Active (v0.21.03) | Tier 1 mechanical; Tier 2 LLM planned |
| Distributed tracing (trace_id) | ✗ Missing | Planned 0.23.00 |
| Per-agent performance metrics | Partial | tok/s tracked; tasks/hour, success rate planned 0.22.00 |
| HITL model preference change flow | ✓ Active (v0.21.03) | model_recommend.py, 6h auto-dispatch |

**S-tier path:** Intelligence scoring column in usage table (0.21.02), distributed trace_id (0.23.00), per-agent perf dashboard (0.22.00).

---

## Refactor Targets (Ranked)

| Priority | Target | File | Size | Effort | Value |
|----------|--------|------|------|--------|-------|
| 1 | Extract `ui/theme.py` | settings.py + launcher.py | Small | Low | Medium |
| 2 | Fix double budget check | _models.py:61,94 | Tiny | Low | High (perf) |
| 3 | Complete TECH_DEBT 4.3 (REST helpers) | launcher.py | Medium | Medium | High (SoC) |
| 4 | Complete TECH_DEBT 4.4 (data_access) | launcher.py + data_access.py | Medium | Medium | High (SoC) |
| 5 | Split settings.py into tab files | settings.py (1,301 LOC) | Medium | Medium | Medium |
| 6 | Add Flask Blueprint split for dashboard | dashboard.py (1,529 LOC) | Medium | Medium | Medium |
| 7 | Health probe → no-inference check | providers.py:268-273 | Small | Low | Medium (cost) |
| 8 | Token estimation → tiktoken | _models.py:87-88 | Small | Low | Medium (accuracy) |
| 9 | Add plugin manifest | modules/__init__.py | Medium | Medium | High (extensibility) |
| 10 | Per-skill pytest fixtures | fleet/tests/ (new) | Large | High | High (reliability) |

---

## Security Checklist

> Per OWASP B+ baseline (26 controls, GDPR B). Refresh at each S4 audit.

| Control | Status | Hardened in Prod? |
|---------|--------|-------------------|
| Docker sandbox for code_write/skill_test | Optional (config) | Needs `sandbox_enabled = true` default |
| Dashboard auth token | Configurable | Must be non-empty in prod |
| 127.0.0.1 binding only | ✓ Verified (pen_test.py) | ✓ |
| DLP secret scrubbing (DB + files) | ✓ Active | ✓ |
| Base64 secret detection | ✓ Active (GR-4) | ✓ |
| Dependency scanning (pip-audit) | ✓ Active | ✓ |
| Budget enforcement (anti-spend attacks) | ✓ Active (CT-4) | ✓ |
| WSL secret store (API keys) | ✓ Active | ✓ |
| Key rotation (secret_rotate.py) | ✓ Active | ✓ |
| Evaluator-optimizer adversarial review | ✓ Active | ✓ |
| SQLCipher encryption | Planned (0.24.00) | ✗ |
| TLS for dashboard | Planned (0.24.00) | ✗ |
| RBAC (operator/admin/viewer) | Planned (0.24.00) | ✗ |
| API attribution logging | Planned (0.24.00) | ✗ |
| MQTT wildcard blocking | ✓ Active (0.11.00) | ✓ |

**Prod hardening checklist (do before any public deployment):**
- [ ] `sandbox_enabled = true` in fleet.toml
- [ ] `dashboard_token` set to non-empty secret
- [ ] SQLCipher migration complete (0.24.00)
- [ ] TLS cert generated (0.24.00)
- [ ] RBAC roles configured (0.24.00)

---

## S-Tier Milestone Readiness

| Milestone | Theme | Status | Blockers |
|-----------|-------|--------|---------|
| **0.21.00 — S1 Reliability** | 99.99% uptime | **DONE** | All blockers resolved: P1-03, P2-06/07/08, escalating backoff, Ollama degradation |
| **0.22.00 — S2 Observability** | Unified health | Not started | `/api/health` aggregate, JSON logging, per-agent metrics |
| **0.23.00 — S3 Auto-Intelligence** | Self-improving fleet | Not started | Quality scoring, distributed tracing, auto-trigger pipelines |
| **0.24.00 — S4 Security** | Hardened defaults | Not started | SQLCipher, TLS, RBAC, audit attribution |
| **0.25.00 — Multi-Backend** | Provider abstraction | Not started | Backend ABC in providers.py |

---

## Resolved (History)

| Item | Resolution | Version |
|------|-----------|---------|
| God-object extraction (TECH_DEBT 4.1) | Extracted boot.py, settings.py, consoles.py, omnibox.py, sse_client.py | v0.21.01 |
| HitL unified flow | WAITING_HUMAN status, Fleet Comm tab, inline action panel | v0.37 + v0.21.01 |
| Dr. Ders memory watchdog | 3-tier RSS monitoring (gc→restart→alert) | v0.21.01 |
| Tok/s tracking | eval_count/eval_duration captured per Ollama call | v0.21.01 |
| Per-skill local model routing | SKILL_COMPLEXITY + LOCAL_COMPLEXITY_ROUTING in providers.py | v0.21.01 |
| Gemini safety handling | finishReason check, SAFETY block raises | v0.21.02 |
| Native key manager (WSL→Windows) | Direct ~/.secrets read/write, no wsl() | v0.21.02 |
| Intelligence scoring | intelligence.py Tier 1 mechanical (0.0-1.0), tasks.intelligence_score | v0.21.03 |
| HITL model recommendations | model_recommend.py, MODEL_QUALITY table, 6h auto-dispatch | v0.21.03 |
| Gemini ToS tagging | provider column in usage, thread-local tracking, dataset exclusion | v0.21.03 |
| Boot stability (16 fixes) | psutil migration, park+guard, adaptive timeouts, RAM scaling | 0.16.00 |
| All TECH_DEBT 4.1–4.8 items | Dead code scan, graveyard, dependency audit | v0.48 / 0.01.x |
| WSL→psutil migration | All pkill/pgrep eliminated (20+ calls) | 0.16.00 |
| DAG task graph | parent_id, depends_on, conditional edges | v0.31 + 0.01.02 |
| HA fallback cascade | Claude→Gemini→Local with circuit breaker | v0.45 |
| Swarm 3-tier intelligence | Evolution, research, specialization | 0.17–0.19 |
| Cost tracking CT-1/2/3/4 | Token budgets, cost attribution, enforcement | v0.31–v0.38 |
| Double budget check (P1-01) | Cached first check_budget() result, single DB round-trip | v0.21.04 |
| Health probe token burn (P1-02) | client.models.list(limit=1) replaces inference call | v0.21.04 |
| Redundant import (P2-01) | Removed in-function `from providers import PRICING` | v0.21.04 |
| Fleet tab IQ scores | Intelligence score on agent cards (color-coded thresholds) | v0.21.04 |
| Fleet Comm modernization | Orange accent stripe, stacked headers, counter badge | v0.21.04 |
| SSE race condition (P2-06) | threading.Lock on _callbacks, snapshot-under-lock dispatch | v0.21.00 |
| Dashboard connection leaks (P2-07) | try/finally on all DB connection sites | v0.21.00 |
| Timer _alive guards (P2-08) | _safe_after() wraps 56 self.after() calls across launcher+boot | v0.21.00 |
| Budget throttle blocking (P1-03) | Immediate [BUDGET THROTTLED] return, no sleep | v0.21.00 |
| Escalating crash backoff | BACKOFF_SCHEDULE [15,30,60,120,300], per-worker crash counter | v0.21.00 |
| Ollama graceful degradation | hw_state.json detection, transition warnings, STATUS.md mode | v0.21.00 |

---

## Audit Log

| Date | Version | Auditor | Scope | Summary |
|------|---------|---------|-------|---------|
| 2026-03-19 | v0.21.01 | Opus | Full codebase | First post-1.0 deep audit. Overall A-. 3 P1, 9 P2, 7 P3 issues logged. S1 path clear via 0.21.00. |
| 2026-03-19 | v0.21.03 | Opus | Incremental | Intelligence scoring, HITL model recs, Gemini ToS — Data Processing+HITL upgraded A-→A. 74 skills. |

---

*Next audit: at 0.21.00 (S1 Reliability milestone). Focus: P2-06/07/08 resolution, timer chain _alive guards, SSE race fix, connection leak fix.*
