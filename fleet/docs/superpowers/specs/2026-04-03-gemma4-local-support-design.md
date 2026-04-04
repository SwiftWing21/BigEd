# Gemma 4 Local Support — Design Spec

**Date:** 2026-04-03
**Status:** Approved (pending spec review)
**Scope:** Benchmark-first integration of Gemma 4 models via Ollama, with swap-ready infrastructure for default replacement.

## Context

BigEd CC currently runs local inference through Ollama with qwen3 variants (1.7b, 4b, 8b) as default models. Google released the Gemma 4 family with four instruct-tuned variants — all multimodal-capable:

| Variant | Params | Type | Est. VRAM |
|---------|--------|------|-----------|
| gemma4:e2b | 5B | Any-to-Any | ~4 GB |
| gemma4:e4b | 8B | Any-to-Any | ~7 GB |
| gemma4:26b-a4b | 27B (4-bit activations) | Multimodal | ~16 GB |
| gemma4:31b | 33B | Multimodal | ~20 GB |

Goal: wire all four variants into BigEd's local model infrastructure, build a benchmark harness to evaluate them against current models, and make swapping to Gemma 4 as the default a one-line config change. Dev rig may be upgraded to 48 GB system RAM to support partial GPU offload on larger variants.

## Out of Scope

- Multimodal/vision skill inputs (follow-up after benchmarks prove value)
- Auto-select quant by VRAM (manual selection only)
- Auto-scaling between Gemma 4 sizes by skill complexity (manual tier assignment)
- Gemma 4 via Google API (local Ollama only)

---

## 1. Fleet.toml Model Configuration

Four new model entries under a `[models.gemma4]` section with per-variant tuning.

```toml
[models.gemma4]
# Active model (user picks one, others available for benchmarking)
# Options: "gemma4:e2b", "gemma4:e4b", "gemma4:26b-a4b", "gemma4:31b"
local = "gemma4:e4b"
complex = "gemma4:26b-a4b"

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

**Swap to default:** Change the main `[models]` section's `local` and `complex` keys to point at Gemma 4 variants. One-line change per key.

**Design decisions:**
- VRAM estimates are conservative starting points, refined by benchmarking.
- Context length set to 8192 as a safe default; stress tests will determine actual usable max.
- `num_gpu_layers = -1` means full GPU. The 31B variant defaults to 24 layers on GPU (partial offload) for rigs with < 24 GB VRAM.

---

## 2. Ollama Model Management

### 2.1 Pull & Verify

New function `ensure_model_available(model_name)` in the Ollama interaction layer:
- Checks Ollama's `/api/tags` for the requested model.
- If missing, pulls via `/api/pull` with progress logging.
- Called on-demand (benchmark or skill dispatch), never auto-pulled on boot to avoid surprise multi-GB downloads.

### 2.2 Modelfiles for Partial Offload

For variants that need non-default Ollama parameters (e.g., 31B with partial offload):
- Modelfile templates stored in `fleet/modelfiles/` (one per variant that needs custom params).
- Generated from fleet.toml's `num_gpu_layers` and `context_length` values.
- Only created when the user explicitly configures partial offload (i.e., `num_gpu_layers` != -1).

### 2.3 Context Overflow Handling

Before dispatching to Ollama:
1. Estimate token count of the input payload.
2. Compare against the model's `context_length` from fleet.toml.
3. If input exceeds limit: truncate oldest messages (preserve system prompt + latest user turn).
4. Log the truncation to the task record — always visible, never silent.

---

## 3. Benchmark Harness

### 3.1 Benchmark Skill

New skill `benchmark_model` that runs any local model through a standardized test suite.

**Metrics collected:**
- **Speed:** tokens/sec on fixed prompt set (short, medium, long context).
- **Quality:** responses scored against reference set (coherence, instruction-following, accuracy). Scored by current `complex` model as judge, or manually.
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

- Results stored in a new `benchmarks` table in fleet.db:
  - Columns: `id`, `model`, `variant`, `metric`, `value`, `unit`, `timestamp`
- Dashboard endpoint: `GET /api/benchmarks/compare?models=gemma4:e4b,qwen3:8b`
- Console prints a summary comparison table after suite runs.

### 3.4 Prompt Sets

- Directory: `fleet/benchmarks/prompts/`
- Categorized prompt files: coding, analysis, summarization, instruction-following.
- Ships with a reasonable default set; user can add custom prompts.
- Each prompt has an optional `expected_output` field for quality scoring.

---

## 4. Memory Safety & hw_supervisor Integration

### 4.1 VRAM Monitoring During Inference

hw_supervisor (Dr. Ders) already polls GPU every 5 seconds. Additions:
- Track which model is active and its expected VRAM from fleet.toml.
- **90% VRAM:** log warning to task record, emit SSE event to dashboard.
- **95% VRAM:** flag task as `MEMORY_PRESSURE` in fleet.db, visible in dashboard. No auto-kill.

### 4.2 RAM Spillover Tracking

When a model uses partial offload (`num_gpu_layers` < -1):
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
| `fleet.toml` | New `[models.gemma4]` section with variant configs |
| `providers.py` | `ensure_model_available()`, context overflow estimation |
| `hw_supervisor.py` | Inference VRAM tracking, RAM spillover monitoring, MEMORY_PRESSURE flag |
| `lead_client.py` | `benchmark` CLI command |
| `skills/benchmark_model.py` | New skill — benchmark execution logic |
| `db.py` / `db_usage.py` | `benchmarks` table schema + queries |
| `dashboard.py` | `/api/benchmarks/compare` endpoint |
| `fleet/modelfiles/` | New directory — Ollama Modelfile templates for partial offload |
| `fleet/benchmarks/prompts/` | New directory — benchmark prompt sets |
| `tests/test_skills.py` | Benchmark skill tests |

## Testing Strategy

- Unit tests for `ensure_model_available()` with mocked Ollama `/api/tags` responses.
- Unit tests for context overflow truncation logic.
- Benchmark skill dispatch test (mocked Ollama inference).
- hw_supervisor VRAM/RAM threshold tests with mocked GPU readings.
- Integration test: full benchmark run against a small model (e2b) if Ollama available.
- OOM error handling test with mocked Ollama error response.
