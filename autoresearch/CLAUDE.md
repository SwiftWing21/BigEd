# Autoresearch Experiment Loop

## Git Policy
**DO NOT commit/push to remote.** Education working copy (upstream: `/home/max/autoresearch/`).
Results in `results.tsv` only. No git commits during sessions.

## Project
Autonomous GPT optimization: 5-min fixed training budget, minimize val_bpb (lower = better).

## Training Profiles
`uv run python train_profile.py --profile <name>` — manages Ollama GPU/CPU and injects hyperparams.

| Profile | VRAM | Ollama | Config | Use case |
|---------|------|--------|--------|----------|
| `micro` | <2GB | GPU | DEPTH=3, dim=256 | Quick iteration |
| `stable` | ≤8.4GB | CPU | DEPTH=6, dim=384 | Current best |
| `flat_out` | ≤11.4GB | CPU | DEPTH=6, dim=512 + grad ckpt | Max capacity |

Direct run: `uv run train.py` (defaults = stable).

## Rules
- **Edit only**: `train.py` | **Read-only**: `prepare.py`, `pyproject.toml`
- Fixed 5min (300s) wall clock, single GPU, no git commits

## Current Best
`aea8bee` — val_bpb=1.106251, 6.9GB, DEPTH=6, WD=0.05, SLR=0.85

## Experiment Loop (AUTO-CONTINUE)
1. Edit `train.py` → `git commit -m "exp: desc"` → `uv run train.py > run.log 2>&1` (~340s)
2. Extract: `grep "^val_bpb:\|^peak_vram_mb:" run.log`
3. Improved → keep, record in results.tsv | Not improved → `git reset --hard HEAD~1`, record "discard"
4. **Never stop** — continue until interrupted

## Architecture
- model_dim = DEPTH x ASPECT_RATIO, rounded to nearest HEAD_DIM (128)
- DEPTH=6/AR=64 → 384-dim, 3 heads (best) | DEPTH=7 → 512-dim (rounds up), slow/OOM risk
- Muon for attention matrices, AdamW for embeddings/scalars
- Warmdown: 0.5x budget. Batch: 32 (65536 tokens)

## Tuning Status
Done: WD=0.05, SLR=0.85, embedding LR, batch size, activations, attention — no further gains.
Future: DEPTH=7 careful AR, composite grid sweep, extended budgets, alt optimizers.

## Files
`prepare.py` (RO) | `train.py` (edit) | `program.md` (instructions) | `results.tsv` (log) | `run.log` (output)
