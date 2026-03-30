-- biged-bridge/control.lua
-- State serializer and command executor for BigEd agent bridge (Factorio 2.0+)
--
-- Architecture: remote interface (not commands.add_command) for reliable RCON responses.
-- Bridge calls via: /c rcon.print(remote.call("biged", "get_state"))
--
-- Remote interface functions:
--   remote.call("biged", "get_state")     -> JSON state dump
--   remote.call("biged", "get_metrics")   -> JSON production metrics
--   remote.call("biged", "exec_cmd", j)   -> execute JSON command, return result
--   remote.call("biged", "observe", x,y,r)-> focused area observation
--   remote.call("biged", "status")        -> bridge status / health check

-- Factorio 2.0: helpers.table_to_json() / helpers.json_to_table()

local CONFIG = {
    observation_radius = 64,
    max_entities = 500,
    agent_inventory_size = 200,
    tracked_items = {
        "iron-plate", "copper-plate", "steel-plate",
        "iron-gear-wheel", "electronic-circuit", "advanced-circuit",
        "automation-science-pack", "logistic-science-pack",
        "transport-belt", "inserter", "assembling-machine-1",
        "assembling-machine-2", "stone-furnace", "electric-mining-drill",
        "pipe", "offshore-pump", "boiler", "steam-engine",
    },
}

-- ─── Agent context (works with or without a player) ─────────────────────────
-- Headless mode: script-owned inventory + standalone character entity.
-- Player mode: uses connected player's inventory + character.
-- Both modes share the same action interface.

local agent_player_index = nil  -- stored in global for save/load persistence

local function get_or_create_agent_inventory()
    if storage.biged_inventory and storage.biged_inventory.valid then
        return storage.biged_inventory
    end
    storage.biged_inventory = game.create_inventory(CONFIG.agent_inventory_size)
    return storage.biged_inventory
end

local function get_agent_context()
    -- Returns: { player, character, surface, force, inventory,
    --            has_player, has_character }
    local ctx = {
        surface = game.surfaces[1],
        force = game.forces["player"],
        player = nil,
        character = nil,
        has_player = false,
        has_character = false,
        inventory = nil,
    }

    -- Try saved agent_player_index — but only if they are the sole player.
    -- With multiple players (spectator connected), skip to headless fallback.
    local total_players = 0
    for _ in pairs(game.players) do total_players = total_players + 1 end

    if agent_player_index and total_players <= 1 then
        local p = game.get_player(agent_player_index)
        if p and p.valid then
            ctx.player = p
            ctx.has_player = true
            ctx.surface = p.surface or ctx.surface
            ctx.force = p.force or ctx.force
            if p.character and p.character.valid then
                ctx.character = p.character
                ctx.has_character = true
                ctx.inventory = p.get_main_inventory()
            end
        end
    end
    if not ctx.has_player then
        -- Only claim a player if there is exactly one (headless mode).
        -- With multiple players (spectator connected), never hijack a human.
        local all_players = game.players
        local count = 0
        local solo = nil
        for _, p in pairs(all_players) do
            if p.valid then count = count + 1; solo = p end
        end
        if count == 1 and solo then
            ctx.player = solo
            ctx.has_player = true
            ctx.surface = solo.surface or ctx.surface
            ctx.force = solo.force or ctx.force
            agent_player_index = solo.index
            storage.agent_player_index = solo.index
            if solo.character and solo.character.valid then
                ctx.character = solo.character
                ctx.has_character = true
                ctx.inventory = solo.get_main_inventory()
            end
        end
        -- If multiple players, fall through to headless character below
    end

    -- Headless fallback: use stored character
    if not ctx.has_character and storage.biged_character then
        if storage.biged_character.valid and storage.biged_character.health > 0 then
            ctx.character = storage.biged_character
            ctx.has_character = true
        else
            storage.biged_character = nil
        end
    end

    -- Always ensure we have an inventory (script-owned if no player)
    if not ctx.inventory then
        ctx.inventory = get_or_create_agent_inventory()
    end

    return ctx
end

-- ─── Entity serializer ──────────────────────────────────────────────────────

local function serialize_entity(entity)
    if not entity.valid then return nil end
    local data = {
        name = entity.name,
        type = entity.type,
        position = { x = math.floor(entity.position.x * 10) / 10,
                     y = math.floor(entity.position.y * 10) / 10 },
        direction = entity.direction,
        health = entity.health,
        unit_number = entity.unit_number,
    }
    if entity.type == "assembling-machine" or entity.type == "furnace" then
        local recipe = entity.get_recipe()
        data.recipe = recipe and recipe.name or nil
        data.crafting_progress = entity.crafting_progress
        local ok_craft, craft_val = pcall(function() return entity.is_crafting end)
        if ok_craft and type(craft_val) == "boolean" then
            data.is_crafting = craft_val
        elseif ok_craft and type(craft_val) == "function" then
            local ok2, val2 = pcall(craft_val)
            data.is_crafting = ok2 and val2 or false
        else
            data.is_crafting = false
        end
        local input_inv = entity.get_inventory(defines.inventory.assembling_machine_input)
                       or entity.get_inventory(defines.inventory.furnace_source)
        local output_inv = entity.get_inventory(defines.inventory.assembling_machine_output)
                        or entity.get_inventory(defines.inventory.furnace_result)
        if input_inv then
            data.input = {}
            for i = 1, #input_inv do
                local stack = input_inv[i]
                if stack.valid_for_read then
                    data.input[stack.name] = (data.input[stack.name] or 0) + stack.count
                end
            end
        end
        if output_inv then
            data.output = {}
            for i = 1, #output_inv do
                local stack = output_inv[i]
                if stack.valid_for_read then
                    data.output[stack.name] = (data.output[stack.name] or 0) + stack.count
                end
            end
        end
    end
    if entity.type == "transport-belt" or entity.type == "splitter"
       or entity.type == "underground-belt" then
        data.belt_contents = {}
        for i = 1, entity.get_max_transport_line_index() do
            local line = entity.get_transport_line(i)
            if line then
                for name, count in pairs(line.get_contents()) do
                    data.belt_contents[name] = (data.belt_contents[name] or 0) + count
                end
            end
        end
    end
    if entity.type == "inserter" then
        local held = entity.held_stack
        data.held_item = (held and held.valid_for_read) and
            { name = held.name, count = held.count } or nil
        data.pickup_position = entity.pickup_position
        data.drop_position = entity.drop_position
    end
    if entity.type == "mining-drill" then
        data.mining_target = entity.mining_target and entity.mining_target.name or nil
        data.mining_progress = entity.mining_progress
    end
    if entity.electric_buffer_size and entity.electric_buffer_size > 0 then
        data.energy = entity.energy
        data.electric_buffer_size = entity.electric_buffer_size
    end
    data.status = entity.status and
        serpent.line(entity.status, { compact = true }) or nil
    return data
end

local function get_terrain(surface, area, step)
    -- Sample terrain type at regular intervals across the observation area.
    -- step=2 means every other tile (64x64 area → 32x32 samples = 1024 entries).
    step = step or 2
    local tiles = {}
    for x = area[1][1], area[2][1], step do
        for y = area[1][2], area[2][2], step do
            local ix = math.floor(x)
            local iy = math.floor(y)
            local tile = surface.get_tile(ix, iy)
            if tile and tile.valid then
                table.insert(tiles, {x=ix, y=iy, t=tile.name})
            end
        end
    end
    return tiles
end

local function get_resources(surface, area)
    local resources = {}
    local positions = {}
    local pos_count = 0
    local res_entities = surface.find_entities_filtered({
        area = area, type = "resource",
    })
    for _, ent in pairs(res_entities) do
        local name = ent.name
        if not resources[name] then
            resources[name] = { name = name, patches = 0, total_amount = 0 }
        end
        resources[name].patches = resources[name].patches + 1
        resources[name].total_amount = resources[name].total_amount + ent.amount
        -- Send all resource positions in local area (no sampling cap)
        table.insert(positions, {
            name = name,
            x = ent.position.x,
            y = ent.position.y,
            amount = ent.amount,
        })
    end
    local result = {}
    for _, v in pairs(resources) do table.insert(result, v) end
    return result, positions
end

-- ─── Remote interface functions (return JSON strings) ────────────────────────

local function fn_get_state()
    local ctx = get_agent_context()
    local surface = ctx.surface
    local force = ctx.force
    if not surface then
        return helpers.table_to_json({error = "no surface available"})
    end

    -- Position: use character (player or headless), else origin
    local char = ctx.character
    local pos
    if ctx.has_player and ctx.player.character then
        pos = ctx.player.position
    elseif char then
        pos = char.position
    else
        pos = {x = 0, y = 0}
    end
    local r = CONFIG.observation_radius
    local area = { { pos.x - r, pos.y - r }, { pos.x + r, pos.y + r } }

    local raw_entities = surface.find_entities_filtered({
        area = area, force = force,
    })
    local entities = {}
    local count = 0
    for _, ent in pairs(raw_entities) do
        if count >= CONFIG.max_entities then break end
        if ent.type ~= "corpse" and ent.type ~= "particle"
           and ent.type ~= "decorative" and ent.type ~= "character" then
            local serialized = serialize_entity(ent)
            if serialized then
                table.insert(entities, serialized)
                count = count + 1
            end
        end
    end

    -- Inventory: from ctx (player inventory or script inventory)
    local inventory = {}
    local inv = ctx.inventory
    if inv and inv.valid then
        for i = 1, #inv do
            local stack = inv[i]
            if stack.valid_for_read then
                inventory[stack.name] = (inventory[stack.name] or 0) + stack.count
            end
        end
    end

    local resources, resource_positions = get_resources(surface, area)
    local terrain_tiles = get_terrain(surface, area, 2)
    local research = nil
    local current = force.current_research
    if current then
        research = { name = current.name, progress = force.research_progress }
    end

    -- Global resource counts (single cheap call — strategic awareness)
    local global_resources = {}
    local ok_rc, rc = pcall(surface.get_resource_counts)
    if ok_rc and rc then global_resources = rc end

    return helpers.table_to_json({
        tick = game.tick,
        time_of_day = surface.daytime,
        has_player = ctx.has_player,
        has_character = ctx.has_character,
        headless_character = (not ctx.has_player and ctx.has_character) or false,
        headless_inventory = not ctx.has_player,
        player = {
            position = pos,
            health = char and char.health or 0,
            max_health = char and char.max_health or 0,
            has_character = ctx.has_character,
            alive = ctx.has_character and char.valid and char.health > 0 or false,
        },
        inventory = inventory,
        entities = entities,
        entity_count = count,
        resources = resources,
        resource_positions = resource_positions,
        terrain = terrain_tiles,
        global_resources = global_resources,
        research = research,
        map_explored_chunks = 0,
    })
end

local function fn_get_metrics()
    local ctx = get_agent_context()
    local force = ctx.force
    local surface = ctx.surface
    if not force or not surface then
        return helpers.table_to_json({error = "no force/surface available"})
    end
    local stats = force.get_item_production_statistics(surface)
    local production = {}
    local consumption = {}
    for _, item_name in pairs(CONFIG.tracked_items) do
        local produced = stats.get_input_count(item_name)
        local consumed = stats.get_output_count(item_name)
        if produced > 0 then production[item_name] = produced end
        if consumed > 0 then consumption[item_name] = consumed end
    end
    local flow = {}
    local fok, _ = pcall(function()
        for _, item_name in pairs(CONFIG.tracked_items) do
            local rate = stats.get_flow_count{
                name = item_name, input = true,
                precision_index = defines.flow_precision_index.five_seconds,
            }
            if rate and rate > 0 then
                flow[item_name] = math.floor(rate * 60 * 10) / 10
            end
        end
    end)
    if not fok then flow = {} end
    local electric = nil
    local eok, enet = pcall(function()
        if surface then
            local networks = force.get_electric_networks(surface)
            if networks and #networks > 0 then
                return { count = #networks, satisfaction = "ok" }
            end
        end
        return nil
    end)
    if eok and enet then
        electric = enet
    end
    local metrics = {
        tick = game.tick,
        total_produced = production,
        total_consumed = consumption,
        flow_per_minute = flow,
        electric = electric,
        research = {
            completed = {},
            current = force.current_research and force.current_research.name or nil,
            progress = force.research_progress,
        },
    }
    for name, tech in pairs(force.technologies) do
        if tech.researched then
            table.insert(metrics.research.completed, name)
        end
    end
    return helpers.table_to_json(metrics)
end

local function fn_exec_cmd(json_str)
    if not json_str then
        return '{"error": "no command provided"}'
    end
    local ok, parsed = pcall(helpers.json_to_table, json_str)
    if not ok or not parsed then
        return '{"error": "invalid JSON"}'
    end
    local ctx = get_agent_context()
    local surface = ctx.surface
    local force = ctx.force
    local inv = ctx.inventory  -- player inventory OR script inventory
    if not surface then
        return helpers.table_to_json({error = "no surface available"})
    end

    local action = parsed.action
    local result = { action = action, success = false }

    if action == "place" then
        local name = parsed.entity
        local pos = parsed.position
        local dir = parsed.direction or 0
        if not name or not pos then
            result.error = "place requires 'entity' and 'position'"
            return helpers.table_to_json(result)
        end
        local has_item = false
        if inv and name then
            local ok_cnt, cnt_val = pcall(inv.get_item_count, inv, name)
            has_item = ok_cnt and cnt_val >= 1
        end
        if not has_item then
            result.error = "missing item: " .. (name or "nil")
            return helpers.table_to_json(result)
        end
        local can_place = surface.can_place_entity({
            name = name, position = pos, direction = dir, force = force,
        })
        if not can_place then
            result.error = "cannot place " .. name .. " at (" .. pos.x .. ", " .. pos.y .. ")"
            return helpers.table_to_json(result)
        end
        local entity = surface.create_entity({
            name = name, position = pos, direction = dir, force = force,
        })
        if entity then
            inv.remove({ name = name, count = 1 })
            result.success = true
            result.unit_number = entity.unit_number
            result.actual_position = entity.position
        else
            result.error = "create_entity returned nil"
        end

    elseif action == "set_recipe" then
        local unit = parsed.unit_number
        local recipe = parsed.recipe
        local entities = surface.find_entities_filtered({
            type = { "assembling-machine", "furnace" },
        })
        for _, ent in pairs(entities) do
            if ent.unit_number == unit then
                local items = ent.set_recipe(recipe)
                result.success = true
                if items and inv then
                    for item_name, count in pairs(items) do
                        inv.insert({ name = item_name, count = count })
                    end
                end
                break
            end
        end
        if not result.success then
            result.error = "entity not found: " .. tostring(unit)
        end

    elseif action == "remove" then
        local unit = parsed.unit_number
        local pos = parsed.position
        local target = nil
        if unit then
            local all = surface.find_entities_filtered({ force = force })
            for _, ent in pairs(all) do
                if ent.unit_number == unit then target = ent; break end
            end
        elseif pos then
            local hits = surface.find_entities_filtered({
                position = pos, radius = 0.5, force = force, limit = 1,
            })
            target = hits[1]
        end
        if target and target.valid then
            local products = target.prototype.mineable_properties
            if products and products.products and inv then
                for _, prod in pairs(products.products) do
                    inv.insert({ name = prod.name, count = prod.amount or 1 })
                end
            end
            target.destroy()
            result.success = true
        else
            result.error = "entity not found"
        end

    elseif action == "craft" then
        -- Manual craft: read recipe prototype, consume inputs, produce outputs.
        -- Works with both player inventory and script inventory.
        if not inv then
            result.error = "no inventory available for crafting"
            return helpers.table_to_json(result)
        end
        local recipe_name = parsed.recipe
        local count = parsed.count or 1
        local recipe = prototypes.recipe[recipe_name]
        if not recipe then
            result.error = "unknown recipe: " .. tostring(recipe_name)
            return helpers.table_to_json(result)
        end
        -- Check how many we can craft
        local can_craft = count
        for _, ingredient in pairs(recipe.ingredients) do
            local have = inv.get_item_count(ingredient.name)
            local max_from_this = math.floor(have / ingredient.amount)
            can_craft = math.min(can_craft, max_from_this)
        end
        if can_craft <= 0 then
            result.error = "insufficient ingredients for " .. recipe_name
            local missing = {}
            for _, ingredient in pairs(recipe.ingredients) do
                local have = inv.get_item_count(ingredient.name)
                local need = ingredient.amount * count
                if have < need then
                    table.insert(missing, ingredient.name .. ": have=" .. have .. " need=" .. need)
                end
            end
            result.missing = table.concat(missing, ", ")
            return helpers.table_to_json(result)
        end
        -- Consume inputs
        for _, ingredient in pairs(recipe.ingredients) do
            inv.remove({ name = ingredient.name, count = ingredient.amount * can_craft })
        end
        -- Produce outputs
        local crafted_items = {}
        for _, product in pairs(recipe.products) do
            local amt = math.floor((product.amount or 1) * can_craft)
            if amt > 0 then
                inv.insert({ name = product.name, count = amt })
                crafted_items[product.name] = amt
            end
        end
        result.success = true
        result.crafted = can_craft
        result.items = crafted_items

    elseif action == "research" then
        local tech_name = parsed.technology
        if not tech_name or tech_name == "" then
            result.error = "no technology specified"
        else
            local tech = force.technologies[tech_name]
            if tech and not tech.researched then
                force.add_research(tech_name)
                result.success = true
            else
                result.error = tech and "already researched" or "unknown technology: " .. tostring(tech_name)
            end
        end

    elseif action == "move" then
        -- Move the agent character (teleport). Works with player or headless character.
        local char = ctx.character
        if not char or not char.valid then
            result.error = "move requires a character (use ensure_player first)"
            return helpers.table_to_json(result)
        end
        local pos = parsed.position
        if not pos or pos.x == nil or pos.y == nil then
            result.error = "move requires position with x and y"
        else
            char.teleport(pos, surface)
            result.success = true
        end

    elseif action == "connect" then
        local entity_name = parsed.entity or "transport-belt"
        local from = parsed.from
        local to = parsed.to
        if not from or not to then
            result.error = "connect requires 'from' and 'to' positions"
            return helpers.table_to_json(result)
        end
        if not inv then
            result.error = "no inventory for connection items"
            return helpers.table_to_json(result)
        end
        local placed = 0
        local dx = to.x > from.x and 1 or (to.x < from.x and -1 or 0)
        local dy = to.y > from.y and 1 or (to.y < from.y and -1 or 0)
        local dir = 0
        if dx == 1 then dir = 2
        elseif dx == -1 then dir = 6
        elseif dy == 1 then dir = 4
        end
        local cx, cy = from.x, from.y
        local max_steps = math.abs(to.x - from.x) + math.abs(to.y - from.y) + 1
        for step = 1, max_steps do
            local ok_cnt, cnt_val = pcall(inv.get_item_count, inv, entity_name)
            if not ok_cnt or cnt_val < 1 then
                result.error = "ran out of " .. entity_name .. " after " .. placed
                break
            end
            local can = surface.can_place_entity({
                name = entity_name, position = { cx, cy },
                direction = dir, force = force,
            })
            if can then
                local ent = surface.create_entity({
                    name = entity_name, position = { cx, cy },
                    direction = dir, force = force,
                })
                if ent then
                    inv.remove({ name = entity_name, count = 1 })
                    placed = placed + 1
                end
            end
            if dx ~= 0 and cx ~= to.x then
                cx = cx + dx
            elseif dy ~= 0 and cy ~= to.y then
                cy = cy + dy
                dir = dy == 1 and 4 or 0
            else
                break
            end
        end
        result.success = placed > 0
        result.placed = placed

    elseif action == "mine" then
        local pos = parsed.position or {x = 0, y = 0}
        if not inv then
            result.error = "no inventory available for mining"
            return helpers.table_to_json(result)
        end
        local area = {{pos.x - 0.5, pos.y - 0.5}, {pos.x + 0.5, pos.y + 0.5}}
        local entities = surface.find_entities_filtered{area = area, limit = 1}
        if #entities > 0 then
            local entity = entities[1]
            if entity.minable then
                local products = entity.prototype.mineable_properties
                if products and products.products then
                    for _, product in pairs(products.products) do
                        if product.type == "item" then
                            inv.insert{name = product.name, count = product.amount or 1}
                        end
                    end
                end
                local mined_name = entity.name
                entity.destroy()
                result.success = true
                result.mined = mined_name
                result.position = pos
            else
                result.error = "entity not minable"
            end
        else
            -- Try resource entities (ore patches)
            local resources = surface.find_entities_filtered{
                area = area, type = "resource", limit = 1
            }
            if #resources > 0 then
                local resource = resources[1]
                local mine_amount = math.min(resource.amount, 5)
                resource.amount = resource.amount - mine_amount
                inv.insert{name = resource.name, count = mine_amount}
                if resource.amount <= 0 then
                    resource.destroy()
                end
                result.success = true
                result.mined = resource.name
                result.amount = mine_amount
                result.position = pos
            else
                result.error = "nothing to mine at position"
            end
        end

    else
        result.error = "unknown action: " .. tostring(action)
    end

    return helpers.table_to_json(result)
end

local function fn_observe(x, y, radius)
    x = tonumber(x) or 0
    y = tonumber(y) or 0
    radius = tonumber(radius) or 32
    local ctx = get_agent_context()
    local surface = ctx.surface
    if not surface then
        return helpers.table_to_json({error = "no surface available"})
    end
    local area = { { x - radius, y - radius }, { x + radius, y + radius } }
    local entities = {}
    for _, ent in pairs(surface.find_entities(area)) do
        local s = serialize_entity(ent)
        if s then table.insert(entities, s) end
    end
    local resources = get_resources(surface, area)
    return helpers.table_to_json({
        center = { x = x, y = y },
        radius = radius,
        entities = entities,
        resources = resources,
    })
end

local function fn_ensure_player()
    local ctx = get_agent_context()

    -- If we have a CONNECTED player but no character, respawn one.
    if ctx.has_player and ctx.player.connected and not ctx.has_character then
        local ok, err = pcall(function()
            ctx.player.set_controller{type = defines.controllers.god}
            ctx.player.create_character()
        end)
        if ok and ctx.player.character and ctx.player.character.valid then
            game.print("[BigEd Bridge] Respawned character for connected player")
        end
    end

    if ctx.has_player then
        local p = ctx.player
        local char = p.character
        if char and char.valid then
            return helpers.table_to_json({
                success = true,
                player_index = p.index,
                position = p.position,
                has_character = true,
                health = char.health,
                max_health = char.max_health,
                alive = char.health > 0,
            })
        end
    end

    -- Headless mode: check existing headless character
    if ctx.has_character and ctx.character and ctx.character.valid then
        return helpers.table_to_json({
            success = true,
            headless = true,
            has_player = false,
            has_character = true,
            position = ctx.character.position,
            health = ctx.character.health,
            max_health = ctx.character.max_health,
            alive = ctx.character.health > 0,
        })
    end

    -- Strategy 1: Only use a connected player if they are the SOLE player
    -- (headless with no spectator). Never hijack a human spectator.
    local player_count = 0
    local sole_player = nil
    for _, p in pairs(game.players) do
        if p.valid then player_count = player_count + 1; sole_player = p end
    end
    if player_count == 1 and sole_player and sole_player.connected and not sole_player.character then
        local ok, err = pcall(function()
            sole_player.set_controller{type = defines.controllers.god}
            sole_player.create_character()
        end)
        if ok and sole_player.character and sole_player.character.valid then
            agent_player_index = sole_player.index
            storage.agent_player_index = sole_player.index
            game.print("[BigEd Bridge] Respawned character for sole player " .. sole_player.name)
            return helpers.table_to_json({
                success = true,
                headless = true,
                has_player = true,
                player_index = sole_player.index,
                has_character = true,
                position = sole_player.character.position,
                health = sole_player.character.health,
                max_health = sole_player.character.max_health,
                alive = sole_player.character.health > 0,
            })
        end
    end

    -- Strategy 2: Standalone character entity (pure headless, no player)
    local surface = game.get_surface("nauvis") or game.surfaces[1]
    local force = game.forces["player"]
    if not surface then
        return helpers.table_to_json({
            success = false, error = "no_surface",
        })
    end

    local spawn = force.get_spawn_position(surface)
    local char = surface.create_entity{
        name = "character", position = spawn, force = force,
    }
    if not char then
        local fallback = surface.find_non_colliding_position("character", spawn, 10, 1)
        if fallback then
            char = surface.create_entity{
                name = "character", position = fallback, force = force,
            }
        end
    end
    if char then
        storage.biged_character = char
        -- Ensure script inventory exists
        get_or_create_agent_inventory()
        game.print("[BigEd Bridge] Headless agent ready: character + script inventory")
        return helpers.table_to_json({
            success = true,
            headless = true,
            has_player = false,
            has_character = true,
            position = char.position,
            health = char.health,
            max_health = char.max_health,
            alive = char.health > 0,
        })
    end

    return helpers.table_to_json({
        success = false,
        error = "spawn_blocked",
        headless = true,
        position = spawn,
    })
end

local function fn_status()
    local ctx = get_agent_context()
    return helpers.table_to_json({
        mod = "biged-bridge",
        version = "0.4.0",
        tick = game.tick,
        has_player = ctx.has_player,
        has_character = ctx.has_character,
        headless_character = (not ctx.has_player and ctx.has_character) or false,
        headless_inventory = not ctx.has_player and ctx.inventory ~= nil,
        player_index = agent_player_index,
        surface_count = #game.surfaces,
        force_name = ctx.force and ctx.force.name or "none",
    })
end

-- ─── Register remote interface ──────────────────────────────────────────────

remote.add_interface("biged", {
    get_state = fn_get_state,
    get_metrics = fn_get_metrics,
    exec_cmd = fn_exec_cmd,
    observe = fn_observe,
    ensure_player = fn_ensure_player,
    status = fn_status,
})

-- ─── Lifecycle ──────────────────────────────────────────────────────────────

script.on_init(function()
    storage.agent_player_index = nil
    storage.biged_character = nil
    storage.biged_inventory = nil  -- script-owned inventory for headless mode
    game.print("[BigEd Bridge] v0.4.0 loaded. Remote interface: biged")
    game.print("[BigEd Bridge] Headless mode: script inventory + standalone character")
end)

script.on_load(function()
    if storage.agent_player_index then
        agent_player_index = storage.agent_player_index
    end
end)

script.on_configuration_changed(function()
    if storage.biged_character and not storage.biged_character.valid then
        storage.biged_character = nil
        game.print("[BigEd Bridge] Headless character invalidated after config change")
    end
    if storage.biged_inventory and not storage.biged_inventory.valid then
        storage.biged_inventory = nil
        game.print("[BigEd Bridge] Script inventory invalidated — will recreate on next use")
    end
end)

script.on_event(defines.events.on_player_joined_game, function(event)
    if not agent_player_index then
        agent_player_index = event.player_index
        storage.agent_player_index = event.player_index
    end
end)
