-- dump_recipes.lua
-- Factorio data-final-fixes script that logs the full recipe graph.
-- Place in a mod's data-final-fixes.lua to dump every recipe to the
-- Factorio log (factorio-current.log).
--
-- Handles:
--   Old format ingredients: {"iron-plate", 1}
--   New format ingredients: {name="iron-plate", amount=1}
--   Single-result shorthand (result + result_count)
--   Multi-result recipes (results table)

local function normalise_ingredient(raw)
    if raw.name then
        -- New format: {name="iron-plate", amount=1, type="item"}
        return {
            type   = raw.type or "item",
            name   = raw.name,
            amount = raw.amount or 1,
        }
    elseif raw[1] then
        -- Old format: {"iron-plate", 1}
        return {
            type   = "item",
            name   = raw[1],
            amount = raw[2] or 1,
        }
    end
    return nil
end

local function normalise_results(recipe)
    local results = {}

    if recipe.results then
        -- Multi-result table (new or old entries)
        for _, r in ipairs(recipe.results) do
            local entry = normalise_ingredient(r)
            if entry then
                -- results can have probability; default to 1
                entry.probability = r.probability or 1
                table.insert(results, entry)
            end
        end
    elseif recipe.result then
        -- Single-result shorthand
        table.insert(results, {
            type   = "item",
            name   = recipe.result,
            amount = recipe.result_count or 1,
        })
    end

    return results
end

local function ingredients_list(recipe)
    local out = {}
    local ings = recipe.ingredients or recipe.normal and recipe.normal.ingredients or {}
    for _, raw in ipairs(ings) do
        local entry = normalise_ingredient(raw)
        if entry then
            table.insert(out, entry)
        end
    end
    return out
end

local function serialise_list(items)
    local parts = {}
    for _, item in ipairs(items) do
        table.insert(parts, item.name .. ":" .. item.amount)
    end
    return table.concat(parts, ", ")
end

-- ── Main dump ──────────────────────────────────────────────────────────

log("=== RECIPE DUMP START ===")

local count = 0
for name, recipe in pairs(data.raw.recipe) do
    local ings    = ingredients_list(recipe)
    local results = normalise_results(recipe)
    local cat     = recipe.category or "crafting"
    local energy  = recipe.energy_required or 0.5

    log(string.format(
        "RECIPE|%s|cat=%s|energy=%.1f|in=[%s]|out=[%s]|enabled=%s",
        name,
        cat,
        energy,
        serialise_list(ings),
        serialise_list(results),
        tostring(recipe.enabled ~= false)
    ))
    count = count + 1
end

log(string.format("=== RECIPE DUMP END === (%d recipes)", count))
