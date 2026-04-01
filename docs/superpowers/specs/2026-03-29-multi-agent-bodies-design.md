# Multi-Agent Bodies for Factorio RL

**Date:** 2026-03-29
**Status:** Design approved, pending implementation plan

## Summary

Replace the single-agent architecture in the Factorio module with a multi-agent system where multiple RL agent bodies (character entities) operate on a shared map and force. Agents train a shared policy with independent rollouts, spawn in zoned map quadrants, and can cooperate across zones when the LLM brain directs them to. The spectator player is fully decoupled from agent logic.

## Requirements (from brainstorming)

- **Independent learners + cooperative swarm:** Agents train independently but share a force (research, power grid)
- **Dynamic scaling:** Agent count configurable in fleet.toml, can spawn/despawn mid-episode (2–16 agents)
- **Shared policy, independent rollouts:** One neural network, each agent collects its own trajectory buffer, PPO updates aggregate all experience
- **Zoned map with override:** Each agent spawns in a designated quadrant, but the LLM brain can move agents anywhere for cooperative planning
- **Pure spectator:** Real players never get claimed as agent bodies; future hooks for commander (B) and possession (C) roles via a `role` field in the player registry
- **Federation-ready:** Design accommodates future cross-PC agent distribution (not implemented now)

## Architecture

### Lua Agent Registry (`control.lua`)

The single `agent_player_index` is replaced with two registries:

```lua
-- Persisted in storage (unit_numbers, not entity refs — refs invalidate on save/load)
agent_registry = {
  ["agent-1"] = {
    unit_number = 42,             -- resolve via game.get_entity_by_unit_number()
    inventory = {},               -- item_name -> count (simulated, canonical source of truth)
    position = {x = 0, y = 0},
    zone = {x_min = -64, y_min = -64, x_max = 64, y_max = 64},
    alive = true,
    craft_queue = {},             -- {recipe, finish_tick, outputs}
    label_id = nil,               -- rendering object id (uint64) from rendering.draw_text()
  },
  ["agent-2"] = { ... },
}

player_registry = {
  ["swiftwing"] = { role = "spectator", following = nil },
}
```

**Entity resolution:** Agent character entities are stored by `unit_number`, not raw `LuaEntity` references (which invalidate on save/load). Resolve on access:
```lua
local function get_agent_character(agent_id)
    local entry = agent_registry[agent_id]
    if not entry or not entry.unit_number then return nil end
    local char = game.get_entity_by_unit_number(entry.unit_number)
    if char and char.valid then return char end
    entry.alive = false
    return nil
end
```

**Name labels:** Created via `rendering.draw_text{text=agent_id, target=character, color={1,1,1}, scale=1.5}`, stored as `label_id` (uint64 render object ID, not a LuaEntity).

**Role field future-proofing:** The `player_registry` role field supports:
- `"spectator"` — current implementation, view-only
- `"commander"` — future: LLM brain routes directives to this player
- `"possessing"` — future: player takes direct control of an agent body, pausing its policy

### Remote Interface Changes

All state/command functions become agent-scoped:

| Current | New | Notes |
|---------|-----|-------|
| `get_state()` | `get_state(agent_id)` | Returns state from agent's perspective |
| `exec_cmd(json)` | `exec_cmd(agent_id, json)` | Actions scoped to agent's character/inventory |
| `ensure_player()` | `ensure_agent(agent_id)` | Spawns/respawns one agent's character |
| — | `spawn_agents(ids_json)` | Creates agents by ID list, returns created IDs |
| — | `despawn_agent(agent_id)` | Removes character, cleans registry |
| — | `get_all_states()` | Batched: returns all agents' states in one call |
| — | `get_all_agents()` | Returns full registry snapshot |
| — | `follow_agent(player_name, agent_id)` | Moves spectator camera to agent |
| `get_metrics()` | `get_metrics()` | Unchanged — force-level stats shared |
| `status()` | `status()` | Extended with agent count |

**`spawn_agents` contract:** Python pre-generates agent IDs and passes them as JSON: `spawn_agents('["agent-3","agent-4"]')`. Returns JSON with created IDs and their spawn positions. This keeps ID ownership on the Python side.

**`get_all_states` (batched):** Returns a single JSON object `{ "agent-1": {state...}, "agent-2": {state...} }`. This avoids the O(N) RCON round-trip problem — one call instead of N. Factorio's RCON handles payloads up to ~100KB, sufficient for 16 agents.

### Inventory Model

**Simulated inventory is the canonical source of truth.** The real character entity inventory is kept empty. All item flows are routed through the simulated inventory:

- **Place:** Deduct item from simulated inventory, call `surface.create_entity`
- **Mine:** Destroy entity, add products to simulated inventory
- **Remove:** Destroy entity, add mineable products to simulated inventory
- **Craft:** Check/deduct ingredients from simulated inventory, schedule output delivery
- **Belt/inserter pickup:** Not simulated (agents don't passively pick up items — all acquisition is explicit via mine/craft actions)

This avoids sync issues between a real inventory and a simulated one. The character entity is purely a physical body (position, health, collision) with no inventory role.

### Craft Simulation

Character entities not attached to players cannot use Factorio's native `player.begin_crafting()`. The Lua mod simulates crafting:

1. Agent calls `craft(agent_id, recipe_name, count)`
2. Lua validates recipe exists and is enabled:
   ```lua
   local proto = prototypes.recipe[recipe_name]  -- Factorio 2.0: game.recipe_prototypes removed
   if not proto then return {error = "unknown recipe"} end
   if not force.recipes[recipe_name].enabled then return {error = "recipe not unlocked"} end
   ```
3. Checks `agent_registry[agent_id].inventory` for required ingredients
4. Deducts ingredients from simulated inventory
5. Adds entry to `craft_queue`:
   ```lua
   { recipe = recipe_name, finish_tick = game.tick + math.ceil(proto.energy * 60), outputs = proto.products }
   ```
6. Craft delivery handler processes finished items

**Probabilistic outputs:** Recipe products in Factorio 2.0 use `ItemProductPrototype` with fields like `name`, `amount`, `probability`. For the simulated craft system, we always award the full `amount` and ignore `probability` — this is a simplification that avoids RNG complexity in training. Productivity bonuses are also ignored (no modules in simulated crafting).

**Craft delivery performance:** A permanent `on_nth_tick(10)` handler runs the craft delivery check. It tracks `next_craft_finish_tick` (minimum finish tick across all agents). When `game.tick < next_craft_finish_tick` or all queues are empty, the handler exits immediately (single integer comparison). The handler is always registered — no dynamic register/deregister — since the idle cost is negligible.

### Agent Death & Respawn

**`on_entity_died` handler:** When a character entity dies (biters, trains, etc.):
1. Check if the destroyed entity's `unit_number` matches any agent in `agent_registry`
2. Set `alive = false`, clear `craft_queue` (in-progress crafts are lost)
3. Simulated inventory is preserved (items "dropped" conceptually, held for respawn)
4. Broadcast event: `game.print("[BigEd] agent-2 was killed")`

**Python-side detection:** `get_state(agent_id)` returns `alive = false`. The bridge calls `ensure_agent(agent_id)` to respawn:
- Creates new character at zone center
- Restores simulated inventory
- Updates `unit_number` in registry
- Death triggers a configurable negative reward (default: -10.0)
- The agent's trajectory buffer marks the death transition as `done=True`

### Despawn Cleanup

When `despawn_agent(agent_id)` is called:
1. Destroy the character entity
2. Destroy the name label (guard with `pcall` — render object may already be gone if character died: `pcall(rendering.destroy, label_id)`)
3. Discard simulated inventory (items are lost)
4. Discard craft queue
5. Remove entry from `agent_registry`

**Python side:** The bridge marks the agent's trajectory buffer `done=True` on the last transition, flushes it into the next PPO update, then removes the `AgentBody`. Despawn is deferred if a PPO update is in progress (queued until update completes).

### Zone System

Zones partition the map into quadrants around origin:

```
     (-128, -128)────(0, -128)────(128, -128)
          │ agent-1 │      │ agent-2 │
     (-128,    0)───(0,    0)───(128,    0)
          │ agent-3 │      │ agent-4 │
     (-128,  128)───(0,  128)───(128,  128)
```

**Configuration:**
```toml
[factorio.zones]
zone_size = 128           # tiles per side
buffer = 16               # spawn margin — agents spawn at least 16 tiles from zone edge
spawn_near_resources = true
```

**Buffer meaning:** The `buffer` value is a spawn margin. Agents spawn at least `buffer` tiles from their zone edge, ensuring they start near the zone center rather than at a boundary. It does not create a no-build zone or restrict movement.

Zones spiral outward as agent count grows: quadrants first, then ring expansion. Each agent spawns near the densest ore patch within their zone (Lua scans `find_entities_filtered{type="resource"}` within the zone, biased toward zone center by the buffer margin).

**Zone override:** Zones are spawn defaults and observation hints, not hard fences. The LLM brain can issue `move` actions to any position, sending agents across zone boundaries for cooperative work.

### Python Multi-Agent Bridge

**New `AgentBody` class:**
```python
class AgentBody:
    """One agent's identity and state tracking."""
    agent_id: str              # "agent-1", "agent-2", ...
    zone: dict                 # spawn zone bounds
    trajectory_buf: TrajectoryBuffer
    prev_state: GameState | None
    step_count: int
    alive: bool
```

**`FactorioBridge` changes:**
```python
class FactorioBridge:
    # Shared across all agents
    policy: FactorioPolicy
    trainer: PPOTrainer
    encoder: StateEncoder
    action_space: ActionSpace
    reward: RewardComputer
    curriculum: CurriculumManager
    episode_mgr: EpisodeManager

    # Per-agent
    agents: dict[str, AgentBody]
    agent_count: int
```

**Tick loop — batched state fetch:**
```python
async def ml_tick():
    # Single RCON call for all agent states
    all_states = parse_all_states(
        await rcon.remote_call("get_all_states")
    )

    for agent in self.agents.values():
        state = all_states[agent.agent_id]
        if not state.player_alive:
            await self._handle_agent_death(agent)
            continue

        grid, feats = encoder.encode(state)
        action = policy.act(grid, feats, mask)
        translated = translate_action(action_dict)
        await rcon.remote_call("exec_cmd", agent.agent_id, action_json)
        agent.trajectory_buf.add(transition)

    # PPO update aggregates ALL agents' buffers
    if total_steps % update_every == 0:
        combined = merge_buffers([a.trajectory_buf for a in agents])
        trainer.update(combined)
```

Note: State fetch is batched (1 RCON call). Action execution remains per-agent (N calls) because each action depends on the current state and actions can't be batched into one Lua call safely. With ~5ms per RCON call, 16 agents = ~80ms for actions, acceptable at the current tick rate.

**`merge_buffers` strategy:** GAE (generalized advantage estimation) is computed per-agent independently before merge — each agent has its own value baseline from the shared policy. The merged buffer is a simple concatenation of transitions with pre-computed advantages. This preserves correct per-agent return estimation while letting PPO see all agents' experience.

**Dynamic scaling:**
- `spawn_agent()` — pre-generates IDs, calls Lua `spawn_agents`, creates `AgentBody` instances
- `despawn_agent(agent_id)` — calls Lua `despawn_agent`, flushes buffer, removes from dict
- Triggers: curriculum phase transitions, dashboard command, fleet capacity signal

### Episode Reset

`EpisodeManager.soft_reset()` becomes multi-agent aware:
- Clears all placed entities (same as current)
- Teleports each agent back to their zone center
- Resets each agent's simulated inventory
- Redistributes `PHASE_ITEMS` to each agent independently
- Resets all craft queues

### Spectator System

**On player connect:** `on_player_joined_game` adds to `player_registry` as spectator. Character exists as a normal Factorio player but is never referenced by agent logic.

**Follow command:** `follow_agent(player_name, agent_id)` teleports the named spectator near the target agent's character. Chat command: `/biged follow agent-2` (uses the chatting player's name). Pass `nil` agent_id to unfollow (free camera).

### Dashboard API

```
GET  /api/factorio/status              # all agents + spectators summary
GET  /api/factorio/state/{agent_id}    # per-agent state detail
POST /api/factorio/spawn               # { "count": 2 }
POST /api/factorio/despawn/{agent_id}  # remove one agent
POST /api/factorio/follow              # { "player_name": "swiftwing", "agent_id": "agent-3" }
```

The follow endpoint takes `player_name` to support multiple spectators. If `player_name` is omitted, defaults to the first spectator in `player_registry`.

Training metrics aggregate across agents: per-agent reward curves, combined buffer size, PPO stats (shared), curriculum progress (shared force).

### Config

```toml
[factorio.agents]
agent_count = 2               # initial spawn count
max_agents = 16                # ceiling
dynamic_scaling = true         # allow spawn/despawn mid-episode
name_prefix = "agent"          # agent-1, agent-2, ...
death_reward = -10.0           # reward penalty on agent death

[factorio.zones]
zone_size = 128
buffer = 16
spawn_near_resources = true
```

## Files Modified

| File | Change |
|------|--------|
| `fleet/factorio/lua_mod/control.lua` | Agent registry, scoped exec_cmd/get_state, craft simulation, spectator separation, follow command, spawn/despawn, death handler, batched get_all_states |
| `fleet/factorio/bridge.py` | `AgentBody` class, multi-agent tick loop, buffer aggregation, dynamic scaling, death handling |
| `fleet/factorio/episode_manager.py` | Multi-agent reset, per-agent item distribution, zone-aware teleport |
| `fleet/factorio/bridge_config.py` | New config fields for agents/zones |
| `fleet/factorio/bridge_api.py` | Per-agent status endpoints, spawn/despawn/follow API |
| `fleet/factorio/state_parser.py` | Parse agent_id from state responses, `parse_all_states()` |
| `fleet/factorio/action_translator.py` | Prepend agent_id to translated commands |
| `fleet/factorio/trainer.py` | `merge_buffers()` utility with per-agent GAE computation |

## Files NOT Modified

- `ml_policy.py` — shared policy, no changes
- `state_encoder.py` — encodes one agent's state at a time, unchanged
- `action_space.py` — same action space per agent
- `reward.py` — per-agent reward computation, interface unchanged
- `curriculum.py` / `curriculum_manager.py` — shared curriculum, checks shared force state

## No New Files

All changes fit into existing modules.

## Future Hooks (Not Implemented Now)

- **Commander role (B):** Player registry `role` field supports promotion to commander
- **Possession mode (C):** `possessed_by` field on agent registry, pauses policy for that agent
- **Federation:** Agent registry shape supports serialization for cross-PC distribution
- **Adversarial (C):** Separate forces per agent (requires registry refactor, not zone change)
- **Population-based training:** Fork shared policy into per-agent policies via `AgentBody.policy` override
