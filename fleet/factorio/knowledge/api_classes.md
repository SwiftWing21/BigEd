# Factorio Runtime API — Classes
> Source: https://lua-api.factorio.com/latest/classes.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Overview
This documentation covers Factorio's Lua scripting API (version 2.0.76), providing access to game instances through global classes. The primary entry point is `LuaGameScript`, which grants access to most API functionality.

## Key Global Classes

### LuaGameScript
The main toplevel type providing access to most API functionality through its members. Usage example: `game.get_player(1)` retrieves a player instance.

### LuaBootstrap
Entry point for event handling registration, supporting:
- `on_init()` - Runs on mod initialization
- `on_load()` - Runs on save load
- `on_event(event, handler, filters?)` - Registers event handlers
- `on_nth_tick(tick, handler)` - Registers periodic handlers
- `raise_event(event, data)` - Raises custom events

### LuaPlayer
Represents a player in the game. Methods include:
- `print(message)` - Display text in player's console
- Character and inventory management
- GUI interaction capabilities

### LuaEntity
Primary interface for interacting with in-game entities through the Lua API. Supports entity manipulation and inspection.

### LuaControl (Abstract Base)
Common functionality between `LuaPlayer` and entities:
- Inventory management: `get_inventory()`
- Item operations: `insert()`, `remove_item()`
- Crafting: `begin_crafting()`, `cancel_crafting()`
- Teleportation: `teleport(position, surface?)`
- GUI operations: `open_technology_gui()`, `open_factoriopedia_gui()`

## Control Behaviors
Specialized control behaviors for various entity types:
- `LuaAssemblingMachineControlBehavior` - Recipe and content management
- `LuaAccumulatorControlBehavior` - Charge monitoring
- `LuaArithmeticCombinatorControlBehavior` - Signal parameters
- `LuaDeciderCombinatorControlBehavior` - Conditional logic with conditions and outputs

## Prototypes
Extensive prototype classes for game data:
- `LuaEntityPrototype` - Entity definitions
- `LuaItemPrototype` - Item definitions
- `LuaTechnologyPrototype` - Research tech data
- `LuaRecipePrototype` - Crafting recipes

## Circuit Networks
- `LuaCircuitNetwork` - Associated with entities and wire types
- `LuaWireConnector` - Entity wire connection management
- Signal-based communication system

## Property Access Pattern
Most properties use consistent notation:
- `:: R` - Read-only properties
- `:: RW` - Read-write properties
- `:: W` - Write-only properties

## Example Usage
```lua
local first_player = game.get_player(1)
first_player.print(first_player.name)
```
