# Action Packs & Blueprint Stamps — Design Spec

**Date:** 2026-03-30
**Status:** Approved
**Goal:** Give RL agents reusable action packs (multi-step sequences) and Factorio blueprint stamps (instant placement) to accelerate progression from bootstrap to white science (space science packs).

## Overview

Hybrid action pack system for the Factorio RL agent:

1. **Hardcoded action packs** — curated multi-step sequences for known-good patterns (smelter lines, science assemblers)
2. **Blueprint stamps** — real Factorio blueprint strings placed via single RCON call (smelter arrays, power stations, science blocks)
3. **Learned packs** — successful action sequences discovered during training, promoted to reusable packs after evaluation
4. **8-checkpoint curriculum** — streamlined path from bootstrap to white science, each checkpoint unlocking new packs/stamps

## Design Decisions

- **Approach:** Pragmatic hybrid (Approach 3) — single policy network with pack/stamp action types, builds on existing dependency resolver and curriculum system
- **Blueprint strategy:** Real blueprint stamps for solved layouts (smelter arrays, science blocks), action-sequence packs for connective tissue (belt routing, inserter placement)
- **Science path:** Streamlined minimum viable factory per tier, not full vanilla optimization
- **Learning:** Post-hoc pack discovery from successful checkpoint completions, not online skill learning

---

## Section 1: Pack Registry & Action Types

### New Action Types

Two new entries in `ActionType(IntEnum)`:

```python
PACK  = 9   # Execute a multi-step action pack (step-by-step, per-step rewards)
STAMP = 10  # Place a Factorio blueprint in one RCON call (instant, one reward)
```

### PackRegistry

Singleton holding all available packs, indexed by ID:

```python
class PackRegistry:
    packs: dict[int, ActionPack]          # step-by-step sequences
    stamps: dict[int, BlueprintStamp]     # real Factorio blueprint strings
    phase_gates: dict[int, list[int]]     # which packs unlock at which checkpoint
    learned_packs: list[ActionPack]       # discovered from successful runs

    def get_available(self, phase: int, lesson: int) -> list[ActionPack | BlueprintStamp]:
        """Return packs/stamps unlocked at current checkpoint."""

    def register_pack(self, pack: ActionPack) -> int:
        """Add a hardcoded pack, return its ID."""

    def register_stamp(self, stamp: BlueprintStamp) -> int:
        """Add a blueprint stamp, return its ID."""

    def promote_learned(self, candidate: ActionPack) -> int | None:
        """Promote a learned candidate if it meets quality threshold."""
```

### ActionPack

```python
@dataclass
class ActionPack:
    name: str                      # "smelt_iron_line"
    actions: list[dict]            # ordered primitive actions
    phase_required: int            # minimum checkpoint to unlock
    origin: str                    # "hardcoded" | "learned"
    success_count: int = 0         # times completed successfully
    avg_reward: float = 0.0        # running average reward earned
```

### BlueprintStamp

```python
@dataclass
class BlueprintStamp:
    name: str                      # "48_furnace_smelter"
    blueprint_string: str          # Factorio export string (0eNR...)
    footprint: tuple[int, int]     # width x height in tiles
    phase_required: int            # minimum checkpoint to unlock
    required_items: dict[str, int] # inventory needed to stamp
```

### Policy Network Additions

Two new heads on the existing 128-dim trunk output:
- `pack_head (128 → MAX_PACK_SLOTS)` — which pack/stamp to invoke (fixed 64 slots, unused slots masked)
- `offset_head (128 → 11×11)` — discrete dx, dy grid matching existing [-5,+5] bin system

The existing action mask mechanism gates pack availability by phase and inventory.

### Thread Safety

PackRegistry uses copy-on-write for `packs`/`stamps`/`learned_packs` — mutations create a new dict, swap atomically. No locks needed for read-heavy access during training.

---

## Section 2: Checkpoint Curriculum

Eight checkpoints aligned with Factorio science progression. Expands the current 4-phase curriculum.

### Checkpoint 0: Bootstrap (existing Phase 1)
- **Goal:** Place furnaces, mine, smelt iron/copper
- **Packs:** `smelt_iron_line`, `smelt_copper_line`
- **Stamps:** None (teach primitives first)
- **Criteria:** `"entities.stone-furnace >= 5 AND entities.burner-mining-drill >= 2 AND produced.iron-plate >= 100"`

### Checkpoint 1: Red Science (Automation)
- **Goal:** Produce 10 automation science packs + feed lab
- **Packs:** `gear_assembler`, `red_science_assembler`
- **Stamps:** `basic_power_station` (boiler + steam-engine + offshore-pump)
- **Criteria:** `"produced.automation-science-pack >= 10 AND entities.lab >= 1"`

### Checkpoint 2: Green Science (Logistic)
- **Goal:** Produce 10 logistic science packs
- **Packs:** `belt_assembler`, `inserter_assembler`, `green_science_line`
- **Stamps:** `main_bus_starter` (4-lane iron/copper bus segment)
- **Criteria:** `"produced.logistic-science-pack >= 10"`

### Checkpoint 3: Blue Science (Chemical) — Oil Unlocks
- **Goal:** Produce 10 chemical science packs
- **Packs:** `oil_refinery_setup`, `sulfur_line`, `red_circuit_line`
- **Stamps:** `oil_processing_block`, `chemical_science_block`
- **Criteria:** `"produced.chemical-science-pack >= 10"`

### Checkpoint 4: Purple Science (Production)
- **Goal:** Produce 10 production science packs
- **Packs:** `steel_smelter_line`, `electric_furnace_upgrade`, `rail_segment`
- **Stamps:** `48_furnace_smelter` (steel), `production_science_block`
- **Criteria:** `"produced.production-science-pack >= 10"`

### Checkpoint 5: Yellow Science (Utility)
- **Goal:** Produce 10 utility science packs
- **Packs:** `blue_circuit_line`, `speed_module_line`, `processing_unit_assembler`
- **Stamps:** `utility_science_block`, `solar_field`
- **Criteria:** `"produced.utility-science-pack >= 10"`

### Checkpoint 6: Rocket Assembly
- **Goal:** Build rocket silo + produce rocket parts
- **Packs:** `rocket_fuel_line`, `low_density_structure_line`, `rocket_control_unit_line`
- **Stamps:** `rocket_silo_complex`
- **Criteria:** `"entities.rocket-silo >= 1 AND produced.rocket-part >= 100"`

### Checkpoint 7: White Science (Space)
- **Goal:** Launch rocket with satellite → space science packs
- **Packs:** `satellite_assembler`, `launch_sequence`
- **Stamps:** `satellite_assembly_block`
- **Criteria:** `"produced.space-science-pack >= 10"`

### Checkpoint Mechanics

- Criteria use the existing `evaluate_criteria()` format (AND/OR expressions)
- Each checkpoint unlocks: new entities/recipes (existing), new packs/stamps (new)
- Completion bonuses scale: +10, +20, +30, +40, +50, +60, +70, +80
- Blueprint stamps declare `required_items` — agent must have inventory before stamping

### Agent Learning Focus

The agent learns *sequencing, placement, and logistics* — not layout design:
- **When** to invoke a smelter stamp (needs more throughput)
- **Where** to place it (near resources, connected to bus)
- **How** to connect it (belt routing, inserter placement, power distribution)

---

## Section 3: Pack Execution Engine

### Bridge Integration

Pack/stamp execution hooks into the existing bridge tick loop:

```
Bridge tick:
  1. Parse state (existing)
  2. Policy selects action
     ├─ Primitive (0-8): execute as today
     ├─ PACK (9): start/continue pack execution
     │   └─ PackExecutor tracks current step
     │   └─ One primitive per tick (preserves per-step rewards)
     │   └─ Can abort if: step fails 3x, state invalidates pack
     └─ STAMP (10): single RCON batch call
         └─ Inventory check first (fail if missing items)
         └─ Send blueprint via /biged-blueprint {string, position}
         └─ One reward signal for whole stamp
```

### PackExecutor

Manages in-flight pack state:

```python
class PackExecutor:
    current_pack: ActionPack | None
    step_index: int
    retry_count: int
    offset: tuple[int, int]       # where the pack is being placed

    def start(self, pack: ActionPack, offset: tuple[int, int]) -> dict:
        """Begin pack execution, return first primitive action."""

    def next_step(self, prev_result: dict) -> dict | None:
        """Return next action, or None if Done/Abort."""

    @property
    def abort_reason(self) -> str | None: ...
```

### Abort Conditions

- Step fails 3 consecutive times → abort, penalty (-0.5)
- Inventory depleted mid-pack → abort, small penalty (-0.2)
- Entity destroyed during pack → abort via existing invalidation events

### Reward Signals

| Signal | Value | When |
|--------|-------|------|
| Per-step rewards | existing signals | Each primitive within a pack |
| Pack completion bonus | +1.0 | All steps completed successfully |
| Stamp completion bonus | +2.0 | Blueprint placed successfully |
| Abort penalty | -0.5 | Pack terminated early |

### Lua-Side Blueprint Support

New RCON command for the Factorio mod:

```
/biged-blueprint {"blueprint": "0eNR...", "position": {"x": 10, "y": 5}}
```

The Lua mod decodes the blueprint string and places all entities. Returns `{"success": true/false, "entities_placed": [...]}`. Requires a Lua extension to the existing mod.

### Dependency Resolver Integration

Before starting a pack, the PackExecutor calls the existing dependency resolver to verify inventory sufficiency. If insufficient, the pack is masked out of the action space for that tick.

---

## Section 4: Learned Pack Discovery

### Recording

When the agent completes a checkpoint, the trainer snapshots recent successful actions:

```python
class PackRecorder:
    buffer: deque[ActionRecord]    # rolling window of recent actions
    max_length: int = 100          # cap pack length

    def on_checkpoint_complete(self, checkpoint_id: int) -> ActionPack | None:
        """Extract and evaluate a candidate pack from recent actions."""
```

### Evaluation Criteria

A candidate becomes a real pack if:

| Criterion | Threshold |
|-----------|-----------|
| Action count | 5-50 (too short = trivial, too long = brittle) |
| Replay success rate | > 70% over 3 replay attempts |
| Net reward | > average reward for that checkpoint |
| Overlap with existing packs | < 80% action similarity |

### Promotion Flow

```
Checkpoint complete
  → Record action subsequence from buffer
  → Replay 3x in same episode (reset to pre-checkpoint state via Factorio save/load)
  → If passes all criteria → registry.promote_learned(candidate)
  → Available to all agents in future runs
```

**Replay mechanism:** Before each checkpoint attempt, the bridge auto-saves the game state via RCON (`/save checkpoint_N_pre`). Replay uses `/load checkpoint_N_pre` to restore the same starting conditions. This is the simplest reliable replay — no state approximation needed.

### Storage

- Learned packs serialize to `fleet/factorio/packs/learned/` as JSON
- Loaded on bridge startup alongside hardcoded packs
- Hardcoded packs stored in `fleet/factorio/packs/hardcoded/`
- Blueprint strings stored in `fleet/factorio/packs/blueprints/`

### Pruning

Learned packs that drop below 40% success rate over 20 invocations get demoted back to candidates. Keeps the library clean as the agent improves.

---

## Section 5: Policy Network Changes

### Modified Architecture

```
Existing trunk (320→256→128)       # 128 local + 128 world + 64 features
  ├─ action_head (128→11)          # was 9, now +PACK +STAMP
  ├─ entity_head (128→max_ents)    # unchanged
  ├─ recipe_head (128→max_recs)    # unchanged
  ├─ tech_head (128→20)            # unchanged
  ├─ position/direction heads      # unchanged
  │
  NEW:
  ├─ pack_head (128→64)            # MAX_PACK_SLOTS=64, unused slots masked
  └─ offset_head (128→11×11)       # discrete dx, dy matching existing bin system
```

### Parameter Head Routing

`get_action_params()` gains two new cases:

```python
elif action_type == ActionType.PACK:
    return {"pack_logits": self.pack_head(trunk), "offset": self.offset_head(trunk)}
elif action_type == ActionType.STAMP:
    return {"pack_logits": self.pack_head(trunk), "offset": self.offset_head(trunk)}
```

`_sample_params()` in bridge.py gains matching cases to sample pack_id from masked pack_logits and (dx, dy) from offset logits.

### Action Masking

Extends existing mask mechanism:

```python
def get_action_mask(phase, lesson, inventory, packs_available):
    mask = existing_mask(phase, lesson)       # entities/recipes

    # Mask PACK/STAMP if no packs available at current checkpoint
    if not packs_available:
        mask[ActionType.PACK] = 0
        mask[ActionType.STAMP] = 0

    # Per-pack mask: hide packs the agent can't afford
    pack_mask = [
        1 if pack.can_execute(inventory) else 0
        for pack in registry.get_available(phase, lesson)
    ]
    return mask, pack_mask
```

### Sampling Flow

```
1. action_head samples → PACK (9) or STAMP (10)
2. pack_head samples → which pack/stamp (masked by availability + inventory)
3. offset_head outputs → (dx, dy) relative placement
4. If PACK: PackExecutor starts, returns primitives one per tick
5. If STAMP: single RCON blueprint call, immediate result
```

### Training

- No PPO algorithm changes needed
- PACK/STAMP are two more action types in the categorical distribution
- Entropy bonus naturally encourages exploring pack usage early in training

**PPO trajectory handling during pack execution:**

During pack execution, the policy is NOT called — the PackExecutor replays primitives. These internal ticks are **excluded from the PPO trajectory buffer**. Instead:
- The PACK action's log_prob is the probability of selecting PACK + selecting the specific pack_id
- The PACK action's reward is the **cumulative reward** of all primitives within the pack, plus the completion bonus (or abort penalty)
- The PACK action's value target uses the cumulative discounted return from pack start to pack end
- This treats the pack as a single temporally-extended action (Options framework semantics)

For STAMP actions: single tick, single reward, standard PPO transition — no special handling needed.

### State Encoder Addition

One new feature added to the feature vector (bumps `_BASE_FEATURE_DIM` from 68 to 69):
- `active_pack_progress` — 0.0 if no pack running, else `step_index / total_steps`
- Lets the value function know a pack is in-flight and estimate remaining reward

**Note:** This changes the policy's input dimension. Saved model checkpoints from before this change are incompatible and require retraining. Version the checkpoint format in the save metadata.

---

## File Structure

```
fleet/factorio/
  ├─ pack_registry.py          # PackRegistry, ActionPack, BlueprintStamp
  ├─ pack_executor.py          # PackExecutor (in-flight pack state machine)
  ├─ pack_recorder.py          # PackRecorder (learned pack discovery)
  ├─ packs/
  │   ├─ hardcoded/            # Curated action packs (JSON)
  │   ├─ blueprints/           # Factorio blueprint strings (JSON)
  │   └─ learned/              # Agent-discovered packs (JSON)
  ├─ curricula/
  │   ├─ checkpoint_0.toml     # Bootstrap (existing phase 1)
  │   ├─ checkpoint_1.toml     # Red science
  │   ├─ checkpoint_2.toml     # Green science
  │   ├─ checkpoint_3.toml     # Blue science
  │   ├─ checkpoint_4.toml     # Purple science
  │   ├─ checkpoint_5.toml     # Yellow science
  │   ├─ checkpoint_6.toml     # Rocket assembly
  │   └─ checkpoint_7.toml     # White science
  ├─ action_space.py           # Modified: +PACK, +STAMP action types
  ├─ ml_policy.py              # Modified: +pack_head, +offset_head
  ├─ state_encoder.py          # Modified: +active_pack_progress feature
  ├─ reward.py                 # Modified: +pack/stamp completion bonuses
  ├─ bridge.py                 # Modified: pack/stamp execution in tick loop
  └─ curriculum_manager.py     # Modified: 8 checkpoints, pack gating
```

## Lua Mod Changes

### Blueprint RCON Command

New handler in the Factorio mod for `/biged-blueprint`:

```lua
-- 1. Decode blueprint string using game.decode_blueprint_string(str)
-- 2. Offset all entity positions by the given (x, y) anchor
-- 3. For each entity in the blueprint:
--    a. Check player inventory has the item
--    b. Check placement area is clear (game.can_place_entity)
--    c. Place entity (surface.create_entity)
--    d. Deduct item from player inventory
--    e. Handle special cases:
--       - Wire connections (red/green circuit network)
--       - Module insertion
--       - Recipe assignment
--       - Fluid connections (pipes, pumps)
-- 4. Return {success: bool, entities_placed: [{name, position}...],
--           entities_failed: [{name, position, reason}...]}
--
-- Partial placement: if some entities fail, placed entities remain.
-- The agent can retry or adapt. No rollback.
```

### Production Metrics Extension

The existing `get_metrics` remote call must track ALL item types in `total_produced`, not a fixed subset. Checkpoints 1-7 rely on `produced.automation-science-pack` through `produced.space-science-pack`. Use Factorio's `game.forces["player"].item_production_statistics` which tracks all items automatically.

## Migration

### Curriculum Transition (4 phases → 8 checkpoints)

The `CurriculumManager` currently globs `phase{N}_*.toml`. The new checkpoint system:

1. **Checkpoint files use the existing naming convention:** `phase{N}_checkpoint.toml` (e.g., `phase5_yellow_science.toml`)
2. **Checkpoints 0-3** wrap the existing phase 1-4 TOML files — no changes to existing files
3. **Checkpoints 4-7** are new TOML files added to `curricula/`
4. `CurriculumManager` gains a `checkpoint` property that maps phase→checkpoint (phases 1-4 map to checkpoints 0-3, phases 5-8 map to checkpoints 4-7)
5. The `_load_phase()` glob pattern remains `phase{N}_*.toml` — no loader changes

### Entity/Recipe Registry Expansion

Checkpoints 3-7 require entities and recipes not in current registries. New entries added to `ENTITY_REGISTRY` and `RECIPE_REGISTRY` in `action_space.py`:

- **Checkpoint 3:** oil-refinery, chemical-plant, pumpjack, storage-tank
- **Checkpoint 4:** electric-furnace, assembling-machine-2, rail, train-stop
- **Checkpoint 5:** assembling-machine-3, beacon, speed-module, productivity-module
- **Checkpoint 6:** rocket-silo, centrifuge, nuclear-reactor (optional)
- **Checkpoint 7:** (no new entities — uses checkpoint 6 set)

**Registry versioning:** Entity indices are deterministic (sorted by name). Adding entities changes indices. Saved model checkpoints are version-tagged and incompatible across registry changes — retraining required.

### Bridge Integration Points

- `bridge.py:90` — `num_action_types=9` must become `num_action_types=len(ActionType)` (dynamic)
- `bridge.py:337` — `_sample_params()` gains PACK/STAMP cases
- `bridge.py:576` — action mask tensor sized by `len(ActionType)` (already dynamic via enum)
- `action_space.py:484` — `get_action_type_mask()` returns `[1] * len(ActionType)` (auto-extends)

### Other

- Phase-gated entity/recipe sets continue working within checkpoints
- No breaking changes to existing LLM brain mode (packs are ML-policy-only initially)
- Dependency resolver (`dependency_resolver.py`) gets imported by PackExecutor — needs shared `RecipeDAG` instance from bridge (passed via constructor injection)
- Saved ML checkpoints are incompatible after this change (new action types + feature dim + registry expansion)

## Future Upgrades

- **Online skill discovery** (option-critic) — learn pack boundaries during training, not just post-hoc
- **Two-level HRL** — separate manager/worker networks if single-policy scaling plateaus
- **Blueprint editor** — agent proposes blueprint modifications based on learned patterns
- **Cross-agent pack sharing** — multi-agent runs share discovered packs in real-time
