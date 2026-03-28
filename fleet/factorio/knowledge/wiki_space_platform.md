# Space Platform
> Source: https://wiki.factorio.com/Space_platform
> Fetched: 2026-03-27
> DLC: Space Age

## Core Purpose
Space platforms serve three main functions: crafting space-exclusive recipes, transporting players and items between planets, and collecting/processing asteroids.

## Creation & Management
- Created by loading a "space platform starter pack" into a rocket
- Players can name platforms upon creation
- Platforms can be renamed via space platform hub menus
- Deletion requires confirmation with a 5-minute undo window
- Hub destruction deletes entire platform and all contents

## Construction Rules
- Edited exclusively through remote view using ghost placement
- Initial platform: 10×10 foundation square with central hub
- Foundation must form single connected area with no detached islands
- Asteroid collectors and thrusters can only be built on the edge
- Thrusters restricted to southern edge only
- 82-tile clearance required below southern thrusters
- **Maximum northward extension: 200 tiles from hub center**

## Placement Restrictions
The following cannot be built on platforms:
- Chests (hub replaces storage)
- Robots, roboports, railway entities
- Burner devices (no atmosphere)
- Buildings with surface restrictions

Electric poles function only for circuit networking; the entire platform acts as a unified electric network.

## Weight & Physics
- Space platform foundation: 200 kg (0.2 tons) per tile
- Space platform hub: 20 tons
- Cargo bays don't add weight beyond foundation requirements
- Platform drifts toward nearest planet when stopped between locations

## Travel System

### Thruster Mechanics
- More thrusters = faster travel but increased asteroid encounters
- Speed proportional to asteroid frequency and damage risk

### Interplanetary Schedules
Platform scheduling functions like train systems with 12 wait condition types:

1. All/any requests satisfied
2. Any request zero
3. Circuit condition
4. Damage taken (default: 1,000)
5. Inactivity (seconds configurable)
6. Item count
7. Passenger present/not present
8. Request satisfied/not satisfied
9. Time passed

**Special shattered planet mechanics:** Uses "fly condition" to trigger mid-journey rather than at destination.

### Circuit Network Integration
- Read hub contents
- Send signals to scheduler
- Read current destination/departure planet
- Read platform velocity (V signal)
- Read damage taken (D signal)

## Resource Acquisition

| Resource | Source | Method |
|----------|--------|--------|
| Water | Oxide asteroid chunks | Oxide crushing + ice melting |
| Iron ore | Metallic asteroid chunks | Metallic crushing |
| Copper ore | Metallic asteroid chunks | Advanced metallic crushing |
| Coal | Carbonic asteroid chunks | Coal synthesis (carbon + sulfur) |
| Heavy oil | Calcite | Simple coal liquefaction |
| Stone | Planetside only | Must import from planets |
| Crude oil | Unobtainable | N/A |

## Asteroid System
- **Chunks:** Collected by asteroid collectors, processed by crushers
- **Large asteroids:** Damage platforms on impact, destroyed by turrets
- **Stationary orbit:** Only chunks appear (safe building environment)
- Asteroid density scales with platform velocity and location

## Transport Between Surfaces

### Uploading to Platform (3 methods)
1. Manual rocket launch with mixed items
2. Logistic request + single item type automation
3. Logistic robots + rocket silo request (single item type per rocket)

### Downloading to Planet (2 methods)
1. Manual transfer via hub orbital drop slots
2. Cargo landing pad logistic requests (no material cost)

## Passenger System
- One character per rocket (no items except equipped weapons/armor)
- Players locked in hub during space stay
- Inventory inaccessible while aboard
- Biter/pentapod eggs spawn enemies normally (no suffocation damage)

## Key Statistics
- Hub cannot be removed
- Platform can be renamed anytime
- No construction robots in space
- Entire platform is unified electric network
- Auto-construction pulls materials from orbited planet if enabled

## Achievement
**"Reach for the stars"** — Create a space platform
