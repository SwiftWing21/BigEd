# Standalone Character Entity Design

**Date:** 2026-03-30
**Status:** Draft
**Priority:** P0 — Critical (blocks spectator mode, causes player hijacking)
**Scope:** control.lua, bridge.py, episode_manager.py

## Problem Statement

The current agent system is built around `game.players[N]` — it scans connected
players, claims one as the "agent player," and uses their character + inventory.
This has four cascading failure modes:

1. **Spectator hijacking**: When a human connects via Steam, the agent claims
   their player object (`game.players[1]`), stealing control of their character.
2. **Save persistence**: `storage.agent_player_index` survives save/load,
   restoring the wrong player index after reconnection.
3. **Multi-player guard fragility**: We have added 4 rounds of guards
   (`total_players <= 1`, `count == 1`, etc.) but Factorio's player lifecycle
   events keep breaking assumptions.
4. **Inventory mismatch**: `player.get_main_inventory()` vs
   `storage.biged_inventory` diverge depending on which code path ran,
   causing ghost items or missing items.

## FLE Reference Architecture

FLE (Factorio Learning Environment) solves this cleanly:

- Characters are **standalone entities** created via
  `surface.create_entity{name="character", position=spawn, force="player"}`
- Stored in `storage.agent_characters[i]` — an array for N agents
- Never references `game.players` for agent logic
- Human players connect as spectators — completely invisible to agent code
- Each agent has its own character entity on the map

## Architecture Overview

```
                    ┌─────────────────────────────────┐
                    │        Factorio Server           │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │   storage.agent_chars[]   │    │
                    │  │  [1] character entity ────┼────┼──► position, health, inventory
                    │  │  [2] character entity     │    │    (future: parallel training)
                    │  │  [N] ...                  │    │
                    │  └──────────────────────────┘    │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │ storage.agent_inventories │    │
                    │  │  [1] script inventory ────┼────┼──► 200 slots, items for agent 1
                    │  │  [2] script inventory     │    │    (future: per-agent)
                    │  └──────────────────────────┘    │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │    game.players[]          │    │
                    │  │  Human spectators only     │    │
                    │  │  NEVER read by agent code  │    │
                    │  └──────────────────────────┘    │
                    └─────────────────────────────────┘
                                    │
                                    │ RCON
                                    ▼
                    ┌─────────────────────────────────┐
                    │          bridge.py               │
                    │                                  │
                    │  remote.call("biged",            │
                    │    "ensure_agent", agent_id)     │
                    │    "get_state", agent_id)        │
                    │    "exec_cmd", json, agent_id)   │
                    └─────────────────────────────────┘
```

## Detailed Design

### 1. Storage Schema (on_init)

Replace the current mixed player/character storage with a clean agent-only schema.

```lua
-- OLD (remove all of these)
storage.agent_player_index   -- DELETED
storage.biged_character      -- DELETED (migrated to agent_chars[1])
storage.biged_inventory      -- DELETED (migrated to agent_inventories[1])

-- NEW
storage.agent_chars = {}        -- array of character entities
storage.agent_inventories = {}  -- array of script inventories (parallel)
```

**on_init pseudocode:**

```lua
script.on_init(function()
    storage.agent_chars = {}
    storage.agent_inventories = {}
    -- Create first agent immediately
    create_agent(1)
    game.print("[BigEd Bridge] v0.5.0 — standalone character mode")
end)
```

### 2. create_agent(agent_id) — New Function

Central factory for creating standalone agent characters. Called from on_init,
ensure_agent, and episode reset.

```lua
local function create_agent(agent_id)
    agent_id = agent_id or 1
    local surface = game.get_surface("nauvis") or game.surfaces[1]
    local force = game.forces["player"]
    local spawn = force.get_spawn_position(surface)

    -- Create character entity (NOT player-associated)
    local char = surface.create_entity{
        name = "character",
        position = spawn,
        force = force,
    }
    if not char then
        -- Spawn blocked — find nearby clear position
        local fallback = surface.find_non_colliding_position(
            "character", spawn, 10, 1)
        if fallback then
            char = surface.create_entity{
                name = "character",
                position = fallback,
                force = force,
            }
        end
    end
    if not char then
        game.print("[BigEd] ERROR: could not create agent " .. agent_id)
        return nil
    end

    -- Create script inventory (independent of any player)
    local inv = game.create_inventory(CONFIG.agent_inventory_size)

    storage.agent_chars[agent_id] = char
    storage.agent_inventories[agent_id] = inv

    game.print("[BigEd] Agent " .. agent_id .. " created at " ..
        serpent.line(char.position))
    return char, inv
end
```

### 3. get_agent_context(agent_id) — Simplified

The current function is 73 lines with 4 fallback strategies. The new version is
~15 lines with zero player scanning.

```lua
local function get_agent_context(agent_id)
    agent_id = agent_id or 1
    local ctx = {
        surface = game.get_surface("nauvis") or game.surfaces[1],
        force = game.forces["player"],
        character = nil,
        has_character = false,
        inventory = nil,
    }

    -- Look up agent character
    local char = storage.agent_chars and storage.agent_chars[agent_id]
    if char and char.valid and char.health > 0 then
        ctx.character = char
        ctx.has_character = true
    else
        -- Character died or was invalidated — recreate
        local new_char, new_inv = create_agent(agent_id)
        if new_char then
            ctx.character = new_char
            ctx.has_character = true
        end
    end

    -- Look up agent inventory
    local inv = storage.agent_inventories
                and storage.agent_inventories[agent_id]
    if inv and inv.valid then
        ctx.inventory = inv
    else
        -- Recreate inventory
        local new_inv = game.create_inventory(CONFIG.agent_inventory_size)
        storage.agent_inventories[agent_id] = new_inv
        ctx.inventory = new_inv
    end

    return ctx
end
```

**Key differences from current code:**
- No `game.players` scanning at all
- No `agent_player_index` variable
- No `has_player` field (removed — players are irrelevant)
- Auto-recreates character if dead/invalid (self-healing)
- `agent_id` parameter for multi-agent future

### 4. fn_ensure_agent(agent_id) — Renamed from fn_ensure_player

```lua
local function fn_ensure_agent(agent_id_str)
    local agent_id = tonumber(agent_id_str) or 1

    -- Check existing agent
    local char = storage.agent_chars and storage.agent_chars[agent_id]
    if char and char.valid and char.health > 0 then
        local inv = storage.agent_inventories[agent_id]
        return helpers.table_to_json({
            success = true,
            agent_id = agent_id,
            has_character = true,
            position = char.position,
            health = char.health,
            max_health = char.max_health,
            alive = char.health > 0,
            inventory_valid = inv and inv.valid or false,
        })
    end

    -- Create new agent
    local new_char, new_inv = create_agent(agent_id)
    if new_char then
        return helpers.table_to_json({
            success = true,
            agent_id = agent_id,
            has_character = true,
            position = new_char.position,
            health = new_char.health,
            max_health = new_char.max_health,
            alive = true,
            inventory_valid = new_inv and new_inv.valid or false,
        })
    end

    return helpers.table_to_json({
        success = false,
        agent_id = agent_id,
        error = "spawn_blocked",
    })
end
```

**Key change:** Response no longer contains `headless`, `has_player`, or
`player_index` fields. These concepts are eliminated.

### 5. fn_exec_cmd(json_str) — Minimal Changes

The exec_cmd function already uses `ctx.character` and `ctx.inventory` from
get_agent_context(). Since those now point to standalone entities, most actions
work unchanged.

**Changes needed:**

```lua
local function fn_exec_cmd(json_str)
    -- ... parse json_str as before ...

    -- NEW: extract optional agent_id from command
    local agent_id = parsed.agent_id or 1
    local ctx = get_agent_context(agent_id)

    -- ... rest of function unchanged ...
    -- ctx.inventory → agent script inventory (already correct)
    -- ctx.character → standalone entity (already correct for move/place/etc.)
end
```

The `move` action already does `char.teleport(pos, surface)` which works on any
character entity. The `place`, `craft`, `remove`, `mine` actions all use
`ctx.inventory` which is the script inventory. No changes needed to action logic.

### 6. fn_get_state(agent_id_str) — Updated

```lua
local function fn_get_state(agent_id_str)
    local agent_id = tonumber(agent_id_str) or 1
    local ctx = get_agent_context(agent_id)
    -- ...

    -- Position: always from standalone character
    local char = ctx.character
    local pos = char and char.position or {x = 0, y = 0}

    -- ... entity scan, terrain, resources unchanged ...

    return helpers.table_to_json({
        tick = game.tick,
        time_of_day = surface.daytime,
        agent_id = agent_id,
        -- REMOVED: has_player, headless_character, headless_inventory
        has_character = ctx.has_character,
        player = {
            position = pos,
            health = char and char.health or 0,
            max_health = char and char.max_health or 0,
            has_character = ctx.has_character,
            alive = ctx.has_character and char.valid and char.health > 0,
        },
        inventory = inventory,
        entities = entities,
        -- ... rest unchanged ...
    })
end
```

**Note:** The `player` key in the response is kept for backward compatibility
with `state_parser.py` which reads `player.position`, `player.health`, etc.
Renaming it to `agent` would require changes in the parser and all downstream
consumers. Not worth it in this iteration.

### 7. on_player_joined_game — Neutered

```lua
script.on_event(defines.events.on_player_joined_game, function(event)
    local p = game.get_player(event.player_index)
    if p then
        game.print("[BigEd] Welcome, " .. p.name ..
            "! You are a spectator. Agent characters are independent.")
    end
    -- NO state mutation. NO agent_player_index. NO character creation.
end)
```

### 8. on_load — Simplified

```lua
script.on_load(function()
    -- Nothing to do. storage.agent_chars and storage.agent_inventories
    -- are persisted automatically by Factorio's save system.
    -- Character entity references remain valid across save/load.
end)
```

### 9. on_configuration_changed — Updated

```lua
script.on_configuration_changed(function()
    -- Validate all agent characters
    if storage.agent_chars then
        for id, char in pairs(storage.agent_chars) do
            if not char.valid then
                storage.agent_chars[id] = nil
                game.print("[BigEd] Agent " .. id ..
                    " character invalidated — will recreate on next use")
            end
        end
    end
    -- Validate inventories
    if storage.agent_inventories then
        for id, inv in pairs(storage.agent_inventories) do
            if not inv.valid then
                storage.agent_inventories[id] = nil
                game.print("[BigEd] Agent " .. id ..
                    " inventory invalidated — will recreate")
            end
        end
    end
    -- Migration from old schema
    if storage.biged_character then
        if not storage.agent_chars then storage.agent_chars = {} end
        if storage.biged_character.valid then
            storage.agent_chars[1] = storage.biged_character
        end
        storage.biged_character = nil
    end
    if storage.biged_inventory then
        if not storage.agent_inventories then storage.agent_inventories = {} end
        if storage.biged_inventory.valid then
            storage.agent_inventories[1] = storage.biged_inventory
        end
        storage.biged_inventory = nil
    end
    storage.agent_player_index = nil  -- clean up old field
end)
```

### 10. Remote Interface Registration

```lua
remote.add_interface("biged", {
    get_state   = fn_get_state,       -- signature: (agent_id_str?)
    get_metrics = fn_get_metrics,     -- unchanged
    exec_cmd    = fn_exec_cmd,        -- signature: (json_str) — agent_id inside JSON
    observe     = fn_observe,         -- unchanged
    ensure_agent = fn_ensure_agent,   -- RENAMED from ensure_player
    status      = fn_status,          -- updated to report agent_chars count
})
```

**Backward compatibility note:** `ensure_player` is removed from the interface.
Bridge.py must call `ensure_agent` instead. This is an intentional breaking
change — the old name implied player association and should not be kept.

### 11. Inventory Strategy Decision

**Option A: Script inventory (current approach, recommended)**
- `game.create_inventory(200)` — independent of any entity
- Already works for craft, place, remove, mine
- Easy to clear/refill on episode reset
- Survives character death (inventory persists even if character entity dies)

**Option B: Character entity inventory**
- `char.get_inventory(defines.inventory.character_main)` — tied to entity
- More "realistic" (character carries items)
- Lost when character dies or is destroyed
- Capacity tied to character type (default ~80 slots for "character" entity)

**Decision: Option A (script inventory).** The script inventory is decoupled from
the character entity, has a configurable size (200 slots), and already works
throughout the codebase. Option B would add complexity for episode resets and
character death handling with no training benefit.

## Migration Plan

### File 1: control.lua (biged-bridge mod)

| Change | Lines Affected | Effort |
|--------|---------------|--------|
| Delete `agent_player_index` local + all references | ~10 lines | XS |
| Delete `get_or_create_agent_inventory()` | 6 lines | XS |
| Add `create_agent(agent_id)` function | ~35 lines new | S |
| Rewrite `get_agent_context()` | 73 → ~25 lines | S |
| Rewrite `fn_ensure_player` → `fn_ensure_agent` | 128 → ~35 lines | S |
| Update `fn_exec_cmd` to extract `agent_id` | 2 lines changed | XS |
| Update `fn_get_state` to accept `agent_id` | ~10 lines changed | XS |
| Update `fn_get_state` to remove player flags | ~5 lines changed | XS |
| Rewrite `on_init` | 5 lines | XS |
| Simplify `on_load` | 4 → 1 lines | XS |
| Rewrite `on_player_joined_game` | 10 → 5 lines | XS |
| Add migration logic to `on_configuration_changed` | ~20 lines new | S |
| Update `remote.add_interface` registration | 1 line | XS |
| Update `fn_status` to report agent_chars | ~5 lines | XS |
| **Total** | | **~2 hours** |

**Net line change:** Current ~930 lines → estimated ~750 lines (significant simplification).

### File 2: episode_manager.py

| Change | Lines Affected | Effort |
|--------|---------------|--------|
| `_SOFT_RESET_LUA`: remove `game.players[1]` reference, use `storage.agent_chars[1]` | ~15 lines | S |
| `_SOFT_RESET_LUA`: clear `storage.agent_inventories[1]` instead of `storage.biged_inventory` | 3 lines | XS |
| `reset()`: change `ensure_player` → `ensure_agent` | 1 line | XS |
| `_give_starting_items()`: remove `game.players[1]` path, use `storage.agent_inventories[1]` directly | ~8 lines simplified | S |
| **Total** | | **~30 min** |

**Updated _SOFT_RESET_LUA pseudocode:**

```lua
'/c local surface = game.get_surface("nauvis"); '
'local force = game.forces["player"]; '
-- Clear non-infrastructure entities (same as before)
...
-- Teleport agent character to spawn
'local char = storage.agent_chars and storage.agent_chars[1]; '
'if char and char.valid then char.teleport({0, 0}, surface) end; '
-- Clear agent inventory
'local inv = storage.agent_inventories and storage.agent_inventories[1]; '
'if inv and inv.valid then inv.clear() end; '
-- Replenish ore near spawn (same as before)
...
'rcon.print("soft_reset_done")'
```

**Updated _give_starting_items pseudocode:**

```lua
"/c local inv = storage.agent_inventories and storage.agent_inventories[1]; "
"if not inv or not inv.valid then "
"  storage.agent_inventories = storage.agent_inventories or {}; "
"  storage.agent_inventories[1] = game.create_inventory(200); "
"  inv = storage.agent_inventories[1]; "
"end; "
f"{inserts}"
```

### File 3: bridge.py

| Change | Lines Affected | Effort |
|--------|---------------|--------|
| All `remote_call("ensure_player")` → `remote_call("ensure_agent")` | 4 occurrences | XS |
| Log messages: "ensure_player" → "ensure_agent" | 4 occurrences | XS |
| Log messages: "no body" → "no agent character" | cosmetic | XS |
| **Total** | | **~15 min** |

Occurrences in bridge.py:
1. `tick()` line ~145: body check calls `ensure_player`
2. `ml_tick()` line ~373: body check calls `ensure_player`
3. `run()` line ~662: initial player init calls `ensure_player`
4. Comment/log references

### File 4: state_parser.py — No Changes

The `player` key in the JSON response is preserved for backward compatibility.
`GameState.player_alive`, `player_has_character`, `player_position` etc. all
still parse correctly because `fn_get_state` still emits a `player` object.

## Risk Assessment

### Low Risk

| Risk | Mitigation |
|------|-----------|
| Character entity position desync | `move` action already teleports entity — no change |
| Script inventory already proven | Used in headless mode for months |
| Factorio save/load character persistence | Standalone entities persist in saves same as player characters |
| Old saves with `storage.biged_character` | `on_configuration_changed` migration handles this |

### Medium Risk

| Risk | Mitigation |
|------|-----------|
| **Character entity death** — standalone characters CAN be killed by biters/trains | `get_agent_context` auto-recreates dead characters. Test: spawn biters near agent, verify auto-respawn on next tick |
| **Character entity collision on spawn** — if spawn point is blocked | `find_non_colliding_position` fallback already in create_agent |
| **RCON argument passing for agent_id** — `remote.call("biged", "get_state", "1")` passes string not number | `tonumber(agent_id_str) or 1` handles this safely |
| **Mod version upgrade on existing save** — old storage schema | `on_configuration_changed` migrates old fields to new arrays |

### High Risk

| Risk | Mitigation |
|------|-----------|
| **Character entity not controllable** — standalone entities can't be directly controlled via Factorio's player input system (WASD etc.) | Not a problem: we only use `char.teleport()` for movement and `surface.create_entity()` for placement. We never use player input APIs. Confirm by auditing all `exec_cmd` actions — none reference `player.character` for input. |
| **Character entity rotation/direction** — standalone entities may not have a facing direction useful for belt placement | Test: create standalone character, verify `entity.direction` is readable. Direction matters for observability, not for `surface.create_entity` which takes explicit direction param. |

### Non-Risks (things that look scary but aren't)

- **"Characters need a player to function"** — False. Character entities are
  regular entities in Factorio. They have position, health, inventory slots, and
  can be teleported. FLE proves this works.
- **"Script inventory won't work for crafting"** — Already works. Our `craft`
  action reads recipe prototypes and manipulates inventory directly. It never
  uses `player.begin_crafting()`.
- **"State won't report correctly"** — The `player` object in the response uses
  `char.position` and `char.health` which come from the standalone entity, not
  from a player object.

## Testing Plan

### Smoke Tests (automated, add to smoke_test.py)

1. **Agent creation**: Start server → `ensure_agent` → verify `success=true`,
   `has_character=true`, valid position
2. **Agent state**: `get_state` returns valid position, inventory, entities
3. **Agent movement**: `exec_cmd(move)` → verify position changed in next
   `get_state`
4. **Agent placement**: Give items via `_give_starting_items` → `exec_cmd(place)`
   → verify entity created
5. **Agent crafting**: Insert ingredients → `exec_cmd(craft)` → verify products
   in inventory
6. **Episode reset**: `soft_reset` → verify character at (0,0), inventory empty
7. **Character death recovery**: Kill character via Lua → next `get_state` →
   verify auto-recreated

### Manual Tests

1. **Spectator isolation**: Start headless server → connect via Steam →
   verify human player is not affected by agent actions
2. **Multi-connect**: Two humans connect → agent still works independently
3. **Save/load persistence**: Save game → reload → verify agent character and
   inventory survive
4. **Mod update migration**: Load save from v0.4.0 → verify
   `on_configuration_changed` migrates old storage fields

## Deleted Code Summary

The following code/concepts are entirely eliminated:

- `agent_player_index` local variable
- `storage.agent_player_index`
- All `game.players` scanning in agent code (`for _, p in pairs(game.players)`)
- `get_or_create_agent_inventory()` helper (replaced by create_agent)
- `ctx.has_player` / `ctx.player` fields
- Player count guards (`total_players <= 1`, `count == 1`, etc.)
- `sole_player` logic
- `player.set_controller` / `player.create_character` calls
- `headless` / `has_player` / `headless_character` / `headless_inventory` flags
  in JSON responses
- The entire "Strategy 1 / Strategy 2" fallback chain in fn_ensure_player

## Future: Multi-Agent Extension

The design supports N agents from day one:

```python
# bridge.py — future parallel training
for agent_id in range(1, num_agents + 1):
    state = await rcon.remote_call("get_state", str(agent_id))
    action = policy.act(encode(state))
    await rcon.remote_call("exec_cmd", json.dumps({
        "agent_id": agent_id,
        "action": action.type,
        **action.params,
    }))
```

Each agent has its own character entity and inventory. They share the same map,
force, and surface. This enables cooperative or competitive multi-agent training
in a single Factorio world.

## Implementation Order

1. **control.lua** — Core change. Do this first, test manually via RCON.
2. **episode_manager.py** — Update Lua strings to use new storage paths.
3. **bridge.py** — Rename ensure_player → ensure_agent (4 call sites).
4. **Manual test** — Connect as spectator, verify no hijacking.
5. **Smoke tests** — Add automated character lifecycle tests.

Estimated total effort: **3-4 hours** including testing.
