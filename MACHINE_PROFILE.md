# Machine Profile — Max's PC

Use this file to understand the hardware constraints and empirically validated limits
of this machine before suggesting model sizes, batch sizes, or training configs.

---

## Hardware

| Component | Detail |
|-----------|--------|
| GPU | NVIDIA RTX 3080 Ti FTW3 Ultra |
| VRAM total | 12 GB |
| VRAM safe limit | ~10 GB (enforced via `VRAM_LIMIT_GB=10`) |
| Safe GPU temp | < 70°C |
| OS | Ubuntu 24.04 via WSL2 on Windows 11 Pro |
| Shell | bash (WSL2) |
| Python mgr | `uv` (preferred), also has `pip`, `venv`, `nvm` |

---

## GPU VRAM Budget — Empirically Validated

From autoresearch experiments (`~/autoresearch/results.tsv`), training a GPT model
with Muon+AdamW optimizer, bf16, Flash Attention 3, `DEVICE_BATCH_SIZE=32`, `MAX_SEQ_LEN=2048`:

| Model Size | DEPTH | model_dim | VRAM Used | val_bpb | Outcome |
|------------|-------|-----------|-----------|---------|---------|
| ~26M params | 4 | 256 | 8.9 GB | 1.127453 | keep (baseline) |
| ~26M params | 6 | 384 | 6.9 GB | 1.106251 | **keep (best)** |
| ~26M params | 6 | 384 | 6.9 GB | 1.110155 | keep |
| DEPTH=7 | 7 | 512 | OOM | crash | discard — exceeds 12GB |

**Key rule**: On this GPU, DEPTH=6 (384-dim, ~26M params) is the sweet spot.
DEPTH=7+ causes OOM at 12GB VRAM. DEPTH=4 (256-dim) uses *more* VRAM (8.9GB)
due to larger batch accumulation needed to fill time budget.

### VRAM headroom by use case

| Use case | Estimated safe max |
|----------|--------------------|
| Training (PyTorch, bf16, Flash Attn) | DEPTH=6, ~26M params, 6.9 GB |
| Ollama inference (while training is OFF) | up to ~8B param models |
| Ollama inference (while training is ON) | CPU-only mode (`CUDA_VISIBLE_DEVICES=-1`) |
| HuggingFace inference (bf16) | ~6B param models max |

---

## Architecture Constraints (autoresearch findings)

- `HEAD_DIM = 128` — attention head dimension, fixed
- `model_dim = DEPTH × ASPECT_RATIO`, rounded up to nearest multiple of HEAD_DIM
- **Always verify actual model_dim after rounding** — small AR reductions don't guarantee smaller models
  - DEPTH=7, AR=56 → 392-dim → rounds to **512** (unexpectedly large, caused timeout)
- `WINDOW_PATTERN = "SSSL"` — sliding window: S=half context, L=full; last layer always full
- `DEVICE_BATCH_SIZE = 32`, `TOTAL_BATCH_SIZE = 2^16 (65536 tokens)`
- Optimizer: Muon for matrix params, AdamW for embeddings/scalars
- Best hyperparams found: `WEIGHT_DECAY=0.05`, `SCALAR_LR=0.85`, `WARMDOWN_RATIO=0.5`

---

## Local LLM Stack

### Ollama
- Installed in WSL2 at `~/.ollama/`
- **Critical**: Do NOT run ollama on GPU while `train.py` is running — causes OOM
- To run ollama safely during training: `CUDA_VISIBLE_DEVICES=-1 ollama serve &`
- Models directory: `~/.ollama/models/`
- To list available models: `ollama list`

### Claude Code
- Installed globally via npm
- API key in `~/.bashrc` as `ANTHROPIC_API_KEY`
- Default model set to `claude-haiku-4-5-20251001` in `~/.bashrc`
  - Override per-session with `ANTHROPIC_MODEL=claude-sonnet-4-6 claude`

---

## Active Projects

| Project | Path (WSL) | Purpose |
|---------|------------|---------|
| autoresearch | `~/autoresearch/` | Autonomous GPT pretraining experiments (Karpathy's framework) |
| Education | `C:\Users\max\Projects\Education` (Windows) | Learning, general Claude Code workspace |

### autoresearch quick facts
- Only `train.py` is editable — `prepare.py` is read-only
- Metric: `val_bpb` (lower = better), extracted with `grep "^val_bpb:" run.log`
- Run: `uv run train.py > run.log 2>&1`
- Time budget: exactly 5 minutes wall clock (excluding startup/compilation)
- Best result to date: **val_bpb = 1.106251** at commit `aea8bee`

---

## Environment Variables (WSL2 `~/.bashrc`)

```bash
HF_TOKEN=hf_...                          # HuggingFace access
ANTHROPIC_API_KEY=sk-ant-...             # Claude API
ANTHROPIC_MODEL=claude-haiku-4-5-20251001  # Default Claude model
VRAM_LIMIT_GB=10                         # Set before training runs
```

> **Note**: API keys are stored in plaintext in `~/.bashrc` — avoid committing `.bashrc`
> or any file that sources it.

---

## General Rules for This Machine

1. **GPU is shared** — check if `train.py` is running before launching any GPU-heavy inference
2. **10GB VRAM soft cap** — stay under this for training; inference can use more if not training
3. **WSL2 paths** — from Windows: `\\wsl.localhost\Ubuntu\home\max\...`; from WSL: `/home/max/...`
4. **Use `uv run`** not `python` in autoresearch — it manages the venv automatically
5. **OOM recovery** — reduce DEPTH or DEVICE_BATCH_SIZE first; batch size has less impact than depth
6. **Hardware Supervisor (`fleet/hw_supervisor.py`)** — A secondary CPU-bound daemon continuously monitors VRAM. If you are drafting fleet skills or managing models, know that this supervisor exists to automatically step down the Ollama model size (e.g., to 1.7b or 4b) when VRAM usage exceeds 80%. This guarantees task distribution and worker agents survive high-VRAM events (like `train.py` runs).
