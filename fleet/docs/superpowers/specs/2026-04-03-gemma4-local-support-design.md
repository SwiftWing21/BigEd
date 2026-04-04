# Gemma 4 Local Support — Design Spec

**Date:** 2026-04-03
**Status:** Approved
**Scope:** Benchmark-first integration of Gemma 4 models via Ollama, with swap-ready infrastructure for default replacement.

## Context

BigEd CC currently runs local inference through Ollama with qwen3 variants (1.7b, 4b, 8b) as default models. Google released the Gemma 4 family with four instruct-tuned variants — all multimodal-capable:

| Variant | Params | Type | Est. VRAM |
|---------|--------|------|-----------|
| gemma4:e2b | 5B | Any-to-Any | ~4 GB |
| gemma4:e4b | 8B | Any-to-Any | ~7 GB |
| gemma4:26b-a4b | 27B (4-bit activations) | Multimodal | ~16 GB |
| gemma4:31b | 33B | Multimodal | ~20 GB |

Goal: wire all four variants into BigEd's local model infrastructure, build a benchmark harness to evaluate them against current models, and make swapping to Gemma 4 as the default a one-line config change. Dev rig has 48 GB system RAM (non-ideal DIMM config) to support partial GPU offload and KV-cache overflow on larger variants.

## Prerequisites

- **Ollama >= 0.20.0** — required for Gemma 4 model support. `ensure_model_available()` checks the Ollama version via `/api/version` and warns if below 0.20.0.

## Out of Scope

- Multimodal/vision skill inputs (follow-up after benchmarks prove value)
- Auto-select quant by VRAM (manual selection only)
- Auto-scaling between Gemma 4 sizes by skill complexity (manual tier assignment)
- Gemma 4 via Google API (local Ollama only)

---

## 1. Fleet.toml Model Configuration

Gemma 4 model metadata lives in a `[models.gemma4]` section. This section is **reference data only** — it stores VRAM estimates, layer counts, and context lengths for each variant. It does **not** replace the existing `[models]` routing keys.

### 1.1 Variant Metadata

```toml
[models.gemma4.variants.e2b]
vram_estimate_gb = 4
num_gpu_layers = -1          # -1 = all layers on GPU
context_length = 8192

[models.gemma4.variants.e4b]
vram_estimate_gb = 7
num_gpu_layers = -1
context_length = 8192

[models.gemma4.variants.26b-a4b]
vram_estimate_gb = 16
num_gpu_layers = -1
context_length = 8192

[models.gemma4.variants.31b]
vram_estimate_gb = 20
num_gpu_layers = 24          # partial offload default for <24 GB VRAM
context_length = 8192
```

### 1.2 Swapping to Default

To make Gemma 4 the default, update the existing flat `[models]` keys and `[models.tiers]`:

```toml
[models]
local = "gemma4:e4b"
complex = "gemma4:26b-a4b"
conductor_model = "gemma4:e2b"

[models.tiers]
default = "gemma4:e4b"
mid     = "gemma4:e2b"
low     = "gemma4:e2b"
crit    = "gemma4:e2b"
```

This is what `_call_local()`, `get_local_model_for_skill()`, and the tier system already consume — no changes to config loading needed.

### 1.3 Dr. Ders VRAM Size Integration

Currently `hw_supervisor.py` has a hardcoded `_model_sizes` dict (e.g., `{"qwen3:8b": 7.0, ...}`). This changes to:

- On startup, Dr. Ders reads `[models.gemma4.variants]` from fleet.toml and merges `vram_estimate_gb` values into `_model_sizes`.
- Any model with a `vram_estimate_gb` in fleet.toml takes precedence over the hardcoded default.
- Unknown models still fall back to the existing 4.0 GB default estimate.

**Design decisions:**
- VRAM estimates are conservative starting points, refined by benchmarking.
- Context length set to 8192 as a safe default for overflow estimation; actual usable context depends on KV-cache type and available memory. Ollama auto-sizes context based on total memory (48 GB system RAM = up to 32k auto). Stress tests will determine actual usable max per variant/KV-cache combo.
- `num_gpu_layers = -1` means full GPU. The 31B variant defaults to 24 layers on GPU (partial offload) for rigs with < 24 GB VRAM.
- With q8_0 KV-cache + flash attention, effective VRAM usage drops significantly — the 26B-A4B variant may fit entirely in GPU with room for extended context.

---

## 2. Ollama Model Management

### 2.1 Pull & Verify

Reuse existing `model_suite._pull_model()` and `_get_installed()` functions via a thin wrapper `ensure_model_available(model_name)`:
- Calls `_get_installed()` to check if model is present.
- If missing, calls `_pull_model()` with progress logging.
- Before pulling, checks available disk space and warns if < 25 GB free (31B variant can be 20+ GB on disk).
- Checks Ollama version via `/api/version` — warns if below 0.20.0.
- Called on-demand (benchmark or skill dispatch), never auto-pulled on boot to avoid surprise multi-GB downloads.

### 2.2 Partial Offload via Ollama Request Options

For variants that need partial GPU offload (`num_gpu_layers != -1`):
- Pass `num_gpu` in the Ollama request options payload: `{"model": model, "prompt": prompt, "options": {"num_gpu": num_gpu_layers, "num_predict": max_tokens}}`.
- The `num_gpu_layers` value is read from `[models.gemma4.variants.<variant>]` in fleet.toml at dispatch time.
- No Modelfiles needed — Ollama's request-level `num_gpu` option handles this directly.
- To verify the layer count took effect: check Ollama's `/api/show` response for the loaded model's layer distribution after first inference.

### 2.3 KV-Cache Optimization & System RAM Overflow

Ollama supports quantized KV-cache and flash attention, which dramatically reduce memory usage for long-context inference. This is critical for running larger Gemma 4 variants (26B-A4B, 31B) on the dev rig.

**Ollama server environment variables** (configured via fleet.toml, applied by supervisor on Ollama startup):

```toml
[ollama]
flash_attention = true           # OLLAMA_FLASH_ATTENTION=1 — required for KV-cache quant
kv_cache_type = "q8_0"           # OLLAMA_KV_CACHE_TYPE — default f16, q8_0 halves memory, q4_0 quarters it
# context_length = 0             # OLLAMA_CONTEXT_LENGTH — 0 = use Ollama auto-sizing (recommended)
```

**KV-cache quantization options:**

| Type | Memory vs f16 | Quality Impact | Recommendation |
|------|--------------|----------------|----------------|
| `f16` | 100% (default) | None | Baseline for benchmarking |
| `q8_0` | ~50% | Negligible | **Recommended default** — best tradeoff |
| `q4_0` | ~25% | Small-medium, worse at high context | Use when VRAM is tight, benchmark quality first |

**How it works:**
- Supervisor sets `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE` from fleet.toml when starting the Ollama process.
- Flash attention is a prerequisite for KV-cache quantization — if `flash_attention = false`, KV-cache type is ignored.
- When model weights + KV-cache exceed VRAM, Ollama automatically spills to system RAM. With 48 GB system RAM, this gives substantial overflow headroom for the 31B model.
- KV-cache type is currently a global Ollama setting (applies to all models). Per-model KV-cache type is not yet supported by Ollama.

**Benchmark harness integration:**
- Benchmark suite runs each model at f16, q8_0, and q4_0 KV-cache types to measure quality vs. memory tradeoff.
- Record peak VRAM + system RAM usage at each KV-cache setting.
- Include KV-cache type in the `benchmarks` table: add `kv_cache_type` column.

**Interaction with partial offload (Section 2.2):**
- `num_gpu` controls how many model weight layers go on GPU vs RAM.
- KV-cache quant controls how much memory the attention cache uses.
- These are independent — both can be combined. E.g., 31B with 24 GPU layers + q8_0 KV-cache minimizes total memory while keeping most compute on GPU.

### 2.4 Context Overflow Handling

Before dispatching to Ollama:
1. Estimate token count using the existing word-count heuristic in `_models.py` with a 1.3x safety margin (words * 1.3 ≈ tokens). This is intentionally conservative — false positives (unnecessary truncation) are preferable to OOM.
2. Look up the model's `context_length` from `[models.gemma4.variants]` in fleet.toml. Fall back to 8192 if not found.
3. If estimated tokens exceed limit: truncate oldest messages (preserve system prompt + latest user turn).
4. Log the truncation to the task record — always visible, never silent.

---

## 3. Benchmark Harness

### 3.1 Benchmark Skill

New skill `benchmark_model` that runs any local model through a standardized test suite.

**Metrics collected:**
- **Speed:** tokens/sec on fixed prompt set (short, medium, long context).
- **Quality:** responses scored against reference set (coherence, instruction-following, accuracy). Judge model is pinned explicitly in benchmark config (default: `claude-haiku-4-5` if API available, otherwise manual scoring). Judge must remain constant across comparison runs for valid results.
- **VRAM profile:** peak VRAM usage, RAM spillover amount, time-to-first-token.
- **Stress test:** max context fill — push tokens until OOM or truncation, record actual usable context length.

### 3.2 CLI Interface

```bash
# Run one model
python lead_client.py benchmark gemma4:e4b

# Run all Gemma 4 variants sequentially
python lead_client.py benchmark --suite gemma4

# Compare results
python lead_client.py benchmark --compare gemma4:e4b,qwen3:8b
```

### 3.3 Storage & Display

- `benchmarks` table added via `CREATE TABLE IF NOT EXISTS` in `init_db()` (same pattern as all other fleet.db tables — no migration needed, auto-created on next startup).
  - Columns: `id`, `model`, `variant`, `metric`, `value`, `unit`, `judge_model`, `kv_cache_type`, `timestamp`
- Dashboard endpoint: `GET /api/benchmarks/compare?models=gemma4:e4b,qwen3:8b`
- Console prints a summary comparison table after suite runs.

### 3.4 Prompt Sets

- Directory: `fleet/benchmarks/prompts/`
- JSON files, one per category (coding.json, analysis.json, summarization.json, instruction_following.json).
- Schema per file:
  ```json
  [
    {
      "id": "code_fizzbuzz",
      "system": "You are a coding assistant.",
      "prompt": "Write a FizzBuzz implementation in Python.",
      "expected_output": "def fizzbuzz...",
      "category": "coding",
      "context_tier": "short"
    }
  ]
  ```
- Ships with a reasonable default set; user can add custom prompts following the same schema.

---

## 4. Memory Safety & hw_supervisor Integration

### 4.1 VRAM Monitoring During Inference

Dr. Ders already has `vram_high` (85%) and `vram_emergency` (92%) thresholds with tier downgrade logic. Rather than adding parallel thresholds, extend the existing system:

- When a model from `[models.gemma4.variants]` is active, Dr. Ders uses its `vram_estimate_gb` for tier downgrade decisions instead of the hardcoded `_model_sizes` default.
- At `vram_emergency` (92%): existing tier downgrade fires as normal. Additionally, set a `MEMORY_PRESSURE` flag on the active task in fleet.db, visible in the dashboard.
- Emit an SSE event on `MEMORY_PRESSURE` so the dashboard can surface it in real time.

No new thresholds — leverage the existing 85%/92% system.

### 4.2 RAM Spillover Tracking

When a model uses partial offload (`num_gpu_layers != -1`):
- Monitor system RAM delta during inference via psutil.
- Log actual RAM used for spillover in benchmark results and per-task usage records.
- Dashboard shows "GPU: X GB / RAM spillover: Y GB" for active tasks.

### 4.3 Graceful OOM Handling

- Wrap Ollama HTTP calls with timeout + error detection for OOM responses.
- On OOM: log error, mark task as failed with clear message ("Model exceeded available memory — try a smaller variant or increase num_gpu_layers offload").
- No automatic retry on OOM.
- No silent truncation — all truncation is logged and visible (see Section 2.3).

### 4.4 Hardware Upgrade Path (48 GB RAM)

- When system RAM changes (detected on boot), hw_supervisor logs the new capacity.
- No auto-reconfiguration — user adjusts fleet.toml manually, consistent with manual selection approach.

---

## Files Modified

| File | Change |
|------|--------|
| `fleet.toml` | New `[models.gemma4.variants]` metadata section, new `[ollama]` section for flash attention + KV-cache config |
| `providers.py` | Pass `num_gpu` in Ollama request options, context overflow estimation |
| `hw_supervisor.py` | Read `vram_estimate_gb` from fleet.toml into `_model_sizes`, `MEMORY_PRESSURE` flag on emergency, RAM spillover tracking for KV-cache overflow |
| `supervisor.py` | Set `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE` env vars from fleet.toml when starting Ollama |
| `skills/_models.py` | Context overflow safety margin (1.3x) |
| `skills/model_suite.py` | Expose `_pull_model` / `_get_installed` for reuse by `ensure_model_available()` |
| `lead_client.py` | `benchmark` CLI command, `ensure_model_available()` wrapper |
| `skills/benchmark_model.py` | New skill — benchmark execution logic |
| `db.py` | `benchmarks` table in `init_db()` |
| `dashboard.py` | `GET /api/benchmarks/compare` endpoint, `MEMORY_PRESSURE` SSE event |
| `fleet/benchmarks/prompts/` | New directory — JSON benchmark prompt sets |
| `tests/test_skills.py` | Benchmark skill tests |

## Testing Strategy

- Unit tests for `ensure_model_available()` with mocked `/api/tags` and `/api/version` responses.
- Unit tests for context overflow truncation logic with the 1.3x safety margin.
- Benchmark skill dispatch test (mocked Ollama inference).
- hw_supervisor `_model_sizes` population from fleet.toml test.
- hw_supervisor `MEMORY_PRESSURE` flag test with mocked GPU readings at 92%+ VRAM.
- Integration test: full benchmark run against a small model (e2b) if Ollama available.
- OOM error handling test with mocked Ollama error response.
- Ollama version check test (below and above 0.20.0).
- Supervisor Ollama env var injection test (flash attention + KV-cache type from fleet.toml).
- Benchmark KV-cache sweep test (verify f16/q8_0/q4_0 results stored with correct kv_cache_type).
