# Logistic Network
> Source: https://wiki.factorio.com/Logistic_network
> Fetched: 2026-03-27

## Core Components

### Roboports
- **Coverage**: 50×50 tiles (orange logistic zone), 110×110 tiles (green construction zone)
- **Charging**: 4 slots per roboport, 1 MW per bot, 1.5 seconds per bot charge
- **Throughput**: 50–70 bots/minute capacity
- **Internal Battery**: 100 MJ
- **Connection**: Roboports link when orange zones touch; creates single network when connected

### Logistic Robots
- **Default Capacity**: 1 item (increases with Worker robot cargo size research)
- **Speed**: 3 tiles/second base
- **Energy Storage**: 1.5 MJ per bot
- **Power Usage**: 3 kW continuous flying + 5 kJ per tile traveled
- **Charge Threshold**: Recharge at 20% energy capacity
- **Max Range Formula**: `1500 / (3 / speed + 5)` = 250 tiles base distance

### Construction Robots
- **Speed**: 3.6 tiles/second base
- **Energy Storage**: 1.5 MJ per bot
- **Max Range**: 257 tiles base distance
- **Functions**: Build, deconstruct, upgrade, repair entities; deliver items from remote view

## Logistic Chest Types

| Chest Type | Function |
|-----------|----------|
| **Active Provider** | Pushes stored items into the logistic network |
| **Passive Provider** | Places items at network's disposal |
| **Storage** | Holds unrequested items; can filter to single type |
| **Requester** | Fills to configured amount; accepts multiple item types |
| **Buffer** | Combines requester and passive provider functions |

## Pickup Priority (Source Order)
1. Active provider chests + player trash slots
2. Storage & buffer chests
3. Passive provider chests

## Delivery Priority (Target Order)
1. Player logistics requests
2. Requester chests (those requesting from buffers have higher priority)
3. Buffer chests (only when requests exist)
4. Storage (last resort for overflow items)

## Key Mechanics

### Robot Charging Queue Logic
- Robots prefer nearest roboport
- Switch to distant roboport if: `distance_difference <= queue_size / 2`
- Example: 10 tiles farther requires 20+ fewer robots waiting

### Negative Numbers in Network Display
The logistic network reports the total number of items in provider, buffer and storage chests, minus the amount of items scheduled to be picked up by robots. This occurs when bots reserve full cargo capacity before arrival.

### Item Delivery Overage
Bots deliver full cargo capacity when unlimited supply exists, exceeding requester minimums based on research levels.

### Distance-Based Selection
When multiple equal-priority sources exist, bots choose the closest. For items being removed (trash/active providers), distance doesn't matter; robots rotate through sources.

## Space Age Mechanics

### Cargo Landing Pad
- Places stored items at the logistic network's disposal
- Requests items from orbiting space platforms
- Trashed items export to planet's logistic network

### Space Platform Requests
- Target specific planets per item request
- "Custom minimum payload" option allows partial rocket launches
- Trashed items drop to orbiting planet when unload enabled

## Research Requirements

- **Logistic Robotics**: Player can make logistic requests
- **Logistic System**: Tank gains logistic request capability
- **Worker Robot Cargo Size**: Increases robot capacity beyond 1 item
- **Worker Robot Speed**: Improves speed (note: minimal range increase)

## Achievements

- "You've got a package": Supply character via logistic robot
- "Delivery Service": Deliver 10k items via robots
- "Logistic Network Embargo": Complete space science research without active provider, buffer, or requester chests
