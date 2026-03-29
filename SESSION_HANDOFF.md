# Session Handoff

> **Purpose:** Cross-session coordination for Claude (and other AI) sessions working in this repo.
> Every session should read this file on start and update it before ending.

---

## Current State

| Metric | Value | Last Updated |
|--------|-------|--------------|
| Version | 0.900.00b (Python) / 0.9.0 (Rust) | 2026-03-25 |
| Skills | 96 standalone + 6 suites (was 132 files) | 2026-03-26 |
| Smoke Tests | 51/51 | 2026-03-28 |
| Factorio Module Tests | 88/88 (11 test files) + 64 ML tests (10 files) | 2026-03-28 |
| Ingest Sources | 18 (12 task + 5 RAG + 1 factorio-knowledge, ~2M+ rows) | 2026-03-27 |
| API Keys | 12 registered, 3 set | 2026-03-26 |
| Rust Tests | 116+ | 2026-03-25 |
| Dashboard Endpoints | 26 (Rust) + 254 (Python, +12 ingest) | 2026-03-26 |
| DB Tables | 9 (Rust schema) | 2026-03-25 |
| Branch | main | 2026-03-28 |
| Rust Crates | 6 (core, supervisor, server, bridge, gui, wasm) | 2026-03-25 |
| Rust Phase | All 6 phases complete + 18 audit fixes | 2026-03-25 |
| Helpers | 11 (_contract, _knowledge, _llm_parse, _dispatch, _report, _http, _models, _flywheel_rubric/grading/audit, _oss_core) | 2026-03-26 |
| Graph Views | 6 (fleet-overview, universe, data-flow, bottleneck-detector, knowledge-graph, training-pipeline) | 2026-03-26 |
| Graph Layouts | 4 (Radial, Radial Cluster, Cluster/fcose, Grid) + Fractal Brain | 2026-03-27 |
| Audit Reports | 4 (backend, frontend, cross-platform, integration) | 2026-03-27 |
| v1.0 Blockers | 20 critical issues identified | 2026-03-27 |

## Last Session

**Date:** 2026-03-28 (session 7, ended ~11:30pm PT)
**Session:** VS Code Claude Code — Factorio ML Overhaul (RL-first architecture) + Launcher Integration

### Factorio ML Overhaul — COMPLETE (8 new files, 64/64 ML tests, 5 skill updates, Lua mod update)
Replaced the LLM-driven Factorio agent with an RL-trained ML policy engine. LLM moved to config/strategy orchestration role outside gameplay loop.

**Architecture:** Three-layer system:
- **LLM Orchestrator** (config layer) — curriculum gen, training diagnostics, strategy, narration. Runs between episodes only.
- **ML Policy Engine** (decision layer) — PPO with CNN+MLP (~500K params), hierarchical action space, phase-gated rewards
- **Factorio Bridge** (execution layer) — existing RCON/Lua, plus new episode manager + state tensor encoder

**New files:**
| File | Purpose |
|------|---------|
| `fleet/factorio/action_space.py` | 8-action hierarchical encoding, phase-gated entity/recipe sets |
| `fleet/factorio/state_encoder.py` | GameState → 64x64x4 grid + 64-dim feature vector |
| `fleet/factorio/ml_policy.py` | PyTorch CNN+MLP policy+value network |
| `fleet/factorio/reward.py` | Phase-gated reward shaping with running normalization |
| `fleet/factorio/trainer.py` | PPO with GAE, trajectory buffer, checkpointing |
| `fleet/factorio/episode_manager.py` | Lua soft reset, game speed control, episode lifecycle |
| `fleet/factorio/llm_orchestrator.py` | Between-episode diagnostics and narration via Ollama |
| `tests/factorio/` (10 files) | 64 tests covering full ML pipeline |

**Key design decisions:**
- `fleet.toml [factorio] mode = "ml" | "llm"` toggles between RL and LLM agent
- All PyTorch imports are lazy (gated behind mode check) — LLM mode never loads torch
- Phase advancement propagates to encoder, action space, reward, episode manager
- `_sample_params()` bridges policy head logits → concrete EncodedAction values
- `mine` action added (ActionType.MINE=7) for hand-mining in early game bootstrap

**Specs/plans:**
- `docs/superpowers/specs/2026-03-28-factorio-ml-overhaul-design.md`
- `docs/superpowers/plans/2026-03-28-factorio-ml-overhaul.md`

**Launcher integration (post-core):**
- Agent Mode dropdown (ml/llm) with fleet.toml persistence
- Start/Stop Training button launches full stack: Factorio headless → bridge → resume
- Stop does graceful save: pause → RCON /server-save → /quit → terminate
- Live training metrics in Factorio tab (episode, step, phase, reward, losses)
- Biters toggle + New Save button (peaceful mode by default for training)
- Button state auto-resets when bridge goes down

**Bug fixes during live testing:**
- `ensure_player()` was only called in LLM tick, not ML tick — agent had no player entity
- Action mask was float32, needed bool — `masked_fill(~mask)` crashed
- Soft reset Lua was split across lines — RCON treated `/c` and code as separate commands
- User added `ml_tick_delay_ms` config for training speed control (already wired in bridge)
- User created `biged-sandbox-Dojo` save with biters disabled

**Commits this session:** 20 (93e26c4..662fea0)

**Next priorities:**
1. **Live training test** — restart bridge with fixes, verify agent actually executes actions
2. **Training metrics API** — bridge needs `/api/training/status` endpoint (skills + launcher poll it but it doesn't exist yet)
3. Dashboard training metrics visualization (reward curves, action distributions)
4. Curriculum Generator (LLM generates new phase TOMLs from performance data)
5. Strategy Advisor (LLM-set goal embeddings, deferred until Phase 3+)

**Known issue:** The save name is hardcoded to `biged-sandbox.zip` but user created `biged-sandbox-Dojo`. Either rename or update `find_or_create_save()` to use the Dojo save.

---

## Previous Session

**Date:** 2026-03-28 (session 6)
**Session:** VS Code Claude Code — Ollama Optimization (auto-detect + dashboard settings)

### Ollama Server Optimization — COMPLETE (3 files, 51/51 smoke)
Researched TurboQuant (Google ICLR 2026) and current Ollama optimization options, then implemented system-adaptive Ollama configuration that auto-detects GPU VRAM and sets optimal env vars for all local models.

**Research findings:**
- **Immediate wins:** Flash Attention (`OLLAMA_FLASH_ATTENTION=1`) + KV Cache Quantization (`OLLAMA_KV_CACHE_TYPE=q8_0/q4_0`) — free VRAM savings, 2x context
- **TurboQuant (future):** 3-bit KV cache, 49% memory reduction, 4.2% slower, zero quality loss. Community C impl exists, `llama.cpp-tq` fork experimental, not in Ollama mainline yet
- **Key insight:** fleet.toml had `gpu.mode = "eco"` (CPU-only) — flipping to `"full"` alone is 4-5x speedup on the RTX 3080 Ti

**What was built:**
- **`fleet.toml`** — new `[ollama.optimization]` section: `flash_attention`, `kv_cache_type`, `num_parallel`, `max_loaded_models` (all default `"auto"`, `tq` reserved for TurboQuant)
- **`fleet/process_manager.py`** — `_detect_vram_gb()` reads GPU VRAM via `gpu.py`, `_resolve_ollama_env()` maps 6 VRAM tiers to optimal env vars, explicit fleet.toml values override auto-detection. Env vars injected into Ollama's `Popen` call. Adopted-Ollama logs optimization warning.
- **`fleet/dashboard.py`** — `ollama` added to `_EDITABLE_SECTIONS` (fixed 403), schema added with descriptions
- **`fleet/templates/dashboard.html`** — `ollama` added to Hardware settings tab, nested TOML sub-tables flattened for generic renderer

**VRAM tier auto-detection:**
| VRAM | Flash | KV Cache | Parallel | Max Models |
|------|-------|----------|----------|------------|
| 0 (CPU) | off | f16 | 2 | 1 |
| 4-6 GB | on | q4_0 | 2 | 1 |
| 6-8 GB | on | q8_0 | 2 | 2 |
| 8-12 GB | on | q8_0 | 4 | 2 |
| 12-16 GB | on | q8_0 | 4 | 3 |
| 16+ GB | on | f16 | 6 | 4 |

**Applies to ALL local models** (qwen3:8b, 4b, 0.6b, conductor, vision) — env vars are server-level.

### Next Priorities
1. **Flip `gpu.mode` to "full"** — user's RTX 3080 Ti unused in eco mode, instant 4-5x speedup
2. **Live testing** — run experiment loop against actual Factorio instance
3. **TurboQuant integration** — when it hits Ollama mainline (est. Q2-Q3 2026), switch `kv_cache_type` to `tq`
4. **v1.0 audit blockers** (20 critical issues from 2026-03-27)
5. **Blueprint library** — scrape factorioprints.com, store in knowledge, deploy via RCON

### Previous Session

**Date:** 2026-03-28 (session 5)
**Session:** VS Code Claude Code — Factorio Autoresearch Experiment Loop (design + full implementation)

### Autoresearch Experiment Loop — COMPLETE (6 commits, 69 tests)
Applied autoresearch methodology to Factorio AgentBrain — autonomous prompt optimization, hyperparameter tuning, and future specialist model training.

**Design decisions (brainstorming):**
- All 3 targets: prompt optimization (now), hyperparameter tuning (now), specialist model (future, 500+ replays)
- Phase-gated metrics: P1=pass/fail, P2+=efficiency, P3+=failure penalty, P4+=throughput
- Save file loading: evaluate agent on existing Factorio saves
- Independent from autoresearch codebase: same philosophy, no shared code
- Specialist model (future): qwen3:0.6b LoRA fine-tune, only for Factorio, fleet's 8b untouched

**What was built:**
- **BridgeConfig** — 4 new fields: `prompt_template`, `temperature`, `top_p`, `idle_assembler_replan`
- **Prompt Loader** (`fleet/factorio/prompt_loader.py`) — TOML template system
- **Baseline Template** (`fleet/factorio/prompts/baseline.toml`) — hardcoded prompt extracted
- **AgentBrain Updates** — template prompts, Ollama options, cumulative counters, `reset_counters()`
- **Experiment Scorer** (`fleet/factorio/experiment_scorer.py`) — phase-gated `compute_score()`
- **Experiment Runner** (`fleet/factorio/experiment_runner.py`) — full loop + CLI
- **Candidates Dir** (`fleet/factorio/candidates/`) — experiment TOML configs
- **Config** — `[factorio.experiments]` in fleet.toml

**Spec:** `docs/superpowers/specs/2026-03-28-factorio-autoresearch-design.md`
**Plan:** `docs/superpowers/plans/2026-03-28-factorio-autoresearch.md`

**Usage:** `cd fleet && python -m factorio.experiment_runner --single fleet/factorio/candidates/baseline_test.toml --budget 600`

### Previous Session (session 4)

**Date:** 2026-03-28 (session 4)
**Session:** VS Code Claude Code — Factorio Agent Loop + Human Takeover Controls

### Agent Loop — COMPLETE (7 commits, 62 tests)
- **AgentBrain** (`fleet/factorio/agent_brain.py`): Hybrid plan-and-drain loop with Ollama qwen3:8b
- **CurriculumManager** (`fleet/factorio/curriculum_manager.py`): Phase lifecycle, TOML loading, auto-advance
- **4 Phases / 15 lessons** (`fleet/factorio/curricula/`): Bootstrap → Automate → First Science → Expand
- **Bridge integration**: `asyncio.to_thread` for brain calls, human commands prioritized

### Human Takeover Controls — COMPLETE (6 commits, 29 new tests, 88 total)
- **Pause/Resume**, **Directives**, **6 Presets**, **8 API endpoints**, **Dashboard UI**, **CLI**

**Standalone headless path:** `F:\Factorio` with `--config F:\Factorio\data-biged\config.ini`
