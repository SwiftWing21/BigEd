# Electric System
> Source: https://wiki.factorio.com/Electric_system
> Fetched: 2026-03-27

## Power Generation

| Generator Type | Key Details |
|---|---|
| Steam Engines | Most common; requires boilers and fuel |
| Solar Panels | Free energy during daylight only; typically paired with accumulators |
| Steam Turbines | High-power alternative to steam engines; used with nuclear reactors |
| Lightning Rods/Collectors | Space Age DLC; converts lightning strikes (Fulgora) into power |
| Fusion Generators | Space Age DLC; converts plasma to electricity |
| Accumulators | Energy storage devices |

**Generator Behavior:** If a network consumes less power than is produced, its steam engines, turbines, and fusion generators will slow down so that no power is wasted.

## Energy Storage

### Storage Methods & Capacity

| Storage Type | Capacity | Equivalent Accumulators | Size | Density (MJ/tile) |
|---|---|---|---|---|
| Accumulator | 5 MJ | 1 | 2×2 | 1.25 |
| Steam Tank (165°C) | 750 MJ | 150 | 3×3 | 83.33 |
| Steam Tank (500°C) | 2,400 MJ | 480 | 3×3 | 266.66 |
| Heat Pipe | 500 MJ (theoretical) | 100 | 1×1 | 500 |

**Accumulator Discharge Rate:** Maximum 300 kW per unit

**Steam Engine Output:** 900 kW from stored steam (3× faster than accumulators)

**Steam Turbine Output:** 5,800 kW (6.4× faster than accumulators)

## Power Distribution

### Electric Poles

| Pole Type | Coverage Area | Cable Reach | Features |
|---|---|---|---|
| Small Electric Pole | 2nd smallest | Shortest | Available without research |
| Medium Electric Pole | 2nd largest | Average | Mid-tier option |
| Big Electric Pole | Smallest | Longest | Recommended for long distances |
| Substation | Largest | 2nd longest | Most expensive |

**Connection Rules:**
- New poles connect to closest available poles first
- Won't form triangles (connect to 2 poles already connected)
- Maximum of 5 connections per pole

## Power Consumption

**Load Sharing:** If an electric network does not have enough power generation to supply all the machines in it, the electricity will be evenly spread across all machines in the network (based on each machine's demand), and all machines will slow down proportionally to the power available.

**Example Calculation:**
- Assembling Machine 3: 210 kW
- Electric Mining Drill: 90 kW
- Total demand: 300 kW
- Available supply: 180 kW (3 solar panels)
- Result: Both machines run at 60% speed (180 ÷ 300 = 0.6)

**Machine Power Components:**
- Energy consumption: Active operation power draw
- Drain: Idle power consumption (usually negligible)
- Example: Active Assembling Machine 2 = 155 kW (150 kW consumption + 5 kW drain)

## Network Priority System

Power generation follows this priority order:

1. **Solar Panels** — Highest priority; max performance unless they meet all demand
2. **Lightning Rods/Collectors** — High priority (Space Age)
3. **Steam Engines, Steam Turbines, Fusion Generators** — Equal priority; share remaining demand equally
4. **Accumulators** — Last resort; only discharge when demand exceeds other sources; only charge when demand is fully met

## Network Monitoring

The Electric Network Info GUI displays:
- **Satisfaction Bar:** Current energy consumed vs. available (yellow >50%, red <50%)
- **Production Bar:** Current generation vs. capacity
- **Accumulator Charge:** Energy stored in joules
- **Consumption/Production Graphs:** Time-series data (adjustable timeframe)
- **Detailed Lists:** Ranked consumers and producers by power level
