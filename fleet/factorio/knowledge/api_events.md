# Factorio Runtime API — Events
> Source: https://lua-api.factorio.com/latest/events.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Overview
Events in Factorio are delivered to mods in response to game actions. Mods register callbacks using `LuaBootstrap::on_event()`. Each event receives a table with `name` and `tick` fields, plus event-specific data.

## Core Event Structure
All events contain:
- **name**: Event identifier (from `defines.events`)
- **tick**: Game tick when event occurred

## Major Event Categories

### Player Events

**on_player_created**
Triggered after player spawns into the game.

**on_player_died**
Triggered when a player's character dies.

**on_player_respawned**
Triggered after player respawns.

**on_player_joined_game**
Triggered when player joins multiplayer session.

**on_player_left_game**
Triggered when player disconnects from session.

**on_player_changed_position**
Triggered when player's tile position changes.

**on_player_changed_surface**
Triggered when player moves between surfaces.

**on_player_changed_force**
Triggered when player switches to different force.

### Building & Construction

**on_built_entity**
"Called when player builds something." Includes `entity`, `player_index`, `consumed_items`, and optional `tags`.

**on_pre_build**
"Called when players uses an item to build something."

**on_player_built_tile**
Triggered after player constructs tiles.

**on_robot_built_entity**
Triggered when construction robot completes building.

### Mining & Deconstruction

**on_player_mined_entity**
"Called after the results of an entity being mined are collected just before the entity is destroyed."

**on_marked_for_deconstruction**
Triggered when entity marked for removal with planner.

**on_cancelled_deconstruction**
Triggered when deconstruction order is cancelled.

### Entity Damage & Death

**on_entity_damaged**
"Called when an entity is damaged." Includes damage type, amounts, and source.

**on_entity_died**
"Called when an entity dies." Provides cause, loot, and force information.

**on_post_entity_died**
Triggered after entity death processing completes.

### Crafting

**on_pre_player_crafted_item**
Triggered when player queues craft recipe.

**on_player_crafted_item**
"Called when the player finishes crafting an item." Fires before inventory insertion.

**on_player_cancelled_crafting**
Triggered when player stops active craft.

### GUI Events

**on_gui_click**
Triggered when GUI element clicked.

**on_gui_text_changed**
Triggered when player modifies textfield.

**on_gui_closed**
Triggered when player closes open GUI.

**on_gui_opened**
Triggered when player opens GUI.

### Research & Technology

**on_research_started**
Triggered when technology research begins.

**on_research_finished**
Triggered when research completes.

**on_research_cancelled**
Triggered when research stops.

### Map & Chunk

**on_chunk_generated**
Triggered when chunk creates. Provides area, position, and surface.

**on_chunk_charted**
Triggered when chunk is revealed on map.

**on_chunk_deleted**
Triggered when chunk is removed via `LuaSurface::delete_chunk()`.

### Force Management

**on_force_created**
Triggered when new force created via `game.create_force()`.

**on_forces_merging**
Triggered before two forces merge.

**on_forces_merged**
Triggered after force merge completes.

### Custom Input

**CustomInputEvent**
"Called when a CustomInputPrototype is activated." Includes player index, input name, cursor position, and selected prototype data.

## Event Filtering
Certain events support filters for improved performance:
- `on_built_entity` - Filter by entity type
- `on_entity_damaged` - Filter by damage type
- `on_entity_died` - Filter by cause
- `on_marked_for_deconstruction` - Filter by entity

## Example Event Handler Pattern

```lua
script.on_event(defines.events.on_player_created, function(event)
  local player = game.get_player(event.player_index)
  -- Handle event
end)
```
