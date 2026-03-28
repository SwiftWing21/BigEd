# Factorio Runtime API — LuaEntity
> Source: https://lua-api.factorio.com/latest/classes/LuaEntity.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Overview
`LuaEntity` is the primary interface for interacting with entities in Factorio through Lua. Entities represent everything on the map except tiles, and most functions work with ghost entities too.

**Extends:** `LuaControl`

---

## Key Methods

### Inventory Management
- `get_output_inventory()` → `LuaInventory?` - Retrieves the output inventory if available
- `get_module_inventory()` → `LuaInventory?` - Access module storage inventory
- `get_fuel_inventory()` → `LuaInventory?` - Retrieve fuel storage
- `get_burnt_result_inventory()` → `LuaInventory?` - Get burnt result storage

### Entity Manipulation
- `damage(damage, force, type?, source?, cause?)` → `float` - Apply damage to entity
- `destroy(options)` → `boolean` - Destroy the entity with optional parameters
- `die(force?, cause?)` → `boolean` - Immediately kill the entity
- `rotate(options)` → `boolean` - Rotate entity as if player rotated it
- `clone(options)` → `LuaEntity?` - Create a duplicate of this entity

### Construction & Deconstruction
- `order_deconstruction(force, player?, undo_index?)` → `boolean`
- `cancel_deconstruction(force, player?)`
- `to_be_deconstructed()` → `boolean`
- `order_upgrade(options)` → `boolean`
- `cancel_upgrade(force, player?)` → `boolean`
- `apply_upgrade()` → `LuaEntity?, LuaEntity?`

### Crafting & Production
- `is_crafting()` → `boolean` - Check if crafting is in progress
- `get_recipe()` → `LuaRecipe?, LuaQualityPrototype?`
- `set_recipe(recipe?, quality?)` → `ItemWithQualityCounts`
- `crafting_progress` (RW) - Current progress as number [0, 1]

### Fluid Management
- `get_fluid_count(fluid?)` → `double`
- `get_fluid_contents()` → `dictionary[string → FluidAmount]`
- `insert_fluid(fluid)` → `double`
- `remove_fluid(options)` → `double`
- `clear_fluid_inside()`

### Filters & Configuration
- `get_filter(slot_index)` → `ItemFilter | EntityID | AsteroidChunkID?`
- `set_filter(index, filter?)`
- `get_infinity_container_filter(index)` → `InfinityInventoryFilter?`
- `set_infinity_container_filter(index, filter)`

### Rail & Transport
- `get_connected_rail(options)` → `LuaEntity?, rail_direction?, rail_connection_direction?`
- `get_rail_segment_rails(direction)` → `array[LuaEntity]`
- `get_rail_segment_length()` → `double`
- `is_rail_in_same_rail_segment_as(other_rail)` → `boolean`
- `get_transport_line(index)` → `LuaTransportLine`

### Vehicle & Movement
- `get_driver()` → `LuaEntity | LuaPlayer?`
- `set_driver(driver)` - Assign vehicle operator
- `get_passenger()` → `LuaEntity | LuaPlayer?`
- `set_passenger(passenger)` - Assign passenger
- `speed` (RW) - Current/maximum speed depending on entity type

### Logistic Network
- `get_logistic_point(index?)` → `LuaLogisticPoint | array[LuaLogisticPoint]?`
- `logistic_network` (RW) - Associated logistic network or nil

### Circuit Network
- `get_circuit_network(wire_connector_id)` → `LuaCircuitNetwork?`
- `get_signal(signal, wire_connector_id, extra?)` → `int32`
- `get_signals(wire_connector_id, extra?)` → `array[Signal]?`

### Control Behavior
- `get_control_behavior()` → `LuaControlBehavior?`
- `get_or_create_control_behavior()` → `LuaControlBehavior?`

---

## Key Properties

### Status & Identity
- `name` (R) - Prototype identifier
- `type` (R) - Entity prototype type
- `localised_name` (R) - Translated entity name
- `unit_number` (R) - Unique lifetime identifier
- `valid` (R) - Whether object still exists

### Health & Condition
- `health` (RW) - Current health value
- `max_health` (R) - Maximum health capacity
- `get_health_ratio()` → `float?` - Ratio between 1 (full) and 0 (dead)
- `active` (RW) - Enable/disable operations

### Physical Properties
- `position` (R) - Current map location
- `direction` (RW) - Facing direction
- `orientation` (RW) - Smooth rotation value
- `bounding_box` (R) - Collision area
- `selection_box` (R) - Selection area

### Ghost-Related
- `ghost_name` (R) - Entity name within ghost
- `ghost_type` (R) - Type within ghost
- `ghost_prototype` (R) - Prototype of ghosted entity

### Specialized Properties
- `energy` (RW) - Stored electrical energy
- `temperature` (RW) - Heat source temperature
- `crafting_speed` (R) - Current speed with bonuses
- `productivity_bonus` (R) - Module/beacon effects
- `quality` (R) - Entity quality level
- `amount` (RW) - Resource unit count
- `color` (RW) - Display color for certain types
- `backer_name` (RW) - Custom entity label

---

## Ghost Operations

Methods work on ghost entities via parallel functions:
- `ghost_has_flag(flag)` - Check inner entity flags
- `ghost_localised_name` (R) - Translated ghost content name
- `ghost_unit_number` (R) - Unit number of contained entity

---

## Examples

```lua
-- Check if entity can be damaged
if entity.can_be_destroyed() then
  entity.damage(10, game.forces.enemy)
end

-- Set up crafting
local recipe = game.recipe_prototypes["iron-plate"]
entity.set_recipe(recipe)

-- Manage filters
entity.set_filter(1, "iron-ore")
local current = entity.get_filter(1)
```
