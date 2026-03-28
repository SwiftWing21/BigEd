# Mining
> Source: https://wiki.factorio.com/Mining
> Fetched: 2026-03-27

## Mining Speed Formula

**Basic Formula:**
```
Mining speed / Mining time = Production rate (resource/sec)
Mining time / Mining speed = Seconds per resource item
```

**Hand Mining (Modified):**
```
(1 + Mining Speed Modifier) * 0.5 / Mining time = Production rate
Expanded: (1 + Force Modifier) * (1 + Character Modifier) * (Character mining speed) / Mining time
```

## Mining Drill Types & Stats

| Drill Type | Mining Speed | Power | Coverage | Max Health | Pollution |
|---|---|---|---|---|---|
| Player | 0.5 | — | 1×1 | 250 | — |
| Burner Mining Drill | 0.25 | 150 kW (burner) | 2×2 | 150 | 12/min |
| Electric Mining Drill | 0.5 | 90 kW (electric) | 5×5 | 300 | 10/min |
| Big Mining Drill | 2.5 | 300 kW (electric) | 13×13 | 300 | 40/min |

## Resource Output Rates

All rates assume mining time of 1 second unless noted:

| Resource | Burner (0.25/s) | Electric (0.5/s) | Big (2.5/s) |
|---|---|---|---|
| Iron/Copper/Coal/Stone/Calcite | 0.25/s | 0.5/s | 2.5/s |
| Uranium (1s mining time) | N/A | 0.25/s | 1.25/s |
| Tungsten (5s mining time) | N/A | N/A | 0.5/s |

## Resource Drain & Productivity

- **Burner/Electric Drills:** 100% resource drain
- **Big Mining Drills:** 50% resource drain
- Quality reduces drain by 1/6 per level (Legendary gets double bonus)
- Mining productivity adds bonus ore without slowing drills
- Combined effects multiply: +100% productivity × 50% drain = 4× ore from patch

## Placement & Output Rules

- Drills output directly to transport belts, chests, furnaces, assemblers without inserters
- Can only place if mining area touches mineable resource
- Drill shuts down when resources in area exhaust
- Big drills have 5×5 footprint covering 13×13 area
- Electric drills have 3×3 footprint covering 5×5 area
- Burner drills have 2×2 footprint covering 2×2 area
