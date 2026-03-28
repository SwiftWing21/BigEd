# Circuit Network
> Source: https://wiki.factorio.com/Circuit_network
> Fetched: 2026-03-27

## Core System
Circuit networks enable control of devices based on signals broadcast by senders. Networks use red or green wires and operate independently by color.

## Signal Channels
- One channel per item/fluid type in the game
- 48 additional virtual signal channels (digits 0–9, letters A–Z, 9 colors)
- Three logic signals: Everything, Anything, Each
- **Value range**: Signed 32-bit integers (−2,147,483,648 to 2,147,483,647)
- Values wrap on overflow; multiple broadcasts of same item are additive

## Network Structure
- Each color wire forms separate networks
- Devices connected via same-color wires share all channel information
- Red and green wires touching same pole remain separate networks
- Wire length limited by previous connection point

## Device Connection Rules
- Receiving devices sum signals from all connected wires (red + green combined)
- Multiple same-color wires share and sum their signals
- Each device has a circuit network panel icon for configuration

## Combinators

### Constant Combinator
- Broadcasts up to 20 values on any channels
- Cannot distinguish red vs. green output directly

### Arithmetic Combinator
- Performs math operations on input values
- Input and output sides are separate networks (prevents automatic feedback)
- Supports: addition, subtraction, multiplication, division, modulo, power, bit shifts, bitwise AND/OR/XOR
- Can use "Each" signal to process all non-zero channels individually

### Decider Combinator
- Compares values and outputs conditionally
- Handles "Everything," "Anything," and "Each" signals
- Outputs either input value or 1 (configurable)

### Selector Combinator
- Filters specific signals
- Functions: find largest/smallest signal, count inputs, output random signal, detect stack size, filter quality grades (Space Age)

## Virtual Signals
- 177 available signals (241 with Space Age expansion)
- Categories: numbers, letters, arrows, enemy variants, environmental features, planets
- Cannot be broadcast over network but apply special logic

### Logic Signals

**Everything**: Condition true when satisfied for ALL signals; true if no signals present

**Anything**: Condition true when satisfied for AT LEAST ONE signal; false if no signals present

**Each**: Processes each input signal individually; output sums all individual results

## Connected Devices Overview

### Storage/Inventory
- Chests, storage tanks, cargo landing pads: output contents
- Train stops: output stopped train contents (fluids rounded down except <1 shows as 1)
- Roboport: output logistic network contents and bot statistics

### Production
- Crafting machines, furnaces, refineries, chemical plants, centrifuges: output contents + recipe ingredients + completion signal
- Mining drills: output expected resources
- Pumpjacks: output oil mining rate

### Transport
- Transport belts: pulse mode (1 tick) or hold mode (continuous)
- Inserters: output held items; can override stack size via signal
- Splitters: set priority input/output; filter by signal

### Power/Control
- Accumulators: output charge percentage
- Power switches: connect networks conditionally
- Lamps: enable on condition; color modes (mapping, RGB components, packed hex)
- Programmable speaker: alert/sound based on signals
- Display panel: show label/message on condition

### Transportation
- Rail signals: output state (red/yellow/green)
- Rail chain signals: output state (red/yellow/green/blue)
- Gates: output player detection signal

### Military
- Turrets: output ammunition count; enable/set priorities on condition
- Artillery turrets: output ammunition; enable on condition

### Specialized (Space Age)
- Space platform hub: output contents, destination, source planet, speed, damage taken
- Asteroid collector: output contents; enable/filter on signal
- Agricultural tower: output seeds/plants; enable on condition
- Electromagnetic plant, biochamber, cryogenic plant: similar to crafting machines
- Heating tower: output fuel and temperature
- Recycler: similar to furnaces

## Connection Interface
- Click entity, then power pole base to connect wire
- Place same color wire over existing connection to erase
- Shift-click pole: removes all electrical connections (first); removes wires (second)
- Hover to highlight connected wires
- Cut-paste preserves external wire connections
- Deconstruct pole keeps wire connections intact

## Interaction with Logistic Network
- Devices can have conditions for both circuit AND logistic networks (logical AND)
- Logistic network is wireless, based on roboport coverage
- Affected devices: inserters, transport belts, pumps, mining drills, turrets, train stops, lamps, etc.
