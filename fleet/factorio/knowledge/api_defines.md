# Factorio Runtime API — Defines
> Source: https://lua-api.factorio.com/latest/defines.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Overview
The `defines` table contains enumeration constants used throughout Factorio's Lua API. These values are organized by category and used to specify behavior, state, and configuration options.

## Key Define Categories

### Game States & Logic

**`defines.command`** - Unit behavior commands
- `attack` - Attack another entity
- `go_to_location` - Move to specific position
- `compound` - Chain commands together
- `group` - Follow group behavior
- `attack_area` - Go to location and engage visible enemies
- `wander` - Patrol aimlessly
- `flee` - Retreat from entity
- `stop` - Cease movement
- `build_base` - Construct base at position

**`defines.train_state`** - Train operational status
- `on_the_path` - Following scheduled route
- `no_schedule` - No destination assigned
- `no_path` - Cannot reach destination
- `arrive_signal` - Braking before signal
- `wait_signal` - Stopped at signal
- `arrive_station` - Braking before station
- `wait_station` - Stopped at station
- `manual_control_stop` - User-controlled halt
- `manual_control` - User-controlled movement
- `destination_full` - All stops at capacity

**`defines.entity_status`** - Entity operational conditions
- `working` - Active operation
- `no_power` - Insufficient energy
- `low_power` - Below optimal power
- `no_fuel` - Fuel depleted
- `no_recipe` - Recipe not set
- `no_ingredients` - Crafting materials unavailable
- `full_output` - Output inventory full
- `waiting_for_space_in_destination` - Cannot insert items
- `waiting_at_stop` - Train stopped
- (40+ additional status values for specialized entities)

### Control & Input

**`defines.controllers`** - Player control modes
- `character` - Control humanoid avatar (default freeplay)
- `god` - Unrestricted movement (default sandbox)
- `editor` - Maximum building/editing capabilities
- `ghost` - Observation-only mode
- `cutscene` - Cinematic camera control
- `spectator` - View-only access
- `remote` - Limited building without movement

**`defines.input_action`** - 400+ player input events including:
- `build`, `deconstruct`, `mine_entity`
- `open_gui`, `close_gui`, `gui_click`
- `start_walking`, `toggle_driving`
- `craft`, `cancel_craft`

### Inventory & Logistics

**`defines.inventory`** - Container types
- `character_main` - Player inventory
- `character_armor` - Equipment slot
- `chest` - Generic storage
- `logistic_container_trash` - Logistics trash bin
- `robot_cargo` - Robot carrying capacity
- `assembling_machine_input` / `_output` / `_modules`
- `rocket_silo_rocket` - Rocket assembly area
- (30+ additional inventory types)

**`defines.logistic_mode`** - Container request behavior
- `none` - No logistics participation
- `active_provider` - Always supplies items
- `storage` - Passive storage
- `requester` - Requests items
- `passive_provider` - Supplies when available
- `buffer` - Hybrid provider/requester

### Signals & Rails

**`defines.signal_state`** - Rail signal colors
- `open` - Green (proceed)
- `closed` - Red (stop)
- `reserved` - Orange (reserved ahead)
- `reserved_by_circuit_network` - Red (circuit override)

**`defines.chain_signal_state`** - Chain signal variants
- `none` - No path open
- `all_open` - Full passage available
- `partially_open` - Some paths open
- `none_open` - Complete obstruction

### Game Mechanics

**`defines.difficulty`**
- `easy`, `normal`, `hard`

**`defines.direction`** - 16-point compass
- `north`, `northeast`, `east`, `southeast`, `south`, `southwest`, `west`, `northwest`
- (and intermediate 16-point values)

**`defines.distraction`** - Unit attack behavior
- `none` - Ignore interference
- `by_enemy` - Attack hostile creatures
- `by_anything` - Attack player structures too
- `by_damage` - Retaliate when hit

**`defines.rocket_silo_status`** - Launch sequence stages
- `building_rocket` - Crafting parts
- `create_rocket` - Assembling rocket
- `lights_blinking_open` - Pre-launch prep
- `doors_opening` / `doors_opened` - Access sequence
- `rocket_rising` - Elevation phase
- `arms_advance` - Arm deployment
- `rocket_ready` - Launch-capable
- (additional launch stages through completion)

### Data Structures

**`defines.events`** - 300+ event hooks for script monitoring:
- `on_tick` - Every game update
- `on_entity_died` - Entity destruction
- `on_built_entity` - Structure placed
- `on_player_crafted_item` - Item creation
- `on_train_changed_state` - Train status change

Refer to the [events page](https://lua-api.factorio.com/latest/events.html) for detailed event parameters and trigger conditions.

**`defines.prototypes`** - Hierarchical prototype registry

```lua
defines.prototypes['entity']['furnace']    -- exists (value is 0)
defines.prototypes['item']['iron-plate']   -- exists
defines.prototypes['recipe']['steel-plate'] -- exists
```

Values are always `0` (used as existence checks).

### Advanced Options

**`defines.build_mode`**
- `normal` - Standard placement rules
- `forced` - Override some restrictions
- `superforced` - Maximum override

**`defines.print_skip`** - Message deduplication
- `never` - Always display
- `if_redundant` - Skip recent duplicates (60 ticks)
- `if_visible` - Skip currently visible messages (1152 ticks)

**`defines.space_platform_state`** - Orbital platform lifecycle
- `waiting_for_starter_pack` through `paused`

---

**Documentation Version:** 2.0.76
**Source:** Factorio Auxiliary Docs - Defines
