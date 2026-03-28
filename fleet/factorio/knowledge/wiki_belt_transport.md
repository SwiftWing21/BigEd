# Belt Transport System
> Source: https://wiki.factorio.com/Belt_transport_system
> Fetched: 2026-03-27

## Belt Tiers & Speeds

| Belt Type | Speed (tiles/sec) | Throughput (stacks/sec) | Color | Research Required |
|-----------|-------------------|------------------------|-------|-------------------|
| Transport Belt | 1.875 | 15 | Yellow | Logistics (base) |
| Fast Transport Belt | 3.75 | 30 | Red | Logistics 2 |
| Express Transport Belt | 5.625 | 45 | Blue | Logistics 3 |
| Turbo Transport Belt | 7.5 | 60 | Green | Turbo transport belt |

## Belt Storage Capacity

- **Straight belt segment**: Holds exactly 8 stacks maximum
- **Underground belt (4 tiles)**: Stores up to 44 items
- **Express underground belt (max length 8)**: Stores up to 72 items

## Underground Belt Distances

The underground distance is 4, 6 and 8 tiles, respectively, for the three belt types in base game.

## Belt Mechanics

**Lanes**: All belts have 2 lanes for item transport, allowing either doubled flow or mixed materials on one belt.

**Splitter Behavior**:
- Splits input 1:1 between two outputs
- Preserves item lanes (right lane stays right)
- Supports priority settings (left/right input and output)
- Can filter specific items to designated outputs
- Must match belt speed to avoid bottlenecks

**Lane Balancing**: Required when inserters create unbalanced lane usage; essential for maintaining throughput.

## Throughput Enhancement Methods

1. **Density increase**: Force items into gaps via mining drills, inserters, or sideloading
2. **Speed upgrade**: Replace belts with faster tiers
3. **Parallel belts**: Add additional parallel belt lines

## Circuit Network Integration

Belts connect via red or green wires for two operational modes:
- **Enable/disable**: Circuit conditions control item flow
- **Read belt contents**: Monitors transported items
- **Read modes**: Pulse (single tick) or hold (continuous)
