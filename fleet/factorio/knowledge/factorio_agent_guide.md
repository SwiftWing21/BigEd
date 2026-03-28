# Factorio Agent Guide
> Source: BigEd skill-factorio-agent.md (bundled)
> Type: Agent Strategy Reference

## Core Mechanics

### The Production Chain
Everything in Factorio flows from raw resources to intermediate products to science packs to research.

```
Iron Ore --> Stone Furnace --> Iron Plate --> Iron Gear Wheel --+
                                    |                           |
                                    +--> Automation Science Pack +
                                              |
Copper Ore --> Stone Furnace --> Copper Plate --+
                                    |
                                    +--> Electronic Circuit --> Logistic Science Pack
                                              ^                        ^
                                    Copper Wire |                Iron Gear |
```

### Key Ratios
- 1 stone furnace smelts 0.3125 plates/sec (one plate every 3.2 sec)
- 1 electric mining drill produces 0.5 ore/sec
- So: **1 drill feeds ~1.6 furnaces** for iron/copper
- Red science (automation-science-pack): 1 assembler-1 needs 1 iron-gear assembler + raw plates
- Iron gear wheel: 2 iron plates = 1 gear (0.5 sec craft time)
- Transport belt moves 15 items/sec (one side = 7.5/sec)
- 1 inserter moves ~0.83 items/sec (regular), ~2.31 items/sec (fast)

### Entity Placement Rules
- All entities snap to integer grid positions
- **Inserters** face a direction. They pick up from BEHIND and drop in FRONT.
  - If inserter faces SOUTH (direction=4): picks from NORTH side, drops to SOUTH side
  - Place inserter between source and destination, facing FROM source TO destination
- **Belts** move items in the direction they face
- **Assemblers** are 3x3 tiles. The center tile is the position.
- **Furnaces** are 2x2. Position is center.
- **Mining drills** are 3x3. Place ON TOP of ore patches.

### Power
Nothing electric works without power. Basic power chain:
```
Offshore Pump --> Boiler --> Steam Engine --> Electric Poles
     (water)      (coal)     (power)         (distribution)
```
- 1 offshore pump feeds 20 boilers
- 1 boiler feeds 2 steam engines
- 1 steam engine = 900 kW
- Connect everything with small-electric-poles (wire reach: 7.5 tiles)

## Strategy Guide

### Phase 1: Bootstrap (Steps 0-50)
1. Check inventory -- you start with a stone furnace, burner drill, and some materials
2. Hand-craft what you need: `{"action": "craft", "recipe": "iron-gear-wheel", "count": 10}`
3. Place burner mining drills on iron and coal patches
4. Place stone furnaces next to drills
5. Hand-feed coal into drills and furnaces initially

### Phase 2: Automate Smelting (Steps 50-100)
1. Build a proper smelting line:
   - Row of electric mining drills on ore
   - Belt carrying ore to furnaces
   - Inserters loading ore into furnaces
   - Inserters pulling plates out onto a belt
2. Set up coal distribution (belts or inserters feeding fuel)
3. Get power running (offshore pump --> boiler --> steam engine)

### Phase 3: First Science (Steps 100-200)
1. Belt iron plates and copper plates to assembler area
2. Place assembling-machine-1, set recipe to iron-gear-wheel
3. Place another assembling-machine-1, set recipe to automation-science-pack
4. Feed gears + copper plates into the science assembler
5. Belt science packs to a lab
6. Start research: `{"action": "research", "technology": "automation"}`

### Phase 4: Expand (Steps 200+)
1. Scale up: more miners, more furnaces, more assemblers
2. Add green science (logistic-science-pack) -- needs electronic circuits
3. Electronic circuits need copper wire + iron plate
4. Always check bottlenecks in the observation -- fix the slowest link first

## Action Patterns

### Pattern: Smelting Column
Place furnaces in a line with inserters on both sides:
```json
[
  {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
  {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 2}},
  {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 4}},
  {"action": "place", "entity": "inserter", "position": {"x": -1, "y": 0}, "direction": "east"},
  {"action": "place", "entity": "inserter", "position": {"x": -1, "y": 2}, "direction": "east"},
  {"action": "place", "entity": "inserter", "position": {"x": -1, "y": 4}, "direction": "east"},
  {"action": "place", "entity": "inserter", "position": {"x": 1, "y": 0}, "direction": "east"},
  {"action": "place", "entity": "inserter", "position": {"x": 1, "y": 2}, "direction": "east"},
  {"action": "place", "entity": "inserter", "position": {"x": 1, "y": 4}, "direction": "east"}
]
```
Input belt at x=-2 (flowing south), output belt at x=2 (flowing south).

### Pattern: Belt Bus
Run main item belts in parallel, tap off with splitters:
```json
[
  {"action": "connect", "entity": "transport-belt", "from": {"x": 0, "y": -20}, "to": {"x": 0, "y": 20}},
  {"action": "connect", "entity": "transport-belt", "from": {"x": 2, "y": -20}, "to": {"x": 2, "y": 20}}
]
```
Iron on lane 0, copper on lane 2. Tap off at any y position.

## Decision Framework

Each step, follow this priority:
1. **Fix bottlenecks** -- if the observation shows idle assemblers or full outputs, fix those first
2. **Maintain power** -- if power entities = 0 or energy is low, build power
3. **Advance the production chain** -- work toward the current task's goal
4. **Optimize layout** -- rearrange for better throughput only after basics work
5. **Explore** -- if you need to find ore patches, move and observe

## Common Mistakes to Avoid
- Placing inserters facing the WRONG direction (remember: picks from behind, drops in front)
- Forgetting to set_recipe on assemblers after placing them
- Building assemblers before having belt infrastructure to feed them
- Running out of materials -- check inventory before placing
- Ignoring power -- electric miners and assemblers need electricity
- Building too spread out -- keep things compact to minimize belt length

## Response Format
Always respond with ONLY a JSON array. No markdown, no explanation.
Good: `[{"action": "place", "entity": "stone-furnace", "position": {"x": 5, "y": 0}}]`
Bad: `I think we should place a furnace... {"action": "place"...}`
