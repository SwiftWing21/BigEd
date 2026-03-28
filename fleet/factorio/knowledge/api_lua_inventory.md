# Factorio Runtime API — LuaInventory
> Source: https://lua-api.factorio.com/latest/classes/LuaInventory.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Class Overview
A storage system for item stacks in Factorio, providing methods to manage inventory contents, filters, and capacity constraints.

## Core Methods

### Inventory Management
- **`clear()`** - "Clear this inventory of all items so that it becomes empty."
- **`insert(items)`** → `uint32` - Insert items and receive count of items actually added
- **`remove(items)`** → `uint32` - Remove items and receive count of items actually removed
- **`sort_and_merge()`** - Organize items in the inventory

### Item Queries
- **`get_item_count(item?)`** → `uint32` - Count all items or filter by specific item
- **`get_item_count_filtered(filter)`** → `uint32` - Count items matching provided filter criteria
- **`get_item_quality_counts(item?)`** → `dictionary` - "Get the number of all or some items in this inventory, aggregated by quality."
- **`get_contents()`** → `ItemWithQualityCounts` - Retrieve all items with their counts
- **`can_insert(items)`** → `boolean` - Determine if any portion of items can be added

### Item Location
- **`find_item_stack(item)`** → `LuaItemStack?, uint32?` - Locate first matching stack and its index
- **`find_empty_stack(item?)`** → `LuaItemStack?, uint32?` - "Finds the first empty stack" (filters excluded unless item specified)
- **`count_empty_stacks(include_filtered?, include_bar?)`** → `uint32` - Count available slots

### Capacity
- **`get_insertable_count(item)`** → `uint32` - "Gets the number of the given item that can be inserted into this inventory"
- **`is_empty()`** → `boolean` - Check if inventory contains nothing
- **`is_full()`** → `boolean` - Verify all stacks are full (ignoring bar-blocked slots)

### Inventory Bar (Limits)
- **`supports_bar()`** → `boolean` - "Bar is the draggable red thing...that limits the portion of the inventory that may be manipulated by machines"
- **`get_bar()`** → `uint32` - Retrieve current bar index position
- **`set_bar(bar?)`** - Adjust bar position or clear it with nil

### Filters
- **`supports_filters()`** → `boolean` - Check if slots can have restrictions
- **`is_filtered()`** → `boolean` - "If this inventory supports filters and has at least 1 filter set"
- **`get_filter(index)`** → `ItemFilter?` - Retrieve slot filter
- **`set_filter(index, filter)`** → `boolean` - Apply filter; returns whether operation succeeded
- **`can_set_filter(index, filter)`** → `boolean` - Validate filter application

### Advanced
- **`resize(size)`** - Modify inventory size (script-created only)
- **`destroy()`** - Remove inventory (script-created only)

## Properties

### Ownership
- **`entity_owner`** :: R LuaEntity? - The entity containing this inventory
- **`player_owner`** :: R LuaPlayer? - The player who owns this inventory
- **`equipment_owner`** :: R LuaEquipment? - Equipment containing this inventory
- **`mod_owner`** :: R string? - Creating mod identifier

### Metadata
- **`index`** :: R defines.inventory? - Inventory slot designation
- **`name`** :: R string? - Custom name if available
- **`object_name`** :: R string - Class identifier
- **`valid`** :: R boolean - Whether object reference remains valid

### Capacity
- **`weight`** :: R Weight - Current total weight
- **`max_weight`** :: R Weight? - Maximum weight capacity

## Operators
- **`[index]`** - Access LuaItemStack by position (1-indexed)
- **`#`** (length) - Get total number of slots

## Usage Example

```lua
local inv = game.player.get_main_inventory()
local count = inv:get_item_count("iron-plate")
inv:insert({name="copper-ore", count=50})
inv:set_bar(20)  -- Limit manipulable area

-- Check before inserting
if inv:can_insert({name="iron-plate", count=10}) then
  inv:insert({name="iron-plate", count=10})
end

-- Find a specific item stack
local stack, index = inv:find_item_stack("iron-ore")
if stack then
  print("Found iron-ore at index " .. index)
end

-- Iterate over inventory slots
for i = 1, #inv do
  local slot = inv[i]
  if slot.valid_for_read then
    print(slot.name, slot.count)
  end
end
```
