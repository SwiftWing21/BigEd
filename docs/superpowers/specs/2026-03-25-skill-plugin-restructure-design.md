# Skill Plugin Restructure Design

**Date:** 2026-03-25
**Status:** Approved (design phase)
**Scope:** 125 skills (132 files total), 28,620 lines, 6 existing helpers
**Priority Order:** Plugin Contract (3) -> Suite Consolidation (1) -> Shared Patterns (2)
**Estimated Savings:** ~3,200 lines (11% reduction)

---

## 1. Plugin Contract Formalization

### 1.1 Problem

- 125 skills, no formal contract
- Inconsistent `run()` signatures: `(task, context)`, `(payload, config)`, `(payload, config, log)`, `(payload, config, log=None)`
- 27 skills return `str` instead of `dict` (double-serialization bug in worker.py)
- 8 skills require `log` as 3rd positional arg — worker.py only passes 2 (broken at runtime)
- Metadata is ad-hoc: SKILL_NAME in 125/125, DESCRIPTION in 125/125, VERSION in 0/125
- COMPLEXITY routing lives in a disconnected dict in `providers.py`, not on skills themselves
- Marketplace has no way to auto-generate manifests from skill metadata

### 1.2 Contract Specification

#### Required Constants

```python
SKILL_NAME = "flashcard"                          # str: unique, matches filename stem
DESCRIPTION = "Generate Q&A flashcards from ..."  # str: one-line human-readable
VERSION = "1.0.0"                                 # str: semver
```

#### Optional Constants (with defaults)

```python
REQUIRES_NETWORK = False       # bool: True if skill calls external APIs
COMPLEXITY = "simple"          # "simple" | "medium" | "complex" — drives model routing
TIMEOUT = 600                  # int: max seconds (overrides DEFAULT_SKILL_TIMEOUT)
AUTHOR = ""                    # str: "agent:coder_1" or "human:max"
SUITE = ""                     # str: domain grouping ("code", "security", "ml", "model", "git", "skill_lifecycle", "research", "ops")
TAGS = []                      # list[str]: free-form searchable tags
```

#### Capability Declarations (optional)

```python
READS_TABLES = []              # list[str]: DB tables this skill SELECTs from
WRITES_TABLES = []             # list[str]: DB tables this skill writes to
READS_DIRS = []                # list[str]: relative paths skill reads ("knowledge/summaries")
WRITES_DIRS = []               # list[str]: relative paths skill writes to
DEPENDS_ON_SKILLS = []         # list[str]: other skills this calls (for skill_chain)
DEPENDS_ON_HELPERS = []        # list[str]: helper modules ("_models", "_report")
DEPENDS_ON_PACKAGES = []       # list[str]: pip packages beyond stdlib ("anthropic", "httpx")
```

#### Standardized Entry Point

```python
def run(payload: dict, config: dict) -> dict:
    """Skill entry point.

    Args:
        payload: Task-specific input (from task.payload_json, already deserialized).
        config:  Fleet configuration (from fleet.toml, already loaded).

    Returns:
        dict with at minimum {"status": "ok"|"error"}.
        On error: {"status": "error", "error": "human-readable message"}.
        On success: {"status": "ok", ...skill-specific keys...}.
    """
```

**Why 2 args:** worker.py already calls `module.run(payload, config)` everywhere (line 255). The 8 skills with required `log` param are broken today. Skills needing logging use `log = logging.getLogger(SKILL_NAME)` at module level.

**Why always dict:** worker.py calls `json.dumps(result)` unconditionally. Returning `str` causes double-serialization. The 27 str-returning skills are bugs.

**Parameter names:** Always `payload, config`. The 3 skills using `task, context` work positionally but are confusing.

#### Return Type Contract

```python
# Minimal valid return:
{"status": "ok"}

# Success with data:
{"status": "ok", "cards_generated": 3, "source": "paper.md"}

# Error:
{"status": "error", "error": "No summaries found"}

# Reserved keys (injected by worker.py post-return, skills must NOT set these):
# _conventions, _review, _ab_variant
```

#### Lifecycle Hooks (optional)

```python
def health_check(config: dict) -> dict:
    """Called by smoke_test to verify skill is operational."""
    return {"healthy": True, "detail": "ok"}

def on_install(config: dict) -> None:
    """Called once when installed via marketplace."""

def on_uninstall(config: dict) -> None:
    """Called when removed via marketplace."""

def on_upgrade(config: dict, from_version: str) -> None:
    """Called when updated to a newer version."""
```

### 1.3 Contract Validator

New file: `fleet/skills/_contract.py`

```python
def validate_skill(module) -> list[str]:
    """Return list of contract violations (empty = compliant)."""
    warnings = []
    if not hasattr(module, 'SKILL_NAME'):
        warnings.append("missing SKILL_NAME")
    if not hasattr(module, 'DESCRIPTION'):
        warnings.append("missing DESCRIPTION")
    if not hasattr(module, 'VERSION'):
        warnings.append("missing VERSION (default 0.0.0)")
    sig = inspect.signature(module.run)
    params = list(sig.parameters.keys())
    if len(params) < 2:
        warnings.append(f"run() has {len(params)} params, need 2+")
    if len(params) > 2 and sig.parameters[params[2]].default is inspect.Parameter.empty:
        warnings.append(f"run() 3rd param '{params[2]}' has no default — will crash")
    if sig.return_annotation is str:
        warnings.append("run() -> str: will cause double-serialization")
    return warnings
```

Integrated into `smoke_test.py` for compliance reporting.

### 1.4 Migration Phases

| Phase | What | Skills Changed | Risk |
|-------|------|---------------|------|
| **0** | Add `_contract.py` validator + worker.py result coercion safety net | 0 | None |
| **1** | Fix 27 str-returning skills (`return json.dumps({...})` -> `return {...}`) | 27 | Very low |
| **2** | Fix 18 non-standard signatures (8 broken `log`, 3 `task/context`, 7 `log=None`) | 18 | Low |
| **3** | Add `VERSION` + `COMPLEXITY` to all skills; refactor `providers.py` to read from modules | 125 | Low |
| **4** | Incrementally add optional metadata (`SUITE`, `TAGS`, capability declarations) | Ongoing | None |

#### Worker.py Result Coercion (Phase 0 safety net)

```python
# Added after run_skill() returns, before complete_task()
if isinstance(result, str):
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            result = parsed
        else:
            result = {"status": "ok", "result": parsed}
    except (json.JSONDecodeError, ValueError):
        result = {"status": "ok", "result": result}
```

### 1.5 Gotchas Found

1. **8 skills with required `log` param are broken at runtime** — worker passes 2 args, they need 3. Either never dispatched via normal loop or crash silently in threading wrapper.
2. **config_validate.py expects full task row** (`task.get("payload")`) but worker passes deserialized payload. Latent bug — always gets empty payload.
3. **SKILL_TIMEOUTS dict in worker.py** should be replaced by per-skill `TIMEOUT` constant, with dict as fallback.
4. **Air-gap whitelist in config.py** is disconnected from `REQUIRES_NETWORK`. Should unify: air-gap auto-allows any skill where `REQUIRES_NETWORK = False`.

---

## 2. Suite Consolidation

### 2.1 Suites to Create

#### `ml_train_suite.py` — 40% reduction, LOW risk
**Merging:** `embedding_train.py`, `reranker_train.py`, `router_retrain.py`, `scaler_train.py`
**Current:** 499 lines -> **~300 lines**

These are structural clones — identical ExperimentFramework boilerplate, identical pending-approval checks, identical error handling. Only `train_fn` and `eval_fn` bodies differ.

```python
SKILL_NAME = "ml_train"
SUITE = "ml"
COMPLEXITY = "complex"

def run(payload: dict, config: dict) -> dict:
    action = payload.get("action", "embedding")
    handlers = {"embedding": _train_embedding, "reranker": _train_reranker,
                "router": _train_router, "scaler": _train_scaler}
    return _dispatch(payload, config, handlers)

def _experiment_lifecycle(agent, exp_type, hypothesis, config, train_fn, eval_fn):
    """Shared: propose -> approve check -> run -> eval -> return."""
    fw = ExperimentFramework()
    exp_id = fw.propose(agent=agent, experiment_type=exp_type, hypothesis=hypothesis)
    exp = fw.get(exp_id)
    if exp and exp["status"] not in ("APPROVED",):
        return {"status": "pending_approval", "experiment_id": exp_id, "experiment": exp}
    result = fw.run(exp_id, train_fn, eval_fn)
    return {"status": "ok", "experiment_id": exp_id, "experiment": result}
```

#### `code_suite.py` — 28% reduction, LOW risk
**Merging:** `code_review.py`, `code_quality.py`, `code_refactor.py`, `code_discuss.py`, `evaluate.py`, `pair_program.py`
**Keeping separate:** `fma_review.py` (slimmed to ~80 lines, delegates to code_suite)
**Current:** 1,454 lines -> **~1,050 lines**

Key dedup: `PERSPECTIVE_FOCUS` dict (3 copies -> 1, note: `code_write_review.py` also has a copy — it stays separate but should import from the suite), `_pick_file()` (2 copies with different signatures — `code_review` returns `Path | None`, `fma_review` returns `tuple[Path, str]`; unify to `Path | None` with status as separate return), discussion thread loading (2 identical SQL queries -> 1), JSON response parsing (2 implementations -> shared `_llm_parse`).

Subactions: `review`, `quality`, `refactor`, `discuss`, `evaluate`, `pair`

#### `model_suite.py` — 28% reduction, LOW-MED risk
**Merging:** `model_manager.py`, `hardware_profiler.py`, `auto_profile.py`
**Current:** 1,082 lines -> **~780 lines**

Key dedup: 3 separate `detect_hardware()` implementations become 1 canonical version. `auto_profile` already imports from `hardware_profiler`, confirming they belong together. Both `hardware_profiler` and `auto_profile` use non-standard 3-arg signatures — normalized to 2-arg contract.

Subactions: `check`, `install`, `profiles`, `apply_profile`, `detect`, `recommend`, `auto_generate`

#### `security_suite.py` — 19% reduction, LOW-MED risk
**Merging:** `security_audit.py`, `security_review.py`, `security_apply.py`, `pen_test.py`, `cve_watch.py`, `sql_review.py`
**Current:** 1,853 lines -> **~1,500 lines**

Key dedup: Advisory creation (2 copies -> 1), `db.post_message()` notification (3 copies -> 1), severity constants/ordering (2 copies -> 1), AST-based scanning (overlaps with code_quality).

Subactions: `audit`, `code_scan`, `apply`, `pentest`, `cve`, `sql`

#### `git_suite.py` — 17% reduction, LOW risk
**Merging:** `git_manager.py`, `github_sync.py`, `github_interact.py`
**Keeping separate:** `branch_manager.py` (specialized product branching tool)
**Current:** 843 lines -> **~700 lines**

Key dedup: `_run_git()` helper (2 copies -> 1), GitHub API auth headers (2 copies -> 1).

Subactions: `status`, `diff`, `log`, `commit`, `stash`, `checkout`, `auth`, `clone`, `push`, `backup`, `list_issues`, `create_issue`, `comment_issue`, `list_prs`, `create_pr`

#### `skill_lifecycle_suite.py` — 32% reduction, MED risk
**Merging:** `skill_draft.py`, `skill_test.py`, `skill_evolve.py`, `skill_promote.py`, `deploy_skill.py`
**Keeping separate:** `skill_train.py` (complex, own eval registry), `skill_chain.py` (workflow orchestration), `skill_dependency_map.py` (static analysis), `skill_learn.py` (gap analysis)
**Current:** 791 lines -> **~540 lines**

Key dedup: `_find_draft()` (3 copies -> 1), `_extract_code()` from LLM (3 copies -> 1), module validation (centralized).

Subactions: `draft`, `test`, `evolve`, `promote`, `deploy`

### 2.2 Smaller Merges

| Merge | Lines Saved | Risk |
|-------|------------|------|
| `oss_review.py` + `oss_review_swarm.py` -> swarm as mode flag | ~100 | LOW |
| `config_validate.py` + `config_drift_detect.py` -> shared TOML schema | ~100 | LOW |

### 2.3 NOT Merging

| Cluster | Skills | Reason |
|---------|--------|--------|
| Content Generation | 7 | No shared logic — different backends (Stability AI, Replicate, PIL, LLM) |
| RAG Pipeline | 6 | Already well-separated, single-responsibility, clean imports |

### 2.4 Backward Compatibility

Worker.py routing shim — old task types transparently dispatch to suites.

**Naming convention:** All suite files drop the `_suite` suffix to match existing skill naming (e.g., `ml_train.py`, `code_suite.py`, `model_suite.py`). Module names in the routing table must exactly match filenames.

```python
SUITE_ROUTING = {
    # code_suite.py
    "code_review":     ("code_suite",             "review"),
    "code_quality":    ("code_suite",             "quality"),
    "code_refactor":   ("code_suite",             "refactor"),
    "code_discuss":    ("code_suite",             "discuss"),
    "evaluate":        ("code_suite",             "evaluate"),
    "pair_program":    ("code_suite",             "pair"),
    # ml_train_suite.py
    "embedding_train": ("ml_train_suite",         "embedding"),
    "reranker_train":  ("ml_train_suite",         "reranker"),
    "router_retrain":  ("ml_train_suite",         "router"),
    "scaler_train":    ("ml_train_suite",         "scaler"),
    # security_suite.py
    "security_audit":  ("security_suite",         "audit"),
    "security_review": ("security_suite",         "code_scan"),
    "security_apply":  ("security_suite",         "apply"),
    "pen_test":        ("security_suite",         "pentest"),
    "cve_watch":       ("security_suite",         "cve"),
    "sql_review":      ("security_suite",         "sql"),
    # git_suite.py
    "git_manager":     ("git_suite",              "status"),
    "github_sync":     ("git_suite",              "sync"),
    "github_interact": ("git_suite",              "list_issues"),
    # model_suite.py
    "model_manager":   ("model_suite",            "check"),
    "hardware_profiler":("model_suite",           "detect"),
    "auto_profile":    ("model_suite",            "auto_generate"),
    # skill_lifecycle_suite.py
    "skill_draft":     ("skill_lifecycle_suite",  "draft"),
    "skill_test":      ("skill_lifecycle_suite",  "test"),
    "skill_evolve":    ("skill_lifecycle_suite",  "evolve"),
    "skill_promote":   ("skill_lifecycle_suite",  "promote"),
    "deploy_skill":    ("skill_lifecycle_suite",  "deploy"),
}
```

**Critical:** The shim uses `setdefault` so it only injects the default action when the caller did not already specify one. This preserves existing multi-action dispatching (e.g., `git_manager` tasks that already carry `{"action": "commit"}`):

```python
if skill_name in SUITE_ROUTING:
    suite_module, default_action = SUITE_ROUTING[skill_name]
    payload.setdefault("action", default_action)
    skill_name = suite_module
```

**Rollback kill-switch:** A config flag `[fleet] suite_routing_enabled = true` in fleet.toml allows disabling the routing shim during the deprecation period. When false, worker.py dispatches to original skill files (which remain as `_deprecated_<name>.py` during the transition).

### 2.5 Implementation Order

1. ML Training (smallest, highest %, cleanest separation)
2. Model Management (untangle 3 hardware detection copies)
3. Code Review (deduplicate perspectives, discussion loading)
4. Git/GitHub (mechanical merge)
5. Security (advisory pipeline is safety-critical)
6. Skill Lifecycle (most complex — pipeline gate semantics)
7. Smaller merges (OSS review, config)

### 2.6 Deprecation Strategy

1. Create suite file with all subactions
2. Add routing shim in worker.py (with `suite_routing_enabled` kill-switch in fleet.toml)
3. Update `idle_evolution.py` skill weights
4. Rename old files to `_deprecated_<name>.py` for one release cycle
5. If regression detected: set `suite_routing_enabled = false` to revert to original files
6. Remove deprecated files in next milestone

---

## 3. Shared Pattern Extraction

### 3.1 New Helper Modules

#### P0: Fix Raw sqlite3 Violations (2 files)

- `doc_freshness.py:50-61` — replace `sqlite3.connect()` with `db.get_conn()`
- `_flywheel_core.py:525` — replace `sqlite3.connect()` with `db.get_conn()`
- `account_review.py` and `db_encrypt.py` are legitimate (different DBs / encryption testing)

#### P1: `_knowledge.py` — Directory Management + File Save

**Affects:** 57 skills, ~250 lines saved

```python
# fleet/skills/_knowledge.py
from pathlib import Path
from datetime import datetime

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
PROJECT_DIR = FLEET_DIR.parent

def get_output_dir(*parts: str) -> Path:
    """Return knowledge/<parts> directory, creating it if needed."""
    d = KNOWLEDGE_DIR.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d

def save_report(subdir: str, prefix: str, content: str, ext: str = ".md") -> Path:
    """Save a date-stamped report: knowledge/<subdir>/<prefix>_YYYYMMDD.<ext>."""
    d = get_output_dir(subdir)
    path = d / f"{prefix}_{datetime.now().strftime('%Y%m%d')}{ext}"
    path.write_text(content, encoding="utf-8")
    return path

def save_timestamped(subdir: str, prefix: str, content: str, ext: str = ".md") -> Path:
    """Save with full timestamp: knowledge/<subdir>/<prefix>_YYYYMMDD_HHMM.<ext>."""
    d = get_output_dir(subdir)
    path = d / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}{ext}"
    path.write_text(content, encoding="utf-8")
    return path
```

#### P2: `_llm_parse.py` — JSON Extraction from LLM Responses

**Affects:** 12+ skills, ~150 lines saved
**Replaces 6 different implementations:** flashcard.py bracket-matching, dataset_synthesize.py brace-matching, evaluate.py regex, _review.py verdict parser, clinical_review.py (4 identical blocks), plan_workload.py array regex.

```python
# fleet/skills/_llm_parse.py
import json, re

def extract_json_object(text: str, required_key: str = None) -> dict | None:
    """Extract first JSON object from LLM response text.
    Tries: direct parse -> regex with required_key -> brace-matching fallback."""

def extract_json_array(text: str) -> list | None:
    """Extract first JSON array from LLM response text."""

def extract_verdict(text: str) -> dict:
    """Extract PASS/FAIL verdict dict from LLM review response.
    Returns: {"verdict": "PASS"|"FAIL", "critique": str, "confidence": float}"""
```

#### P3: `_report.py` — Markdown Report Builder

**Affects:** 40+ skills, ~400 lines saved

```python
# fleet/skills/_report.py
class ReportBuilder:
    def __init__(self, title: str, include_date: bool = True): ...
    def metadata(self, **kwargs) -> "ReportBuilder": ...
    def table(self, headers: list[str], rows: list[list]) -> "ReportBuilder": ...
    def section(self, heading: str, body: str = "") -> "ReportBuilder": ...
    def findings(self, items: list[dict], severity_key="severity", detail_key="detail") -> "ReportBuilder": ...
    def build(self) -> str: ...
```

#### P4: Decompose `_flywheel_core.py` (891 lines, 1 importer)

Split into:
- `_flywheel_rubric.py` (72 lines) — rubric definitions, `score_to_grade()`
- `_flywheel_grading.py` (~470 lines) — all grading functions
- `_flywheel_audit.py` (~350 lines) — orchestration, `run_full_audit()`, reporting

#### P5: `_http.py` — URL Probing

**Affects:** 10+ skills, ~200 lines saved

```python
# fleet/skills/_http.py
def probe_url(url: str, method: str = "GET", timeout: int = 10,
              headers: dict = None) -> dict:
    """Probe URL, return {status, code, latency_ms, body, error}."""

def fetch_json(url: str, timeout: int = 10, headers: dict = None) -> dict | None:
    """Fetch and parse JSON from URL. Returns None on failure."""
```

#### P6: `_dispatch.py` — Action Routing

**Affects:** 38 skills (and all new suites), ~250 lines saved

```python
# fleet/skills/_dispatch.py
def dispatch_action(payload: dict, config: dict, actions: dict,
                    default: str = None) -> dict:
    """Route payload to handler by 'action' key. Returns error dict if unknown."""
    action = payload.get("action", default or next(iter(actions)))
    handler = actions.get(action)
    if not handler:
        return {"status": "error", "error": f"Unknown action: {action}",
                "valid_actions": list(actions.keys())}
    return handler(payload, config)
```

#### P7: Logging Bootstrap

**Affects:** 97 skills without logging

Add `log = logging.getLogger(SKILL_NAME)` to each skill during Phase 2-3 of contract migration. Not a separate helper — just a standard line in each skill.

### 3.2 Existing Helper Assessment

| Helper | Lines | Importers | Verdict |
|--------|-------|-----------|---------|
| `_models.py` | 256 | ~31 skills (~39 total) | Healthy, well-used |
| `_flywheel_core.py` | 891 | 1 skill | Decompose (P4) |
| `_watchdog.py` | 298 | 1 skill + 3 fleet-level (worker, supervisor, soak_test) | Cross-boundary usage |
| `_oss_core.py` | 194 | 2 skills | Appropriately scoped |
| `_review.py` | 164 | 0 skills (worker.py only) | Fine — pipeline component |
| `_security.py` | 75 | 2 (claude_code.py + discord_bot.py) | Underused — more file-path skills should use `safe_path` |

### 3.3 Priority Summary

| # | Action | Lines Saved | Difficulty |
|---|--------|-------------|------------|
| P0 | Fix 2 raw sqlite3 violations | 10 | Easy |
| P1 | `_knowledge.py` (57 skills) | ~250 | Easy |
| P2 | `_llm_parse.py` (12 skills) | ~150 | Easy-Med |
| P3 | `_report.py` (40+ skills) | ~400 | Medium |
| P4 | Decompose `_flywheel_core.py` | 0 (refactor) | Easy |
| P5 | `_http.py` (10 skills) | ~200 | Medium |
| P6 | `_dispatch.py` (38 skills + suites) | ~250 | Easy |
| P7 | Logging bootstrap (97 skills) | 0 (quality improvement, adds ~97 lines) | Easy |
| **Total** | | **~1,260** | |

---

## 4. Combined Impact

| Workstream | Lines Saved | Skills Affected |
|------------|-------------|-----------------|
| Suite Consolidation | ~1,850 | 31 skills -> 6 suites + 2 consolidations |
| Shared Patterns | ~1,260 | 57+ skills |
| **Total** | **~3,110 (11%)** | |

**Additional benefits:**
- Formal contract with validation for all 125 skills
- 27 double-serialization bugs fixed
- 8 broken `log` param skills fixed
- 97 skills gain logging
- Marketplace can auto-generate manifests from skill metadata
- COMPLEXITY routing moves from disconnected dict to source of truth on skills
- Air-gap whitelist unified with REQUIRES_NETWORK flag

## 5. Implementation Sequence

**Phase A — Contract Foundation (no suite changes):**
1. Create `_contract.py` validator
2. Add worker.py result coercion
3. Fix 27 str-returning skills
4. Fix 18 non-standard signatures
5. Fix 2 raw sqlite3 violations (note: worker.py line 535 also has raw sqlite3 for PHI audit — known debt, out of scope)
6. Add `VERSION` + `COMPLEXITY` to all skills

**Phase B — Shared Helpers (enables suites):**
1. Create `_knowledge.py` (P1)
2. Create `_llm_parse.py` (P2)
3. Create `_dispatch.py` (P6) — suites will use this
4. Create `_report.py` (P3)
5. Decompose `_flywheel_core.py` (P4)
6. Create `_http.py` (P5)

**Phase C — Suite Consolidation (uses contract + helpers):**
1. `ml_train_suite.py`
2. `model_suite.py`
3. `code_suite.py`
4. `git_suite.py`
5. `security_suite.py`
6. `skill_lifecycle_suite.py`
7. Smaller merges (oss_review, config)
8. Add SUITE_ROUTING shim to worker.py
9. Update idle_evolution.py
10. Deprecation cycle for old files

**Phase D — Polish:**
1. Add optional metadata (SUITE, TAGS, capabilities) to all skills
2. Integrate `health_check()` into smoke_test.py
3. Auto-generate marketplace manifests from skill metadata
4. Unify air-gap whitelist with REQUIRES_NETWORK
5. Add logging to 97 skills without it
