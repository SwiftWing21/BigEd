# Factorio Runtime API — LuaForce
> Source: https://lua-api.factorio.com/latest/classes/LuaForce.html
> Fetched: 2026-03-27
> Type: Lua API Reference

## Class Overview
`LuaForce` represents a faction/force in Factorio. The default forces are "player", "enemy", and "neutral". Up to 64 custom forces can be created.

## Key Methods

### Research & Technology
- `enable_research()` / `disable_research()` - Toggle research capability
- `add_research(technology)` - Queue technology for research
- `cancel_current_research()` - Stop active research
- `research_all_technologies(include_disabled_prototypes?)` - Research everything
- `reset_technologies()` - Reload tech from prototypes
- `script_trigger_research(technology)` - Trigger scripted research

### Recipe Management
- `enable_all_recipes()` - Unlock all recipes
- `reset_recipes()` - Reload recipes from prototypes
- `get_hand_crafting_disabled_for_recipe(recipe)` - Check if hand-crafting disabled
- `set_hand_crafting_disabled_for_recipe(recipe, disabled)` - Toggle hand-crafting

### Evolution & Difficulty
- `get_evolution_factor(surface?)` - Fetch overall evolution
- `get_evolution_factor_by_pollution(surface?)` - Pollution component
- `get_evolution_factor_by_time(surface?)` - Time component
- `get_evolution_factor_by_killing_spawners(surface?)` - Spawner kill component
- `set_evolution_factor(factor, surface?)` - Set overall evolution
- `reset_evolution()` - Reset to zero

### Chart & Map
- `chart(surface, area)` - Reveal map area
- `clear_chart(surface?)` - Erase chart data
- `is_chunk_charted(surface, position)` - Check if chunk revealed
- `add_chart_tag(surface, tag)` - Place custom map tag

### Military & Relations
- `set_cease_fire(other, cease_fire)` - Prevent attacks on another force
- `set_friend(other, friend)` - Grant building access
- `is_friend(other)` / `is_enemy(other)` - Check relationships
- `kill_all_units()` - Eliminate all units

### Space & Unlocks
- `lock_space_location(name)` / `unlock_space_location(name)` - Planet access
- `lock_quality(quality)` / `unlock_quality(quality)` - Quality tier access
- `lock_space_platforms()` / `unlock_space_platforms()` - Platform feature
- `create_space_platform{name?, planet, starter_pack}` - Build platform

### Statistics
- `get_item_production_statistics(surface)` - Item production data
- `get_fluid_production_statistics(surface)` - Fluid production data
- `get_kill_count_statistics(surface)` - Enemy kills data
- `get_entity_build_count_statistics(surface)` - Building construction data

## Key Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `name` | string | R | Force identifier |
| `technologies` | Table | R | Tech indexed by name |
| `recipes` | Table | R | Recipes indexed by name |
| `current_research` | LuaTechnology | R | Active research |
| `research_progress` | double | RW | Research 0-1 |
| `research_queue` | array | RW | Queued technologies |
| `players` | array | R | Belonging players |
| `connected_players` | array | R | Online players only |

### Modifiers (RW)
- `manual_mining_speed_modifier` - Mining multiplier
- `manual_crafting_speed_modifier` - Crafting multiplier
- `laboratory_speed_modifier` - Lab speed
- `worker_robots_speed_modifier` - Robot movement
- `character_running_speed_modifier` - Player speed
- `inserter_stack_size_bonus` - Stack size
- `character_inventory_slots_bonus` - Inventory capacity
- `maximum_following_robot_count` - Follower limit

### Flags (RW)
- `friendly_fire` - Allow team damage
- `share_chart` - Enable map sharing
- `ai_controllable` - Enable faction AI
- `circuit_network_enabled` - Circuit availability
- `cliff_deconstruction_enabled` - Auto-deconstruct cliffs

## Examples

```lua
-- Queue research
game.player.force.add_research("steel-processing")

-- Modify speed (double mining)
game.player.force.manual_mining_speed_modifier = 1

-- Check evolution
local evo = game.player.force.get_evolution_factor()

-- Set relationships
game.player.force.set_friend("neutral", true)
game.player.force.set_cease_fire("enemy", true)

-- Chart area
game.player.force.chart(game.player.surface, {{x=-1024, y=-1024}, {x=1024, y=1024}})

-- Message force
game.player.force.print("Hello team!")
```
