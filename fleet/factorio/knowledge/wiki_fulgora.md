# Fulgora
> Source: https://wiki.factorio.com/Fulgora
> Fetched: 2026-03-27
> DLC: Space Age

## Planet Overview
Fulgora is a barren desert planet where the surface alternates between island plateaus and deep oilsands. It features a severe lightning storm system and no hostile enemies. Day/night cycle is 3 minutes.

## Surface Properties

| Property | Value |
|----------|-------|
| Pollutant Type | None |
| Day/Night Cycle | 3 minutes |
| Magnetic Field | 99 |
| Solar Power | 20% |
| Pressure | 800 |
| Gravity | 8 |

## Orbital Properties

| Property | Value |
|----------|-------|
| Solar Power | 120% |

### Asteroid Spawn Ratios (Orbital)
| Type | Spawn Ratio |
|------|------------|
| Metallic | 4 |
| Carbonic | 3 |
| Oxide | 1 |
| Promethium | 0 |

## Biomes & Terrain

**Plateaus**: Island-like landmasses where factories can be built. Hold scrap deposits and alien ruins.

**Oilsands**: Lowlands between plateaus. Only rail is buildable here. Slows player movement significantly. Offshore pumps placed on oilsand shores produce unlimited heavy oil.

**Island Classes:**
| Class | Resources | Build Space |
|-------|-----------|-------------|
| Small | High | Limited |
| Medium | Moderate | Small |
| Large | None | Maximum |

## Lightning Mechanics
Lightning strikes once per chunk approximately every 10 seconds. Lightning prioritizes hitting protection structures over damaging factory buildings.

### Lightning Strike Priorities (Highest First)
| Entity | Priority |
|--------|----------|
| Lightning Collector | 10,000 |
| Lightning Rod | 1,000 |
| Fulgoran Lightning Attractor | 1,000 |
| Fulgoran Vault Ruin | 95 |
| Colossal Fulgoran Ruin | 94 |
| Huge Fulgoran Ruin | 93 |
| Big Fulgoran Ruin | 92 |
| Medium Fulgoran Ruin | 91 |
| Metal entities (pipes, poles, furnaces, etc.) | 1 |

**Lightning-Immune Entities**: Rail pieces, rail signals, trains, walls, trees, rocks, fulgurite.

## Resources & Crafting

### Primary Resources
- **Scrap**: Found in mineable deposits on small/medium islands; primary input for all crafting via recycling
- **Heavy Oil**: Infinite from offshore pumps placed on oilsand shores
- **Water**: Obtained by melting ice (recycled from scrap)

### Scrap Recycling Yields
| Output | Chance | Rate (per second) |
|--------|--------|------------------|
| Iron Gear Wheel | 20% | 0.5/s |
| Solid Fuel | 7% | 0.175/s |
| Concrete | 6% | 0.15/s |
| Ice | 5% | 0.125/s |
| Steel Plate | 4% | 0.1/s |
| Battery | 4% | 0.1/s |
| Stone | 4% | 0.1/s |
| Copper Cable | 3% | 0.075/s |
| Advanced Circuit | 3% | 0.075/s |
| Processing Unit | 2% | 0.05/s |
| Low Density Structure | 1% | 0.025/s |
| Holmium Ore | 1% | 0.025/s |

### Deriving Basic Resources from Scrap
| Needed Resource | Recycle Route |
|----------------|---------------|
| Iron Ore | Recycle concrete |
| Iron Plate | Recycle iron gear wheels, batteries, or electronic circuits |
| Copper Plate | Recycle copper cables, batteries, or low density structures |
| Plastic Bar | Recycle advanced circuits or low density structures |
| Coal | Must be imported |
| Copper Ore | Not available on-planet |
| Crude Oil | Not available; heavy oil substitutes |

## Exclusive Items (Fulgora-Only Crafting)
- Electromagnetic Science Pack
- Electromagnetic Plant
- Lightning Collector
- Lightning Rod
- Recycler
- Mech Armor
- Personal Battery MK3
- Energy Shield MK2
- Personal Roboport MK2
- Quality Module 3
- Tesla Turret

## Space Routes

| Destination | Distance |
|-------------|----------|
| Nauvis | 15,000 km |
| Gleba | 15,000 km |
| Aquilo | 30,000 km |

## Key Design Notes
- No enemies on-planet; explosives/grenades unnecessary
- Lightning serves dual purpose: protection hazard AND power generation source
- Weak solar output (20%) makes lightning power generation critical
- Transport between islands typically requires trains (roboports/poles limited without landfill/foundations)
- Global electric networks difficult to establish before foundation research
