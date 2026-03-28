# Aquilo
> Source: https://wiki.factorio.com/Aquilo
> Fetched: 2026-03-27
> DLC: Space Age

## Planet Overview
Aquilo is a desolate, freezing ocean planet. Its surface consists solely of a vast ocean of liquid ammonia, with occasional floating icebergs. It is the most distant planet and the last in the Space Age progression.

## Surface Properties

| Property | Value |
|----------|-------|
| Pollutant Type | None |
| Day/Night Cycle | 20 minutes |
| Magnetic Field | 10 |
| Solar Power | 1% |
| Pressure | 300 |
| Gravity | 15 |
| Robot Energy Usage | 500% |

## Progression Requirements
Planet discovery research for Aquilo requires completing research from all other planets first:
- Rocket turrets (from Gleba)
- Advanced asteroid processing (research)
- Heating towers (from Gleba)
- Asteroid reprocessing (from Vulcanus)
- Electromagnetic science pack (from Fulgora)

## Natural Resources

**Extractable on Aquilo only:**
- Ammoniacal solution (via offshore pump)
- Lithium brine (via pumpjack)
- Fluorine (via pumpjack)
- Crude oil (via pumpjack)

**Must be imported from other planets:**
- Stone
- Iron ore
- Copper ore
- Coal

## Exclusive Crafting Locations
Items craftable only on Aquilo:
- Cryogenic plant
- Fusion generator
- Fusion reactor
- Cryogenic science pack
- Quantum processor

Non-barrellable fluids (Aquilo-exclusive):
- Ammoniacal solution
- Fluorine
- Lithium brine
- Ammonia

## Ice Terrain Mechanics
Construction requires ice platforms (not standard landfill or foundation). Process:
1. Separate ammoniacal solution into ice + ammonia
2. Recombine separated ice into placeable ice platforms

Most buildings require an insulating floor of concrete tiles or derivatives before placement. Stone bricks cannot be used to pave ice.

## Freezing Mechanics
Most buildings will freeze and stop working unless heated by a heat pipe or heat generator. Minimum heat activation threshold: **30°C**.

### Entity Heat Consumption

| Entity | Heat Consumption |
|--------|-----------------|
| Transport belts | 10 kW |
| Underground belts (tier-dependent) | 50–200 kW |
| Splitters | 40 kW |
| Pipes | 1 kW |
| Pipe-to-ground | 150 kW |
| Pumps | 30 kW |
| Storage tanks | 100 kW |
| Inserters | 30–50 kW |
| Assembling machines / Chemical plants / Labs | 100 kW |
| Oil refineries / Artillery turrets | 200 kW |
| Beacons | 400 kW |

**Entities immune to freezing:** Burner devices, heat-producing machines, chests, poles, solar panels, robots, vehicles, trains, offshore pumps, walls, gates.

## Solar Power
Solar panels on Aquilo's surface output only 1% of Nauvis rate: **0.6 kW peak production** per panel. This makes solar essentially useless for power.

**Bootstrap path note**: Require 59 base solar panels (with 3 efficiency modules each) to run a single assembling machine 2 at full speed for initial water production.

## Space Routes

| Destination | Distance |
|------------|----------|
| Gleba | 30,000 km |
| Fulgora | 30,000 km |
| Solar system edge | 100,000 km |

## Orbital Properties

| Property | Value |
|----------|-------|
| Solar Power | 60% |

### Asteroid Spawn Ratios (Orbital)
| Type | Spawn Ratio |
|------|------------|
| Metallic | 1 |
| Carbonic | 2 |
| Oxide | 20 |
| Promethium | 0 |

### Asteroid Size Distribution (Orbital)
| Size | Rate |
|------|------|
| Chunk | 0.10% |
| Medium | 0% |
| Big | 0.25% |
| Huge | 0% |

## Key Design Challenges
- Virtually no solar power; must rely on heat-based power (fusion generators)
- Every building needs both insulating floor and active heating
- Robot energy usage is 5× normal — logistics networks expensive
- Core resources (stone, iron, copper, coal) must all be imported
- Ice platform construction is prerequisite for any expansion on the liquid ammonia ocean
