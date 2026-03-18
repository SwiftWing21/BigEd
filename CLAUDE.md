# Education Project

## Project Structure
- Local education/learning project on Windows 11
- `fleet/` — 8-agent local AI worker fleet (Ollama/qwen3:8b + Sonnet for synthesis)
- `BigEd/` — personal reference docs, command sheets, notes

## Machine (RTX 3080 Ti, 12GB VRAM)
- VRAM safe limit: 10GB. Training sweet spot: DEPTH=6, ~26M params, 6.9GB
- DEPTH=7+ causes OOM. Don't run Ollama on GPU while train.py is running
- Ollama safe during training: `CUDA_VISIBLE_DEVICES=-1 ollama serve &`
- Python: use `uv run` not `python`. Full details: `MACHINE_PROFILE.md`
- **See `GEMINI.md` for architectural history, VRAM optimizations, and error-recovery mechanisms.**

## Fleet
- Architecture includes a dual-supervisor system:
  - `supervisor.py`: Core task distribution, agent lifecycles, and queue management.
  - `hw_supervisor.py`: CPU-bound hardware manager. Actively monitors VRAM and dynamically scales Ollama `local` models down (to 4b/1.7b) during high pressure to ensure task distribution never stalls from an OOM event.
- Config/status: `fleet/CLAUDE.md`, live status: `uv run python lead_client.py status`
- Commands reference: `BigEd/fleet_commands.md` (read on demand, not loaded here)
- Eco mode default: CPU-only Ollama, ~40% CPU, 0 VRAM

## API Guidelines (when making API calls or building apps)
- Throttle to 20% of rate limits, 300ms min between requests, exponential backoff on 429s
- Prefer Message Batches API for bulk/non-real-time (50% savings)
- Always use `cache_control: { type: "ephemeral" }` on stable system prompts
- Models: `claude-sonnet-4-6` default, `claude-haiku-4-5` for high-volume, `claude-opus-4-6` for complex reasoning
