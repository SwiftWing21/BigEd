# Home Assistant Helper Module — BigEd CC

> **Goal:** Non-technical users get a guided, agent-powered smart home setup experience. The fleet researches, recommends, configures, and evolves automations — users just approve.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  LAUNCHER MODULE: mod_ha_helper.py                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐              │
│  │ Discovery   │ │ Recommender│ │ Builder    │              │
│  │ Panel      │ │ Panel      │ │ Panel      │              │
│  │            │ │            │ │            │              │
│  │ • Entities │ │ • Suggested│ │ • Active   │              │
│  │ • Devices  │ │   Automats │ │   Task List│              │
│  │ • BT Scan  │ │ • Templates│ │ • Agent    │              │
│  │ • Network  │ │ • Community│ │   Progress │              │
│  └────────────┘ └────────────┘ └────────────┘              │
│         │              │              │                      │
│         ▼              ▼              ▼                      │
│  ┌─────────────────────────────────────────┐                │
│  │        FLEET TASK QUEUE                  │                │
│  │  Agents pick up HA helper work items     │                │
│  │  Swarm evolves HA skills over time       │                │
│  └─────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│  FLEET SKILLS (new + enhanced)                               │
│                                                              │
│  ha_discover    — scan HA instance for entities/devices      │
│  ha_recommend   — suggest automations based on entity mix    │
│  ha_automate    — build YAML automations from templates      │
│  ha_validate    — test automation before deploying           │
│  ha_community   — research community blueprints/templates    │
│  ha_matter      — Matter/Thread device management            │
│  ha_bt_gateway  — local Bluetooth LE scanning + proxy        │
│  ha_teach       — learn from user corrections/preferences    │
│                                                              │
│  EXISTING SKILLS (enhanced for HA)                           │
│  home_assistant — already exists (REST API integration)      │
│  mqtt_inspect   — already exists (broker scanning)           │
│  unifi_manage   — already exists (network device discovery)  │
└──────────────────────────────────────────────────────────────┘
```

---

## Module: mod_ha_helper.py

### Tab Layout (3 panels)

#### Panel 1: Discovery
- **Entity Scanner**: Connect to HA instance, list all entities with state
- **Device Inventory**: Group entities by device/area/integration
- **Network Scan**: Use UniFi + mDNS to find undiscovered smart devices
- **Bluetooth Gateway**: Scan local BT LE for nearby devices (ESPHome, Shelly, sensors)
- **Matter Devices**: Detect Thread/Matter devices via HA commissioner

#### Panel 2: Recommendations
- **Smart Suggestions**: Based on discovered entities, suggest automations
  - "You have 3 motion sensors + 5 lights → Auto-lights when motion detected"
  - "You have a thermostat + door sensors → Turn off HVAC when doors open"
  - "You have a media player + smart lights → Movie mode automation"
- **Community Blueprints**: Search HA blueprint exchange for matching configs
- **Seasonal**: Time-of-year suggestions (holiday lighting, heating schedules)
- **Energy**: Optimize energy usage based on smart plugs/meters

#### Panel 3: Builder (Agent Task List)
- **Active Tasks**: Shows what agents are working on for HA
- **Pending Approvals**: Automations built by agents waiting for user OK
- **Deployed**: Automations successfully pushed to HA
- **Evolution Log**: How agents improved HA skills over time

### User Flow (minimal clicks)
```
1. User enters HA URL + token (once, saved to ~/.secrets)
2. Module auto-discovers all entities (background task)
3. Recommendations appear based on entity inventory
4. User clicks "Build This" on a recommendation
5. Agent picks up task, builds YAML, validates, shows preview
6. User clicks "Deploy" → automation pushed to HA
7. Agent monitors automation health, suggests improvements
```

---

## Fleet Skills

### ha_discover.py (NEW)
```
Actions:
  scan_entities    — GET /api/states, return categorized entity list
  scan_devices     — GET /api/config/device_registry/list
  scan_areas       — GET /api/config/area_registry/list
  scan_integrations — GET /api/config/entries
  network_scan     — mDNS + UPnP discovery for unregistered devices
  bt_scan          — Bluetooth LE scan for nearby IoT devices
```

### ha_recommend.py (NEW)
```
Actions:
  suggest          — analyze entity inventory, generate automation suggestions
  community_search — search HA blueprint exchange for matching templates
  energy_optimize  — analyze energy entities, suggest optimization automations
  seasonal         — time-based seasonal automation suggestions
```

### ha_automate.py (NEW)
```
Actions:
  build            — generate HA YAML automation from description/template
  validate         — test automation against HA config (dry run)
  deploy           — push automation to HA via REST API
  rollback         — remove a deployed automation
  monitor          — check if deployed automations are firing correctly
```

### ha_matter.py (NEW)
```
Actions:
  scan_thread      — discover Thread border routers and devices
  commission       — start Matter commissioning flow
  status           — check Matter device connectivity
  migrate          — help migrate from WiFi/Zigbee to Matter/Thread
```

### ha_bt_gateway.py (NEW)
```
Actions:
  scan             — BLE scan using local Bluetooth adapter
  identify         — match BLE advertisements to known device types
  proxy            — act as BLE-to-MQTT bridge for unsupported devices
  monitor          — continuous BLE presence tracking (room-level)

Implementation:
  - Uses `bleak` library (cross-platform BLE)
  - pip install bleak
  - Scans for BLE advertisements, matches against known OUI database
  - Can proxy BLE data to MQTT for HA consumption
  - Identifies: ESPHome, Shelly, Xiaomi, Switchbot, iBeacon, Tile, AirTag
```

### ha_context.py (NEW)
```
Actions:
  gather           — collect all environmental context (time, weather, presence, energy)
  build_profile    — analyze motion/presence history → build activity_profile.json
  detect_patterns  — find recurring patterns (wake, leave, return, sleep, room usage)
  seasonal_adjust  — detect seasonal shifts in patterns and suggest automation updates
  predict          — predict next likely activity based on current time + day + patterns
```

### ha_teach.py (NEW)
```
Actions:
  learn            — record user correction/preference for future suggestions
  profile          — build user preference profile (conservative/aggressive, rooms, schedules)
  improve          — re-analyze past suggestions and outcomes to improve
```

---

## Bluetooth Gateway Detail

### Architecture
```
BigEd CC (Windows/Linux/macOS)
    │
    ├── bleak library (BLE scanning)
    │   └── Scans for BLE advertisements
    │       ├── ESPHome devices (service UUID matching)
    │       ├── Shelly devices (manufacturer data)
    │       ├── Xiaomi sensors (MiBeacon protocol)
    │       ├── Switchbot (service UUID)
    │       └── Generic BLE (iBeacon, Eddystone)
    │
    ├── MQTT Bridge (optional)
    │   └── Publishes BLE data to HA via MQTT
    │       └── homeassistant/sensor/ble_{mac}/state
    │
    └── HA REST API
        └── Creates sensor entities for discovered BLE devices
```

### Prerequisites
```
pip install bleak          # BLE scanning (cross-platform)
pip install paho-mqtt      # already installed (MQTT bridge)
```

### BLE → HA Flow
```
1. bleak scans nearby BLE devices (10s window)
2. Match advertisements against known device signatures
3. For known devices: parse sensor data (temp, humidity, battery, etc.)
4. Publish to MQTT: homeassistant/sensor/ble_{mac_clean}/config (auto-discovery)
5. HA picks up MQTT auto-discovery → sensor entities appear
6. User sees new devices in Discovery panel
```

---

## Community Knowledge Pipeline

```
Research Agent
    │
    ├── Search HA Community Forums
    │   └── "best automations for [entity_type]"
    │
    ├── Search HA Blueprint Exchange
    │   └── https://community.home-assistant.io/c/blueprints-exchange/
    │
    ├── Search GitHub
    │   └── HA automation YAML repositories
    │
    └── Synthesize
        └── Build knowledge base of automation templates
            └── fleet/knowledge/ha_templates/*.yaml
```

### Template Structure
```yaml
# knowledge/ha_templates/motion_lights.yaml
name: "Motion-Activated Lights"
description: "Turn on lights when motion detected, off after 5 minutes"
required_entities:
  - domain: binary_sensor
    device_class: motion
  - domain: light
difficulty: beginner
tags: [lighting, motion, energy-saving]
blueprint_url: "https://community.home-assistant.io/..."
automation:
  alias: "Motion Lights - {{ area }}"
  trigger:
    - platform: state
      entity_id: "{{ motion_sensor }}"
      to: "on"
  action:
    - service: light.turn_on
      target:
        entity_id: "{{ light_entity }}"
    - delay: "00:05:00"
    - service: light.turn_off
      target:
        entity_id: "{{ light_entity }}"
```

---

## Context-Aware Automation Engine

### Environmental Context (auto-detected)

Every automation recommendation and blueprint generation considers:

```
┌─────────────────────────────────────────────────┐
│  CONTEXT LAYER (gathered automatically)          │
│                                                  │
│  TIME                                            │
│  ├── Local timezone (from HA or system)          │
│  ├── Day/night cycle (sunrise/sunset for lat/lon)│
│  ├── Day of week (weekday vs weekend patterns)   │
│  ├── Season (spring/summer/fall/winter)           │
│  ├── Time of year (holidays, DST transitions)     │
│  └── Work hours vs leisure (learned from motion)  │
│                                                  │
│  WEATHER                                         │
│  ├── Current temp + forecast (from HA weather)   │
│  ├── Humidity, wind, UV index                    │
│  ├── Heating/cooling degree days                 │
│  └── Severe weather alerts                       │
│                                                  │
│  PRESENCE + ACTIVITY                             │
│  ├── Motion sensor patterns (per room, per hour) │
│  ├── Door open/close frequency                   │
│  ├── Device tracker (home/away/zones)            │
│  ├── Media player state (active entertainment)   │
│  └── Sleep patterns (bedroom motion gaps)        │
│                                                  │
│  ENERGY                                          │
│  ├── Current consumption (smart plugs/meters)    │
│  ├── Solar production (if available)             │
│  ├── Utility rate periods (peak/off-peak)        │
│  └── Historical usage patterns                   │
└─────────────────────────────────────────────────┘
```

### Activity Learning (opt-in, motion-based)

When user enables activity logging, agents build a **life pattern model**:

```python
# knowledge/ha_context/activity_profile.json
{
  "timezone": "America/Los_Angeles",
  "location": {"lat": 36.6, "lon": -121.9},  # from HA config
  "patterns": {
    "weekday": {
      "wake": {"avg": "06:45", "range": ["06:15", "07:30"], "confidence": 0.85},
      "leave_home": {"avg": "08:15", "range": ["07:45", "09:00"], "confidence": 0.72},
      "return_home": {"avg": "17:30", "range": ["16:45", "18:30"], "confidence": 0.68},
      "wind_down": {"avg": "21:00", "range": ["20:30", "22:00"], "confidence": 0.78},
      "sleep": {"avg": "22:30", "range": ["21:45", "23:30"], "confidence": 0.82}
    },
    "weekend": {
      "wake": {"avg": "08:30", "confidence": 0.65},
      "active_hours": ["09:00-12:00", "14:00-18:00"],
      "entertainment": {"peak": "19:00-23:00", "confidence": 0.70}
    },
    "rooms": {
      "kitchen": {"peak_hours": ["07:00-08:30", "17:30-19:30"], "daily_visits": 8},
      "living_room": {"peak_hours": ["18:00-22:00"], "daily_visits": 12},
      "bedroom": {"quiet_hours": ["22:30-06:45"], "daily_visits": 4},
      "office": {"peak_hours": ["09:00-17:00"], "weekday_only": true}
    }
  },
  "seasonal_shifts": {
    "summer": {"wake_earlier": true, "outdoor_time": "18:00-21:00"},
    "winter": {"wake_later": true, "heating_start": "06:00"}
  },
  "learning_window_days": 14,  # rolling window for pattern detection
  "last_updated": "2026-03-19T12:00:00"
}
```

### Context-Aware Recommendation Examples

| Context | Detected Pattern | Suggested Automation |
|---------|-----------------|---------------------|
| **Morning routine** | Motion in kitchen 06:45-07:30 weekdays | "Turn on kitchen lights at 06:40, start coffee maker, play morning news" |
| **Leaving home** | No motion + door close at ~08:15 | "Lock doors, arm security, set thermostat to away mode, turn off lights" |
| **Sunset shift** | Sunset time changes seasonally | "Adjust outdoor lights trigger from 17:00 (winter) to 20:30 (summer)" |
| **Cold snap** | Weather forecast < 35°F tonight | "Pre-heat house before wake time, close garage door if open" |
| **Weekend mode** | Later wake, more living room activity | "Delay morning automations 2hrs on weekends, extend evening entertainment lighting" |
| **Seasonal** | December detected | "Suggest holiday lighting schedule, fireplace automation, guest mode" |
| **Energy peak** | Utility peak hours 14:00-19:00 | "Shift EV charging to off-peak, pre-cool house before peak" |
| **Sleep detected** | Bedroom motion stops, no activity 15min | "Dim all lights, lock doors, arm night security, set thermostat to sleep" |
| **Rain detected** | Weather: rain + windows open (if sensor) | "Alert: close windows, adjust irrigation schedule" |
| **Guest mode** | Unusual activity patterns (more rooms active) | "Suggest guest lighting preset, extend HVAC zones" |

### Ebb & Flow Adaptation

The system continuously adapts as life schedules shift:

```
Week 1-2: Baseline learning (minimum data needed)
Week 3+:  Confident patterns emerge, recommendations get specific
Monthly:  Seasonal adjustments detected
Ongoing:  Detects schedule changes automatically:
          - New job (leave time shifted)
          - Vacation (extended away)
          - Work from home days (no leave, office active)
          - New baby (night activity pattern change)
          - Roommate moved in (second activity cluster)
```

### Privacy Design

- **All processing local** — activity data never leaves the machine
- **Opt-in only** — activity logging requires explicit user toggle
- **Anonymized patterns** — stores time ranges, not raw motion events
- **Rolling window** — old data expires after configurable period (default 14 days)
- **GDPR erasure** — `lead_client.py gdpr-erase` includes activity profile
- **No cloud dependency** — works entirely with local HA + Ollama

---

## Agent Work Integration

### How Agents "Apply Work" to HA Helper

The module creates tasks in the fleet queue. Agents pick them up naturally:

```python
# User clicks "Build This" on a recommendation
db.post_task("ha_automate", json.dumps({
    "action": "build",
    "template": "motion_lights",
    "entities": {"motion_sensor": "binary_sensor.hallway_motion", "light_entity": "light.hallway"},
    "area": "Hallway",
}), priority=6, assigned_to="implementation")

# Swarm evolution: agents improve HA skills
db.post_task("evolution_coordinator", json.dumps({
    "action": "evolve",
    "skill": "ha_recommend",
}), priority=2)
```

### Idle Evolution for HA
When idle, agents can:
- Research new automation patterns
- Test existing automations for reliability
- Update community template knowledge base
- Improve recommendation accuracy from user feedback

---

## Implementation Phases

### Phase 1: Core Discovery (0.26.00)
- `ha_discover.py` skill (entity/device/area scanning)
- `mod_ha_helper.py` launcher module (Discovery panel only)
- Enhanced `home_assistant.py` with better error handling
- **Effort: 1 day**

### Phase 2: Recommendations (0.26.01)
- `ha_recommend.py` skill (suggestion engine)
- `ha_community.py` skill (blueprint search)
- Recommendations panel in module
- Template knowledge base structure
- **Effort: 2 days**

### Phase 3: Builder (0.26.02)
- `ha_automate.py` skill (YAML generation + validation + deploy)
- Builder panel with approval flow
- Agent task integration
- **Effort: 2 days**

### Phase 4: Matter + Bluetooth (0.26.03)
- `ha_matter.py` skill (Thread/Matter scanning)
- `ha_bt_gateway.py` skill (BLE scanning + MQTT bridge)
- `pip install bleak` dependency
- BLE → MQTT auto-discovery flow
- **Effort: 3 days**

### Phase 5: Learning (0.26.04)
- `ha_teach.py` skill (user preference learning)
- Feedback loop from deployed automations
- Swarm evolution of HA skills
- **Effort: 2 days**

---

## Dependencies

| Package | Purpose | Install |
|---------|---------|---------|
| `bleak` | BLE scanning (cross-platform) | `pip install bleak` |
| `paho-mqtt` | MQTT bridge (already installed) | — |
| `httpx` | HA REST API (already installed) | — |
| `pyyaml` | YAML generation for automations | `pip install pyyaml` |

---

## User Experience Goals

| Goal | Metric |
|------|--------|
| First automation deployed | < 5 minutes from module open |
| Clicks to deploy recommendation | 2 (Build This → Deploy) |
| Discovery time | < 30 seconds for full entity scan |
| BLE scan time | < 15 seconds |
| Recommendation relevance | > 80% useful (measured by deploy rate) |
| Zero YAML knowledge required | User never sees raw YAML |
