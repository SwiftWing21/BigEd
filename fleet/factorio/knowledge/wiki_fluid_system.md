# Fluid System
> Source: https://wiki.factorio.com/Fluid_system
> Fetched: 2026-03-27

## Available Fluids

| Fluid | Sources | Notes |
|-------|---------|-------|
| Water | Offshore pump, Ice melting, Steam condensation | Found on Nauvis, Gleba |
| Steam | Boiler, Heat exchanger, Chemical plant | No natural resource |
| Crude oil | Pumpjack | Found on Nauvis, Aquilo |
| Petroleum gas | Oil refinery, Chemical plant, Biochamber | Produced only |
| Light oil | Oil refinery, Chemical plant, Biochamber | Produced only |
| Heavy oil | Oil refinery, Chemical plant, Biochamber | Found on Fulgora |
| Lubricant | Chemical plant, Biochamber | Produced only |
| Sulfuric acid | Pumpjack, Chemical plant | Found on Vulcanus |
| Lava | Offshore pump | Found on Vulcanus |
| Molten iron/copper | Foundry, smelting from lava | Space Age content |
| Holmium solution, Electrolyte, Ammoniacal solution | Chemical/Cryogenic plants | Space Age content |
| Fluoroketone (hot/cold) | Cryogenic plant | Space Age content |

## Core Mechanics

### Storage & Volume
- Fluids stored in "fluid boxes" with defined maximum volumes
- Pipe capacity: 100 units
- Storage tank capacity: 25,000 units
- Fluid level expressed as percentage of max capacity

### Pressure & Flow
All connected tanks and pipes are treated as a single vessel in that the level of fluid must be equal in all parts. Flow rates depend on pressure differences between adjacent entities. Pressure equalizes automatically between connected pipes/tanks.

### Flow Balance Example
With 12,550 units flowing into a 25,000-capacity tank and a 100-capacity pipe connected: tank holds 12,500 units (50%), pipe holds 50 units (50%). Both maintain equal percentage fill.

### Temperature
- Default temperature: 15°C
- Boiler output: 165°C steam
- Heat exchanger output: 500°C steam
- Maximum: 1,000°C; Minimum: 15°C
- Steam energy ratio: 0.2 kJ per °C per unit
- Maximum work per unit: 197 kJ

## Throughput Limits

**Theoretical maximum:** 6,000 fluid/second per input/output connection (100 per tick)

**Practical limit:** ~4,200 fluid/second per connection

**Multi-output scaling:** Machine with two outputs: ~8,400 fluid/second practical maximum

Flow restriction based on segment fullness:
- Empty segment → faster inflow
- Full segment → faster outflow

## Pipeline Constraints

- **Maximum pipe length without pump:** 320×320 tiles (10×10 chunk area)
- **Pumps required:** To break up excessively long sections and prevent backflow
- **Underground pipes:** Connect only in two opposite directions
- **Automatic connection:** Pipes connect to all four cardinal directions simultaneously

## Transport Methods

### Pipelines
- Instant fluid transfer within segments (no distance penalty)
- Requires pumps for long distances
- Cannot mix different fluids in single segment

### Barrels
- Capacity: 20,000 units (cargo wagon)
- Cannot transport: Steam or Space Age fluids (except fluoroketone variants)
- Requires Assembling machines to fill/empty
- Allows belt and logistic network transport

### Railway
- **Fluid wagons:** 50,000 capacity, single fluid type
- **Cargo wagons:** 20,000 capacity, mixed fluids possible, flexible alignment

## Machine Behavior

Machines with fluid inputs:
- Drain connected pipes at fixed rates until input slot full
- Behave like pipes that "never fill"

Machines with fluid outputs:
- Attempt to empty output slots into connected entities
- Distribute equally across multiple outputs unless some blocked/full

Machines with both inputs and outputs:
- Prioritize self-consumption
- Excess behaves as standard pipe

## Fluid Mixing Rules

- Game prevents most accidental mixing (pipes reject placement)
- Single segment can only contain one fluid type
- Non-matching fluids deleted if forced together

## Space Age Recipes — Throughput Limits

**Acid neutralisation:**
- Chemical plant: 4.2 crafting speed reaches limit (both outputs)
- Cryogenic plant: 6.3 crafting speed reaches limit

**Steam condensation:**
- Chemical plant: 8.4 crafting speed reaches limit (both inputs)
- Cryogenic plant: 12.6 crafting speed reaches limit

**Advanced thruster fuel/oxidizer:**
- Maximum productivity: 75%
- Limited at 32 crafting speed

**Molten metal from lava:**
- Foundry base crafting speed: 4
- Input limited at 268 crafting speed (0% productivity)
- Output limited at 215 crafting speed (150% productivity)

**Ore-based molten metals:**
- Always output limited
- Limited at 358 crafting speed (base 50% productivity)
- Limited at 240 crafting speed (150% productivity)
