# Inserters
> Source: https://wiki.factorio.com/Inserters
> Fetched: 2026-03-27

## Types of Inserters

| Inserter Type | Fuel Type | Speed Tier | Notes |
|---|---|---|---|
| Burner inserter | Fuel-powered | Slowest | Only fuel-based option |
| Inserter | Electric | Standard | Basic electrical inserter |
| Long-handed inserter | Electric | Standard | Extended reach capability |
| Fast inserter | Electric | Fast | Significantly improved speed |
| Bulk inserter | Electric | Fast | Moves multiple items simultaneously |
| Stack inserter | Electric | Fast | Can stack items on belts (Space Age) |

## Inserter Speed Specifications

**Rotation & Extension Speeds (per tick):**

- Burner: 0.013 rotation / 0.035 extension
- Yellow (Standard): 0.014 rotation / 0.035 extension
- Long-handed: 0.02 rotation / 0.05 extension
- Fast/Bulk/Stack: 0.04 rotation / 0.1 extension

**Full Rotation Times:**

| Type | Ticks | Game-seconds | Turns/second |
|---|---|---|---|
| Burner | 76 | ~1.267 | ~0.79 |
| Yellow | 70 | ~1.167 | ~0.86 |
| Long-handed | 50 | 0.833 | 1.2 |
| Fast/Bulk/Stack | 24 | 0.4 | 2.5 |

## Core Mechanics

**Inserters will:**
- Pick items from belts, ground, chests, furnaces, assembling machines, vehicles
- Place items onto ground, belts, or storage entities
- Operate at reduced speed when starved for energy
- Pick up stack-sized quantities if timing permits
- Destroy items dropped into void/lava permanently

**Inserters will NOT:**
- Pick items that cannot fit in adjacent entity
- Pick from ghost entities
- Pick when target inventory is full
- Place multiple items on same ground tile
- Insert into filtered/limited-slot inventories
- Load moving trains or trains not at stops
- Completely fill boilers, reactors, furnaces, turrets

## Insertion Limits by Entity Type

| Entity | Item Type | Automatic Limit |
|---|---|---|
| Boilers, Furnaces, Nuclear reactors | Fuel | 5 items |
| Gun turrets | Magazines | 10 items |
| Artillery turrets | Shells | 5 items |
| Production buildings | Recipe ingredients | 2-100 items (based on craft duration) |
| Labs | Science packs | Based on research unit duration |

## Transport Belt Interaction Rules

**Drop-off Pattern:**
- Standard placement: furthest lane from inserter
- Parallel belts: right-side lane (from belt perspective)
- Curved belts: always far side

**Pick-up Pattern:**
- Perpendicular belts: prefer nearest lane, fall back to far lane
- Parallel/curved belts: prefer left lane (from belt perspective)

## Chest-to-Chest Throughput (items/second)

**Normal Quality, No Capacity Bonus (stack size 1):**
- Burner: 0.79
- Yellow: 0.86
- Long-handed: 1.2
- Fast/Bulk: 2.5
- Stack: 2.5

**With Full Upgrades (capacity bonus 7, stack size varies):**
- Burner: 2.37
- Yellow: 3.43
- Long-handed: 4.8
- Fast: 10
- Bulk: 30 (stack size 4) / 30 (stack size 12)
- Stack: 40 (stack size 8) / 40 (stack size 16)

## Chest-to-Belt Throughput Notes

Output varies based on:
- Belt type (basic, fast, express, turbo)
- Capacity bonus level
- Quality of inserter
- Belt fullness state

Example: Yellow inserter at capacity bonus 2 achieves 2.3–3.27 items/second depending on belt tier.

## Belt-to-Chest Pickup Factors

Throughput influenced by:
- Item movement speed on belt (queued vs belt speed)
- Belt orientation (perpendicular vs head-on)
- Item lane position (near or far)
- Belt curve position (inner or outer)
- Underground belt entrance/exit
- Inserter-item timing synchronization

## Splitter Insertion Bonus

When inserters drop onto perpendicular splitter sides (input side), items divide between both output belts, increasing throughput. Example: Bulk inserter capacity bonus 7 achieves 16.0 items/second (vs. 14.4 standard).

## Power Consumption

**Power Modifiers (kJ):**

| Type | Modifier |
|---|---|
| Burner | 50 |
| Yellow | 5 |
| Red | 5 |
| Blue | 7 |
| Green | 20 |
| Stack | 40 |

**Power draw includes:**
- Constant drain (existence cost)
- Rotational movement cost
- Extension/retraction movement cost
- Item spike cost (0.2 unit lateral movement after pickup/dropoff)

Note: Burner inserters draw from internal fuel battery rather than grid.

## Lava/Space Dumping (Space Age)

- One item removed per tick from inserter hand
- Stack inserter with 16 items requires 16 ticks to dump
- Significantly slower than container insertion (which transfers entire hand in 1 tick)

## Competitive Pickup Rules

When multiple inserters target the same tile, the fastest inserter claims priority. Inner lane preference on perpendicular belts provides additional advantage.
