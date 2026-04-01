# Autoresearch — Windows Quick Start

Previously this ran from WSL via `run_claude.sh`. That's no longer needed.
Everything runs natively on Windows with your GPU.

## Prerequisites

1. **ANTHROPIC_API_KEY** set in your environment
   ```bash
   # Git Bash — permanent (add to ~/.bashrc or set via Windows env vars)
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```
2. **Python 3.11+** on PATH (native Windows, not WSL)
3. **PyTorch with CUDA** installed in the autoresearch venv
4. **Ollama** running (train_profile.py manages GPU/CPU switching)

## Option A: Fleet-Managed Loop (Recommended)

The fleet dispatches cycles, tracks budget, and logs to the dashboard.

```bash
cd c:/Users/max/Projects/Education

# 1. Ensure fleet is running
python fleet/supervisor.py

# 2. Launch via CLI (20 cycles, $2 budget cap)
python fleet/lead_client.py task '{"type": "autoresearch_loop", "max_cycles": 20, "budget": 2.00, "profile": "stable"}'
```

Monitor in the dashboard at `http://localhost:5555` — look for `autoresearch_loop` in the task list.

## Option B: Direct CLI (No Fleet Required)

Runs the loop standalone. Only needs `ANTHROPIC_API_KEY` and GPU.

```bash
cd c:/Users/max/Projects/Education

# Default: 20 cycles, $2.00 budget, stable profile
python fleet/skills/autoresearch_loop.py

# Custom: 50 cycles, $5 budget, flat_out profile
python fleet/skills/autoresearch_loop.py --max-cycles 50 --budget 5.00 --profile flat_out
```

## Option C: Manual Single Experiment (Original Workflow)

Same as before, just without WSL.

```bash
cd c:/Users/max/Projects/Education/autoresearch

# Run training directly
python train_profile.py --profile stable

# Or raw (uses env defaults)
python train.py > run.log 2>&1
grep "^val_bpb:\|^peak_vram_mb:" run.log
```

## What Changed from WSL

| Before (WSL) | Now (Windows native) |
|---------------|---------------------|
| `run_claude.sh` launches Claude Code REPL in WSL | `autoresearch_loop.py` runs autonomously |
| Claude Code manually picks experiments | Claude API (Sonnet) proposes experiments programmatically |
| `uv run train.py` (WSL Python) | `python train.py` (Windows Python) |
| Manual keep/discard decisions | Automatic: compare val_bpb, keep if improved |
| No cost tracking | API gate: $2.00 default budget, ~$0.02/cycle |
| No caching | System prompt cached via `cache_control: ephemeral` |

## Cost Controls

- **API gate** in `fleet/fleet.toml`: `default_budget = 2.00` (hard cap)
- **Per-cycle cost**: ~$0.02 (Sonnet, with caching)
- **20 cycles** = ~$0.40 | **100 cycles** = ~$2.00
- Budget exhaustion stops the loop gracefully (no mid-training interruption)

## Training Profiles

```bash
python autoresearch/train_profile.py --list
```

| Profile | VRAM | Ollama | Use case |
|---------|------|--------|----------|
| `micro` | <2GB | GPU | Quick iteration |
| `stable` | <=8.4GB | CPU | Current best (default) |
| `balanced` | auto | GPU | Train alongside Ollama |
| `flat_out` | <=11.4GB | CPU | Maximum capacity |

## Results

All results append to `autoresearch/results.tsv` (same format as before).
The loop never commits results.tsv — only train.py changes get committed.

```bash
# View results
cat autoresearch/results.tsv

# Analyze via fleet skill
python -c "
import sys; sys.path.insert(0, 'fleet')
from skills.autoresearch_analyze import run
r = run({}, {})
print(f'Best: {r[\"best_bpb\"]}')
print(f'Experiments: {r[\"total_experiments\"]}')
for s in r.get('suggestions', []): print(f'  - {s}')
"
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ANTHROPIC_API_KEY not set` | Set env var (see Prerequisites) |
| `API gate blocked claude` | Check `fleet.toml` → `[api_gate]` enabled + claude enabled |
| OOM during training | Switch to `stable` or `balanced` profile |
| `providers module not available` | Run from project root, not autoresearch/ |
| Git errors | Ensure you're on an `autoresearch/*` branch |
