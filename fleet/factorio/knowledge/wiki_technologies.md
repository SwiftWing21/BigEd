# Technologies
> Source: https://wiki.factorio.com/Technologies
> Fetched: 2026-03-27

## Infinite Technologies Overview

Infinite technologies provide permanent bonuses and can be researched indefinitely. They follow mathematical progressions for cost calculation.

### Base Game Infinite Technologies

| Technology | Bonus | Formula | First Cost | Progression |
|---|---|---|---|---|
| Mining productivity | +10% mining yield | 2,500 × (N − F) | 2,500 | +2,500/level |
| Physical projectile damage | +40% bullet, +70% turret, +40% shotgun, +100% cannon | 1,000 × 2^(N − F − 1) | 1,000 | ×2 |
| Stronger explosives | +50% rocket, +20% grenade/mine | 1,000 × 2^(N − F − 1) | 1,000 | ×2 |
| Refined flammables | +20% fire, +20% flamethrower turret | 1,000 × 2^(N − F − 1) | 1,000 | ×2 |
| Energy weapons damage | +70% laser, +70% electric, +30% beam | 1,000 × 2^(N − F − 1) | 1,000 | ×2 |
| Artillery shell range | +30% range | 1,000 × 2^N | 2,000 | ×2 |
| Artillery shell shooting speed | +100% speed | 1,000 + 1,000 × 3^(N − 1) | 2,000 | ×3 then −2,000 |
| Follower robot count | +25 robots | 1,000 × (N − F) | 1,000 | +1,000/level |
| Worker robot speed | +65% speed | 1,000 × 2^(N − F − 1) | 1,000 | ×2 |

### Space Age Infinite Technologies

#### New/Modified Productivities

| Technology | Bonus | Formula | First Cost | Progression |
|---|---|---|---|---|
| Research productivity | +10% lab speed | 1,000 × 1.2^N | 1,200 | ×1.2 |
| Steel plate productivity | +10% yield | 1,000 × 1.5^N | 1,500 | ×1.5 |
| Low density structure productivity | +10% yield | 1,000 × 1.5^N | 1,500 | ×1.5 |
| Scrap recycling productivity | +10% yield | 500 × 1.5^N | 750 | ×1.5 |
| Processing unit productivity | +10% yield | 1,000 × 1.5^N | 1,500 | ×1.5 |
| Plastic bar productivity | +10% yield | 1,000 × 1.5^N | 1,500 | ×1.5 |
| Rocket fuel productivity | +10% yield | 1,000 × 1.5^N | 1,500 | ×1.5 |
| Asteroid productivity | +10% all asteroid yields | 1,000 × 1.5^N | 1,500 | ×1.5 |
| Rocket part productivity | +10% yield | 2,000 × 1.5^N | 3,000 | ×1.5 |

#### Combat Enhancements

| Technology | Bonus | Formula | First Cost | Progression |
|---|---|---|---|---|
| Laser weapons damage | +70% damage | 1,000 × 2^(N − F − 1) | 1,000 | ×2 |
| Artillery shell damage | +10% damage | 1,000 × 2^(N − 1) | 1,000 | ×2 |
| Electric weapons damage | +70% tesla/electric, +30% beam | 1,000 × 2^(N − F) | 2,000 | ×2 |
| Railgun damage | +40% damage | 1,000 × 2^(N − 1) | 1,000 | ×2 |
| Railgun shooting speed | +15% speed | 1,000 × 2^(N − 1) | 1,000 | ×2 |
| Health | +50 character health | 50 × 2^N | 100 | ×2 |

## Productivity Cap

All recipes have a **maximum 300% productivity cap** to prevent infinite resource exploits. This cap applies at specific research levels:

- **Processing units**: Level 13 (legendary modules full), Level 25 (no modules)
- **Steel/Low density**: Level 15 (legendary modules), Level 25 (no modules)
- **Plastic bar**: Level 10 (cryogenic plants w/ modules), Level 15 (biochambers w/ modules), Levels 25–30 (no modules)
- **Rocket fuel**: Same as plastic bar
- **Rocket parts**: Level 20 (modules), Level 30 (no modules)

**Exceptions**: Mining productivity and research productivity have no cap.

## Cumulative Research Costs

For geometric progressions (×2 base): Cumulative cost to level N ≈ 2 × final level cost

For arithmetic progressions: Cumulative = (N − F) × (mean of first and last level costs)

Expanding science production by 10× allows approximately **3–4 additional levels** before exponential scaling negates benefits.

## Trigger Technologies

Research unlocks automatically upon crafting/mining specific items:

- **Steam power**: Craft 50 iron plates
- **Electronics**: Craft 10 copper plates
- **Oil processing**: Mine crude oil
- **Uranium processing**: Mine uranium ore
- **Steel axe**: Craft 50 steel plates
- **Biter egg handling**: Capture spawner
- **Space platform**: Launch starter pack
- **Recycling**: Mine Fulgoran vault ruin
- **Agricultural science pack**: Craft 100 bioflux

## Combat Breakpoints

Notable efficiency thresholds where weapons reach practical capacity:

- **Physical projectile damage Level 1**: Gun turrets kill basic biters in 3 shots (vs 4)
- **Stronger explosives Level 2**: Grenades destroy trees in one hit
- **Artillery shell damage Level 9**: One-shot spawners and behemoth worms
- **Railgun damage Level 2**: One-shot all asteroid sizes

## Follower Robot Limitations

Maximum 1,200 robots deployable simultaneously (2 capsules/second × 10 robots × 120s lifetime). Level 50 reaches this cap; further research has no practical effect.
