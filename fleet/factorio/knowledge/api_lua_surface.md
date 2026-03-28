# Factorio Runtime API — LuaSurface
> Source: https://lua-api.factorio.com/latest/classes/LuaSurface.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Class Overview
`LuaSurface` represents a "domain" of the world, such as a planet or space platform. Surfaces are uniquely identified by their name.

---

## Key Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `name` | string | RW | The identifier for this surface |
| `index` | uint32 | R | Unique ID in game surfaces collection |
| `map_gen_settings` | MapGenSettings | RW | Generation configuration |
| `peaceful_mode` | boolean | RW | Whether peaceful mode is active |
| `daytime` | double | RW | Current time of day (range: 0-1) |
| `darkness` | float | R | Current darkness level (range: 0-1) |
| `wind_speed` | double | RW | Current wind speed in tiles/tick |
| `ticks_per_day` | uint32 | RW | Duration of day cycle |
| `valid` | boolean | R | Whether this object is still valid |

---

## Core Methods

### Entity Detection & Placement

**`find_entity(entity, position)`**
Locates an entity by name at specified coordinates, checking both position and bounding box.

**`find_entities(area?)`**
Returns all entities within optional bounding box; searches entire surface if omitted.

**`find_entities_filtered(filter)`**
Advanced entity search supporting type, name, force, radius, and limit parameters.

**`can_place_entity{...}`**
Validates whether an entity prototype can be placed at a location without collisions.

**`can_fast_replace{...}`**
Determines if fast-replacement building is possible at a position.

### Entity Creation

**`create_entity{name, position, ...}`**
Creates an entity with support for direction, force, quality, and entity-specific properties like recipes, inventory filters, and control behaviors.

**`create_unit_group{position, force?}`**
Instantiates a commandable unit group at specified coordinates.

### Tile Operations

**`get_tile(x, y)`**
Retrieves tile object at integer coordinates.

**`set_tiles(tiles, correct_tiles?, ...)`**
Batch tile placement with automatic edge correction; supports entity/decorative removal.

**`find_tiles_filtered(filter)`**
Searches tiles by name within area or radius.

**`count_tiles_filtered(filter)`**
Returns count of matching tiles (more efficient than finding).

### Pollution Management

**`get_pollution(position)`**
Reads pollution level for chunk containing position.

**`set_pollution(position, amount)`**
Sets chunk pollution (excludes from statistics tracking).

**`pollute(source, amount, prototype?)`**
Spawns pollution at location (counted in statistics).

**`clear_pollution()`**
Removes all surface pollution.

### Unit & Combat Operations

**`find_enemy_units(center, radius, force?)`**
Efficiently locates hostile units within circular area.

**`find_nearest_enemy{position, max_distance, force?}`**
Returns closest military target to position.

**`set_multi_command{command, unit_count, force?, ...}`**
Issues command to multiple units automatically selected from search area.

**`build_enemy_base(position, unit_count, force?)`**
Dispatches units to construct enemy base.

### Chunk Management

**`is_chunk_generated(chunk_position)`**
Verifies if chunk has been generated.

**`request_to_generate_chunks(position, radius?)`**
Queues chunks for generation by map generator.

**`force_generate_chunk_requests()`**
Synchronously generates all queued chunks using available threads.

### Logistics & Networks

**`find_logistic_network_by_position(position, force)`**
Returns logistic network covering specified position for force.

**`find_closest_logistic_network_by_position(position, force)`**
Locates nearest logistic network cell to position.

### Item Spillage

**`spill_item_stack{position, stack, ...}`**
Drops items on ground with options for looting flags, belt placement, radius limits.

**`spill_inventory{position, inventory, ...}`**
Empties inventory contents across ground area.

---

## Common Usage Examples

```lua
-- Find specific entity
local inserter = surface.find_entity('filter-inserter', {0, 0})

-- Search with filters
local resources = surface.find_entities_filtered{
  area = {{-10, -10}, {10, 10}},
  type = "resource"
}

-- Create entity with control behavior
surface.create_entity{
  name = "assembling-machine-1",
  position = {15, 3},
  force = game.forces.player,
  recipe = "iron-stick"
}

-- Check placement validity
if surface.can_place_entity{name = "inserter", position = {0, 0}} then
  -- safe to create
end

-- Batch tile modification
surface.set_tiles{{name = "grass", position = {0, 0}}}
```

---

## Event Emissions
Methods that raise events:
- `create_entity` → `script_raised_built` (when `raise_built=true`)
- `create_unit_group` → `on_unit_group_created`
- `set_tiles` → `script_raised_set_tiles` (when `raise_event=true`)
