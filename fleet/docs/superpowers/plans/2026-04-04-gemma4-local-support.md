# Gemma 4 Local Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all four Gemma 4 instruct variants into BigEd's local Ollama infrastructure with a benchmark harness, KV-cache optimization, memory safety, and one-line swap-to-default capability.

**Architecture:** Extend the existing fleet.toml config, Ollama provider layer, and hw_supervisor with Gemma 4 variant metadata. Add a benchmark skill + CLI + DB table + dashboard endpoint. Supervisor already injects Ollama env vars — extend with KV-cache settings. All changes follow existing patterns.

**Tech Stack:** Python 3.11+, Flask, httpx/urllib, SQLite (WAL mode), Ollama HTTP API, psutil, SSE

**Spec:** `docs/superpowers/specs/2026-04-03-gemma4-local-support-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet.toml` | Modify (lines 67-75, 129-141, 167-177) | Add `[models.gemma4.variants]` metadata, update `[ollama.optimization]` defaults |
| `db.py` | Modify (lines 50-172 SCHEMA) | Add `benchmarks` table to schema |
| `skills/model_suite.py` | Modify (lines 301-375) | Make `_pull_model`, `_get_installed` importable; add `ensure_model_available()` |
| `providers.py` | Modify (lines 911-920) | Pass `num_gpu` in Ollama request options |
| `skills/_models.py` | Modify (lines 113-119) | Add context overflow check with 1.3x margin + variant context_length lookup |
| `hw_supervisor.py` | Modify (lines 1179-1180, 73-117) | Populate `_model_sizes` from fleet.toml variants; add `MEMORY_PRESSURE` flag |
| `process_manager.py` | Modify (lines 69-128) | Ensure KV-cache type and flash attention read from fleet.toml `[ollama.optimization]` |
| `skills/benchmark_model.py` | Create | Benchmark execution skill |
| `fleet/benchmarks/prompts/coding.json` | Create | Benchmark prompt set — coding |
| `fleet/benchmarks/prompts/analysis.json` | Create | Benchmark prompt set — analysis |
| `fleet/benchmarks/prompts/summarization.json` | Create | Benchmark prompt set — summarization |
| `fleet/benchmarks/prompts/instruction_following.json` | Create | Benchmark prompt set — instruction following |
| `lead_client.py` | Modify | Add `cmd_benchmark()` CLI command |
| `dashboard.py` | Modify | Add `GET /api/benchmarks/compare` endpoint |
| `sse_blueprint.py` | Modify | Add `MEMORY_PRESSURE` event type |
| `tests/test_benchmark.py` | Create | All benchmark + Gemma 4 integration tests |

---

## Task 1: Fleet.toml — Gemma 4 Variant Metadata

**Files:**
- Modify: `fleet.toml:67-75` (models section), `fleet.toml:129-141` (ollama.optimization), `fleet.toml:167-177` (tiers)

- [ ] **Step 1: Add Gemma 4 variant metadata to fleet.toml**

After the existing `[models.tiers]` section (~line 177), add:

```toml
# ── Gemma 4 variant reference data (VRAM estimates, offload, context) ────────
[models.gemma4.variants.e2b]
vram_estimate_gb = 4
num_gpu_layers = -1
context_length = 8192

[models.gemma4.variants.e4b]
vram_estimate_gb = 7
num_gpu_layers = -1
context_length = 8192

[models.gemma4.variants."26b-a4b"]
vram_estimate_gb = 16
num_gpu_layers = -1
context_length = 8192

[models.gemma4.variants."31b"]
vram_estimate_gb = 20
num_gpu_layers = 24
context_length = 8192
```

- [ ] **Step 2: Verify `[ollama.optimization]` already has flash_attention and kv_cache_type**

Check that `fleet.toml` lines 129-141 already contain `flash_attention` and `kv_cache_type` keys. If `kv_cache_type` defaults to `"auto"`, change it to `"q8_0"` as the recommended default for Gemma 4 workloads:

```toml
kv_cache_type = "q8_0"
```

- [ ] **Step 3: Commit**

```bash
git add fleet.toml
git commit -m "feat(config): add Gemma 4 variant metadata + q8_0 KV-cache default"
```

---

## Task 2: Database — Benchmarks Table

**Files:**
- Modify: `db.py:50-172` (SCHEMA string)
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_benchmark.py`:

```python
"""Tests for Gemma 4 benchmark infrastructure."""
import os
import tempfile
import unittest

# Point at a temp DB for test isolation
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FLEET_TEST_DB"] = _tmp.name

import db


class TestBenchmarksTable(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_benchmarks_table_exists(self):
        with db.get_conn() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmarks'"
            )
            self.assertIsNotNone(cur.fetchone(), "benchmarks table should exist")

    def test_insert_and_query_benchmark(self):
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO benchmarks
                   (model, variant, metric, value, unit, judge_model, kv_cache_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("gemma4:e4b", "e4b", "tokens_per_sec", 42.5, "tok/s", "claude-haiku-4-5", "q8_0"),
            )
            row = conn.execute("SELECT * FROM benchmarks WHERE model = ?", ("gemma4:e4b",)).fetchone()
            self.assertIsNotNone(row)

    def tearDown(self):
        try:
            os.unlink(os.environ["FLEET_TEST_DB"])
        except Exception:
            pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarksTable -v`
Expected: FAIL — "no such table: benchmarks"

- [ ] **Step 3: Add benchmarks table to SCHEMA in db.py**

In `db.py`, inside the `SCHEMA` string (after the last CREATE TABLE, before the closing triple-quote around line 172), add:

```sql
CREATE TABLE IF NOT EXISTS benchmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model       TEXT    NOT NULL,
    variant     TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL DEFAULT '',
    judge_model TEXT    NOT NULL DEFAULT '',
    kv_cache_type TEXT  NOT NULL DEFAULT 'f16',
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_benchmarks_model ON benchmarks(model);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarksTable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_benchmark.py
git commit -m "feat(db): add benchmarks table for Gemma 4 model evaluation"
```

---

## Task 3: Model Suite — ensure_model_available()

**Files:**
- Modify: `skills/model_suite.py:301-375`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
from unittest.mock import patch, MagicMock
import json

class TestEnsureModelAvailable(unittest.TestCase):
    @patch("skills.model_suite.urllib.request.urlopen")
    def test_model_already_installed(self, mock_urlopen):
        from skills.model_suite import ensure_model_available
        # Mock /api/tags response
        tags_resp = MagicMock()
        tags_resp.read.return_value = json.dumps(
            {"models": [{"name": "gemma4:e4b"}]}
        ).encode()
        tags_resp.__enter__ = lambda s: s
        tags_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = tags_resp
        result = ensure_model_available("gemma4:e4b", host="http://localhost:11434")
        self.assertEqual(result["status"], "ready")

    @patch("skills.model_suite._pull_model")
    @patch("skills.model_suite._get_installed")
    def test_model_not_installed_triggers_pull(self, mock_installed, mock_pull):
        from skills.model_suite import ensure_model_available
        mock_installed.return_value = ["qwen3:8b"]
        mock_pull.return_value = {"status": "installed", "model": "gemma4:e4b"}
        result = ensure_model_available("gemma4:e4b", host="http://localhost:11434")
        mock_pull.assert_called_once_with("gemma4:e4b", "http://localhost:11434")
        self.assertEqual(result["status"], "installed")

    @patch("skills.model_suite.urllib.request.urlopen")
    def test_ollama_version_check_warns_below_020(self, mock_urlopen):
        from skills.model_suite import ensure_model_available
        # First call: /api/version, second: /api/tags
        version_resp = MagicMock()
        version_resp.read.return_value = json.dumps({"version": "0.19.0"}).encode()
        version_resp.__enter__ = lambda s: s
        version_resp.__exit__ = MagicMock(return_value=False)
        tags_resp = MagicMock()
        tags_resp.read.return_value = json.dumps(
            {"models": [{"name": "gemma4:e4b"}]}
        ).encode()
        tags_resp.__enter__ = lambda s: s
        tags_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [version_resp, tags_resp]
        result = ensure_model_available("gemma4:e4b", host="http://localhost:11434")
        self.assertIn("warning", result)
        self.assertIn("0.20.0", result["warning"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestEnsureModelAvailable -v`
Expected: FAIL — ImportError on `ensure_model_available`

- [ ] **Step 3: Implement ensure_model_available() in model_suite.py**

Add at the end of `skills/model_suite.py` (after `_check()` around line 375):

```python
def ensure_model_available(model_name: str, host: str = "http://localhost:11434") -> dict:
    """Check if a model is available in Ollama, pull if missing. Check Ollama version."""
    import shutil

    result = {}

    # Version check
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=5) as r:
            version_data = json.loads(r.read())
        version = version_data.get("version", "0.0.0")
        parts = version.split(".")
        major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        if major == 0 and minor < 20:
            result["warning"] = f"Ollama {version} detected — Gemma 4 requires >= 0.20.0"
    except Exception:
        result["warning"] = "Could not check Ollama version"

    # Disk space check
    disk = shutil.disk_usage("/")
    free_gb = disk.free / (1024 ** 3)
    if free_gb < 25:
        result["disk_warning"] = f"Only {free_gb:.1f} GB free — large models need 20+ GB"

    # Check if installed
    installed = _get_installed(host)
    if model_name in installed:
        result["status"] = "ready"
        return result

    # Pull
    pull_result = _pull_model(model_name, host)
    result["status"] = pull_result.get("status", "error")
    if "error" in pull_result:
        result["error"] = pull_result["error"]
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestEnsureModelAvailable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/model_suite.py tests/test_benchmark.py
git commit -m "feat(models): add ensure_model_available() with version + disk checks"
```

---

## Task 4: Providers — Partial Offload via num_gpu

**Files:**
- Modify: `providers.py:911-920`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
class TestPartialOffload(unittest.TestCase):
    @patch("providers.urllib.request.urlopen")
    def test_num_gpu_passed_in_options(self, mock_urlopen):
        """When a model has num_gpu_layers != -1, num_gpu should appear in options."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": "test response",
            "eval_count": 10, "eval_duration": 1000000000,
            "prompt_eval_count": 5,
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from providers import _call_local
        config = {
            "models": {
                "local": "gemma4:31b",
                "complex": "gemma4:31b",
                "ollama_host": "http://localhost:11434",
                "gemma4": {"variants": {"31b": {
                    "vram_estimate_gb": 20,
                    "num_gpu_layers": 24,
                    "context_length": 8192,
                }}},
            },
        }
        _call_local("system", "user", config["models"], max_tokens=100,
                     skill_name="test", config=config)

        # Inspect the request body sent to Ollama
        call_args = mock_urlopen.call_args
        req = call_args[0][0]  # urllib.request.Request object
        body = json.loads(req.data)
        self.assertEqual(body["options"]["num_gpu"], 24)

    @patch("providers.urllib.request.urlopen")
    def test_no_num_gpu_when_full_offload(self, mock_urlopen):
        """When num_gpu_layers is -1, num_gpu should NOT be in options."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": "test response",
            "eval_count": 10, "eval_duration": 1000000000,
            "prompt_eval_count": 5,
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from providers import _call_local
        config = {
            "models": {
                "local": "gemma4:e4b",
                "complex": "gemma4:e4b",
                "ollama_host": "http://localhost:11434",
                "gemma4": {"variants": {"e4b": {
                    "vram_estimate_gb": 7,
                    "num_gpu_layers": -1,
                    "context_length": 8192,
                }}},
            },
        }
        _call_local("system", "user", config["models"], max_tokens=100,
                     skill_name="test", config=config)

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data)
        self.assertNotIn("num_gpu", body["options"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestPartialOffload -v`
Expected: FAIL — num_gpu not in options

- [ ] **Step 3: Modify _call_local() in providers.py**

In `providers.py`, in the `_call_local()` function around lines 911-920, modify the body construction. Before the `json.dumps` call, add variant lookup and num_gpu injection:

```python
    # --- Partial offload: inject num_gpu if variant specifies it ---
    options = {"num_predict": max_tokens}
    variant_key = model.split(":")[-1] if ":" in model else model
    gemma4_variants = models.get("gemma4", {}).get("variants", {})
    variant_cfg = gemma4_variants.get(variant_key, {})
    num_gpu_layers = variant_cfg.get("num_gpu_layers", -1)
    if num_gpu_layers != -1:
        options["num_gpu"] = num_gpu_layers

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }).encode()
```

Replace the existing body construction (~lines 917-920) with the above.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestPartialOffload -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add providers.py tests/test_benchmark.py
git commit -m "feat(providers): pass num_gpu to Ollama for Gemma 4 partial offload"
```

---

## Task 5: Context Overflow Handling

**Files:**
- Modify: `skills/_models.py:113-119`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
class TestContextOverflow(unittest.TestCase):
    def test_truncation_when_exceeding_context_length(self):
        from skills._models import _estimate_tokens, _truncate_for_context
        # Simulate a long input
        system = "You are a helpful assistant."
        user = " ".join(["word"] * 10000)  # ~10000 words -> ~13000 estimated tokens
        estimated = _estimate_tokens(system, user, skill_name="analysis")
        self.assertGreater(estimated, 8192)

        truncated_system, truncated_user, was_truncated = _truncate_for_context(
            system, user, context_length=8192, skill_name="analysis"
        )
        self.assertTrue(was_truncated)
        new_est = _estimate_tokens(truncated_system, truncated_user, skill_name="analysis")
        self.assertLessEqual(new_est, 8192)
        # System prompt preserved
        self.assertEqual(truncated_system, system)

    def test_no_truncation_when_within_limit(self):
        from skills._models import _estimate_tokens, _truncate_for_context
        system = "You are a helpful assistant."
        user = "Short prompt."
        _, _, was_truncated = _truncate_for_context(
            system, user, context_length=8192, skill_name="analysis"
        )
        self.assertFalse(was_truncated)

    def test_estimation_uses_1_3x_default(self):
        from skills._models import _estimate_tokens
        system = "sys"  # 1 word
        user = "a b c d e f g h i j"  # 10 words
        est = _estimate_tokens(system, user, skill_name="analysis")
        # 11 words * 1.3 = 14.3 -> 15 (int)
        self.assertEqual(est, int(11 * 1.3))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestContextOverflow -v`
Expected: FAIL — ImportError on `_estimate_tokens`, `_truncate_for_context`

- [ ] **Step 3: Add _estimate_tokens() and _truncate_for_context() to skills/_models.py**

Add these functions before `call_complex()` in `skills/_models.py` (around line 60):

```python
_CODE_SKILLS = {"code_write", "code_review", "code_discuss", "refactor_verify", "skill_test", "skill_evolve"}


def _estimate_tokens(system: str, user: str, skill_name: str = "unknown") -> int:
    """Estimate token count using word-count heuristic with safety margin."""
    multiplier = 2.0 if skill_name in _CODE_SKILLS else 1.3
    return int((len(system.split()) + len(user.split())) * multiplier)


def _truncate_for_context(
    system: str, user: str, context_length: int, skill_name: str = "unknown"
) -> tuple[str, str, bool]:
    """Truncate user input if estimated tokens exceed context_length.

    Preserves system prompt entirely. Trims user text from the front (oldest).
    Returns (system, user, was_truncated).
    """
    estimated = _estimate_tokens(system, user, skill_name)
    if estimated <= context_length:
        return system, user, False

    # Budget for user after reserving system tokens + output buffer (512 tokens)
    system_tokens = _estimate_tokens(system, "", skill_name)
    output_buffer = 512
    user_budget = context_length - system_tokens - output_buffer
    if user_budget <= 0:
        return system, "", True

    # Trim user words to fit budget
    multiplier = 2.0 if skill_name in _CODE_SKILLS else 1.3
    max_words = int(user_budget / multiplier)
    words = user.split()
    if len(words) > max_words:
        # Keep the latest words (trim from front)
        words = words[-max_words:]
    return system, " ".join(words), True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestContextOverflow -v`
Expected: PASS

- [ ] **Step 5: Wire truncation into call_complex()**

In `skills/_models.py`, inside `call_complex()`, after the existing token estimation (~line 115), add variant context_length lookup and truncation:

```python
    # Context overflow check for models with known context limits
    gemma4_variants = config.get("models", {}).get("gemma4", {}).get("variants", {})
    model_name = models.get("local", "") or models.get("complex", "")
    variant_key = model_name.split(":")[-1] if ":" in model_name else ""
    variant_cfg = gemma4_variants.get(variant_key, {})
    context_limit = variant_cfg.get("context_length", 0)
    if context_limit > 0:
        system, user, was_truncated = _truncate_for_context(
            system, user, context_limit, skill_name
        )
        if was_truncated:
            log.warning("Context truncated for %s (limit %d) on skill %s",
                        model_name, context_limit, skill_name)
```

- [ ] **Step 6: Run all tests**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add skills/_models.py tests/test_benchmark.py
git commit -m "feat(models): add context overflow detection + truncation for variant limits"
```

---

## Task 6: hw_supervisor — Dynamic _model_sizes + MEMORY_PRESSURE

**Files:**
- Modify: `hw_supervisor.py:73-117, 1179-1180, 1216-1233`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
class TestHwSupervisorModelSizes(unittest.TestCase):
    @patch("hw_supervisor.open", create=True)
    def test_model_sizes_populated_from_fleet_toml(self, mock_open):
        import hw_supervisor
        # Simulate fleet.toml with gemma4 variants
        fake_toml = {
            "models": {
                "gemma4": {
                    "variants": {
                        "e4b": {"vram_estimate_gb": 7},
                        "31b": {"vram_estimate_gb": 20},
                    }
                }
            }
        }
        sizes = hw_supervisor._build_model_sizes(fake_toml)
        self.assertEqual(sizes["gemma4:e4b"], 7.0)
        self.assertEqual(sizes["gemma4:31b"], 20.0)
        # Hardcoded models still present
        self.assertIn("qwen3:8b", sizes)

    def test_unknown_model_falls_back_to_default(self):
        import hw_supervisor
        sizes = hw_supervisor._build_model_sizes({})
        self.assertEqual(sizes.get("unknown:model", 4.0), 4.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestHwSupervisorModelSizes -v`
Expected: FAIL — no `_build_model_sizes` function

- [ ] **Step 3: Add _build_model_sizes() to hw_supervisor.py**

Near `_model_sizes` (line ~1179), replace the hardcoded dict with a builder function:

```python
_HARDCODED_MODEL_SIZES = {
    "qwen3:8b": 7.0, "qwen3:4b": 3.0,
    "qwen3:1.7b": 1.5, "qwen3:0.6b": 0.5,
}


def _build_model_sizes(toml_data: dict) -> dict:
    """Build model VRAM size map from hardcoded defaults + fleet.toml variants."""
    sizes = dict(_HARDCODED_MODEL_SIZES)
    variants = toml_data.get("models", {}).get("gemma4", {}).get("variants", {})
    for variant_key, cfg in variants.items():
        vram = cfg.get("vram_estimate_gb")
        if vram is not None:
            sizes[f"gemma4:{variant_key}"] = float(vram)
    return sizes
```

Then update the code that references `_model_sizes` to call `_build_model_sizes()` during `load_thermal_config()`, storing the result in the config dict so it's available to the tier downgrade logic.

In `load_thermal_config()` (~line 73), after reading the TOML data, add:

```python
    cfg["model_sizes"] = _build_model_sizes(data)
```

Update the tier downgrade logic (~line 1188) to use `cfg["model_sizes"].get(tier, 4.0)` instead of `_model_sizes.get(tier, 4.0)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestHwSupervisorModelSizes -v`
Expected: PASS

- [ ] **Step 5: Add MEMORY_PRESSURE flag at vram_emergency**

In the tier downgrade logic (~line 1216), after the emergency downgrade fires, add:

```python
        # Flag active task as MEMORY_PRESSURE
        try:
            import db as fleet_db
            with fleet_db.get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'MEMORY_PRESSURE' "
                    "WHERE status = 'running' AND assigned_to = ?",
                    (active_agent,),
                )
        except Exception:
            log.warning("Could not set MEMORY_PRESSURE flag")
```

- [ ] **Step 6: Run all hw_supervisor tests**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add hw_supervisor.py tests/test_benchmark.py
git commit -m "feat(hw_supervisor): dynamic model sizes from fleet.toml + MEMORY_PRESSURE flag"
```

---

## Task 7: Benchmark Prompt Sets

**Files:**
- Create: `fleet/benchmarks/prompts/coding.json`
- Create: `fleet/benchmarks/prompts/analysis.json`
- Create: `fleet/benchmarks/prompts/summarization.json`
- Create: `fleet/benchmarks/prompts/instruction_following.json`

- [ ] **Step 1: Create benchmarks/prompts/ directory**

```bash
mkdir -p /c/Users/max/Projects/Education/fleet/benchmarks/prompts
```

- [ ] **Step 2: Create coding.json**

```json
[
  {
    "id": "code_fizzbuzz",
    "system": "You are a Python coding assistant. Write clean, idiomatic code.",
    "prompt": "Write a function called fizzbuzz that takes an integer n and returns a list of strings from 1 to n where multiples of 3 are 'Fizz', multiples of 5 are 'Buzz', multiples of both are 'FizzBuzz', and others are the number as a string.",
    "expected_output": "def fizzbuzz(n):",
    "category": "coding",
    "context_tier": "short"
  },
  {
    "id": "code_binary_search",
    "system": "You are a Python coding assistant. Write clean, idiomatic code.",
    "prompt": "Implement a binary search function that takes a sorted list and a target value. Return the index if found, -1 otherwise. Do not use bisect.",
    "expected_output": "def binary_search(",
    "category": "coding",
    "context_tier": "short"
  },
  {
    "id": "code_csv_parser",
    "system": "You are a Python coding assistant. Write clean, idiomatic code.",
    "prompt": "Write a function that reads a CSV string (not a file) with headers and returns a list of dictionaries. Handle quoted fields containing commas. Do not use the csv module.",
    "expected_output": "def parse_csv(",
    "category": "coding",
    "context_tier": "medium"
  }
]
```

- [ ] **Step 3: Create analysis.json**

```json
[
  {
    "id": "analysis_compare_sort",
    "system": "You are a computer science teacher explaining algorithms.",
    "prompt": "Compare the time and space complexity of merge sort vs quicksort. When would you choose one over the other? Give concrete examples.",
    "expected_output": "",
    "category": "analysis",
    "context_tier": "short"
  },
  {
    "id": "analysis_db_indexing",
    "system": "You are a database performance consultant.",
    "prompt": "A PostgreSQL table 'orders' with 50M rows has columns: id, customer_id, created_at, total, status. Queries filter by customer_id + created_at range. The table has no indexes besides the primary key. Recommend an indexing strategy and explain the tradeoffs.",
    "expected_output": "",
    "category": "analysis",
    "context_tier": "medium"
  }
]
```

- [ ] **Step 4: Create summarization.json**

```json
[
  {
    "id": "summarize_technical",
    "system": "You are a technical writer. Summarize concisely.",
    "prompt": "Summarize the key differences between REST and GraphQL APIs in 3-5 bullet points. Focus on practical implications for a team choosing between them for a new project.",
    "expected_output": "",
    "category": "summarization",
    "context_tier": "short"
  },
  {
    "id": "summarize_long_context",
    "system": "You are a research assistant. Summarize accurately.",
    "prompt": "The following is a description of the CAP theorem and its implications for distributed systems. The CAP theorem states that a distributed data store can provide at most two out of three guarantees: Consistency (every read receives the most recent write), Availability (every request receives a non-error response), and Partition tolerance (the system continues to operate despite network partitions). In practice, since network partitions are unavoidable in distributed systems, the real choice is between CP and AP systems. CP systems like ZooKeeper and HBase prioritize consistency — during a partition, some nodes may refuse requests to maintain data accuracy. AP systems like Cassandra and DynamoDB prioritize availability — during a partition, all nodes continue serving requests but may return stale data. The PACELC theorem extends CAP by noting that even without partitions, there is a tradeoff between latency and consistency. Summarize the key points in 2-3 sentences.",
    "expected_output": "",
    "category": "summarization",
    "context_tier": "medium"
  }
]
```

- [ ] **Step 5: Create instruction_following.json**

```json
[
  {
    "id": "instruct_format_json",
    "system": "You follow instructions precisely. Output only what is asked.",
    "prompt": "List exactly 3 programming languages that compile to WebAssembly. Output as a JSON array of strings. No explanation.",
    "expected_output": "[",
    "category": "instruction_following",
    "context_tier": "short"
  },
  {
    "id": "instruct_constraints",
    "system": "You follow instructions precisely. Output only what is asked.",
    "prompt": "Write a haiku (5-7-5 syllable pattern) about debugging code. Output only the haiku, nothing else.",
    "expected_output": "",
    "category": "instruction_following",
    "context_tier": "short"
  },
  {
    "id": "instruct_step_by_step",
    "system": "You are a helpful assistant that follows instructions exactly.",
    "prompt": "Explain how to make a peanut butter and jelly sandwich. Use exactly 5 numbered steps. Each step must be one sentence. Do not include any text before or after the steps.",
    "expected_output": "1.",
    "category": "instruction_following",
    "context_tier": "short"
  }
]
```

- [ ] **Step 6: Commit**

```bash
git add benchmarks/
git commit -m "feat(benchmarks): add default prompt sets for model evaluation"
```

---

## Task 8: Benchmark Skill

**Files:**
- Create: `skills/benchmark_model.py`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
class TestBenchmarkSkill(unittest.TestCase):
    @patch("skills.benchmark_model._run_prompt")
    @patch("skills.model_suite.ensure_model_available")
    def test_benchmark_single_model(self, mock_ensure, mock_run):
        mock_ensure.return_value = {"status": "ready"}
        mock_run.return_value = {
            "response": "test output",
            "eval_count": 50,
            "eval_duration": 1_000_000_000,  # 1 second
            "prompt_eval_count": 20,
        }
        from skills.benchmark_model import run_benchmark
        results = run_benchmark(
            model="gemma4:e4b",
            prompt_category="coding",
            host="http://localhost:11434",
            kv_cache_type="q8_0",
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["model"], "gemma4:e4b")
        self.assertEqual(results[0]["kv_cache_type"], "q8_0")
        self.assertIn("tokens_per_sec", [r["metric"] for r in results])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarkSkill -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Create skills/benchmark_model.py**

```python
"""Benchmark skill — run local models through standardized test suites."""
import json
import logging
import os
import time
import urllib.request

log = logging.getLogger(__name__)

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "prompts")


def _load_prompts(category: str) -> list[dict]:
    """Load prompt set from benchmarks/prompts/<category>.json."""
    path = os.path.join(_PROMPTS_DIR, f"{category}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt set not found: {path}")
    with open(path) as f:
        return json.load(f)


def _run_prompt(model: str, system: str, prompt: str, host: str,
                max_tokens: int = 1024) -> dict:
    """Send a single prompt to Ollama and return the raw response dict."""
    body = json.dumps({
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def run_benchmark(
    model: str,
    prompt_category: str = "coding",
    host: str = "http://localhost:11434",
    kv_cache_type: str = "f16",
    judge_model: str = "",
) -> list[dict]:
    """Run benchmark suite for a single model + category. Returns list of metric dicts."""
    from skills.model_suite import ensure_model_available

    ensure_result = ensure_model_available(model, host)
    if ensure_result.get("status") == "error":
        return [{"model": model, "metric": "error", "value": 0,
                 "unit": "", "kv_cache_type": kv_cache_type,
                 "error": ensure_result.get("error", "model unavailable")}]

    prompts = _load_prompts(prompt_category)
    results = []
    variant = model.split(":")[-1] if ":" in model else model

    for p in prompts:
        try:
            t0 = time.perf_counter()
            resp = _run_prompt(model, p["system"], p["prompt"], host)
            wall_time = time.perf_counter() - t0

            eval_count = resp.get("eval_count", 0)
            eval_duration_ns = resp.get("eval_duration", 1)
            prompt_eval_count = resp.get("prompt_eval_count", 0)
            tokens_per_sec = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns else 0
            time_to_first = resp.get("prompt_eval_duration", 0) / 1e9

            results.append({
                "model": model, "variant": variant,
                "metric": "tokens_per_sec", "value": round(tokens_per_sec, 2),
                "unit": "tok/s", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
            results.append({
                "model": model, "variant": variant,
                "metric": "time_to_first_token", "value": round(time_to_first, 3),
                "unit": "s", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
            results.append({
                "model": model, "variant": variant,
                "metric": "wall_time", "value": round(wall_time, 3),
                "unit": "s", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
            results.append({
                "model": model, "variant": variant,
                "metric": "eval_tokens", "value": eval_count,
                "unit": "tokens", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
            })
        except Exception as e:
            log.warning("Benchmark failed for %s on %s: %s", model, p["id"], e)
            results.append({
                "model": model, "variant": variant,
                "metric": "error", "value": 0,
                "unit": "", "judge_model": judge_model,
                "kv_cache_type": kv_cache_type,
                "prompt_id": p["id"],
                "error": str(e),
            })

    return results


def save_results(results: list[dict], db_module=None) -> int:
    """Persist benchmark results to fleet.db. Returns count saved."""
    if db_module is None:
        import db as db_module
    saved = 0
    with db_module.get_conn() as conn:
        for r in results:
            if r.get("metric") == "error":
                continue
            conn.execute(
                """INSERT INTO benchmarks
                   (model, variant, metric, value, unit, judge_model, kv_cache_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (r["model"], r["variant"], r["metric"], r["value"],
                 r["unit"], r.get("judge_model", ""), r.get("kv_cache_type", "f16")),
            )
            saved += 1
    return saved


def compare_models(models: list[str], db_module=None) -> list[dict]:
    """Fetch and compare benchmark results for given models."""
    if db_module is None:
        import db as db_module
    placeholders = ",".join("?" * len(models))
    with db_module.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT model, metric, AVG(value) as avg_value, unit, kv_cache_type
                FROM benchmarks
                WHERE model IN ({placeholders})
                GROUP BY model, metric, kv_cache_type
                ORDER BY model, metric""",
            models,
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarkSkill -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/benchmark_model.py tests/test_benchmark.py
git commit -m "feat(skills): add benchmark_model skill for local model evaluation"
```

---

## Task 9: CLI — benchmark Command

**Files:**
- Modify: `lead_client.py`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
class TestBenchmarkCLI(unittest.TestCase):
    @patch("skills.benchmark_model.run_benchmark")
    @patch("skills.benchmark_model.save_results")
    def test_cmd_benchmark_single_model(self, mock_save, mock_run):
        mock_run.return_value = [{"model": "gemma4:e4b", "metric": "tokens_per_sec",
                                   "value": 42.5, "unit": "tok/s",
                                   "variant": "e4b", "kv_cache_type": "q8_0"}]
        mock_save.return_value = 1
        from lead_client import cmd_benchmark
        import argparse
        args = argparse.Namespace(
            model="gemma4:e4b", suite=None, compare=None,
            category="coding", kv_cache_type="q8_0",
        )
        # Should not raise
        cmd_benchmark(args)
        mock_run.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarkCLI -v`
Expected: FAIL — ImportError on `cmd_benchmark`

- [ ] **Step 3: Add cmd_benchmark() to lead_client.py**

Add the function following the existing `cmd_*` pattern. Find the argparse section and add a `benchmark` subparser:

```python
def cmd_benchmark(args):
    """Run model benchmarks."""
    from skills.benchmark_model import run_benchmark, save_results, compare_models
    import tomllib

    config_path = os.path.join(os.path.dirname(__file__), "fleet.toml")
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    kv_cache_type = args.kv_cache_type if hasattr(args, "kv_cache_type") else "f16"

    if args.compare:
        models_list = [m.strip() for m in args.compare.split(",")]
        rows = compare_models(models_list)
        if not rows:
            print("No benchmark data found for those models.")
            return
        print(f"\n{'Model':<25} {'Metric':<22} {'Value':>10} {'Unit':<8} {'KV Cache':<8}")
        print("-" * 75)
        for r in rows:
            print(f"{r['model']:<25} {r['metric']:<22} {r['avg_value']:>10.2f} {r['unit']:<8} {r['kv_cache_type']:<8}")
        return

    if args.suite:
        gemma4_models = [f"gemma4:{v}" for v in ["e2b", "e4b", "26b-a4b", "31b"]]
        categories = ["coding", "analysis", "summarization", "instruction_following"]
    else:
        gemma4_models = [args.model]
        categories = [args.category]

    for model in gemma4_models:
        for category in categories:
            print(f"\nBenchmarking {model} / {category} / kv={kv_cache_type}...")
            results = run_benchmark(model, category, host, kv_cache_type)
            saved = save_results(results)
            print(f"  -> {saved} metrics saved")

            # Print summary
            speed = [r for r in results if r["metric"] == "tokens_per_sec"]
            if speed:
                avg_tps = sum(r["value"] for r in speed) / len(speed)
                print(f"  -> Avg tokens/sec: {avg_tps:.1f}")

    print("\nDone. Use --compare to view results.")
```

In the argparse section, add the benchmark subparser:

```python
    bench_parser = subparsers.add_parser("benchmark", help="Run model benchmarks")
    bench_parser.add_argument("model", nargs="?", default="gemma4:e4b",
                              help="Model to benchmark (e.g., gemma4:e4b)")
    bench_parser.add_argument("--suite", choices=["gemma4"],
                              help="Run all variants in a suite")
    bench_parser.add_argument("--compare", help="Compare models (comma-separated)")
    bench_parser.add_argument("--category", default="coding",
                              help="Prompt category (default: coding)")
    bench_parser.add_argument("--kv-cache-type", default="q8_0",
                              dest="kv_cache_type",
                              help="KV cache type: f16, q8_0, q4_0 (default: q8_0)")
    bench_parser.set_defaults(func=cmd_benchmark)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarkCLI -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lead_client.py tests/test_benchmark.py
git commit -m "feat(cli): add benchmark command to lead_client"
```

---

## Task 10: Dashboard — Benchmark Compare Endpoint + MEMORY_PRESSURE SSE

**Files:**
- Modify: `dashboard.py`
- Modify: `sse_blueprint.py`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_benchmark.py`:

```python
class TestBenchmarkEndpoint(unittest.TestCase):
    def setUp(self):
        db.init_db()
        # Insert test data
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO benchmarks
                   (model, variant, metric, value, unit, judge_model, kv_cache_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("gemma4:e4b", "e4b", "tokens_per_sec", 42.5, "tok/s", "", "q8_0"),
            )

    def test_compare_endpoint_returns_json(self):
        from dashboard import app
        with app.test_client() as client:
            resp = client.get("/api/benchmarks/compare?models=gemma4:e4b")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIsInstance(data, list)
            self.assertGreater(len(data), 0)

    def tearDown(self):
        try:
            os.unlink(os.environ["FLEET_TEST_DB"])
        except Exception:
            pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarkEndpoint -v`
Expected: FAIL — 404 on `/api/benchmarks/compare`

- [ ] **Step 3: Add /api/benchmarks/compare to dashboard.py**

Add a new route in `dashboard.py` following the existing pattern:

```python
@app.route("/api/benchmarks/compare")
def api_benchmarks_compare():
    models_param = request.args.get("models", "")
    if not models_param:
        return jsonify({"error": "models parameter required"}), 400
    models_list = [m.strip() for m in models_param.split(",")]
    from skills.benchmark_model import compare_models
    rows = compare_models(models_list)
    return jsonify(rows)
```

- [ ] **Step 4: Add MEMORY_PRESSURE event to sse_blueprint.py**

In `sse_blueprint.py`, in the broadcaster/generator logic, add support for a `memory_pressure` event type. In the status check function:

```python
    # Check for MEMORY_PRESSURE tasks
    try:
        with fleet_db.get_conn() as conn:
            mp_tasks = conn.execute(
                "SELECT id, assigned_to FROM tasks WHERE status = 'MEMORY_PRESSURE'"
            ).fetchall()
        if mp_tasks:
            for t in mp_tasks:
                yield f"event: memory_pressure\ndata: {json.dumps({'task_id': t['id'], 'agent': t['assigned_to']})}\n\n"
    except Exception:
        pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestBenchmarkEndpoint -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard.py sse_blueprint.py tests/test_benchmark.py
git commit -m "feat(dashboard): add /api/benchmarks/compare + MEMORY_PRESSURE SSE event"
```

---

## Task 11: Process Manager — Verify KV-Cache Env Var Injection

**Files:**
- Modify: `process_manager.py:69-128` (if needed)
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write the test to verify existing behavior**

The exploration showed `process_manager.py` already reads `[ollama.optimization]` and sets `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE`. Write a test to verify this works with the new default values:

```python
class TestProcessManagerOllamaEnv(unittest.TestCase):
    @patch("process_manager.tomllib.load")
    def test_kv_cache_type_from_fleet_toml(self, mock_load):
        mock_load.return_value = {
            "ollama": {"optimization": {
                "flash_attention": True,
                "kv_cache_type": "q8_0",
                "num_parallel": "auto",
                "max_loaded_models": "auto",
            }},
        }
        from process_manager import ProcessManager
        pm = ProcessManager.__new__(ProcessManager)
        env = pm._resolve_ollama_env()
        self.assertEqual(env.get("OLLAMA_FLASH_ATTENTION"), "1")
        self.assertEqual(env.get("OLLAMA_KV_CACHE_TYPE"), "q8_0")

    @patch("process_manager.tomllib.load")
    def test_flash_attention_disabled(self, mock_load):
        mock_load.return_value = {
            "ollama": {"optimization": {
                "flash_attention": False,
                "kv_cache_type": "q8_0",
                "num_parallel": "auto",
                "max_loaded_models": "auto",
            }},
        }
        from process_manager import ProcessManager
        pm = ProcessManager.__new__(ProcessManager)
        env = pm._resolve_ollama_env()
        self.assertNotIn("OLLAMA_FLASH_ATTENTION", env)
```

- [ ] **Step 2: Run test**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py::TestProcessManagerOllamaEnv -v`
Expected: PASS (existing code should handle this). If FAIL, adjust `_resolve_ollama_env()` to handle boolean `True` as well as string `"true"`.

- [ ] **Step 3: Fix if needed and commit**

If tests pass with no changes:
```bash
git add tests/test_benchmark.py
git commit -m "test(process_manager): verify KV-cache + flash attention env var injection"
```

If changes were needed:
```bash
git add process_manager.py tests/test_benchmark.py
git commit -m "fix(process_manager): handle boolean flash_attention + KV-cache env vars"
```

---

## Task 12: Full Integration Test + Final Smoke

**Files:**
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Add integration test that exercises the full flow**

```python
class TestFullBenchmarkFlow(unittest.TestCase):
    """Integration test: config -> ensure_model -> benchmark -> save -> compare."""

    def setUp(self):
        db.init_db()

    @patch("skills.benchmark_model._run_prompt")
    @patch("skills.model_suite.ensure_model_available")
    def test_end_to_end(self, mock_ensure, mock_run):
        mock_ensure.return_value = {"status": "ready"}
        mock_run.return_value = {
            "response": "def fizzbuzz(n): ...",
            "eval_count": 100,
            "eval_duration": 2_000_000_000,
            "prompt_eval_count": 30,
            "prompt_eval_duration": 500_000_000,
        }

        from skills.benchmark_model import run_benchmark, save_results, compare_models
        results = run_benchmark("gemma4:e4b", "coding", "http://localhost:11434", "q8_0")
        self.assertGreater(len(results), 0)

        saved = save_results(results)
        self.assertGreater(saved, 0)

        comparison = compare_models(["gemma4:e4b"])
        self.assertGreater(len(comparison), 0)

    def tearDown(self):
        try:
            os.unlink(os.environ["FLEET_TEST_DB"])
        except Exception:
            pass
```

- [ ] **Step 2: Run full test suite**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/test_benchmark.py -v`
Expected: All PASS

- [ ] **Step 3: Run existing test suite to verify no regressions**

Run: `cd /c/Users/max/Projects/Education/fleet && python -m pytest tests/ -v --timeout=60`
Expected: No new failures

- [ ] **Step 4: Final commit**

```bash
git add tests/test_benchmark.py
git commit -m "test: add full integration test for Gemma 4 benchmark flow"
```

---

## Summary

| Task | Component | Estimated Steps |
|------|-----------|-----------------|
| 1 | Fleet.toml config | 3 |
| 2 | DB benchmarks table | 5 |
| 3 | ensure_model_available() | 5 |
| 4 | Partial offload (num_gpu) | 5 |
| 5 | Context overflow handling | 7 |
| 6 | hw_supervisor model sizes + MEMORY_PRESSURE | 7 |
| 7 | Benchmark prompt sets | 6 |
| 8 | Benchmark skill | 5 |
| 9 | CLI benchmark command | 5 |
| 10 | Dashboard endpoint + SSE | 6 |
| 11 | Process manager verification | 3 |
| 12 | Integration test + smoke | 4 |
| **Total** | | **61 steps** |
