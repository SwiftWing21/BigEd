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
-- In headless mode with 0 players, we use the surface + force directly.
-- When a player connects (spectator), we use that player for inventory/crafting.

local agent_player_index = nil  -- stored in global for save/load persistence

local function get_agent_context()
    -- Returns: { player = LuaPlayer|nil, surface = LuaSurface, force = LuaForce, has_player = bool }
    local ctx = {
        surface = game.surfaces[1],
        force = game.forces["player"],
        player = nil,
        has_player = false,
    }

    -- Try to find a real player (connected spectator or saved index)
    if agent_player_index then
        local p = game.get_player(agent_player_index)
        if p and p.valid then
            ctx.player = p
            ctx.has_player = true
            ctx.surface = p.surface or ctx.surface
            ctx.force = p.force or ctx.force
        end
    end
    if not ctx.has_player then
        for _, p in pairs(game.players) do
            if p.valid then
                ctx.player = p
                ctx.has_player = true
                ctx.surface = p.surface or ctx.surface
                ctx.force = p.force or ctx.force
                agent_player_index = p.index
                break
            end
        end
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
        data.is_crafting = entity.is_crafting
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

local function get_resources(surface, area)
    local resources = {}
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
    end
    local result = {}
    for _, v in pairs(resources) do table.insert(result, v) end
    return result
end

-- ─── Remote interface functions (return JSON strings) ────────────────────────

local function fn_get_state()
    local ctx = get_agent_context()
    local surface = ctx.surface
    local force = ctx.force
    if not surface then
        return helpers.table_to_json({error = "no surface available"})
    end

    -- Position: use player position if available, else origin
    local pos = ctx.has_player and ctx.player.position or {x = 0, y = 0}
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

    -- Inventory: only if player exists
    local inventory = {}
    if ctx.has_player then
        local inv = ctx.player.get_main_inventory()
        if inv then
            for i = 1, #inv do
                local stack = inv[i]
                if stack.valid_for_read then
                    inventory[stack.name] = (inventory[stack.name] or 0) + stack.count
                end
            end
        end
    end

    local resources = get_resources(surface, area)
    local research = nil
    local current = force.current_research
    if current then
        research = { name = current.name, progress = force.research_progress }
    end

    return helpers.table_to_json({
        tick = game.tick,
        time_of_day = surface.daytime,
        has_player = ctx.has_player,
        player = {
            position = pos,
            health = (ctx.has_player and ctx.player.character)
                     and ctx.player.character.health or 0,
        },
        inventory = inventory,
        entities = entities,
        entity_count = count,
        resources = resources,
        research = research,
        map_explored_chunks = 0,  -- force.get_chunks removed in 2.0; TODO: use force.is_chunk_charted
    })
end

local function fn_get_metrics()
    local ctx = get_agent_context()
    local force = ctx.force
    local surface = ctx.surface
    if not force or not surface then
        return helpers.table_to_json({error = "no force/surface available"})
    end
    -- Factorio 2.0: get_item_production_statistics takes a surface parameter
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
    -- Factorio 2.0: get_flow_count may have changed signature
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
    local player = ctx.player  -- may be nil in headless
    local surface = ctx.surface
    local force = ctx.force
    if not surface then
        return helpers.table_to_json({error = "no surface available"})
    end

    local action = parsed.action
    local result = { action = action, success = false }

    if action == "place" then
        local name = parsed.entity
        local pos = parsed.position
        local dir = parsed.direction or 0
        local inv = player and player.get_main_inventory() or nil
        if not inv or inv.get_item_count(name) < 1 then
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
                if items and player then
                    local player_inv = player.get_main_inventory()
                    for item_name, count in pairs(items) do
                        player_inv.insert({ name = item_name, count = count })
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
            if products and products.products and player then
                local inv = player.get_main_inventory()
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
        if not player then
            result.error = "craft requires a connected player"
            return helpers.table_to_json(result)
        end
        local recipe = parsed.recipe
        local count = parsed.count or 1
        local crafted = player.begin_crafting({ recipe = recipe, count = count })
        result.success = crafted > 0
        result.crafted = crafted

    elseif action == "research" then
        local tech_name = parsed.technology
        local tech = force.technologies[tech_name]
        if tech and not tech.researched then
            -- Factorio 2.0: current_research is read-only, use add_research
            force.add_research(tech_name)
            result.success = true
        else
            result.error = tech and "already researched" or "unknown technology"
        end

    elseif action == "move" then
        if not player then
            result.error = "move requires a connected player"
            return helpers.table_to_json(result)
        end
        local pos = parsed.position
        player.teleport(pos, surface)
        result.success = true

    elseif action == "connect" then
        local entity_name = parsed.entity or "transport-belt"
        local from = parsed.from
        local to = parsed.to
        local inv = player and player.get_main_inventory() or nil
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
            if inv and inv.get_item_count(entity_name) < 1 then
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
                    if inv then inv.remove({ name = entity_name, count = 1 }) end
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
    if ctx.has_player then
        return helpers.table_to_json({
            success = true,
            player_index = ctx.player.index,
            position = ctx.player.position,
            has_character = ctx.player.character ~= nil,
        })
    end
    -- No player in headless mode — report what we have
    return helpers.table_to_json({
        success = true,
        headless = true,
        has_player = false,
        has_surface = ctx.surface ~= nil,
        has_force = ctx.force ~= nil,
        note = "Running in playerless headless mode. Place/research/observe work. Craft/move require a connected player.",
    })
end

local function fn_status()
    local ctx = get_agent_context()
    return helpers.table_to_json({
        mod = "biged-bridge",
        version = "0.2.0",
        tick = game.tick,
        has_player = ctx.has_player,
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
    -- Store agent player index in global for save/load persistence
    storage.agent_player_index = nil
    game.print("[BigEd Bridge] v0.2.0 loaded. Remote interface: biged")
end)

script.on_load(function()
    -- Restore agent player index from save
    if storage.agent_player_index then
        agent_player_index = storage.agent_player_index
    end
end)

-- Persist agent player index when it changes
script.on_event(defines.events.on_player_joined_game, function(event)
    if not agent_player_index then
        agent_player_index = event.player_index
        storage.agent_player_index = event.player_index
    end
end)
