# Skill Plugin Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize the skill plugin contract, extract shared helpers, and consolidate 31 skills into 6 domain suites — reducing ~3,100 lines (11%) while fixing 27 double-serialization bugs and 8 broken runtime signatures.

**Architecture:** Four phases executed sequentially: (A) contract foundation with no structural changes, (B) shared helper extraction enabling suites, (C) suite consolidation with backward-compatible routing, (D) polish and metadata.

**Tech Stack:** Python 3.11+, importlib, inspect, logging, pathlib, pytest

**Spec:** `docs/superpowers/specs/2026-03-25-skill-plugin-restructure-design.md`

**Naming convention:** Suite files use the `_suite` suffix (e.g., `ml_train_suite.py`, `code_suite.py`). The spec has a contradictory sentence about dropping the suffix — ignore it; the SUITE_ROUTING table and all code references use the `_suite` suffix consistently.

---

## File Structure

### New Files
| File | Purpose |
|------|---------|
| `fleet/skills/_contract.py` | Skill contract validator (Phase A) |
| `fleet/skills/_knowledge.py` | Knowledge dir management + file save helpers (Phase B) |
| `fleet/skills/_llm_parse.py` | JSON extraction from LLM responses (Phase B) |
| `fleet/skills/_dispatch.py` | Action routing helper for suites (Phase B) |
| `fleet/skills/_report.py` | Markdown report builder (Phase B) |
| `fleet/skills/_http.py` | URL probing with timeout/latency (Phase B) |
| `fleet/skills/_flywheel_rubric.py` | Rubric definitions split from _flywheel_core (Phase B) |
| `fleet/skills/_flywheel_grading.py` | Grading functions split from _flywheel_core (Phase B) |
| `fleet/skills/_flywheel_audit.py` | Audit orchestration split from _flywheel_core (Phase B) |
| `fleet/skills/ml_train_suite.py` | ML training suite (Phase C) |
| `fleet/skills/model_suite.py` | Model management suite (Phase C) |
| `fleet/skills/code_suite.py` | Code review/quality suite (Phase C) |
| `fleet/skills/git_suite.py` | Git/GitHub suite (Phase C) |
| `fleet/skills/security_suite.py` | Security suite (Phase C) |
| `fleet/skills/skill_lifecycle_suite.py` | Skill lifecycle suite (Phase C) |

### Modified Files
| File | Changes |
|------|---------|
| `fleet/worker.py` | Result coercion (:735,:773), SUITE_ROUTING shim, SKILL_TIMEOUTS migration |
| `fleet/smoke_test.py` | Contract validation test integration |
| `fleet/providers.py` | SKILL_COMPLEXITY fallback to module attribute (:332-341) |
| `fleet/config.py` | AIR_GAP_SKILLS unification with REQUIRES_NETWORK (:9-14) |
| 27 skill files | Fix `return json.dumps(...)` → `return {...}` |
| 18 skill files | Fix run() signatures (log params, task/context naming) |
| 125 skill files | Add VERSION, COMPLEXITY constants (Phase A.3) |

---

## Phase A: Contract Foundation

### Task 1: Create `_contract.py` Validator

**Files:**
- Create: `fleet/skills/_contract.py`
- Modify: `fleet/smoke_test.py:32-48`

- [ ] **Step 1: Write `_contract.py`**

```python
# fleet/skills/_contract.py
"""Skill contract validator — checks module compliance without breaking anything."""
import inspect
import logging

log = logging.getLogger(__name__)

REQUIRED_CONSTANTS = ("SKILL_NAME", "DESCRIPTION")
OPTIONAL_CONSTANTS = {
    "VERSION": "0.0.0",
    "REQUIRES_NETWORK": False,
    "COMPLEXITY": "medium",  # matches providers.py fallback default
    "TIMEOUT": 600,
    "SUITE": "",
    "TAGS": [],
}


def validate_skill(module) -> list[str]:
    """Return list of contract violations (empty = compliant)."""
    warnings = []
    for const in REQUIRED_CONSTANTS:
        if not hasattr(module, const):
            warnings.append(f"missing {const}")

    if not hasattr(module, "VERSION"):
        warnings.append("missing VERSION (defaulting to 0.0.0)")

    if not hasattr(module, "run") or not callable(module.run):
        warnings.append("missing callable run()")
        return warnings

    sig = inspect.signature(module.run)
    params = list(sig.parameters.keys())
    if len(params) < 2:
        warnings.append(f"run() has {len(params)} params, need at least 2")
    if len(params) > 2:
        third = params[2]
        if sig.parameters[third].default is inspect.Parameter.empty:
            warnings.append(
                f"run() 3rd param '{third}' has no default — will crash at runtime"
            )
    if sig.return_annotation is str:
        warnings.append("run() -> str annotation: will cause double-serialization")

    return warnings


def get_metadata(module) -> dict:
    """Extract contract metadata from a skill module."""
    meta = {}
    for const in REQUIRED_CONSTANTS:
        meta[const.lower()] = getattr(module, const, None)
    for const, default in OPTIONAL_CONSTANTS.items():
        meta[const.lower()] = getattr(module, const, default)
    return meta
```

- [ ] **Step 2: Add contract validation test to smoke_test.py**

Add a new test function after `test_skill_imports()` at line 48:

```python
def test_skill_contracts():
    """1b. All skills comply with plugin contract."""
    from skills._contract import validate_skill
    skills_dir = FLEET_DIR / "skills"
    violations = {}
    count = 0
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_name = f.stem
        count += 1
        try:
            mod = importlib.import_module(f"skills.{mod_name}")
            warns = validate_skill(mod)
            if warns:
                violations[mod_name] = warns
        except Exception:
            pass  # import failures caught by test_skill_imports
    if violations:
        summary = "; ".join(f"{k}: {len(v)} issues" for k, v in list(violations.items())[:5])
        return False, f"{len(violations)}/{count} non-compliant: {summary}"
    return True, f"{count} skills contract-compliant"
```

Register it in the test runner list (around line 746).

- [ ] **Step 3: Run smoke tests to get baseline compliance report**

Run: `cd fleet && python smoke_test.py --fast`
Expected: The new test will FAIL (expected — 125 skills have no VERSION, 18 have bad signatures). This establishes the baseline.

- [ ] **Step 4: Commit**

```bash
git add fleet/skills/_contract.py fleet/smoke_test.py
git commit -m "feat: add skill contract validator (_contract.py) and smoke test"
```

---

### Task 2: Add Worker.py Result Coercion Safety Net

**Files:**
- Modify: `fleet/worker.py:735,773`

- [ ] **Step 1: Add result coercion helper function**

Add after the imports section (around line 23):

```python
def _coerce_result(result):
    """Coerce skill result to dict. Safety net for str-returning skills."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
            return {"status": "ok", "result": parsed}
        except (json.JSONDecodeError, ValueError):
            return {"status": "ok", "result": result}
    if result is None:
        return {"status": "ok"}
    return {"status": "ok", "result": result}
```

- [ ] **Step 2: Apply coercion before complete_task calls**

At line 735, change:
```python
db.complete_task(task['id'], json.dumps(result))
```
to:
```python
result = _coerce_result(result)
db.complete_task(task['id'], json.dumps(result))
```

Apply the same change at line 773.

- [ ] **Step 3: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All existing tests PASS (coercion is backward-compatible).

- [ ] **Step 4: Commit**

```bash
git add fleet/worker.py
git commit -m "fix: add result coercion safety net for str-returning skills"
```

---

### Task 3: Fix 27 str-Returning Skills

**Files:** 27 skill files (listed below)

Each fix is mechanical: change `return json.dumps({...})` to `return {...}` and update the type annotation from `-> str` to `-> dict`.

- [ ] **Step 1: Fix all 21 skills with `-> str` annotation**

Files to fix (change `-> str` to `-> dict` AND remove `json.dumps()` wrapper from returns):
1. `fleet/skills/claude_code.py:24`
2. `fleet/skills/dataset_synthesize.py:188`
3. `fleet/skills/db_encrypt.py:16`
4. `fleet/skills/db_migrate.py:15`
5. `fleet/skills/evolution_coordinator.py:16`
6. `fleet/skills/git_manager.py:31`
7. `fleet/skills/github_interact.py:227`
8. `fleet/skills/github_sync.py:21`
9. `fleet/skills/knowledge_prune.py:16`
10. `fleet/skills/ml_bridge.py:16`
11. `fleet/skills/model_manager.py:15`
12. `fleet/skills/model_recommend.py:26`
13. `fleet/skills/oom_prevent.py:52`
14. `fleet/skills/rag_compress.py:14`
15. `fleet/skills/refactor_verify.py:20`
16. `fleet/skills/research_loop.py:15`
17. `fleet/skills/secret_rotate.py:27`
18. `fleet/skills/service_manager.py:23`
19. `fleet/skills/stability_report.py:19`
20. `fleet/skills/swarm_consensus.py:13`
21. `fleet/skills/swarm_intelligence.py:15`

- [ ] **Step 2: Fix remaining 6 skills that use `json.dumps()` in returns without `-> str` annotation**

Files (change `return json.dumps(...)` to `return ...`):
22. `fleet/skills/ingest.py`
23. `fleet/skills/deploy_skill.py`
24. `fleet/skills/mqtt_inspect.py`
25. `fleet/skills/marathon_log.py`
26. `fleet/skills/code_refactor.py`
27. `fleet/skills/evaluate.py`

- [ ] **Step 3: Ensure every fixed skill returns `{"status": "ok"|"error", ...}`**

For each file, verify the return dict includes a `"status"` key. If missing, add it. Example transform:

```python
# Before:
return json.dumps({"branch": branch, "staged": staged})

# After:
return {"status": "ok", "branch": branch, "staged": staged}
```

**Important:** Many of these 27 skills also have `json.dumps()` returns inside internal helper functions whose values flow through to `run()`. Check ALL return paths, not just `run()` itself. Key offenders with multiple `json.dumps` in helpers:
- `claude_code.py` — 12+ handler functions
- `git_manager.py` — 7 action handlers (`_git_status`, `_git_diff`, etc.)
- `github_interact.py` — 6 action handlers
- `github_sync.py` — 5 action handlers
- `service_manager.py` — 6 action handlers

Each internal handler's `return json.dumps(...)` must also be converted to `return {...}`.

- [ ] **Step 4: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add fleet/skills/*.py
git commit -m "fix: convert 27 str-returning skills to return dict (eliminates double-serialization)"
```

---

### Task 4: Fix 18 Non-Standard run() Signatures

**Files:** 18 skill files

#### Part A: Fix 8 skills with required `log` param (broken at runtime)

Each fix: remove `log` param, add `log = logging.getLogger(SKILL_NAME)` at module level, update any internal `log.xxx()` calls that reference the param.

- [ ] **Step 1: Fix all 8 required-log skills**

Files:
1. `fleet/skills/auto_profile.py:59` — `run(payload, config, log)` → `run(payload, config)`
2. `fleet/skills/billing_ocr.py:22` — same
3. `fleet/skills/hardware_profiler.py:120` — same
4. `fleet/skills/memory_optimizer.py:45` — same
5. `fleet/skills/screenshot.py:30` — same
6. `fleet/skills/screenshot_diff.py:35` — same
7. `fleet/skills/speech_to_text.py:76` — same
8. `fleet/skills/token_optimizer.py:108` — same

For each file, add after the SKILL_NAME constant:
```python
import logging
log = logging.getLogger(SKILL_NAME)
```
Then change `def run(payload: dict, config: dict, log) -> dict:` to `def run(payload: dict, config: dict) -> dict:`.

Remove any `if log:` guards inside these files — `log` is now always available.

#### Part B: Fix 7 skills with `log=None`

- [ ] **Step 2: Fix all 7 log=None skills**

Files:
1. `fleet/skills/claude_efficiency.py:753`
2. `fleet/skills/clinical_review.py:696`
3. `fleet/skills/oss_review.py:15`
4. `fleet/skills/oss_review_swarm.py:26`
5. `fleet/skills/packet_optimizer.py:44`
6. `fleet/skills/quality_flywheel.py:17`
7. `fleet/skills/regression_detector.py:79`

Same fix: add module-level `log = logging.getLogger(SKILL_NAME)`, remove `log=None` param, update internal references.

#### Part C: Fix 3 skills with `task/context` naming

- [ ] **Step 3: Rename params in 3 skills**

Files:
1. `fleet/skills/config_validate.py:399` — `run(task, context)` → `run(payload, config)`
2. `fleet/skills/hitl_respond.py:11` — same
3. `fleet/skills/js_lint.py:253` — same

For `config_validate.py`, also fix the latent bug at line 412 where it does `payload = task.get("payload") or {}`. After renaming, this becomes `payload_inner = payload.get("payload") or {}` — but the real fix is to just use `payload` directly since worker.py already deserializes it.

- [ ] **Step 4: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS. Contract test should now show 18 fewer signature violations.

- [ ] **Step 5: Commit**

```bash
git add fleet/skills/*.py
git commit -m "fix: standardize 18 skill run() signatures to (payload, config) -> dict"
```

---

### Task 5: Fix 2 Raw sqlite3 Violations

**Files:**
- Modify: `fleet/skills/doc_freshness.py:50-61`
- Modify: `fleet/skills/_flywheel_core.py:525`

- [ ] **Step 1: Fix doc_freshness.py**

Replace the `sqlite3.connect()` call at line 50-61 with:
```python
import db
conn = db.get_conn()
tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()
```

- [ ] **Step 2: Fix _flywheel_core.py**

Replace the `sqlite3.connect()` call at line 525 with:
```python
import db
conn = db.get_conn()
```

- [ ] **Step 3: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add fleet/skills/doc_freshness.py fleet/skills/_flywheel_core.py
git commit -m "fix: replace raw sqlite3 with db.get_conn() in 2 skills"
```

---

### Task 6: Add VERSION + COMPLEXITY to All Skills

**Files:** All 125 skill files, `fleet/providers.py:332-341`

- [ ] **Step 1: Add VERSION = "1.0.0" to all skills missing it**

For each of the 125 skills, add `VERSION = "1.0.0"` after the existing DESCRIPTION constant. Skip if already present.

- [ ] **Step 2: Move COMPLEXITY from providers.py into each skill**

Reference the `SKILL_COMPLEXITY` dict at `fleet/providers.py:299-329`. For each skill listed there, add the corresponding `COMPLEXITY` constant:

```python
# Skills in "simple" tier (providers.py:300-306):
COMPLEXITY = "simple"   # flashcard, rag_query, summarize, ingest, etc.

# Skills in "medium" tier (providers.py:307-321):
COMPLEXITY = "medium"   # web_search, code_review, discuss, etc.

# Skills in "complex" tier (providers.py:322-328):
COMPLEXITY = "complex"  # plan_workload, lead_research, code_write, etc.
```

Skills NOT listed in `SKILL_COMPLEXITY` get `COMPLEXITY = "medium"` (the default).

- [ ] **Step 3: Update providers.py to read from module attribute**

Modify `_get_skill_complexity()` at line 332 to check the module first:

```python
def _get_skill_complexity(skill_name: str) -> str:
    """Get complexity tier. Reads from skill module, falls back to dict."""
    try:
        mod = importlib.import_module(f"skills.{skill_name}")
        if hasattr(mod, "COMPLEXITY"):
            return mod.COMPLEXITY
    except Exception:
        pass
    # Fallback to hardcoded dict during migration
    for tier, skills in SKILL_COMPLEXITY.items():
        if skill_name in skills:
            return tier
    return "medium"
```

- [ ] **Step 4: Run smoke tests after VERSION**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS.

- [ ] **Step 5: Commit VERSION changes**

```bash
git add fleet/skills/*.py
git commit -m "feat: add VERSION = 1.0.0 to all 125 skills"
```

- [ ] **Step 6: Run smoke tests after COMPLEXITY**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS. Contract test should now show 0 VERSION violations.

- [ ] **Step 7: Commit COMPLEXITY changes**

```bash
git add fleet/skills/*.py fleet/providers.py
git commit -m "feat: add COMPLEXITY constants to all skills, update providers.py to read from modules"
```

---

## Phase B: Shared Helper Extraction

### Task 7: Create `_knowledge.py` — Directory Management

**Files:**
- Create: `fleet/skills/_knowledge.py`

- [ ] **Step 1: Write `_knowledge.py`**

```python
# fleet/skills/_knowledge.py
"""Shared knowledge directory management and file-save helpers."""
from datetime import datetime
from pathlib import Path

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

- [ ] **Step 2: Verify import works**

Run: `cd fleet && python -c "from skills._knowledge import get_output_dir, save_report, FLEET_DIR; print(FLEET_DIR)"`
Expected: prints the fleet directory path.

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/_knowledge.py
git commit -m "feat: add _knowledge.py shared helper for dir management and file save"
```

---

### Task 8: Create `_llm_parse.py` — JSON Extraction

**Files:**
- Create: `fleet/skills/_llm_parse.py`

- [ ] **Step 1: Write `_llm_parse.py`**

```python
# fleet/skills/_llm_parse.py
"""Extract structured data from LLM text responses."""
import json
import re


def extract_json_object(text: str, required_key: str = None) -> dict | None:
    """Extract first JSON object from LLM response text.
    Tries: direct parse -> regex with required_key -> brace-matching fallback.
    """
    if not text:
        return None
    text = text.strip()
    # Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Regex with required key
    if required_key:
        pattern = r'\{[^{}]*"' + re.escape(required_key) + r'"[^{}]*\}'
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except (json.JSONDecodeError, TypeError):
                pass
    # Brace-matching fallback
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def extract_json_array(text: str) -> list | None:
    """Extract first JSON array from LLM response text."""
    if not text:
        return None
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def extract_verdict(text: str) -> dict:
    """Extract PASS/FAIL verdict dict from LLM review response.
    Returns: {"verdict": "PASS"|"FAIL", "critique": str, "confidence": float}
    """
    obj = extract_json_object(text, required_key="verdict")
    if obj and "verdict" in obj:
        obj["verdict"] = obj["verdict"].upper()
        obj.setdefault("confidence", 0.5)
        obj.setdefault("critique", "")
        return obj
    upper = (text or "").upper()
    verdict = "FAIL" if "FAIL" in upper else "PASS"
    return {"verdict": verdict, "critique": (text or "")[:500], "confidence": 0.3}
```

- [ ] **Step 2: Verify import works**

Run: `cd fleet && python -c "from skills._llm_parse import extract_json_object; print(extract_json_object('blah {\"key\": 1} blah'))"`
Expected: `{'key': 1}`

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/_llm_parse.py
git commit -m "feat: add _llm_parse.py shared helper for LLM JSON extraction"
```

---

### Task 9: Create `_dispatch.py` — Action Routing

**Files:**
- Create: `fleet/skills/_dispatch.py`

- [ ] **Step 1: Write `_dispatch.py`**

```python
# fleet/skills/_dispatch.py
"""Action routing helper for suite-style skills."""


def dispatch_action(payload: dict, config: dict, actions: dict,
                    default: str = None) -> dict:
    """Route to handler by payload['action'] key.

    Args:
        payload: Task payload (must contain 'action' key or default is used).
        config: Fleet configuration.
        actions: Dict mapping action names to handler functions.
                 Each handler signature: handler(payload, config) -> dict.
        default: Default action if payload has no 'action' key.
                 Falls back to first key in actions dict.

    Returns:
        Handler result dict, or error dict if action is unknown.
    """
    action = payload.get("action", default or next(iter(actions), None))
    if action is None:
        return {"status": "error", "error": "No action specified and no handlers registered"}
    handler = actions.get(action)
    if not handler:
        return {
            "status": "error",
            "error": f"Unknown action: {action}",
            "valid_actions": sorted(actions.keys()),
        }
    return handler(payload, config)
```

- [ ] **Step 2: Verify import works**

Run: `cd fleet && python -c "from skills._dispatch import dispatch_action; print(dispatch_action({}, {}, {'test': lambda p,c: {'status':'ok'}}, default='test'))"`
Expected: `{'status': 'ok'}`

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/_dispatch.py
git commit -m "feat: add _dispatch.py shared action routing helper"
```

---

### Task 10: Create `_report.py` — Markdown Report Builder

**Files:**
- Create: `fleet/skills/_report.py`

- [ ] **Step 1: Write `_report.py`**

```python
# fleet/skills/_report.py
"""Markdown report builder for skill output."""
from datetime import date


class ReportBuilder:
    """Fluent builder for markdown reports."""

    def __init__(self, title: str, include_date: bool = True):
        self._lines: list[str] = []
        date_str = f" -- {date.today().isoformat()}" if include_date else ""
        self._lines.append(f"# {title}{date_str}")
        self._lines.append("")

    def metadata(self, **kwargs) -> "ReportBuilder":
        """Add a metadata line: **Key:** value | **Key2:** value2."""
        parts = [f"**{k}:** {v}" for k, v in kwargs.items()]
        self._lines.append(" | ".join(parts))
        self._lines.append("")
        return self

    def section(self, heading: str, body: str = "", level: int = 2) -> "ReportBuilder":
        """Add a section heading with optional body text."""
        prefix = "#" * level
        self._lines.append(f"{prefix} {heading}")
        self._lines.append("")
        if body:
            self._lines.append(body)
            self._lines.append("")
        return self

    def table(self, headers: list[str], rows: list[list]) -> "ReportBuilder":
        """Add a markdown table."""
        self._lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        self._lines.append("|" + "|".join("---" for _ in headers) + "|")
        for row in rows:
            self._lines.append("| " + " | ".join(str(c) for c in row) + " |")
        self._lines.append("")
        return self

    def findings(self, items: list[dict], severity_key: str = "severity",
                 detail_key: str = "detail") -> "ReportBuilder":
        """Add a findings list: - [SEVERITY] detail."""
        for item in items:
            sev = item.get(severity_key, "NOTE")
            det = item.get(detail_key, "")
            self._lines.append(f"- [{sev}] {det}")
        self._lines.append("")
        return self

    def line(self, text: str) -> "ReportBuilder":
        """Add a raw line."""
        self._lines.append(text)
        return self

    def blank(self) -> "ReportBuilder":
        """Add a blank line."""
        self._lines.append("")
        return self

    def build(self) -> str:
        """Build the final markdown string."""
        return "\n".join(self._lines)
```

- [ ] **Step 2: Verify import works**

Run: `cd fleet && python -c "from skills._report import ReportBuilder; r = ReportBuilder('Test'); print(r.section('Sec1').build()[:30])"`
Expected: prints first 30 chars of a markdown report.

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/_report.py
git commit -m "feat: add _report.py markdown report builder helper"
```

---

### Task 11: Create `_http.py` — URL Probing

**Files:**
- Create: `fleet/skills/_http.py`

- [ ] **Step 1: Write `_http.py`**

```python
# fleet/skills/_http.py
"""HTTP probing helpers with timeout and latency tracking."""
import json
import time
import urllib.error
import urllib.request


def probe_url(url: str, method: str = "GET", timeout: int = 10,
              headers: dict = None) -> dict:
    """Probe a URL, return {status, code, latency_ms, body, error}."""
    req = urllib.request.Request(url, method=method, headers=headers or {})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.monotonic() - start) * 1000
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "status": "ok",
                "code": resp.status,
                "latency_ms": round(elapsed, 1),
                "body": body,
            }
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "http_error",
            "code": e.code,
            "latency_ms": round(elapsed, 1),
            "error": str(e.reason),
        }
    except urllib.error.URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "unreachable",
            "code": 0,
            "latency_ms": round(elapsed, 1),
            "error": str(e.reason),
        }
    except Exception as e:
        return {"status": "error", "code": 0, "latency_ms": 0, "error": str(e)}


def fetch_json(url: str, timeout: int = 10, headers: dict = None) -> dict | None:
    """Fetch and parse JSON from a URL. Returns None on failure."""
    result = probe_url(url, timeout=timeout, headers=headers)
    if result["status"] == "ok":
        try:
            return json.loads(result["body"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None
```

- [ ] **Step 2: Verify import works**

Run: `cd fleet && python -c "from skills._http import probe_url, fetch_json; print('imported ok')"`
Expected: `imported ok`

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/_http.py
git commit -m "feat: add _http.py URL probing helper with latency tracking"
```

---

### Task 12: Decompose `_flywheel_core.py`

**Files:**
- Create: `fleet/skills/_flywheel_rubric.py`
- Create: `fleet/skills/_flywheel_grading.py`
- Create: `fleet/skills/_flywheel_audit.py`
- Modify: `fleet/skills/_flywheel_core.py` (reduce to re-exports)
- Modify: `fleet/skills/quality_flywheel.py` (the only importer)

- [ ] **Step 1: Read `_flywheel_core.py` to identify exact split points**

Read `fleet/skills/_flywheel_core.py` in full. Identify the three sections:
- Lines 1-72: Rubric definitions, `score_to_grade()` → `_flywheel_rubric.py`
- Lines 86-559: All grading functions → `_flywheel_grading.py`
- Lines 560+: Audit orchestration → `_flywheel_audit.py`

- [ ] **Step 2: Create `_flywheel_rubric.py`** with rubric defs and `score_to_grade()`

- [ ] **Step 3: Create `_flywheel_grading.py`** with all `grade_*` functions. Import `RUBRIC` from `_flywheel_rubric`.

- [ ] **Step 4: Create `_flywheel_audit.py`** with `run_full_audit()`, `run_evidence_audit()`, `format_audit_report()`, `discover_novel_patterns()`. Import from rubric and grading modules.

- [ ] **Step 5: Update `_flywheel_core.py` to re-export everything**

```python
# _flywheel_core.py — backward compatibility re-exports
from skills._flywheel_rubric import *    # noqa: F401,F403
from skills._flywheel_grading import *   # noqa: F401,F403
from skills._flywheel_audit import *     # noqa: F401,F403
```

This ensures `quality_flywheel.py` and any other importer continues to work.

- [ ] **Step 6: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add fleet/skills/_flywheel_rubric.py fleet/skills/_flywheel_grading.py fleet/skills/_flywheel_audit.py fleet/skills/_flywheel_core.py
git commit -m "refactor: decompose _flywheel_core.py (891 lines) into 3 focused modules"
```

---

## Phase C: Suite Consolidation

> **Important:** Each suite task follows the same pattern:
> 1. Read all source skills to understand their full logic
> 2. Create the suite file using `_dispatch.py`
> 3. Add backward-compatible entries to `SUITE_ROUTING`
> 4. Run smoke tests
> 5. Commit

### Task 13: Create `ml_train_suite.py`

**Files:**
- Create: `fleet/skills/ml_train_suite.py`
- Read: `fleet/skills/embedding_train.py`, `reranker_train.py`, `router_retrain.py`, `scaler_train.py`

- [ ] **Step 1: Read all 4 source skills in full**

Understand the ExperimentFramework boilerplate, train_fn/eval_fn for each type, and the pending-approval check pattern.

- [ ] **Step 2: Write `ml_train_suite.py`**

Structure:
```python
SKILL_NAME = "ml_train_suite"
DESCRIPTION = "Train ML models: embeddings, reranker, router, scaler"
VERSION = "1.0.0"
REQUIRES_NETWORK = False
COMPLEXITY = "complex"
SUITE = "ml"

import logging
log = logging.getLogger(SKILL_NAME)

def run(payload: dict, config: dict) -> dict:
    from skills._dispatch import dispatch_action
    return dispatch_action(payload, config, {
        "embedding": _train_embedding,
        "reranker": _train_reranker,
        "router": _train_router,
        "scaler": _train_scaler,
    }, default="embedding")

def _experiment_lifecycle(agent, exp_type, hypothesis, config, train_fn, eval_fn, exp_config=None):
    """Shared lifecycle: propose -> approve check -> run -> eval -> return."""
    # ... shared boilerplate from all 4 skills ...

def _train_embedding(payload, config):
    # ... embedding-specific train_fn and eval_fn ...
    return _experiment_lifecycle(...)

# ... similar for reranker, router, scaler ...
```

- [ ] **Step 3: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS (new suite imports cleanly, old skills still exist).

- [ ] **Step 4: Commit**

```bash
git add fleet/skills/ml_train_suite.py
git commit -m "feat: create ml_train_suite.py consolidating 4 ML training skills"
```

---

### Task 14: Create `model_suite.py`

**Files:**
- Create: `fleet/skills/model_suite.py`
- Read: `fleet/skills/model_manager.py`, `hardware_profiler.py`, `auto_profile.py`

- [ ] **Step 1: Read all 3 source skills in full**

Key: identify the 3 `detect_hardware()` implementations and create 1 canonical version.

- [ ] **Step 2: Write `model_suite.py`**

Subactions: `check`, `install`, `install_one`, `profiles`, `apply_profile`, `update_check`, `debug`, `detect`, `recommend`, `apply`, `auto_detect`, `auto_generate`, `auto_recommend`

Key shared function: `_detect_hardware() -> dict` — one canonical version replacing 3.

- [ ] **Step 3: Run smoke tests and commit**

```bash
git add fleet/skills/model_suite.py
git commit -m "feat: create model_suite.py consolidating 3 model management skills"
```

---

### Task 15: Create `code_suite.py`

**Files:**
- Create: `fleet/skills/code_suite.py`
- Read: `fleet/skills/code_review.py`, `code_quality.py`, `code_refactor.py`, `code_discuss.py`, `evaluate.py`, `pair_program.py`
- Modify: `fleet/skills/fma_review.py` (slim down, delegate to code_suite)

- [ ] **Step 1: Read all 6 source skills + fma_review.py + code_write_review.py**

Key dedup targets: `PERSPECTIVE_FOCUS` (3 copies), `_pick_file()` (2 copies), discussion thread SQL (2 copies).

- [ ] **Step 2: Write `code_suite.py`**

Subactions: `review`, `quality`, `refactor`, `discuss`, `evaluate`, `pair`

Shared core:
```python
PERSPECTIVES = { "coder_1": "software architect", ... }
PERSPECTIVE_FOCUS = { ... }  # one canonical copy

def _pick_review_file(requested, base_dir, reviews_dir) -> Path | None: ...
def _load_discussion(topic) -> list[dict]: ...
def _read_source(path, max_lines=400) -> str: ...
```

- [ ] **Step 3: Slim down `fma_review.py` to delegate to code_suite**

`fma_review.py` should import `PERSPECTIVE_FOCUS` and `_load_discussion` from `code_suite` instead of defining its own copies. Target: ~80 lines.

- [ ] **Step 4: Run smoke tests and commit**

```bash
git add fleet/skills/code_suite.py fleet/skills/fma_review.py
git commit -m "feat: create code_suite.py consolidating 6 code review/quality skills"
```

---

### Task 16: Create `git_suite.py`

**Files:**
- Create: `fleet/skills/git_suite.py`
- Read: `fleet/skills/git_manager.py`, `github_sync.py`, `github_interact.py`

- [ ] **Step 1: Read all 3 source skills**

Key dedup: `_run_git()` (2 copies), GitHub auth headers (2 copies).

- [ ] **Step 2: Write `git_suite.py`**

Subactions: `status`, `diff`, `log`, `commit`, `stash`, `checkout`, `auth`, `sync`, `clone`, `push`, `backup`, `list_issues`, `create_issue`, `comment_issue`, `close_issue`, `list_prs`, `create_pr`

Shared core:
```python
def _run_git(args, cwd=None, timeout=30) -> dict: ...  # one canonical version
def _github_api(method, path, body=None) -> dict: ...
def _github_headers() -> dict: ...
```

- [ ] **Step 3: Run smoke tests and commit**

```bash
git add fleet/skills/git_suite.py
git commit -m "feat: create git_suite.py consolidating 3 git/GitHub skills"
```

---

### Task 17: Create `security_suite.py`

**Files:**
- Create: `fleet/skills/security_suite.py`
- Read: `fleet/skills/security_audit.py`, `security_review.py`, `security_apply.py`, `pen_test.py`, `cve_watch.py`, `sql_review.py`

- [ ] **Step 1: Read all 6 source skills**

Key dedup: advisory creation (2 copies), `db.post_message()` (3 copies), severity constants (2 copies).

- [ ] **Step 2: Write `security_suite.py`**

Subactions: `audit`, `code_scan`, `apply`, `pentest`, `cve`, `sql`

Shared core:
```python
SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

def _create_advisory(findings, scope, config) -> dict: ...
def _save_advisory(advisory, pending_dir) -> Path: ...
def _notify_lead(advisory_id, counts, summary) -> None: ...
def _build_severity_report(findings, title) -> str: ...
```

- [ ] **Step 3: Regression-verify advisory creation**

Create a test payload and verify the `audit` subaction produces output identical in structure to the old `security_audit.py`. Key checks:
- Advisory JSON written to `knowledge/pending/` with correct schema
- `db.post_message()` called with lead notification
- Severity ordering matches `SEVERITY_ORDER` constant

Run: `cd fleet && python -c "from skills.security_suite import run; r = run({'action': 'audit', 'scope': 'test'}, {}); print(r.get('status'))"`
Expected: `ok` (or `error` with meaningful message if no files to audit)

- [ ] **Step 4: Run smoke tests and commit**

```bash
git add fleet/skills/security_suite.py
git commit -m "feat: create security_suite.py consolidating 6 security skills"
```

---

### Task 18: Create `skill_lifecycle_suite.py`

**Files:**
- Create: `fleet/skills/skill_lifecycle_suite.py`
- Read: `fleet/skills/skill_draft.py`, `skill_test.py`, `skill_evolve.py`, `skill_promote.py`, `deploy_skill.py`

- [ ] **Step 1: Read all 5 source skills**

Key dedup: `_find_draft()` (3 copies), `_extract_code()` (3 copies), module validation.

- [ ] **Step 2: Write `skill_lifecycle_suite.py`**

Subactions: `draft`, `test`, `evolve`, `promote`, `deploy`

Shared core:
```python
DRAFTS_DIR = FLEET_DIR / "knowledge" / "code_drafts"

def _find_draft(name_or_path) -> Path | None: ...
def _extract_code_block(llm_response) -> str: ...
def _validate_skill_module(path) -> tuple[bool, str]: ...
def _draft_filename(skill_name, verb, agent) -> str: ...
```

**Important:** Preserve the multi-stage gate semantics. `promote` and `deploy` are separate gate checks for safety.

- [ ] **Step 3: Regression-verify gate semantics**

The skill lifecycle pipeline has safety gates — `promote` requires prior `test` pass, `deploy` requires prior `promote`. Verify these checks are preserved:
- `promote` subaction rejects drafts without a passing test result
- `deploy` subaction rejects skills without prior promotion
- `_validate_skill_module()` correctly catches invalid skill files (missing SKILL_NAME, no run())

Run: `cd fleet && python -c "from skills.skill_lifecycle_suite import run; r = run({'action': 'promote', 'skill_name': 'nonexistent'}, {}); print(r.get('status'), r.get('error', '')[:50])"`
Expected: `error` with a meaningful message about missing draft/test

- [ ] **Step 4: Run smoke tests and commit**

```bash
git add fleet/skills/skill_lifecycle_suite.py
git commit -m "feat: create skill_lifecycle_suite.py consolidating 5 lifecycle skills"
```

---

### Task 19: Smaller Merges

**Files:**
- Modify: `fleet/skills/oss_review.py` (add swarm mode)
- Modify: `fleet/skills/config_validate.py` (add drift detection)

- [ ] **Step 1: Merge oss_review_swarm into oss_review as a mode flag**

Add `swarm: bool` payload flag to `oss_review.py`. When `swarm=True`, use the multi-agent review flow from `oss_review_swarm.py`. Import shared core from `_oss_core.py`.

- [ ] **Step 2: Merge config_drift_detect into config_validate**

Add `action: "drift"` subaction to `config_validate.py` using the drift detection logic from `config_drift_detect.py`.

- [ ] **Step 3: Run smoke tests and commit**

```bash
git add fleet/skills/oss_review.py fleet/skills/config_validate.py
git commit -m "feat: merge oss_review_swarm and config_drift_detect into parent skills"
```

---

### Task 20: Add SUITE_ROUTING to Worker.py

**Files:**
- Modify: `fleet/worker.py`
- Modify: `fleet/fleet.toml` (add `suite_routing_enabled` flag)

- [ ] **Step 1: Add SUITE_ROUTING dict to worker.py**

Add after the SKILL_TIMEOUTS dict (around line 91):

```python
SUITE_ROUTING = {
    "code_review":      ("code_suite",              "review"),
    "code_quality":     ("code_suite",              "quality"),
    "code_refactor":    ("code_suite",              "refactor"),
    "code_discuss":     ("code_suite",              "discuss"),
    "evaluate":         ("code_suite",              "evaluate"),
    "pair_program":     ("code_suite",              "pair"),
    "embedding_train":  ("ml_train_suite",          "embedding"),
    "reranker_train":   ("ml_train_suite",          "reranker"),
    "router_retrain":   ("ml_train_suite",          "router"),
    "scaler_train":     ("ml_train_suite",          "scaler"),
    "security_audit":   ("security_suite",          "audit"),
    "security_review":  ("security_suite",          "code_scan"),
    "security_apply":   ("security_suite",          "apply"),
    "pen_test":         ("security_suite",          "pentest"),
    "cve_watch":        ("security_suite",          "cve"),
    "sql_review":       ("security_suite",          "sql"),
    "git_manager":      ("git_suite",               "status"),
    "github_sync":      ("git_suite",               "sync"),
    "github_interact":  ("git_suite",               "list_issues"),
    "model_manager":    ("model_suite",             "check"),
    "hardware_profiler":("model_suite",             "detect"),
    "auto_profile":     ("model_suite",             "auto_generate"),
    "skill_draft":      ("skill_lifecycle_suite",   "draft"),
    "skill_test":       ("skill_lifecycle_suite",   "test"),
    "skill_evolve":     ("skill_lifecycle_suite",   "evolve"),
    "skill_promote":    ("skill_lifecycle_suite",   "promote"),
    "deploy_skill":     ("skill_lifecycle_suite",   "deploy"),
}
```

- [ ] **Step 2: Add routing logic at the TOP of run_skill()**

**Critical:** The routing shim must go at the very top of `run_skill()` (line 190, immediately after the function definition), BEFORE the air-gap check (line 191) and BEFORE `_is_valid_skill()` (line 231). If placed after these checks, air-gap mode would reject suite names not in the `AIR_GAP_SKILLS` set, and `_is_valid_skill()` would reject the original skill names after deprecation renames.

```python
def run_skill(skill_name, payload, config, log):
    # Suite routing shim — backward compatibility for old task types
    if config.get("fleet", {}).get("suite_routing_enabled", True):
        if skill_name in SUITE_ROUTING:
            suite_module, default_action = SUITE_ROUTING[skill_name]
            payload.setdefault("action", default_action)
            skill_name = suite_module

    # ... existing air-gap check, validation, etc. ...
```

- [ ] **Step 2b: Update AIR_GAP_SKILLS to include suite names**

In `fleet/config.py:9-14`, add suite names to the set:

```python
AIR_GAP_SKILLS = {
    "code_review", "code_discuss", "code_index", "code_quality",
    "summarize", "discuss", "flashcard", "analyze_results",
    "rag_index", "rag_query", "benchmark", "ingest",
    "security_review", "security_audit",
    # Suites (non-network subactions are safe in air-gap)
    "code_suite", "security_suite", "ml_train_suite",
    "model_suite", "skill_lifecycle_suite",
}
```

Note: `git_suite` is excluded since GitHub operations require network.

- [ ] **Step 3: Add config flag to fleet.toml**

Add under `[fleet]`:
```toml
suite_routing_enabled = true   # set false to bypass suite routing (rollback)
```

- [ ] **Step 4: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS. Old task types route to suites transparently.

- [ ] **Step 5: Commit**

```bash
git add fleet/worker.py fleet/fleet.toml
git commit -m "feat: add SUITE_ROUTING shim for backward-compatible suite dispatch"
```

---

### Task 21: Deprecation Cycle

**Files:**
- Rename: 31 old skill files to `_deprecated_<name>.py`
- Modify: `fleet/worker.py:27` (IDLE_SKILLS list)

- [ ] **Step 1: Rename merged skill files**

For each skill that was merged into a suite, rename the original:

```bash
cd fleet/skills
# ML Training
mv embedding_train.py _deprecated_embedding_train.py
mv reranker_train.py _deprecated_reranker_train.py
mv router_retrain.py _deprecated_router_retrain.py
mv scaler_train.py _deprecated_scaler_train.py
# Model Management
mv model_manager.py _deprecated_model_manager.py
mv hardware_profiler.py _deprecated_hardware_profiler.py
mv auto_profile.py _deprecated_auto_profile.py
# Code Suite
mv code_review.py _deprecated_code_review.py
mv code_quality.py _deprecated_code_quality.py
mv code_refactor.py _deprecated_code_refactor.py
mv code_discuss.py _deprecated_code_discuss.py
mv evaluate.py _deprecated_evaluate.py
mv pair_program.py _deprecated_pair_program.py
# Git/GitHub
mv git_manager.py _deprecated_git_manager.py
mv github_sync.py _deprecated_github_sync.py
mv github_interact.py _deprecated_github_interact.py
# Security
mv security_audit.py _deprecated_security_audit.py
mv security_review.py _deprecated_security_review.py
mv security_apply.py _deprecated_security_apply.py
mv pen_test.py _deprecated_pen_test.py
mv cve_watch.py _deprecated_cve_watch.py
mv sql_review.py _deprecated_sql_review.py
# Skill Lifecycle
mv skill_draft.py _deprecated_skill_draft.py
mv skill_test.py _deprecated_skill_test.py
mv skill_evolve.py _deprecated_skill_evolve.py
mv skill_promote.py _deprecated_skill_promote.py
mv deploy_skill.py _deprecated_deploy_skill.py
# Smaller merges
mv oss_review_swarm.py _deprecated_oss_review_swarm.py
mv config_drift_detect.py _deprecated_config_drift_detect.py
```

Note: `_deprecated_*` files start with `_` so `_is_valid_skill()` automatically excludes them from dispatch. They remain importable for rollback.

- [ ] **Step 2: Update IDLE_SKILLS in worker.py:27**

`IDLE_SKILLS` currently contains `["skill_evolve", "skill_test", "code_quality", "benchmark"]`. After deprecation:
- `skill_evolve` and `skill_test` are now `_deprecated_*` — update to suite equivalents or route through SUITE_ROUTING (which handles this automatically if the names stay the same in IDLE_SKILLS, since SUITE_ROUTING resolves them before dispatch). However, `_is_valid_skill()` would reject them since their files are renamed. **Fix:** Update the list to use suite skill names:

```python
IDLE_SKILLS = ["skill_lifecycle_suite", "code_suite", "benchmark"]
```

- [ ] **Step 3: Update idle_evolution.py if it hardcodes skill names**

`idle_evolution.py` pulls skill names dynamically from DB (`SELECT DISTINCT type FROM tasks WHERE status='DONE'`), so no code change needed there. However, old skill names in the DB will persist. The SUITE_ROUTING shim in `run_skill()` handles the translation. Verify this by confirming the routing shim runs before `_is_valid_skill()`.

- [ ] **Step 4: Restart fleet after deprecation rename**

The `_valid_skills` cache in `worker.py:94-101` is module-level and only populated on first call. Running workers will have stale caches with old filenames. A fleet restart is required after renaming:

```bash
python fleet/lead_client.py stop
python fleet/supervisor.py &
```

- [ ] **Step 5: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS. Deprecated files are skipped by the `_` prefix filter. Suites handle all routing.

- [ ] **Step 4: Commit**

```bash
git add fleet/skills/_deprecated_*.py
git commit -m "chore: deprecate 29 skill files merged into 6 suites (prefixed with _deprecated_)"
```

---

## Phase D: Polish

### Task 22: Add SUITE and TAGS Metadata

**Files:** All active skill files (suites + standalone)

- [ ] **Step 1: Add SUITE constant to standalone skills**

Suite files already define their own `SUITE` constant. This step adds `SUITE` to the ~94 standalone skills (those not merged into a suite). Use descriptive domains: `"research"`, `"ops"`, `"content"`, `"rag"`, `"swarm"`, or `""` for truly standalone skills.

- [ ] **Step 2: Add TAGS to skills where useful**

Focus on skills frequently dispatched. Example:
```python
TAGS = ["education", "flashcards", "knowledge"]  # flashcard.py
TAGS = ["rag", "search", "retrieval"]             # rag_query.py
```

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/*.py
git commit -m "feat: add SUITE and TAGS metadata to all skills"
```

---

### Task 23: Integrate health_check() into Smoke Tests

**Files:**
- Modify: `fleet/smoke_test.py`

- [ ] **Step 1: Add health_check test**

```python
def test_skill_health_checks():
    """All skills with health_check() report healthy."""
    skills_dir = FLEET_DIR / "skills"
    checked = 0
    failures = []
    cfg = load_config()
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod = importlib.import_module(f"skills.{f.stem}")
        if hasattr(mod, "health_check"):
            checked += 1
            try:
                result = mod.health_check(cfg)
                if not result.get("healthy"):
                    failures.append(f"{f.stem}: {result.get('detail', 'unhealthy')}")
            except Exception as e:
                failures.append(f"{f.stem}: {e}")
    if failures:
        return False, f"{len(failures)}/{checked} unhealthy: {'; '.join(failures[:3])}"
    return True, f"{checked} health checks passed"
```

- [ ] **Step 2: Commit**

```bash
git add fleet/smoke_test.py
git commit -m "feat: integrate health_check() into smoke test suite"
```

---

### Task 24: Unify Air-Gap Whitelist

**Files:**
- Modify: `fleet/config.py:9-14`
- Modify: `fleet/worker.py` (air-gap check in run_skill)

- [ ] **Step 1: Update air-gap check to use REQUIRES_NETWORK**

In `worker.py` `run_skill()` (around line 191), replace the `AIR_GAP_SKILLS` check with:

```python
if is_air_gap(config):
    mod = importlib.import_module(f"skills.{skill_name}")
    if getattr(mod, "REQUIRES_NETWORK", False):
        return {"status": "error", "error": f"Skill {skill_name} requires network (air-gap mode)"}
```

This replaces the hardcoded `AIR_GAP_SKILLS` set with the per-skill `REQUIRES_NETWORK` flag.

- [ ] **Step 2: Keep AIR_GAP_SKILLS as fallback during transition**

Don't delete `AIR_GAP_SKILLS` yet — use it as a secondary check for skills that haven't been updated.

- [ ] **Step 3: Commit**

```bash
git add fleet/config.py fleet/worker.py
git commit -m "feat: unify air-gap whitelist with per-skill REQUIRES_NETWORK flag"
```

---

### Task 25: Auto-Generate Marketplace Manifests

**Files:**
- Modify: `fleet/marketplace.py`

- [ ] **Step 1: Add manifest auto-generation function**

Add to `fleet/marketplace.py`:

```python
def build_manifest_from_skill(skill_name: str) -> dict:
    """Auto-generate a marketplace manifest from a skill module's contract metadata."""
    import importlib
    from skills._contract import get_metadata
    mod = importlib.import_module(f"skills.{skill_name}")
    meta = get_metadata(mod)
    return {
        "name": meta["skill_name"],
        "description": meta["description"],
        "version": meta.get("version", "0.0.0"),
        "category": meta.get("suite", "general"),
        "tags": meta.get("tags", []),
        "requires_network": meta.get("requires_network", False),
        "complexity": meta.get("complexity", "medium"),
        "skill_names": [meta["skill_name"]],
    }
```

- [ ] **Step 2: Commit**

```bash
git add fleet/marketplace.py
git commit -m "feat: auto-generate marketplace manifests from skill contract metadata"
```

---

### Task 26: Add Logging to Remaining Skills

**Files:** ~97 skill files without logging

- [ ] **Step 1: Add module-level logger to all skills without one**

For each skill missing logging, add after the metadata constants:
```python
import logging
log = logging.getLogger(SKILL_NAME)
```

- [ ] **Step 2: Run smoke tests**

Run: `cd fleet && python smoke_test.py --fast`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/*.py
git commit -m "feat: add module-level logging to 97 skills"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `python fleet/smoke_test.py --fast` — all tests pass
- [ ] Contract validator reports 0 violations
- [ ] All 6 suites import and dispatch correctly
- [ ] SUITE_ROUTING routes old task types to suites
- [ ] `suite_routing_enabled = false` in fleet.toml disables routing (rollback works)
- [ ] Deprecated files are excluded from `_is_valid_skill()` (start with `_`)
- [ ] No raw `sqlite3.connect()` in skill files (except legitimate: account_review, db_encrypt)
- [ ] No `return json.dumps(...)` in any skill
- [ ] No `def run(... log)` without default value in any skill
- [ ] All skills have VERSION and COMPLEXITY constants
- [ ] `build_manifest_from_skill()` in marketplace.py works for all active skills
- [ ] Fleet restarted after deprecation rename (stale `_valid_skills` cache cleared)
