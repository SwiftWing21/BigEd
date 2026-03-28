-- biged-bridge/control.lua
-- State serializer and command executor for BigEd agent bridge
--
-- Exposes RCON commands:
--   /biged-state    - dumps current game state as JSON
--   /biged-cmd      - executes a structured command from the agent
--   /biged-observe  - dumps a focused observation around a position
--   /biged-metrics  - dumps production/research metrics

-- Factorio 2.0 uses helpers.table_to_json() / helpers.json_to_table() (no require needed)

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

-- Helper: get or create a player for headless mode
local function get_agent_player()
    local player = get_agent_player()
    if player and player.valid then
        -- Ensure player has a character (may be missing in headless)
        if not player.character then
            local surface = player.surface or game.surfaces[1]
            local pos = surface.find_non_colliding_position("character", {0, 0}, 50, 1)
            if pos then
                player.teleport(pos, surface)
                player.create_character()
            end
        end
        return player
    end
    -- No player at all — can't create one via script in headless without a connection
    return nil
end

commands.add_command("biged-state", "Dump game state for agent", function(cmd)
    local player = get_agent_player()
    if not player then
        rcon.print(helpers.table_to_json({error = "no player — connect as spectator first or join the server"}))
        return
    end
    local surface = player.surface
    local pos = player.position
    local r = CONFIG.observation_radius
    local area = { { pos.x - r, pos.y - r }, { pos.x + r, pos.y + r } }
    local raw_entities = surface.find_entities_filtered({
        area = area, force = player.force,
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
    local inv = player.get_main_inventory()
    local inventory = {}
    if inv then
        for i = 1, #inv do
            local stack = inv[i]
            if stack.valid_for_read then
                inventory[stack.name] = (inventory[stack.name] or 0) + stack.count
            end
        end
    end
    local resources = get_resources(surface, area)
    local research = nil
    local current = player.force.current_research
    if current then
        research = { name = current.name, progress = player.force.research_progress }
    end
    local state = {
        tick = game.tick,
        time_of_day = surface.daytime,
        player = { position = pos, health = player.character and player.character.health or 0 },
        inventory = inventory,
        entities = entities,
        entity_count = count,
        resources = resources,
        research = research,
        map_explored_chunks = #player.force.get_chunks(surface),
    }
    rcon.print(helpers.table_to_json(state))
end)

commands.add_command("biged-metrics", "Dump production metrics", function(cmd)
    local player = get_agent_player()
    if not player then
        rcon.print(helpers.table_to_json({error = "no player available"}))
        return
    end
    local force = player.force
    local stats = force.item_production_statistics
    local production = {}
    local consumption = {}
    for _, item_name in pairs(CONFIG.tracked_items) do
        local produced = stats.get_input_count(item_name)
        local consumed = stats.get_output_count(item_name)
        if produced > 0 then production[item_name] = produced end
        if consumed > 0 then consumption[item_name] = consumed end
    end
    local flow = {}
    for _, item_name in pairs(CONFIG.tracked_items) do
        local rate = stats.get_flow_count{
            name = item_name, input = true,
            precision_index = defines.flow_precision_index.five_seconds,
        }
        if rate and rate > 0 then
            flow[item_name] = math.floor(rate * 60 * 10) / 10
        end
    end
    local electric = nil
    if player.surface then
        local networks = player.force.get_electric_networks(player.surface)
        if networks and #networks > 0 then
            local net = networks[1]
            electric = {
                capacity_mw = math.floor(net.statistics.get_input_count("steam-engine") or 0),
                satisfaction = net.valid and "ok" or "unknown",
                entity_count = #net.entity_ids,
            }
        end
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
    rcon.print(helpers.table_to_json(metrics))
end)

commands.add_command("biged-cmd", "Execute agent command (JSON)", function(cmd)
    if not cmd.parameter then
        rcon.print('{"error": "no command provided"}')
        return
    end
    local ok, parsed = pcall(helpers.json_to_table, cmd.parameter)
    if not ok or not parsed then
        rcon.print('{"error": "invalid JSON"}')
        return
    end
    local action = parsed.action
    local result = { action = action, success = false }

    if action == "place" then
        local player = get_agent_player()
        local surface = player.surface
        local name = parsed.entity
        local pos = parsed.position
        local dir = parsed.direction or 0
        local inv = player.get_main_inventory()
        if not inv or inv.get_item_count(name) < 1 then
            result.error = "missing item: " .. (name or "nil")
            rcon.print(helpers.table_to_json(result))
            return
        end
        local can_place = surface.can_place_entity({
            name = name, position = pos, direction = dir, force = player.force,
        })
        if not can_place then
            result.error = "cannot place " .. name .. " at (" .. pos.x .. ", " .. pos.y .. ")"
            rcon.print(helpers.table_to_json(result))
            return
        end
        local entity = surface.create_entity({
            name = name, position = pos, direction = dir, force = player.force,
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
        local entities = get_agent_player().surface.find_entities_filtered({
            type = { "assembling-machine", "furnace" },
        })
        for _, ent in pairs(entities) do
            if ent.unit_number == unit then
                local items = ent.set_recipe(recipe)
                result.success = true
                if items then
                    local player_inv = get_agent_player().get_main_inventory()
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
        local player = get_agent_player()
        local target = nil
        if unit then
            local all = player.surface.find_entities_filtered({ force = player.force })
            for _, ent in pairs(all) do
                if ent.unit_number == unit then target = ent; break end
            end
        elseif pos then
            local hits = player.surface.find_entities_filtered({
                position = pos, radius = 0.5, force = player.force, limit = 1,
            })
            target = hits[1]
        end
        if target and target.valid then
            local products = target.prototype.mineable_properties
            if products and products.products then
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
        local player = get_agent_player()
        local recipe = parsed.recipe
        local count = parsed.count or 1
        local crafted = player.begin_crafting({ recipe = recipe, count = count })
        result.success = crafted > 0
        result.crafted = crafted

    elseif action == "research" then
        local force = get_agent_player().force
        local tech_name = parsed.technology
        local tech = force.technologies[tech_name]
        if tech and not tech.researched then
            force.current_research = tech
            result.success = true
        else
            result.error = tech and "already researched" or "unknown technology"
        end

    elseif action == "move" then
        local player = get_agent_player()
        local pos = parsed.position
        player.teleport(pos, player.surface)
        result.success = true

    elseif action == "connect" then
        local player = get_agent_player()
        local surface = player.surface
        local entity_name = parsed.entity or "transport-belt"
        local from = parsed.from
        local to = parsed.to
        local inv = player.get_main_inventory()
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
            if inv.get_item_count(entity_name) < 1 then
                result.error = "ran out of " .. entity_name .. " after " .. placed
                break
            end
            local can = surface.can_place_entity({
                name = entity_name, position = { cx, cy },
                direction = dir, force = player.force,
            })
            if can then
                local ent = surface.create_entity({
                    name = entity_name, position = { cx, cy },
                    direction = dir, force = player.force,
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

    else
        result.error = "unknown action: " .. tostring(action)
    end

    rcon.print(helpers.table_to_json(result))
end)

commands.add_command("biged-observe", "Observe area (x y radius)", function(cmd)
    local params = cmd.parameter or ""
    local x, y, radius = params:match("([%d%-%.]+)%s+([%d%-%.]+)%s*([%d%.]*)")
    x = tonumber(x) or 0
    y = tonumber(y) or 0
    radius = tonumber(radius) or 32
    local surface = get_agent_player().surface
    local area = { { x - radius, y - radius }, { x + radius, y + radius } }
    local entities = {}
    for _, ent in pairs(surface.find_entities(area)) do
        local s = serialize_entity(ent)
        if s then table.insert(entities, s) end
    end
    local resources = get_resources(surface, area)
    rcon.print(helpers.table_to_json({
        center = { x = x, y = y },
        radius = radius,
        entities = entities,
        resources = resources,
    }))
end)

script.on_init(function()
    game.print("[BigEd Bridge] Agent bridge mod loaded. Use /biged-state to test.")
end)

script.on_load(function() end)
