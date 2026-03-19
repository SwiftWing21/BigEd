# Gemini Architectural Notes & Implementation History

This document tracks major architectural decisions, system stability improvements, and UI enhancements implemented for the BigEd fleet.

## Version Scheme Note
When adding roadmap items or version references, use the post-1.0 format: `0.XX.YY` (e.g., 0.16.00).
Place new items chronologically after the last completed version in ROADMAP_v030_v040.md.
Pre-1.0 versions (v0.31-v0.48) are historical and should not be modified.

## 1. Dual Supervisor Architecture & VRAM Management
- **Hardware Supervisor (`hw_supervisor.py`)**: CPU-bound daemon that actively monitors GPU VRAM (target: 10GB safe limit on RTX 3080 Ti).
- **Graceful Handoffs**: Communicates with the main task supervisor via `fleet/hw_state.json` (`transitioning`, `ready`) to pause task dispatch while Ollama models are being swapped or unloaded.
- **Parallel Models**: Fleet runs with `OLLAMA_MAX_MODELS=2` and `OLLAMA_NUM_PARALLEL=4`.
- **CPU Maintainer**: A small `qwen3:0.6b` model is pre-loaded and permanently pinned to system RAM (`"options": {"num_gpu": 0}`). When the primary model is evicted due to `train.py` VRAM pressure, background tasks seamlessly hand off to this zero-VRAM maintainer model.

## 2. Fleet Stability & Error Recovery
- **OOM Soft Recovery (`train_profile.py`)**: If `train.py` crashes with CUDA OutOfMemory, the script parses `run.log`. It executes a soft recovery by halving `DEVICE_BATCH_SIZE`. If that fails, it steps down `DEPTH` and retries, ensuring the autoresearch loop survives overnight.
- **Overload Requeueing (`db.py` & `worker.py`)**: Transient API errors (timeouts, rate limits, 502/503s) resulting from model transitions trigger a `requeue_task` flow, putting the task back in `PENDING` rather than failing it, accompanied by a 10s worker backoff.
- **Crash Backoff (`supervisor.py`)**: Dead workers enter a 15-second cool-down period before respawning to prevent 100% CPU crash loops.

## 3. UI & UX Enhancements (BigEd CC)
- **Responsive Header**: System stats (CPU, RAM, GPU, ETH) use grid weights to prevent clipping when the window is resized.
- **Collapsible Sidebar**: Added a hamburger menu (`≡`) to toggle the fleet sidebar, granting more space to the main log/output viewers.
- **Agent States**: Standardized agent display states to `ACTIVE` (Green), `RESTING` (Yellow/Blue), and `SLEEPING` (Red).
- **Supervisor Sync**: The GUI reads `hw_state.json` and `STATUS.md` timestamps to display live heartbeats for both the Task Supervisor and HW Supervisor (`ONLINE`, `SCALING`, `HUNG`, `OFFLINE`).

## 4. API Cost Optimizations
- Wrote robust wrappers in `skills/_models.py` (`call_complex`, `call_complex_batch`).
- **Anthropic API Guidelines Enforced**:
  - 300ms minimum throttling between requests.
  - Exponential backoff on `429 RateLimitError`.
  - System prompt ephemeral caching `cache_control: {"type": "ephemeral"}` applied to expensive, frequently called background skills (`plan_workload`, `review_discards`, `synthesize`).
  - Message Batches API ready for bulk/non-real-time operations.