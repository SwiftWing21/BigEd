# Autoresearch Experiment Loop — Education / FMA Copy

## ⚠️ Git Policy
**DO NOT commit or push to this repo's remote.**
This is the Education directory working copy. The original upstream is `/home/max/autoresearch/`.
All experiment tracking stays local in `results.tsv`. No git commits during sessions.

## Hardware Constraints
- GPU: NVIDIA RTX 3080 Ti FTW3 Ultra (12GB VRAM)
- Safe VRAM limit: 10GB (~83%). Hard limit: 12GB.
- System RAM: 32GB (available for CPU offload in flat_out profile)
- Safe operating temp: <70°C
- OS: Ubuntu 24 via WSL2

## Project Overview
Autonomous LLM model optimization. Train a GPT model for 5 minutes (fixed time budget), measure val_bpb (lower = better), iterate on architecture/hyperparameters.

**Goal**: Minimize val_bpb on validation set.

## Training Profiles
Use `uv run python train_profile.py --profile <name>` instead of running train.py directly.
Profiles manage Ollama GPU/CPU mode and inject hyperparameters as env vars.

| Profile | VRAM budget | Ollama | Architecture | Use case |
|---------|-------------|--------|-------------|----------|
| `micro` | <2GB train | GPU (stays on) | DEPTH=3, dim=256 | Quick iteration, Ollama co-running |
| `stable` | ≤8.4GB (70%) | CPU-only | DEPTH=6, dim=384 | Real research — current best |
| `flat_out` | ≤11.4GB (95%) | CPU-only | DEPTH=6, dim=512 + grad ckpt | Max capacity, system RAM spillover |

To run directly without profile system: `uv run train.py` (uses env var defaults = stable params).

## Key Constraints & Rules
- **Only edit**: `train.py` (model architecture, optimizer, hyperparameters, training loop)
- **Cannot modify**: `prepare.py` (fixed data, tokenizer, evaluation), `pyproject.toml` (dependencies)
- **Fixed training time**: 5 minutes (300s) wall clock, excluding startup/compilation
- **GPU**: Single NVIDIA GPU (12GB on this machine)
- **Metric**: val_bpb (bits per byte) — extracted from `run.log`
- **No git commits** in this working copy

## Current Best Results
| Commit | val_bpb | VRAM | Status | Description |
|--------|---------|------|--------|-------------|
| aea8bee | 1.106251 | 6.9GB | ✓ keep | DEPTH=6, WD=0.05, SLR=0.85 (improved from 1.110155) |

## Experiment Tracking
Results logged in `results.tsv` (untracked by git). Format:
```
commit	val_bpb	memory_gb	status	description
aea8bee	1.106251	6.9	keep	WEIGHT_DECAY 0.2->0.05 + SCALAR_LR 0.5->0.85 (best)
```

## Experiment Loop (ALWAYS AUTO-CONTINUE)
1. Modify `train.py` (hyperparameters, architecture)
2. `git commit -m "exp: description"`
3. `uv run train.py > run.log 2>&1` (wait ~340s)
4. Extract: `grep "^val_bpb:\|^peak_vram_mb:" run.log`
5. If improved: keep commit, record in results.tsv
6. If not improved: `git reset --hard HEAD~1`, record as "discard"
7. **NEVER STOP** — continue experimenting until manually interrupted

## Architecture Notes
- **DEPTH**: Layers (baseline=4, current=6). Tried 7→too slow or rounds unexpectedly.
- **ASPECT_RATIO**: 64 (model_dim = depth × ratio). Wider attempts (96→192) were discarded.
- **DEVICE_BATCH_SIZE**: 32. Full training batch = 65536 tokens.
- **Attention**: Muon optimizer for matrices, AdamW for embeddings/scalars.
- **Warmdown**: 0.5× budget (last 2.5min at reduced LR). Reducing to 0.3 made it worse.

### ASPECT_RATIO Math
`model_dim = DEPTH × ASPECT_RATIO` rounded up to nearest multiple of HEAD_DIM (128).

Safe combinations (with HEAD_DIM=128):
- DEPTH=4, AR=64  → 256-dim, 2 heads
- DEPTH=6, AR=64  → 384-dim, 3 heads  ✅ current best (1.106251 bpb)
- DEPTH=7, AR=64  → 448-dim → rounds to 512, 4 heads (larger model, slower training)
- DEPTH=7, AR=56  → 392-dim → rounds to 512, 4 heads ⚠️ (unexpected! caused final pass timeout)
- DEPTH=8, AR=64  → 512-dim → OOM at 12GB

**Key insight**: Always verify actual model_dim after rounding. Small AR reductions don't guarantee smaller models.

## What to Try Next
**Tuning complete for current epoch:**
- ✅ WEIGHT_DECAY: 0.2 → 0.05 (improved)
- ✅ SCALAR_LR: 0.5 → 0.85 (improved)
- ✅ EMBEDDING_LR variants, batch size, activations, attention patterns: no gains

**Future directions (if pursuing further optimization):**
1. **DEPTH=7 with careful AR**: Use `build_model_config()` to verify actual dimensions before committing.
2. **Composite tuning**: Try WEIGHT_DECAY=0.04-0.06 + SCALAR_LR=0.83-0.87 grid sweep.
3. **Extended training budget**: If time allows, test 10-minute runs to see if deeper models train to convergence.
4. **Alternative optimizers**: Test different Muon settings (momentum, beta2).
5. **Architecture experiments**: Value embeddings scaling, residual lambda initialization.
6. **Data/tokenization**: Different sequence packing or sampling strategies.

## Tips
- **OOM handling**: If crash, reduce model size (lower DEPTH/ASPECT_RATIO, batch size) and retry.
- **Quick iteration**: Each run ~340s (5min training + ~40s overhead). ~10 runs/hour = 100+ overnight.
- **Metric stability**: Results can vary slightly; improvements >0.001 are meaningful.
- **Simplicity wins**: Small improvement + simpler code = keep. Marginal gain + complex code = skip.

## Files
- `prepare.py` — data, tokenizer, evaluation (READ-ONLY)
- `train.py` — edit this: model, optimizer, hyperparams
- `program.md` — experiment instructions
- `results.tsv` — experiment log (untracked)
- `run.log` — latest training output
