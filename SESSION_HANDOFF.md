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
| Dashboard Endpoints | 26 (Rust) + 254+ (Python, +12 ingest, +2 mode control) | 2026-03-28 |
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

**Date:** 2026-03-28 (session 9)
**Session:** VS Code Claude Code — Training API + Save Name Fix + ML Metrics Wiring

### Training Status API — COMPLETE
- Added `/api/training/status` endpoint to `bridge_api.py` — returns ML metrics (episode, step, phase, reward, PPO losses, buffer size) or LLM-mode curriculum fallback
- Added `/api/training/diagnose` endpoint for `factorio_analyze` skill
- Added `update_training_status()` function — bridge pushes metrics after each `ml_tick()`
- Resolves 404 from launcher (`mod_factorio.py:630`), `factorio_observe.py:29`, `factorio_train.py:22`

### Save Name Fix — COMPLETE
- `bridge_config.py` default: `"sandbox.zip"` → `"biged-sandbox.zip"` (matches `setup_and_launch.py:137`)
- `fleet.toml` config: `save_file = "sandbox.zip"` → `"biged-sandbox.zip"`

### ML Metrics Wiring — COMPLETE
- `bridge.py` now tracks `_last_reward` and `_last_ppo_stats` across ticks
- After each `ml_tick()`, pushes full training snapshot via `update_training_status()`
- Fields: mode, episode, step, phase, phase_name, lessons_completed/total, last_reward, policy_loss, value_loss, entropy, total_updates, total_episodes, buffer_size

### Dashboard Training Proxy — COMPLETE
- New `/api/factorio/training-status` proxy endpoint in `dashboard.py`
- Browser can fetch ML training metrics without CORS issues

### Bridge Improvements (user edits during session)
- `ensure_player` body-check added to both LLM and ML tick (skip tick if no body)
- Curriculum `flat_state` enriched: player health/alive/has_character, resource totals
- Curriculum check runs every tick (not just when `_prev_state` exists)

**Files changed:** `bridge_api.py`, `bridge.py`, `bridge_config.py`, `fleet.toml`, `dashboard.py`

**Next priorities:**
1. **Live training observation** — verify ML agent makes coherent actions with all fixes applied
2. **Dashboard training visualization** — reward curves, action distribution charts in Factorio panel
3. **Reload Factorio mod** — `is_crafting` and `get_item_count` pcall fixes need mod reload
4. **Item name validation** — `burner-miner` in RCON logs (should be `burner-mining-drill`), trace source
5. **Curriculum Generator** — LLM generates new phase TOMLs from performance data
