# Education Project

## Structure
- `fleet/` — 8-agent AI worker fleet (Ollama/qwen3:8b + Sonnet)
- `BigEd/` — reference docs, command sheets, notes

## Machine (RTX 3080 Ti, 12GB VRAM)
- VRAM safe: 10GB. Sweet spot: DEPTH=6, ~26M params, 6.9GB
- DEPTH=7+ OOMs. No Ollama on GPU during train.py
- Ollama safe during training: `CUDA_VISIBLE_DEVICES=-1 ollama serve &`
- Python: `uv run` not `python`. Details: `MACHINE_PROFILE.md`
- Arch history/VRAM opts: `GEMINI.md`

## Fleet
- Dual-supervisor: `supervisor.py` (task distribution) + `hw_supervisor.py` (VRAM monitor, auto-scales models under pressure)
- Config: `fleet/CLAUDE.md` | Status: `uv run python lead_client.py status`
- Commands: `BigEd/fleet_commands.md` | Eco mode default: CPU-only, ~40% CPU, 0 VRAM

## API
- Throttle 20% of rate limits, 300ms min between requests, exponential backoff on 429s
- Models: `claude-sonnet-4-6` default, `claude-haiku-4-5` high-volume, `claude-opus-4-6` complex
