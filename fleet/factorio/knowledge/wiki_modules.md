# Module
> Source: https://wiki.factorio.com/Module
> Fetched: 2026-03-27

## Module Types Overview

Modules enhance building capabilities with 3 tiers each. Machine properties (speed, energy consumption and pollution) cannot be reduced below 20% of the original value.

### Speed Modules

Increase machine speed and energy consumption.

| Tier | Speed Bonus | Energy Penalty | Quality Impact |
|------|------------|----------------|----------------|
| 1 | +20% to +50% | +50% | Reduces quality chance |
| 2 | +30% to +75% | +60% | Reduces quality chance |
| 3 | +50% to +125% | +70% | Reduces quality chance |

**Additive scaling:** Multiple modules stack additively, not multiplicatively.

### Productivity Modules

Generate bonus output without consuming resources when productivity bar reaches 100%.

| Tier | Productivity | Energy | Speed | Pollution |
|------|-------------|--------|-------|-----------|
| 1 | +4% to +10% | +40% | −5% | +5% |
| 2 | +6% to +15% | +60% | −10% | +7% |
| 3 | +10% to +25% | +80% | −15% | +10% |

**Restrictions:** Cannot be used in beacons. Limited to intermediate product recipes. Ejects automatically if recipe changes to incompatible type.

### Efficiency Modules

Reduce electricity consumption.

| Tier | Energy Reduction |
|------|------------------|
| 1 | −30% to −75% |
| 2 | −40% to −100% |
| 3 | −50% to −125% |

**Cap rule:** Cannot reduce energy below 20% of base consumption.

### Quality Modules

Increase chance of higher-rarity item production. Space Age expansion exclusive feature.

| Tier | Quality Bonus | Speed Penalty |
|------|---------------|---------------|
| 1 | +1% to +2.5% | −5% |
| 2 | +2% to +5% | −5% |
| 3 | +2.5% to +6.2% | −5% |

## Synergy & Optimization

**Productivity + Speed:** Speed modules offset productivity's speed penalty, sometimes improving efficiency more than efficiency modules alone.

**Quality scaling breakpoints:**
- Speed 1: Requires legendary quality to match speed bonus with power penalty
- Speed 3: Reaches breakeven at rare quality (80% speed vs 70% power)
- Productivity 1: Epic quality needed for 6.3% productivity to offset 6% penalty

## Usage Recommendations

- **Speed modules:** For infinite resources with limited deposits (e.g., oil)
- **Productivity modules:** For scarce resources; recommended for high-consumption recipes
- **Efficiency modules:** For high-power machines like electric furnaces (180 kW baseline)
- **Quality modules:** Should not pair with speed modules

## Effect Mechanics

- **Additive stacking:** Two identical modules produce double effect, not diminishing returns
- **Energy-pollution link:** Reducing energy consumption by X% also reduces pollution generation by X%
- **Pollution multiplier:** Applies per module tier, stacking additively with energy changes
