# Factorio AgentBrain Autoresearch — Design Spec

**Date:** 2026-03-28
**Status:** Draft
**Author:** Claude + Max

## Overview

Apply autoresearch's autonomous experiment methodology to the Factorio AgentBrain module. The system optimizes prompts, brain parameters, and (eventually) trains a specialist model — all using autoresearch's core philosophy: fixed-budget experiment runs, single metric comparison, keep/discard logic.

**Not a fork of autoresearch.** Independent implementation in `fleet/factorio/`, same philosophy, no shared code. Autoresearch stays untouched for its GPT optimization job.

## Context: Human Speedrun Records

For calibration — what's possible from expert humans:

| Category | Time | Notes |
|----------|------|-------|
| Any% (Space Age 2.0, random seed) | ~3h 44m | Rapidly evolving |
| Default Settings (Vanilla 1.0/2.0) | ~1h 18m | No blueprints (Zaspar) |
| 100% Achievements (Space Age) | ~14h 11m | |

Our Phase 1-4 curriculum covers roughly the first 15-20 minutes of a speedrun's progression. Phase 5+ could push toward mid-game with SPM as the metric.

## Approach: Prompt Lab First

Three optimization targets, phased:

1. **Now:** Prompt optimization — swap prompt templates, measure lesson outcomes
2. **Now:** Hyperparameter tuning — brain params (temperature, plan size, thresholds)
3. **Future (500+ replays):** Specialist model — fine-tune 0.6b model on successful replay data

The specialist model does NOT replace qwen3:8b in the fleet. It only loads when the Factorio module is active.

## Section 1: Experiment Runner Core

**File:** `fleet/factorio/experiment_runner.py`

### Experiment Loop

```
1. Load baseline score (best so far per phase, from experiment_results.tsv)
2. Generate or select next candidate config
3. Setup:
   - Load save file via RCON (or reset to new game)
   - Inject candidate prompt + params into AgentBrain
   - Reset metrics counters
4. Run:
   - Start tick loop (reuses existing bridge.py — wraps, not replaces)
   - Wall clock timer: 10 min default (configurable in fleet.toml)
   - Collect all replay tuples during run
5. Score:
   - Compute phase-gated metric
   - Compare to baseline
6. Decision:
   - If better: update baseline, mark "keep", save candidate as new best
   - If worse/equal: mark "discard", revert to previous best candidate
7. Log:
   - Append to experiment_results.tsv
   - Append replay tuples to replay_log.jsonl (keep AND discard — failures are useful)
8. Repeat from 2
```

### Phase-Gated Metrics

| Phase | Metric | "Better" means |
|-------|--------|-----------------|
| 1 — Bootstrap | `lessons_passed` (0-3) | Higher |
| 2 — Automate | `lessons_passed + (1 / actions_taken)` | Higher |
| 3 — Science | `lessons_passed + (1 / actions_taken) - (0.1 × failure_rate)` | Higher |
| 4 — Expand | `lessons_passed + (1 / actions_taken) - (0.1 × failure_rate) + throughput_bonus` | Higher |

Phase 1 is pure pass/fail. Each subsequent phase layers in efficiency, reliability, and throughput.

**Metric definitions (aggregated per budget window, not per plan):**
- `lessons_passed` — count of lessons whose criteria evaluate to true at budget expiry
- `actions_taken` — total RCON actions attempted across all plans in the budget window
- `failure_rate` — `actions_failed / actions_taken` (0.0-1.0)
- `throughput_bonus` — normalized production rate from `GameMetrics` (items/min at budget end vs. baseline)

These are tracked by cumulative counters on `AgentBrain` (see Changes to Existing Files).

### Budget

Fixed 10-minute wall clock per experiment (configurable via `fleet.toml`). Comparable across runs, same principle as autoresearch's 5-minute constraint.

### Bridge Lifecycle Control

The experiment runner creates a new `FactorioBridge` instance per experiment. The bridge's `run()` method is currently an infinite async loop — the runner controls it via:

1. **Start:** `asyncio.create_task(bridge.run())` in a managed event loop
2. **Budget timer:** `asyncio.wait_for(bridge_task, timeout=budget_seconds)`
3. **Stop:** On timeout, call `bridge.stop()` (sets `_running = False`, bridge exits tick loop cleanly)
4. **Collect:** Read cumulative counters from `brain` before discarding the bridge instance
5. **Reset:** Create fresh `FactorioBridge` + `AgentBrain` for next experiment

The bridge needs a `stop()` method added (sets a flag checked in the tick loop). This is a small change — see Changes to Existing Files.

### Config Injection

The experiment runner builds a modified `BridgeConfig` per experiment:

```python
def build_experiment_config(base_config, candidate):
    """Merge candidate overrides into a copy of the base config."""
    cfg = copy.deepcopy(base_config)
    cfg.prompt_template = candidate.get("prompt", "baseline")
    cfg.plan_max_actions = candidate["params"].get("plan_size", cfg.plan_max_actions)
    cfg.ollama_timeout_secs = candidate["params"].get("ollama_timeout", cfg.ollama_timeout_secs)
    cfg.ollama_cooldown_secs = candidate["params"].get("cooldown_after_failure", cfg.ollama_cooldown_secs)
    cfg.plan_invalidation_failures = candidate["params"].get("failure_threshold", cfg.plan_invalidation_failures)
    cfg.temperature = candidate["params"].get("temperature", None)
    cfg.top_p = candidate["params"].get("top_p", None)
    return cfg
```

`AgentBrain.__init__` already receives `BridgeConfig` — the new fields flow through without a new injection mechanism. The prompt template name is resolved to a loaded TOML at brain construction time.

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Ollama crash mid-experiment | Brain's existing cooldown kicks in; if no plans generated for 2+ minutes, runner marks experiment `status=error` |
| Bridge exception | Runner catches, marks `status=error`, logs traceback, continues to next experiment |
| Factorio server unreachable | RCON reconnect backoff; if unreachable for full budget, `status=error` |
| OOM or system-level crash | Runner exits; results.tsv is append-only so no data lost |

Experiments marked `error` are excluded from baseline comparisons but replay data is still logged.

### Stop Conditions

- Manual interrupt (Ctrl+C)
- Configurable max experiments count (default: unlimited)
- Configurable total wall clock limit (e.g., "run for 2 hours then stop")
- Factorio server unreachable (graceful pause, retry after cooldown)

## Section 2: What Gets Optimized

### A) Prompt Templates

Extract current hardcoded system/user prompts into swappable TOML templates:

```
fleet/factorio/prompts/
  baseline.toml          # current hardcoded prompt, extracted as-is
  compact_v1.toml        # experiment: shorter, more structured
  cot_v1.toml            # experiment: chain-of-thought style
  ...
```

Each TOML contains `system_template` and `user_template` with placeholders:
- `{state}` — flattened game state markdown
- `{objective}` — current lesson from CurriculumManager
- `{previous_results}` — last plan's action outcomes

### B) Brain Parameters

Currently hardcoded in AgentBrain — extracted to a config dict:

| Parameter | Current | Range |
|-----------|---------|-------|
| Candidate Key | Code Field (`BridgeConfig`) | Current | Range |
|---------------|----------------------------|---------|-------|
| `plan_size` | `plan_max_actions` | 5-20 actions | 3-30 |
| `ollama_timeout` | `ollama_timeout_secs` | 60s | 15-120s |
| `cooldown_after_failure` | `ollama_cooldown_secs` | 30s | 5-60s |
| `failure_threshold` | `plan_invalidation_failures` | 3 consecutive | 1-5 |
| `idle_assembler_replan` | *(hardcoded in agent_brain.py)* | 3 ticks | 1-10 |
| `temperature` | *(new field)* | (Ollama default) | 0.1-1.0 |
| `top_p` | *(new field)* | (Ollama default) | 0.5-1.0 |

### Candidate Config Format

Each experiment run gets a `candidate.toml`:

```toml
prompt = "compact_v1"
load_save = "my_midgame_save"    # optional — loads this save before running
phase_override = 3                # which phase metrics to use for scoring
start_lesson = 0                  # which lesson to start eval from

[params]
plan_size = 10
temperature = 0.5
cooldown_after_failure = 15
failure_threshold = 3
```

### Candidate Generation Strategy

Manual + simple mutation for v1 — no fancy search algorithms:

| Mode | How it works |
|------|-------------|
| **Manual** | Drop a `.toml` in `fleet/factorio/prompts/`, runner picks it up |
| **Parameter sweep** | Vary one param at a time from baseline |
| **Random perturbation** | Jitter 2-3 params within ±20% of baseline |

The operator reads `experiment_results.tsv` and writes new candidates based on what's working. Same philosophy as autoresearch — human-guided, loop-validated.

## Section 3: Data Pipeline & Replay Logging

Every experiment run produces training data as a byproduct.

### Replay Log Format

**File:** `fleet/factorio/replay_log.jsonl` (gitignored, backed up by backup_manager)

```jsonl
{"ts": 1711612800, "phase": 1, "lesson": "Craft iron gear wheels", "state": {...}, "plan": [...], "actions_taken": 3, "actions_succeeded": 3, "lesson_passed": true, "experiment_id": "exp_0042"}
```

One line per plan execution (plan-level signal, not per-action). The `state` is the flattened dict AgentBrain already produces. Thousands of entries stay under 50MB.

### Results TSV Format

**File:** `fleet/factorio/experiment_results.tsv` (git tracked)

```tsv
experiment_id	timestamp	phase	save_file	prompt	metric	baseline	delta	status	description
exp_0001	2026-03-28T22:15:00	1	-	baseline	2.0	-	-	keep	initial baseline
exp_0002	2026-03-28T22:26:00	1	-	compact_v1	3.0	2.0	+1.0	keep	shorter prompt
exp_0003	2026-03-28T22:37:00	1	-	compact_v1	2.5	3.0	-0.5	discard	temperature too high
```

### Filtering for Future Training Data

When specialist model training is ready:

```
replay_log.jsonl
  → filter: lesson_passed == true
  → filter: actions_succeeded / actions_taken >= 0.8
  → format as (state + objective) → plan pairs
  → deduplicate near-identical states
  → split 90/10 train/val
```

**Data threshold:** Training unlocks at 500 successful plan entries. Runner logs a milestone but does not auto-trigger training — manual decision.

## Section 4: Save File Loading

Factorio saves are zip files at `%APPDATA%/Factorio/saves/`. The RCON `/load` command loads any save by name.

### Use Cases

| Use Case | How |
|----------|-----|
| **Evaluate from known positions** | Load a mid-game save → test adaptability, not just curriculum progression |
| **Learn from human play** | Load your saves → snapshot state → expert demonstration data for training |
| **Reproducible benchmarks** | Save a specific world state → every experiment starts from identical conditions |

### Integration

The `load_save` field in candidate configs triggers save loading:

1. If `load_save` set → RCON `/load {save_name}` → wait for game ready
2. Snapshot initial state (for replay log context)
3. Run brain against phase metrics as normal
4. If `save_file` omitted → start from default new game

**Caveat:** Existing saves may have progression beyond Phase 4. Higher-phase curricula or a freeplay evaluation mode (measure SPM/throughput over budget window) would be needed. Good scaling problem.

## Section 5: Future Specialist Model Training

Not built now. The design accommodates it cleanly when data is ready.

### Training Setup

**File:** `fleet/factorio/train_specialist.py` (future)

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Base model** | qwen3:0.6b | Smallest qwen, fits alongside 8b on GPU |
| **Method** | LoRA fine-tune | Cheap, fast, small adapter file |
| **Framework** | Unsloth or transformers + PEFT | Standard tooling |
| **Input format** | `<state>{flattened}</state><objective>{lesson}</objective>` | |
| **Output format** | JSON action plan (same as current Ollama output) | |
| **Training budget** | 5 min wall clock (autoresearch philosophy) | |
| **Eval metric** | Phase-gated, same as experiment runner | |
| **Output artifact** | GGUF → Ollama as `biged-factorio:0.6b` | |

### AgentBrain Integration

New config field in `bridge_config.py`:

```toml
[factorio.brain]
model = "qwen3:8b"                        # default
specialist_model = "biged-factorio:0.6b"  # optional, used when available
specialist_phases = [1, 2]                # phases where specialist is trusted
fallback_to_default = true                # use 8b if specialist fails
```

Brain checks: specialist available for this phase? Use it. Fails or untrained phase? Fall back to 8b. Zero disruption to the rest of the fleet.

### Training Loop (Future)

Same keep/discard pattern:

1. Train LoRA adapter on filtered replay data (5 min budget)
2. Export to GGUF
3. Load into Ollama as `biged-factorio:0.6b`
4. Run experiment runner: specialist vs. 8b baseline
5. If specialist scores better → keep as default for those phases
6. If worse → discard adapter, try different training params

### Note: Claude API Synthetic Data (Approach D, Much Later)

Would slot in at the data layer — generate high-quality (state → plan) pairs via Claude API, add to `replay_log.jsonl` with `source: "synthetic"` tag. Flows into training naturally, no architectural changes needed.

## File Layout

```
fleet/factorio/
  experiment_runner.py          # orchestrator — loop, scoring, keep/discard
  experiment_results.tsv        # scoreboard (git tracked)
  replay_log.jsonl              # training data accumulator (gitignored)
  train_specialist.py           # future — LoRA fine-tuning
  prompts/
    baseline.toml               # current prompt, extracted
    compact_v1.toml             # experiment candidates
    cot_v1.toml
    ...
  agent_brain.py                # existing — receives config injection
  bridge.py                     # existing — wrapped by experiment runner, gets stop() method
  bridge_config.py              # existing — new fields for prompt_template, temperature, top_p
  curriculum_manager.py         # existing — provides phase metrics
  curricula/                    # existing — phase definitions
```

## Config Integration

New section in `fleet.toml`:

```toml
[factorio.experiments]
budget_minutes = 10
max_experiments = 0              # 0 = unlimited
max_total_hours = 0              # 0 = unlimited
training_data_threshold = 500    # successful plans before training unlocks
results_file = "fleet/factorio/experiment_results.tsv"
replay_file = "fleet/factorio/replay_log.jsonl"
```

## Changes to Existing Files

| File | Changes |
|------|---------|
| `fleet/factorio/agent_brain.py` | Extract `SYSTEM_PROMPT` to template loader (`_load_prompt_template(name)`). Add `temperature`/`top_p` to Ollama `options` in `_generate_plan()`. Add cumulative counters: `total_actions`, `total_successes`, `total_failures`. Extract `idle_assembler_replan` threshold (hardcoded `3`) to `BridgeConfig`. Add `reset_counters()` method for experiment runner. |
| `fleet/factorio/bridge_config.py` | Add fields: `prompt_template` (str, default `"baseline"`), `temperature` (Optional[float]), `top_p` (Optional[float]), `idle_assembler_replan` (int, default 3). |
| `fleet/factorio/bridge.py` | Add `stop()` method (sets `_running = False`). Tick loop checks `_running` flag each iteration. |
| `fleet.toml` | Add `[factorio.experiments]` section (see Config Integration). |
| `.gitignore` | Add `fleet/factorio/replay_log.jsonl`. |

## Non-Goals (Explicit)

- **Not replacing autoresearch** — independent system, same philosophy
- **Not replacing qwen3:8b fleet-wide** — specialist only loads for Factorio
- **Not auto-triggering training** — human decides when to train
- **No Bayesian optimization / genetic algorithms** — manual + simple mutation for v1
- **No multi-GPU** — single 3080 Ti, time-sliced if training overlaps
