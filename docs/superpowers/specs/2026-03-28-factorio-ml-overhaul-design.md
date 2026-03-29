# Factorio ML Overhaul — RL-First with LLM Config Layer

**Date:** 2026-03-28
**Status:** Approved
**Scope:** Replace LLM-driven Factorio agent with ML policy engine; LLM becomes config/strategy orchestrator

---

## 1. Problem Statement

The current Factorio agent uses Ollama (qwen3:8b) to generate action plans every tick cycle. This approach:
- Has produced zero useful gameplay progress
- Is expensive per-decision (60s timeout, full prompt rebuild each cycle)
- Cannot learn from experience — each plan is stateless
- Doesn't scale — LLM quality plateaus regardless of playtime

**Goal:** Replace with a trained ML policy that learns to play through reinforcement learning, with the LLM moved to a configuration/orchestration role outside the gameplay loop.

## 2. Architecture

Three-layer system:

```
┌─────────────────────────────────────┐
│  LLM Orchestrator (config layer)    │
│  - Curriculum generation            │
│  - Training diagnostics             │
│  - Strategy objectives              │
│  - Hyperparameter tuning            │
│  - Natural language explanations    │
└──────────────┬──────────────────────┘
               │ sets objectives, tunes params
┌──────────────▼──────────────────────┐
│  ML Policy Engine (decision layer)  │
│  - State encoder (grid + features)  │
│  - Policy network (actions)         │
│  - Value network (expected reward)  │
│  - RL training loop (PPO)           │
│  - Replay buffer                    │
└──────────────┬──────────────────────┘
               │ actions
┌──────────────▼──────────────────────┐
│  Factorio Bridge (execution layer)  │
│  - RCON/Lua (existing, kept)        │
│  - State parser → tensor encoder    │
│  - Action translator (existing)     │
│  - Episode manager (NEW)            │
└─────────────────────────────────────┘
```

### Layer Responsibilities

**LLM Orchestrator** — runs between episodes, never during. Advisory and async.
- Generates/modifies curriculum TOML files based on agent performance
- Analyzes reward curves and failure patterns, suggests hyperparameter changes
- Sets high-level strategy objectives (encoded as goal embeddings in feature vector)
- Narrates agent behavior for the dashboard UI
- Uses Ollama (qwen3:8b) locally, Claude API for complex diagnostics

**ML Policy Engine** — the hot loop. Makes every gameplay decision.
- Encodes game state as tensors (spatial grid + feature vector)
- Runs policy network forward pass → action selection
- Collects trajectories for PPO training
- Checkpoints models, logs metrics
- Trains on local GPU (RTX 3080 Ti), optional cloud burst via OAuth/VSCode

**Factorio Bridge** — execution and perception. Mostly existing code.
- RCON connection, Lua mod, state parsing (existing)
- NEW: Tensor encoder (GameState → grid + feature tensors)
- NEW: Episode manager (save/restore, timeout, curriculum-aware reset)
- Action translator (existing, maps action dicts → RCON Lua commands)

## 3. State Representation

### Spatial Grid (CNN input)

Fixed-size grid centered on player position: **64×64 tiles, 4 channels.**

| Channel | Encoding | Range |
|---------|----------|-------|
| 0 | Entity type ID | 0 = empty, 1 = furnace, 2 = inserter, 3 = belt, 4 = assembler, ... |
| 1 | Entity direction | 0-7 normalized to 0.0-1.0 |
| 2 | Resource density | Ore/coal amounts, normalized 0.0-1.0 by max observed |
| 3 | Connectivity | Belt flow direction, inserter I/O encoding |

Entities outside the 64×64 window are not visible to the agent. The window moves with the player. This forces the agent to learn local placement patterns first — spatial awareness scales with training, not architecture.

### Feature Vector (MLP input)

~80-dimensional flat vector:

| Feature Group | Dimensions | Encoding |
|---------------|-----------|----------|
| Inventory counts | ~30 | Top 30 item types, normalized by typical maximums |
| Research state | ~10 | Current tech one-hot + progress 0.0-1.0 |
| Power status | 3 | Satisfaction ratio, generation, consumption (normalized) |
| Production rates | ~15 | Items/min for key resources (normalized) |
| Time | 2 | Game tick (normalized), episode step (normalized) |
| Curriculum context | ~10 | Phase one-hot + lesson index + goal embedding |
| Strategy objective | ~10 | LLM-set goal embedding (from orchestrator) |

### Combined Network

```
Grid 64×64×4  → Conv2d layers → 128-dim spatial embedding
Feature vec ~80 → Linear layers → 64-dim context embedding
                                    ↓
                              Concat (192-dim)
                                    ↓
                            Shared MLP (256 → 128)
                                    ↓
                    ┌───────────────┴───────────────┐
              Policy head                     Value head
         (action type logits)            (scalar expected return)
         + parameter heads
```

**Size estimate:** ~500K parameters. Trains comfortably on 3080 Ti. Inference: <1ms per step.

## 4. Action Space

Hierarchical discrete action space:

| ID | Action | Parameters | Parameter Encoding |
|----|--------|------------|--------------------|
| 0 | place | entity_type, dx, dy, direction | Categorical(~20) × Discrete(11) × Discrete(11) × Discrete(8) |
| 1 | craft | recipe_id, count | Categorical(~30) × Discrete(10) |
| 2 | research | tech_id | Categorical(~20) |
| 3 | move | dx, dy | Discrete(11) × Discrete(11) — range [-5, +5] |
| 4 | set_recipe | grid_x, grid_y, recipe_id | Discrete(64) × Discrete(64) × Categorical(~30) |
| 5 | remove | grid_x, grid_y | Discrete(64) × Discrete(64) |
| 6 | wait | (none) | — |

**Policy outputs:**
1. Action type: 7-way softmax
2. Per-action parameter heads: only the selected action's head is used/trained per step
3. Invalid action masking: mask unavailable actions based on inventory/research state

This is a standard hierarchical action space. PPO handles it well with per-head losses.

## 5. Reward Function

### Curriculum-Driven Rewards

Built on the existing `experiment_scorer.py` and curriculum criteria:

**Milestone rewards:**
- +1.0 on lesson completion
- +5.0 on phase completion
- +10.0 on full curriculum completion (all 4 phases)

**Shaping rewards (per step):**
- +0.01 per new item type appearing in inventory (exploration bonus)
- +0.05 per newly placed entity that's functional (connected to power/belt)
- +0.1 per research progress increment
- -0.1 per failed action (invalid placement, missing materials, bad target)
- -0.01 per step (time pressure — discourages idling)
- +0.02 per production rate increase (items/min delta for any tracked resource)

**Phase-gated scaling:**
- Phase 1: Only milestone + failed action + time pressure (simple signal)
- Phase 2: Add entity placement and production rate bonuses
- Phase 3+: Add throughput bonuses, research progress weighting increased
- Phase 4: Full reward function active

**Reward normalization:** Running mean/std normalization on returns (standard PPO practice).

## 6. Training Loop

### Episode Structure

```
def run_episode(policy, bridge, curriculum, max_steps=2000):
    bridge.episode_manager.reset(curriculum.current_phase)  # load clean save
    state = bridge.get_state_tensors()
    trajectory = []

    for step in range(max_steps):
        action, log_prob, value = policy.act(state)
        result = bridge.execute(action)
        reward = compute_reward(state, action, result, curriculum)
        next_state = bridge.get_state_tensors()
        done = curriculum.check_complete(next_state)

        trajectory.append((state, action, reward, log_prob, value, done))
        state = next_state

        if done:
            break

    return trajectory
```

### Training Configuration

| Parameter | Value | Notes |
|-----------|-------|-------|
| Algorithm | PPO (clip) | Stable, works with discrete hierarchical actions |
| Clip ratio | 0.2 | Standard |
| Value loss coefficient | 0.5 | Standard |
| Entropy bonus | 0.01 | Encourage exploration, decay over training |
| Learning rate | 3e-4 | Adam, with linear warmup |
| Batch size | 64 steps | Fits in 3080 Ti VRAM easily |
| Update frequency | Every 512 steps | ~4 episodes worth |
| Discount (γ) | 0.99 | Long-horizon (factory building is sequential) |
| GAE (λ) | 0.95 | Standard |
| Max episode steps | 2000 | ~30 min real-time at medium cadence |
| Checkpoint frequency | Every 20 episodes | Save model + metrics |

### Episode Manager (NEW bridge component)

```
class EpisodeManager:
    """Manages game state for RL training episodes."""

    def reset(self, phase: int) -> None:
        """Load the clean save file for the given curriculum phase."""
        # RCON: /c game.server_save("checkpoint")
        # RCON: load save file for phase

    def save_checkpoint(self, name: str) -> None:
        """Save current game state for later restoration."""

    def restore_checkpoint(self, name: str) -> None:
        """Restore a previously saved game state."""

    def get_episode_info(self) -> dict:
        """Return episode step count, elapsed time, phase."""
```

### Training Speed (Real Factorio)

- Factorio headless runs at ~60 UPS (updates per second)
- Agent acts every 1-5 seconds (cadence-dependent)
- ~2000 steps/episode → ~30-60 min per episode
- ~20-40 episodes/day with real game
- Sufficient for Phase 1 curriculum (simple tasks, fast feedback)
- Build Python sim for Phase 2+ when speed becomes the bottleneck

## 7. LLM Orchestrator

### Trigger Points (between episodes, never during)

| Trigger | LLM Job | Output |
|---------|---------|--------|
| Phase complete | Curriculum Generator | New/modified phase TOML |
| 10 episodes with no lesson progress | Training Diagnostician | Diagnosis + hyperparameter suggestions |
| Manual request (dashboard/CLI) | Strategy Advisor | Goal embedding update |
| Every episode end | Dashboard Narrator | Natural language summary |
| Training stall detected | Training Diagnostician | Reward shaping adjustments |

### Curriculum Generator

```
Input: Agent performance history (rewards, lesson pass rates, common failure actions)
Output: Modified or new curriculum TOML file

Example LLM prompt:
  "The RL agent has completed Phase 1 (bootstrap) in 45 episodes.
   Success rate by lesson: craft_gears=92%, place_furnaces=78%, smelt_iron=65%.
   Common failures: placing furnaces without fuel access, smelting before mining.
   Generate a Phase 1.5 remedial curriculum focusing on resource chain awareness."
```

### Training Diagnostician

```
Input: Last N episodes' reward curves, action distributions, failure logs
Output: Natural language diagnosis + suggested changes

Example output:
  "Agent is stuck in a local optimum: crafting items but never placing them.
   The placement reward (+0.05) may be too low relative to craft exploration (+0.01).
   Suggestion: increase placement reward to +0.15 for next 20 episodes.
   Also: entropy bonus is too low (0.005) — agent has collapsed to 3 action types.
   Suggestion: reset entropy to 0.02."
```

### Strategy Advisor

Encodes high-level goals as a 10-dim embedding in the feature vector. The LLM sets this based on game phase and agent needs:

```
"prioritize_power" → [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
"expand_mining"    → [0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
"build_science"    → [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]
(or learned embeddings from training)
```

### Dashboard Narrator

Generates human-readable explanations for the dashboard:
- "Episode 47: Agent learned to chain furnaces with inserters. Smelting throughput up 3x."
- "Training stall detected: agent loops between move and wait actions. Diagnostician triggered."

Uses Ollama locally for routine narration, Claude API for complex diagnostics.

## 8. Migration Strategy

### What Gets Replaced

| Current Component | Fate | Replacement |
|-------------------|------|-------------|
| `agent_brain.py` | **Replaced** | `ml_policy.py` (policy network + inference) |
| `AgentBrain._generate_plan()` | **Removed** | Policy forward pass |
| `AgentBrain.next_action()` | **Replaced** | `policy.act(state_tensors)` |
| Plan queue / directives / presets | **Moved to LLM orchestrator** | Goal embeddings + strategy objectives |
| `bridge.py` tick loop | **Modified** | Tensor encoding + episode management added |
| `experiment_runner.py` | **Evolved** | Becomes RL training loop |
| `experiment_scorer.py` | **Evolved** | Becomes reward function |
| `prompt_loader.py` | **Kept** | LLM orchestrator still uses TOML prompts |
| Curriculum system | **Kept** | Reward function + phase progression |
| RCON/Lua/state parser | **Kept** | Execution layer unchanged |
| 5 fleet skills | **Updated** | New skill interfaces for ML agent |
| Dashboard tab | **Updated** | Show training metrics, reward curves |

### What Gets Added

| New Component | Purpose |
|---------------|---------|
| `fleet/factorio/ml_policy.py` | Policy + value network (PyTorch) |
| `fleet/factorio/state_encoder.py` | GameState → grid tensor + feature vector |
| `fleet/factorio/reward.py` | Reward computation from state transitions |
| `fleet/factorio/trainer.py` | PPO training loop, replay buffer |
| `fleet/factorio/episode_manager.py` | Save/restore, reset, episode lifecycle |
| `fleet/factorio/llm_orchestrator.py` | Curriculum gen, diagnostics, strategy, narration |
| `fleet/factorio/action_space.py` | Hierarchical action encoding/decoding |
| `fleet/factorio/checkpoints/` | Model checkpoints directory |

## 9. Training Infrastructure

### Local (Default)

- **GPU:** RTX 3080 Ti — 12GB VRAM, more than enough for 500K param model
- **Framework:** PyTorch (already in autoresearch dependencies)
- **Training:** Single-process, synchronous (one Factorio instance)
- **Storage:** Checkpoints in `fleet/factorio/checkpoints/`, metrics in fleet.db
- **Monitoring:** Dashboard shows reward curves, episode metrics, action distributions

### Cloud Burst (Manual via OAuth/VSCode)

- Dispatched manually through existing OAuth flow in VSCode
- Use case: longer RL runs, hyperparameter sweeps, multi-seed experiments
- Same codebase, just runs on bigger GPU
- Results sync back to local checkpoints directory

## 10. Phase Gating

### Phase 1: Bootstrap (episodes 1-100)
- Simple curriculum (craft, place, smelt)
- Sparse rewards (lesson completion + failed action penalty)
- Fast episodes (~500 steps, ~10 min)
- Goal: prove the pipeline works end-to-end

### Phase 2: Automation (episodes 100-500)
- Power, belts, mining drills
- Add production rate shaping rewards
- Longer episodes (~1000 steps)
- Goal: agent builds functional production lines

### Phase 3: Science (episodes 500-2000)
- Research, labs, red science
- Full reward function active
- Consider building Python sim here if episode speed is bottleneck
- Goal: agent completes research trees

### Phase 4: Expansion (episodes 2000+)
- Green science, electronics, scaling
- Self-distillation opportunity (clone best RL runs into smaller net)
- LLM orchestrator generating novel curriculum phases
- Goal: agent plays Factorio competently beyond hand-crafted curriculum

## 11. Success Criteria

| Metric | Target | How Measured |
|--------|--------|-------------|
| Phase 1 completion | <100 episodes | Curriculum lesson pass tracking |
| Phase 2 completion | <500 episodes | Curriculum lesson pass tracking |
| Actions per second | >1 action/sec | Bridge step timing |
| Failed action rate | <20% by episode 50 | Action result tracking |
| Training stability | No reward collapse over 50 episodes | Reward curve monitoring |
| LLM orchestrator latency | <30s per diagnosis | Timer on orchestrator calls |
| Model checkpoint size | <10MB | File size check |
| GPU memory usage | <4GB during training | nvidia-smi monitoring |

## 12. Dependencies

- **PyTorch** — policy network, training (already available via autoresearch)
- **NumPy** — tensor operations (already available)
- **Factorio 2.0.76+** headless server — training environment
- **Existing bridge infrastructure** — RCON, Lua mod, state parser, action translator
- **Existing curriculum system** — phase TOMLs, criteria evaluation
- **Ollama** — LLM orchestrator (local)
- **Claude API** — complex diagnostics (optional, existing OAuth)

## 13. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Factorio episode reset is slow | Training bottleneck | Start with real game; build Python sim when needed |
| Reward shaping is wrong | Agent learns degenerate behavior | LLM diagnostician monitors; phase-gated reward introduction |
| Action space too large | Slow convergence | Invalid action masking; start with reduced entity/recipe sets per phase |
| State encoding misses key info | Agent can't learn | Validate encoding against known-good states; add channels iteratively |
| RCON latency | Slow step time | Batch state reads; cache entity grid between steps |
| Training instability | Wasted compute | PPO is robust; checkpoint frequently; LLM diagnostician catches stalls |
