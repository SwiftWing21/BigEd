# Railway
> Source: https://wiki.factorio.com/Railway
> Fetched: 2026-03-27

## Train Composition
- **Minimum requirement**: At least one locomotive
- **Flexible wagons**: Any number of cargo wagons, fluid wagons, or artillery wagons can be attached
- **Multiple locomotives**: Trains can have more than one locomotive; two locomotives facing different directions enable bidirectional automatic travel

## Fuel System
- Locomotives have inventory exclusively for fuel storage
- Fuel insertion occurs only when: train in manual mode, parked at station, or not waiting at signals
- Cannot refuel while in automatic mode at signals

## Station Mechanics
- **Loading capacity**: Up to 12 adjacent inserters per cargo wagon (6 per side)
- **Fluid transfer**: Maximum 3 pumps per fluid wagon
- **Placement requirement**: Train stops must be on right-hand side of track from forward-facing perspective
- **Yellow arrows**: Indicate proper stop orientation when hovering

## Signaling Rules
- **Block occupancy**: Only one train per block at any time
- **Signal states**:
  - Green = block free
  - Yellow = train approaching with approval
  - Red = block occupied
- **Chain signal indicator**: Blue means at least one path blocked (but not all)

## Rail Infrastructure
- **Grid system**: Rails placed on two-tile grid; cannot move by single tile
- **Elevated rails** (Space Age feature):
  - Support placement: One support required every 16 straight tiles
  - Ramps and supports are only ocean-buildable structures in game
  - Cannot place stations on elevated rail

## Wait Conditions (15 Types Available)
Circuit condition, empty cargo, fluid count, fuel levels, full cargo, full fuel, has cargo, inactivity, item count, passenger presence, station capacity status, time passed

## Path Finding
- Trains automatically choose shortest route to enabled stops with correct name
- Path penalties account for apparent delays at time of departure
