# Factorio Runtime API — LuaPlayer
> Source: https://lua-api.factorio.com/latest/classes/LuaPlayer.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Class Overview
`LuaPlayer` extends `LuaControl` and represents a player in the game. Key distinction: "a player may or may not have a character, which is the `LuaEntity` of the little guy running around."

## Core Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `name` | string | R | The player's username |
| `index` | uint32 | R | Unique player ID in `LuaGameScript::players` |
| `connected` | boolean | R | Whether player is currently connected |
| `character` | LuaEntity? | RW | The character attached to this player |
| `admin` | boolean | RW | Admin status of the player |
| `color` | Color | RW | Player tint color for character and buildings |
| `tag` | string | RW | Tag shown in chat, map, and selection rectangles |

## Controller & Navigation Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `controller_type` | defines.controllers | R | Current controller type |
| `physical_controller_type` | defines.controllers | R | Physical controller before remote/editor mode |
| `position` | MapPosition | R | Current entity position |
| `surface` | LuaSurface | R | Current surface the entity is on |
| `zoom` | double | RW | Camera zoom level (>1 zooms in, <1 zooms out) |
| `zoom_limits` | ZoomLimits | RW | Min/max zoom constraints per controller |

## Display Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `display_resolution` | DisplayResolution | R | Player's screen resolution |
| `display_scale` | double | R | UI scaling factor |
| `display_density_scale` | double | R | Physical DPI factor |
| `locale` | string | R | Active language locale |
| `minimap_enabled` | boolean | RW | Minimap visibility toggle |

## Inventory & Cursor Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `cursor_stack` | LuaItemStack? | R | Item in player's cursor |
| `cursor_ghost` | ItemIDAndQualityIDPair | RW | Ghost prototype in cursor |
| `hand_location` | ItemStackLocation? | RW | Original location of cursor item |
| `cursor_stack_temporary` | boolean | RW | Whether cursor stack destroys on clear |

## Status Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `afk_time` | uint32 | R | Ticks since last player action |
| `online_time` | uint32 | R | Total ticks played in save |
| `last_online` | uint32 | R | Tick when player last connected |
| `ticks_to_respawn` | uint32? | RW | Ticks until respawn (nil if not waiting) |

## Core Methods

### Character Management

```lua
-- Create character for connected player
player.create_character(character?)
  → boolean

-- Associate character (not controlled by any player)
player.associate_character(character)

-- Disassociate character from player
player.disassociate_character(character)

-- Get all associated characters
player.get_associated_characters()
  → array[LuaEntity]

-- Swap characters with another player
player.swap_characters(player)
  → boolean
```

### Controller Management

```lua
-- Set player controller type
player.set_controller{
  type = defines.controllers,
  character = LuaEntity?,
  waypoints = array[CutsceneWaypoint]?,
  position = MapPosition?,
  surface = SurfaceIdentification?
}

-- Set zoom limits for controller type
player.set_zoom_limits(controller_type, zoom_limits)
```

### Building & Placement

```lua
-- Check if cursor contents can be built at position
player.can_build_from_cursor{
  position = MapPosition,
  direction = defines.direction?,
  flip_horizontal = boolean?,
  flip_vertical = boolean?,
  build_mode = defines.build_mode?,
  terrain_building_size = uint32?
}
  → boolean

-- Build from cursor contents
player.build_from_cursor{
  position = MapPosition,
  direction = defines.direction?,
  mirror = boolean?,
  flip_horizontal = boolean?,
  flip_vertical = boolean?
}

-- Clear cursor as if player pressed the key
player.clear_cursor()
  → boolean

-- Smart pipette tool (transfer item to cursor)
player.pipette(id, quality?, allow_ghost?)
  → boolean
```

### Communication

```lua
-- Print message to player's chat console
player.print(message, print_settings?)

-- Clear chat console
player.clear_console()

-- Set goal text (top-left display)
player.set_goal_description(text?, only_update?)

-- Get current goal text
player.get_goal_description()
  → LocalisedString
```

### Alerts

```lua
-- Add alert for entity
player.add_alert(entity, alert_type)

-- Add custom alert
player.add_custom_alert(entity, icon, message, show_on_map)

-- Remove alerts by filter
player.remove_alert{
  entity = LuaEntity?,
  prototype = EntityID?,
  position = MapPosition?,
  type = defines.alert_type?,
  surface = SurfaceIdentification?
}

-- Get filtered alerts
player.get_alerts{...}
  → dictionary[uint32 → dictionary[alert_type → array[Alert]]]

-- Mute/unmute/enable/disable alert category
player.mute_alert(alert_type) → boolean
player.unmute_alert(alert_type) → boolean
player.is_alert_muted(alert_type) → boolean
player.enable_alert(alert_type) → boolean
player.disable_alert(alert_type) → boolean
player.is_alert_enabled(alert_type) → boolean
```

### Quick Bar Management

```lua
-- Get quick bar filter for slot
player.get_quick_bar_slot(index)
  → ItemFilter?

-- Set quick bar slot filter
player.set_quick_bar_slot(index, filter)

-- Get active quick bar page for screen position
player.get_active_quick_bar_page(index)
  → uint8?

-- Set active quick bar page
player.set_active_quick_bar_page(screen_index, page_index)
```

### Map Pins & Flying Text

```lua
-- Add map pin (specify entity OR position+surface)
player.add_pin{
  label = string?,
  preview_distance = uint16?,
  always_visible = boolean?,
  entity = LuaEntity?,
  player = PlayerIdentification?,
  surface = SurfaceIdentification?,
  position = MapPosition?
}

-- Create flying text visible only to this player
player.create_local_flying_text{
  text = LocalisedString,
  position = MapPosition?,
  surface = SurfaceIdentification?,
  create_at_cursor = boolean?,
  color = Color?,
  time_to_live = uint32?,
  speed = double?
}

-- Clear all local flying text
player.clear_local_flying_texts()
```

### Cutscene & Views

```lua
-- Start cutscene with waypoints
player.set_controller{type = defines.controllers.cutscene, waypoints = {...}}

-- Jump to cutscene waypoint
player.jump_to_cutscene_waypoint(waypoint_index)

-- Exit cutscene
player.exit_cutscene()

-- Exit remote view
player.exit_remote_view()

-- Enter space platform
player.enter_space_platform(space_platform)
  → boolean

-- Leave space platform
player.leave_space_platform()

-- Land on planet from platform
player.land_on_planet()
  → boolean
```

### Tools & Actions

```lua
-- Unlock achievement
player.unlock_achievement(name)

-- Drag wire for combinators
player.drag_wire{position = MapPosition}
  → boolean

-- Use capsule from cursor
player.use_from_cursor(position)

-- Start selection tool
player.start_selection(position, selection_mode)

-- Play sound for player
player.play_sound(sound_specification)
```

### Shortcuts

```lua
-- Check if custom shortcut is toggled
player.is_shortcut_toggled(prototype_name)
  → boolean

-- Check if custom shortcut is available
player.is_shortcut_available(prototype_name)
  → boolean

-- Toggle shortcut state
player.set_shortcut_toggled(prototype_name, toggled)

-- Enable/disable shortcut
player.set_shortcut_available(prototype_name, available)
```

## Inherited from LuaControl

```lua
-- Inventory management
player.get_inventory(inventory) → LuaInventory?
player.get_main_inventory() → LuaInventory?
player.insert(items) → uint32
player.remove_item(items) → uint32
player.get_item_count(item?) → uint32
player.has_items_inside() → boolean
player.clear_items_inside()

-- Movement & interaction
player.teleport(position, surface?, raise_telepted?, snap_to_grid?)
  → boolean
player.can_reach_entity(entity) → boolean

-- Selection & GUI
player.update_selected_entity(position)
player.clear_selected_entity()
player.selected → LuaEntity?
player.opened → LuaEntity | LuaItemStack | ... ?

-- Mining & building
player.mine_entity(entity, force?) → boolean
player.mine_tile(tile) → boolean
player.can_place_entity{name=..., position=...} → boolean

-- Light & movement
player.enable_flashlight()
player.disable_flashlight()
player.is_flashlight_enabled() → boolean
player.walking_state → table (RW)
player.riding_state → RidingState (RW)
player.driving → boolean (RW)

-- Combat & tools
player.mining_state → table (RW)
player.shooting_state → table (RW)
player.picking_state → boolean (RW)
player.repair_state → table (RW)
player.in_combat → boolean (R)
player.following_robots → array[LuaEntity] (R)

-- Crafting
player.begin_crafting{count=..., recipe=...} → uint32
player.cancel_crafting{index=..., count=...}
player.get_craftable_count(recipe) → uint32
player.crafting_queue → array[CraftingQueueItem]?
player.crafting_queue_size → uint32
player.crafting_queue_progress → double (RW)

-- Technology & research
player.open_technology_gui(technology?)
player.open_factoriopedia_gui(prototype?)
player.close_factoriopedia_gui()

-- Character modifiers
player.character_crafting_speed_modifier → double (RW)
player.character_mining_speed_modifier → double (RW)
player.character_running_speed_modifier → double (RW)
player.character_build_distance_bonus → uint32 (RW)
player.character_reach_distance_bonus → uint32 (RW)
player.character_item_pickup_distance_bonus → uint32 (RW)
player.character_inventory_slots_bonus → uint32 (RW)
player.character_health_bonus → float (RW)
player.character_running_speed → double (R)
```

## Common Usage Patterns

```lua
-- Print to player console
game.players[1].print("Hello!")

-- Create character for player
local player = game.players[1]
if not player.character then
  player.create_character()
end

-- Teleport player
player.character.teleport({100, 50})

-- Set zoom limits
player.set_zoom_limits(
  defines.controllers.character,
  {closest = {zoom = 4}, furthest = {distance = 800}}
)

-- Add alert
player.add_alert(entity, defines.alert_type.entity_destroyed)

-- Build from cursor
if player.can_build_from_cursor{position = {100, 100}} then
  player.build_from_cursor{position = {100, 100}}
end
```
