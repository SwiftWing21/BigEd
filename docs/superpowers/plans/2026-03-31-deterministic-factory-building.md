# Deterministic Factory Building — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure Factorio RL agents from freeform PLACE actions to blueprint-first factory building with deterministic tools, per FLE/factorioctl research findings.

**Architecture:** Agents select WHICH blueprint to place and WHERE (zone), then deterministic Lua tools handle entity placement, grid snapping, and collision resolution. A zone system reserves rectangular areas for mining/smelting/assembly. The litmus test is an automated iron plate production chain (4 drills → belts → 4 furnaces → chest) with verified throughput.

**Tech Stack:** Python 3.14, Lua (Factorio mod), PyTorch, pytest

**Research reference:** `C:\Users\max\Downloads\factorio-agent-research.md`

---

## File Map

| File | Changes | Reason |
|---|---|---|
| `fleet/factorio/bridge.py:785` | Modify | Fix `biged-blueprint` → `biged_blueprint` (RCON call name mismatch) |
| `fleet/factorio/server_data/mods/biged-bridge/control.lua:1019-1021` | Modify | Add creative-mode auto-insert to blueprint handler |
| `fleet/factorio/zone_manager.py` | Create | Zone system — rectangular area reservations |
| `fleet/factorio/layout_templates.py` | Create | Pre-computed coordinate layouts for common factory patterns |
| `fleet/factorio/belt_router.py` | Create | A* belt routing between two points |
| `fleet/factorio/packs/blueprints/iron_smelter_line.json` | Create | Litmus test blueprint: 4 drills → belts → 4 furnaces → chest |
| `fleet/factorio/curricula/phase1_bootstrap.toml` | Modify | Blueprint-first curriculum |
| `tests/factorio/test_zone_manager.py` | Create | Zone system tests |
| `tests/factorio/test_belt_router.py` | Create | Belt routing tests |
| `tests/factorio/test_layout_templates.py` | Create | Layout template tests |
| `tests/factorio/test_blueprint_placement.py` | Create | Blueprint RCON integration tests |

---

## TIER 0: Fix What's Broken (blueprint placement doesn't work at all)

### Task 1: Fix blueprint RCON call name mismatch

**Files:**
- Modify: `fleet/factorio/bridge.py:785`

The bridge calls `remote_call("biged-blueprint", ...)` but the Lua mod registers the function as `biged_blueprint` (underscore). Every blueprint stamp placement silently fails because Lua can't find the function.

- [ ] **Step 1: Fix the function name**

In `fleet/factorio/bridge.py:785`, change:
```python
# Before:
resp = await self.rcon.remote_call("biged-blueprint", stamp_cmd)

# After:
resp = await self.rcon.remote_call("biged_blueprint", stamp_cmd)
```

- [ ] **Step 2: Also fix the response parsing**

The current code (line 790) treats any non-empty response as success:
```python
stamp_result = {"success": True} if resp else {"success": False}
```

Replace with proper JSON parsing:
```python
try:
    stamp_result = json.loads(resp) if resp else {"success": False}
except (json.JSONDecodeError, TypeError):
    stamp_result = {"success": False, "error": str(resp)[:200]}
```

- [ ] **Step 3: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "fix(factorio): blueprint RCON call used wrong name (hyphen vs underscore) — every stamp was failing"
```

---

### Task 2: Add creative-mode auto-insert to blueprint handler

**Files:**
- Modify: `fleet/factorio/server_data/mods/biged-bridge/control.lua:1019-1021`

The `fn_biged_blueprint` function checks inventory and fails with `"no item"` if the agent doesn't have the entity. In creative/sandbox mode, it should auto-insert like `exec_cmd place` does.

- [ ] **Step 1: Add auto-insert to the per-entity placement loop**

In `control.lua`, inside `fn_biged_blueprint`, find the inventory check block (around line 1019):
```lua
local item_count = inv.get_item_count(ent_name)
if item_count < 1 then
    table.insert(failed, {name = ent_name, reason = "no item"})
```

Replace with:
```lua
local item_count = inv.get_item_count(ent_name)
if item_count < 1 then
    -- Creative mode: auto-insert missing items
    local ok_ins = pcall(function() inv.insert{name = ent_name, count = 50} end)
    if ok_ins then
        item_count = inv.get_item_count(ent_name)
    end
end
if item_count < 1 then
    table.insert(failed, {name = ent_name, reason = "no item"})
```

- [ ] **Step 2: Deploy the updated Lua mod**

```bash
cp fleet/factorio/server_data/mods/biged-bridge/control.lua \
   "$APPDATA/Factorio/mods/biged-bridge/control.lua"
```

- [ ] **Step 3: Commit**

```bash
git add -f fleet/factorio/server_data/mods/biged-bridge/control.lua
git commit -m "fix(factorio): blueprint handler auto-inserts items in creative mode"
```

---

### Task 3: Test blueprint placement end-to-end

**Files:**
- Create: `tests/factorio/test_blueprint_placement.py`

This is not a unit test — it's a validation that the blueprint system works. Run it manually when the server is up.

- [ ] **Step 1: Write a manual integration test**

```python
"""Manual integration test — requires running Factorio server + bridge."""
import json
import urllib.request

BRIDGE = "http://localhost:27016"


def test_stamp_basic_power():
    """Place the basic_power_station blueprint via bridge API."""
    # Load the blueprint from packs
    import pathlib
    bp_path = pathlib.Path(__file__).parent.parent.parent / "fleet" / "factorio" / "packs" / "blueprints" / "basic_power_station.json"
    bp_data = json.loads(bp_path.read_text())

    # Submit as an action via the command API
    cmd = {
        "actions": [{
            "action": "stamp",
            "blueprint": bp_data["blueprint_string"],
            "position": {"x": 20, "y": 20},
            "agent_id": 1,
        }]
    }
    req = urllib.request.Request(
        f"{BRIDGE}/api/command",
        data=json.dumps(cmd).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())
    print("Stamp result:", result)
    assert result.get("queued") or result.get("success"), f"Stamp failed: {result}"
```

- [ ] **Step 2: Commit**

```bash
git add tests/factorio/test_blueprint_placement.py
git commit -m "test(factorio): add blueprint placement integration test"
```

---

## TIER 1: Zone System

### Task 4: Create zone manager

**Files:**
- Create: `fleet/factorio/zone_manager.py`
- Test: `tests/factorio/test_zone_manager.py`

Zones are rectangular areas reserved for a purpose (mining, smelting, assembly, logistics, power). Each zone has a name, bounding box, and type. Zones cannot overlap. The agent selects a zone type and the system allocates the next available area.

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_zone_manager.py
from factorio.zone_manager import ZoneManager, Zone, ZoneType


def test_create_zone():
    zm = ZoneManager()
    zone = zm.create_zone("smelter_1", ZoneType.SMELTING, x=0, y=0, width=20, height=10)
    assert zone.name == "smelter_1"
    assert zone.bounds == (0, 0, 20, 10)


def test_zones_cannot_overlap():
    zm = ZoneManager()
    zm.create_zone("a", ZoneType.SMELTING, x=0, y=0, width=10, height=10)
    result = zm.create_zone("b", ZoneType.ASSEMBLY, x=5, y=5, width=10, height=10)
    assert result is None  # overlap rejected


def test_allocate_next_zone():
    zm = ZoneManager()
    zone = zm.allocate(ZoneType.SMELTING, width=20, height=10)
    assert zone is not None
    assert zone.zone_type == ZoneType.SMELTING
    zone2 = zm.allocate(ZoneType.ASSEMBLY, width=20, height=10)
    assert zone2 is not None
    assert not zm.overlaps(zone, zone2)


def test_get_zone_center():
    zm = ZoneManager()
    zone = zm.create_zone("test", ZoneType.MINING, x=10, y=20, width=8, height=6)
    cx, cy = zone.center
    assert cx == 14  # 10 + 8/2
    assert cy == 23  # 20 + 6/2


def test_is_position_in_zone():
    zm = ZoneManager()
    zone = zm.create_zone("test", ZoneType.SMELTING, x=0, y=0, width=10, height=10)
    assert zone.contains(5, 5)
    assert not zone.contains(15, 5)


def test_serialize_deserialize():
    zm = ZoneManager()
    zm.create_zone("a", ZoneType.SMELTING, x=0, y=0, width=20, height=10)
    zm.create_zone("b", ZoneType.POWER, x=25, y=0, width=10, height=10)
    data = zm.to_dict()
    zm2 = ZoneManager.from_dict(data)
    assert len(zm2.zones) == 2
    assert zm2.zones[0].name == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/factorio/test_zone_manager.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement ZoneManager**

```python
# fleet/factorio/zone_manager.py
"""Zone system — rectangular area reservations for factory organization."""
import logging
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)

# Layout constants — zones placed in a grid pattern radiating from spawn
_ZONE_GAP = 3          # tiles between zones
_ZONE_ORIGIN_X = -5    # starting x for auto-allocation
_ZONE_ORIGIN_Y = 15    # starting y (below spawn, away from infinity chests)
_ZONES_PER_ROW = 3     # zones before wrapping to next row


class ZoneType(Enum):
    MINING = "mining"
    SMELTING = "smelting"
    ASSEMBLY = "assembly"
    LOGISTICS = "logistics"
    POWER = "power"
    GENERAL = "general"


@dataclass
class Zone:
    name: str
    zone_type: ZoneType
    x: int
    y: int
    width: int
    height: int

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height

    def overlaps_rect(self, x: int, y: int, w: int, h: int) -> bool:
        return not (
            x >= self.x + self.width or x + w <= self.x
            or y >= self.y + self.height or y + h <= self.y
        )


class ZoneManager:
    def __init__(self) -> None:
        self.zones: list[Zone] = []
        self._next_col = 0
        self._next_row = 0

    def create_zone(self, name: str, zone_type: ZoneType,
                    x: int, y: int, width: int, height: int) -> Zone | None:
        for z in self.zones:
            if z.overlaps_rect(x, y, width, height):
                log.warning("Zone '%s' overlaps with '%s'", name, z.name)
                return None
        zone = Zone(name, zone_type, x, y, width, height)
        self.zones.append(zone)
        return zone

    def allocate(self, zone_type: ZoneType, width: int = 20,
                 height: int = 10) -> Zone | None:
        """Auto-allocate a non-overlapping zone in a grid pattern."""
        for attempt in range(50):
            x = _ZONE_ORIGIN_X + self._next_col * (width + _ZONE_GAP)
            y = _ZONE_ORIGIN_Y + self._next_row * (height + _ZONE_GAP)
            self._next_col += 1
            if self._next_col >= _ZONES_PER_ROW:
                self._next_col = 0
                self._next_row += 1
            name = f"{zone_type.value}_{len(self.zones)}"
            zone = self.create_zone(name, zone_type, x, y, width, height)
            if zone is not None:
                return zone
        return None

    def overlaps(self, a: Zone, b: Zone) -> bool:
        return a.overlaps_rect(b.x, b.y, b.width, b.height)

    def find_zone(self, name: str) -> Zone | None:
        for z in self.zones:
            if z.name == name:
                return z
        return None

    def zones_by_type(self, zone_type: ZoneType) -> list[Zone]:
        return [z for z in self.zones if z.zone_type == zone_type]

    def to_dict(self) -> dict:
        return {
            "zones": [
                {"name": z.name, "type": z.zone_type.value,
                 "x": z.x, "y": z.y, "width": z.width, "height": z.height}
                for z in self.zones
            ]
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneManager":
        zm = cls()
        for zd in data.get("zones", []):
            zm.create_zone(
                zd["name"], ZoneType(zd["type"]),
                zd["x"], zd["y"], zd["width"], zd["height"],
            )
        return zm
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/factorio/test_zone_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/zone_manager.py tests/factorio/test_zone_manager.py
git commit -m "feat(factorio): zone system — rectangular area reservations for factory layout"
```

---

## TIER 2: Layout Templates + Belt Router

### Task 5: Create layout templates

**Files:**
- Create: `fleet/factorio/layout_templates.py`
- Test: `tests/factorio/test_layout_templates.py`

Pre-computed coordinate layouts for common factory patterns. The agent NEVER invents placements — it picks a template and a zone, and the template provides exact entity coordinates.

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_layout_templates.py
from factorio.layout_templates import get_template, list_templates, LayoutTemplate


def test_list_templates():
    templates = list_templates()
    assert "iron_smelter_4x" in templates
    assert "basic_power" in templates


def test_iron_smelter_template():
    t = get_template("iron_smelter_4x")
    assert t is not None
    assert len(t.entities) > 0
    # Should have 4 furnaces, inserters, belts
    furnaces = [e for e in t.entities if e["name"] == "stone-furnace"]
    assert len(furnaces) == 4


def test_template_at_offset():
    t = get_template("iron_smelter_4x")
    placed = t.at_position(10, 20)
    # All entity positions should be offset
    for e in placed:
        assert e["position"]["x"] >= 10
        assert e["position"]["y"] >= 20


def test_template_footprint():
    t = get_template("iron_smelter_4x")
    w, h = t.footprint
    assert w > 0
    assert h > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/factorio/test_layout_templates.py -v`

- [ ] **Step 3: Implement layout templates**

```python
# fleet/factorio/layout_templates.py
"""Pre-computed factory layout templates — exact entity coordinates.

Each template is a list of entities with relative positions. Call
at_position(x, y) to get absolute coordinates for placement.
The agent picks a template + zone; the system handles placement.
"""
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class LayoutTemplate:
    name: str
    description: str
    entities: list[dict]  # [{name, position: {x, y}, direction}]
    footprint: tuple[int, int] = (0, 0)  # (width, height)

    def at_position(self, ox: int, oy: int) -> list[dict]:
        """Return entity list with positions offset by (ox, oy)."""
        result = []
        for e in self.entities:
            entry = dict(e)
            entry["position"] = {
                "x": e["position"]["x"] + ox,
                "y": e["position"]["y"] + oy,
            }
            result.append(entry)
        return result


# ── Iron smelter line: 4 furnaces with input/output belts + inserters ────
# Layout (top-down):
#   belt(in) → inserter → furnace → inserter → belt(out)
#   Repeated 4 times vertically, 2 tiles apart
_IRON_SMELTER_4X = LayoutTemplate(
    name="iron_smelter_4x",
    description="4 stone furnaces with input/output belts and inserters",
    footprint=(5, 8),
    entities=[
        # Factorio 16-dir: 0=north, 4=east, 8=south, 12=west
        # Layout: belt(in) → inserter → furnace(2x2) → inserter → belt(out)
        # Inserter reach is 1 tile, so inserter must be adjacent to both belt and furnace
        # Input belt column (x=0, flowing south)
        *[{"name": "transport-belt", "position": {"x": 0, "y": i * 2}, "direction": 8}
          for i in range(4)],
        # Input inserters (x=1, facing east — picks from belt at x=0, drops into furnace at x=2)
        *[{"name": "burner-inserter", "position": {"x": 1, "y": i * 2}, "direction": 4}
          for i in range(4)],
        # Furnaces (x=2, 2x2 entities — adjacent to inserter at x=1)
        *[{"name": "stone-furnace", "position": {"x": 2, "y": i * 2}, "direction": 0}
          for i in range(4)],
        # Output inserters (x=3, facing east — picks from furnace at x=2, drops to belt at x=4)
        *[{"name": "burner-inserter", "position": {"x": 3, "y": i * 2}, "direction": 4}
          for i in range(4)],
        # Output belt column (x=4, flowing south)
        *[{"name": "transport-belt", "position": {"x": 4, "y": i * 2}, "direction": 8}
          for i in range(4)],
    ],
)

# ── Basic power: offshore pump → boiler → steam engine ───────────────────
_BASIC_POWER = LayoutTemplate(
    name="basic_power",
    description="Offshore pump + boiler + steam engine (needs water tile at pump)",
    footprint=(7, 3),
    entities=[
        # Factorio 16-dir: 0=north, 4=east, 8=south, 12=west
        {"name": "offshore-pump", "position": {"x": 0, "y": 1}, "direction": 4},  # east
        {"name": "boiler", "position": {"x": 2, "y": 1}, "direction": 4},          # east
        {"name": "steam-engine", "position": {"x": 5, "y": 1}, "direction": 4},    # east
        {"name": "small-electric-pole", "position": {"x": 6, "y": 0}, "direction": 0},
    ],
)

# ── 4 mining drills on ore patch ─────────────────────────────────────────
_DRILL_ARRAY_4X = LayoutTemplate(
    name="drill_array_4x",
    description="4 electric mining drills in a row (place on ore patch)",
    footprint=(12, 3),
    entities=[
        # Direction 8 = south (output drops below the drill)
        *[{"name": "electric-mining-drill", "position": {"x": i * 3, "y": 0}, "direction": 8}
          for i in range(4)],
    ],
)

# ── Output chest at end of belt ──────────────────────────────────────────
_OUTPUT_CHEST = LayoutTemplate(
    name="output_chest",
    description="Iron chest at belt terminus with inserter feeding into it",
    footprint=(2, 1),
    entities=[
        # Inserter facing east: picks from belt (left), drops into chest (right)
        {"name": "inserter", "position": {"x": 0, "y": 0}, "direction": 4},  # east
        {"name": "iron-chest", "position": {"x": 1, "y": 0}, "direction": 0},
    ],
)

# ── Belt segment (straight, N tiles) ────────────────────────────────────
def belt_segment(length: int, direction: int = 4) -> LayoutTemplate:
    """Generate a straight belt segment of N tiles."""
    if direction in (0, 4):  # north/south — vertical
        entities = [
            {"name": "transport-belt", "position": {"x": 0, "y": i}, "direction": direction}
            for i in range(length)
        ]
        footprint = (1, length)
    else:  # east/west — horizontal
        entities = [
            {"name": "transport-belt", "position": {"x": i, "y": 0}, "direction": direction}
            for i in range(length)
        ]
        footprint = (length, 1)
    return LayoutTemplate(
        name=f"belt_{length}_{direction}",
        description=f"{length}-tile belt segment",
        footprint=footprint,
        entities=entities,
    )


# ── Registry ────────────────────────────────────────────────────────────

_TEMPLATES: dict[str, LayoutTemplate] = {
    "iron_smelter_4x": _IRON_SMELTER_4X,
    "basic_power": _BASIC_POWER,
    "drill_array_4x": _DRILL_ARRAY_4X,
    "output_chest": _OUTPUT_CHEST,
}


def get_template(name: str) -> LayoutTemplate | None:
    return _TEMPLATES.get(name)


def list_templates() -> list[str]:
    return list(_TEMPLATES.keys())
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/factorio/test_layout_templates.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/layout_templates.py tests/factorio/test_layout_templates.py
git commit -m "feat(factorio): layout templates — pre-computed entity coordinates for factory patterns"
```

---

### Task 6: Create A* belt router

**Files:**
- Create: `fleet/factorio/belt_router.py`
- Test: `tests/factorio/test_belt_router.py`

Deterministic belt routing between two points. Uses A* pathfinding on a 2D grid, avoiding occupied tiles. Returns a list of belt entity placements with correct directions.

- [ ] **Step 1: Write failing tests**

```python
# tests/factorio/test_belt_router.py
from factorio.belt_router import route_belt, BeltRoute


def test_straight_horizontal():
    route = route_belt(start=(0, 0), end=(5, 0), obstacles=set())
    assert route is not None
    assert len(route.belts) == 6  # inclusive of start and end
    assert all(b["name"] == "transport-belt" for b in route.belts)


def test_straight_vertical():
    route = route_belt(start=(0, 0), end=(0, 5), obstacles=set())
    assert route is not None
    assert len(route.belts) == 6


def test_route_around_obstacle():
    obstacles = {(2, 0), (2, 1)}  # wall blocking straight path
    route = route_belt(start=(0, 0), end=(4, 0), obstacles=obstacles)
    assert route is not None
    # Route exists and avoids obstacles
    belt_positions = {(b["position"]["x"], b["position"]["y"]) for b in route.belts}
    assert belt_positions.isdisjoint(obstacles)


def test_no_route_possible():
    # Completely surrounded
    obstacles = {(1, 0), (-1, 0), (0, 1), (0, -1)}
    route = route_belt(start=(0, 0), end=(5, 5), obstacles=obstacles)
    assert route is None


def test_belt_directions_correct():
    """Belts should face the direction of travel (16-dir: 4=east)."""
    route = route_belt(start=(0, 0), end=(3, 0), obstacles=set())
    assert route is not None
    for b in route.belts:
        assert b["direction"] == 4  # east
```

- [ ] **Step 2: Implement belt router**

```python
# fleet/factorio/belt_router.py
"""A* belt routing between two points on the Factorio grid."""
import heapq
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Factorio 2.0 16-direction values for cardinal directions
DIR_NORTH = 0
DIR_EAST = 4
DIR_SOUTH = 8
DIR_WEST = 12

_NEIGHBORS = [(0, -1, DIR_NORTH), (1, 0, DIR_EAST),
              (0, 1, DIR_SOUTH), (-1, 0, DIR_WEST)]


@dataclass
class BeltRoute:
    belts: list[dict]  # [{name, position: {x, y}, direction}]
    length: int


def route_belt(
    start: tuple[int, int],
    end: tuple[int, int],
    obstacles: set[tuple[int, int]],
    belt_type: str = "transport-belt",
    max_search: int = 2000,
) -> BeltRoute | None:
    """Find shortest belt path from start to end avoiding obstacles.

    Returns BeltRoute with belt entities and directions, or None if no path.
    """
    sx, sy = int(start[0]), int(start[1])
    ex, ey = int(end[0]), int(end[1])

    # A* search
    open_set: list[tuple[float, int, int]] = []
    heapq.heappush(open_set, (0, sx, sy))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {(sx, sy): 0}
    visited = 0

    while open_set and visited < max_search:
        _, cx, cy = heapq.heappop(open_set)
        visited += 1

        if (cx, cy) == (ex, ey):
            # Reconstruct path
            path = [(ex, ey)]
            pos = (ex, ey)
            while pos in came_from:
                pos = came_from[pos]
                path.append(pos)
            path.reverse()
            return _path_to_belts(path, belt_type)

        for dx, dy, _ in _NEIGHBORS:
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in obstacles:
                continue
            new_g = g_score[(cx, cy)] + 1
            if new_g < g_score.get((nx, ny), float("inf")):
                g_score[(nx, ny)] = new_g
                f = new_g + abs(nx - ex) + abs(ny - ey)  # Manhattan heuristic
                heapq.heappush(open_set, (f, nx, ny))
                came_from[(nx, ny)] = (cx, cy)

    return None  # no path found


def _path_to_belts(path: list[tuple[int, int]], belt_type: str) -> BeltRoute:
    """Convert a coordinate path to belt entities with correct directions."""
    belts = []
    for i, (x, y) in enumerate(path):
        if i < len(path) - 1:
            nx, ny = path[i + 1]
            dx, dy = nx - x, ny - y
            direction = _delta_to_direction(dx, dy)
        else:
            # Last belt keeps previous direction
            direction = belts[-1]["direction"] if belts else DIR_SOUTH

        belts.append({
            "name": belt_type,
            "position": {"x": x, "y": y},
            "direction": direction,
        })
    return BeltRoute(belts=belts, length=len(belts))


def _delta_to_direction(dx: int, dy: int) -> int:
    if dx > 0:
        return DIR_EAST
    if dx < 0:
        return DIR_WEST
    if dy > 0:
        return DIR_SOUTH
    return DIR_NORTH
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/factorio/test_belt_router.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/factorio/belt_router.py tests/factorio/test_belt_router.py
git commit -m "feat(factorio): A* belt routing — deterministic pathfinding between points"
```

---

## TIER 3: Litmus Test — Iron Plate Production Chain

### Task 7: Create the litmus test blueprint + placement function

**Files:**
- Create: `fleet/factorio/factory_builder.py`
- Test: `tests/factorio/test_factory_builder.py`

The litmus test from the research doc: 4 drills on iron ore → transport belts → 4 stone furnaces → transport belts → iron chest. Verified by iron plates appearing at steady rate over 60 seconds with no manual intervention.

This task creates a `build_iron_line()` function that uses ZoneManager + LayoutTemplates + BeltRouter to place the complete chain via RCON commands.

- [ ] **Step 1: Write the test**

```python
# tests/factorio/test_factory_builder.py
from factorio.factory_builder import IronLinePlan
from factorio.zone_manager import ZoneManager, ZoneType


def test_iron_line_plan_generates_entities():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    entities = plan.compute()
    assert len(entities) > 0
    # Should have drills, furnaces, belts, inserters, chest
    names = {e["name"] for e in entities}
    assert "electric-mining-drill" in names or "burner-mining-drill" in names
    assert "stone-furnace" in names
    assert "transport-belt" in names
    assert "iron-chest" in names or "wooden-chest" in names


def test_iron_line_plan_allocates_zones():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    plan.compute()
    # Should have created mining and smelting zones
    assert len(zm.zones_by_type(ZoneType.MINING)) >= 1
    assert len(zm.zones_by_type(ZoneType.SMELTING)) >= 1


def test_iron_line_no_overlaps():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    entities = plan.compute()
    # No two entities at same position
    positions = [(e["position"]["x"], e["position"]["y"]) for e in entities]
    assert len(positions) == len(set(positions)), "Overlapping entity positions"
```

- [ ] **Step 2: Implement IronLinePlan**

```python
# fleet/factorio/factory_builder.py
"""Deterministic factory builder — produces entity placement lists.

Uses ZoneManager for spatial organization, LayoutTemplates for patterns,
and BeltRouter for connecting zones. The agent calls build functions;
all spatial decisions are pre-computed.
"""
import logging
from factorio.zone_manager import ZoneManager, ZoneType
from factorio.layout_templates import get_template, belt_segment
from factorio.belt_router import route_belt

log = logging.getLogger(__name__)


class IronLinePlan:
    """Plan for: 4 drills → belts → 4 furnaces → belts → chest.

    The litmus test for a working factory automation chain.
    """

    def __init__(self, zone_manager: ZoneManager,
                 ore_position: tuple[int, int] = (10, 10)):
        self.zm = zone_manager
        self.ore_x, self.ore_y = ore_position
        self._entities: list[dict] = []

    def compute(self) -> list[dict]:
        """Compute all entity placements. Returns list of entity dicts."""
        self._entities = []

        # 1. Allocate mining zone at ore location
        mining_zone = self.zm.create_zone(
            "iron_mining", ZoneType.MINING,
            x=self.ore_x, y=self.ore_y, width=14, height=5,
        )
        if not mining_zone:
            mining_zone = self.zm.allocate(ZoneType.MINING, width=14, height=5)

        # 2. Place 4 drills
        drill_template = get_template("drill_array_4x")
        if drill_template and mining_zone:
            drills = drill_template.at_position(mining_zone.x, mining_zone.y)
            self._entities.extend(drills)

        # 3. Allocate smelting zone below mining
        smelt_x = mining_zone.x if mining_zone else self.ore_x
        smelt_y = (mining_zone.y + mining_zone.height + 3) if mining_zone else self.ore_y + 8
        smelting_zone = self.zm.create_zone(
            "iron_smelting", ZoneType.SMELTING,
            x=smelt_x, y=smelt_y, width=8, height=10,
        )

        # 4. Place smelter line
        smelter_template = get_template("iron_smelter_4x")
        if smelter_template and smelting_zone:
            smelters = smelter_template.at_position(smelting_zone.x, smelting_zone.y)
            self._entities.extend(smelters)

        # 5. Route belts from drill output to smelter input
        if mining_zone and smelting_zone:
            belt_start = (mining_zone.x + 6, mining_zone.y + mining_zone.height)
            belt_end = (smelting_zone.x, smelting_zone.y)
            occupied = {(int(e["position"]["x"]), int(e["position"]["y"]))
                        for e in self._entities}
            belt_route = route_belt(belt_start, belt_end, occupied)
            if belt_route:
                self._entities.extend(belt_route.belts)

        # 6. Place output chest after smelter
        chest_template = get_template("output_chest")
        if chest_template and smelting_zone:
            chest_pos_x = smelting_zone.x + 6
            chest_pos_y = smelting_zone.y + smelting_zone.height
            chests = chest_template.at_position(chest_pos_x, chest_pos_y)
            self._entities.extend(chests)

        return self._entities

    def to_rcon_commands(self) -> list[dict]:
        """Convert to RCON exec_cmd place actions."""
        return [
            {
                "action": "place",
                "entity": e["name"],
                "position": e["position"],
                "direction": e.get("direction", 0),
                "agent_id": 1,
            }
            for e in self._entities
        ]
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/factorio/test_factory_builder.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add fleet/factorio/factory_builder.py tests/factorio/test_factory_builder.py
git commit -m "feat(factorio): IronLinePlan — deterministic litmus test factory layout"
```

---

### Task 8: Wire factory builder into bridge as a high-level action

**Files:**
- Modify: `fleet/factorio/bridge.py`
- Modify: `fleet/factorio/bridge_api.py`

Add a `/api/build/iron_line` endpoint that executes the full IronLinePlan via RCON. This lets us test the litmus test manually before wiring it into the RL agent.

- [ ] **Step 1: Add API endpoint**

In `fleet/factorio/bridge_api.py`, add a new route:

```python
@app.route("/api/build/iron_line", methods=["POST"])
def api_build_iron_line():
    """Build the litmus test iron plate production chain."""
    if _rcon_client is None:
        return jsonify({"error": "RCON not available"}), 503
    data = request.get_json(silent=True) or {}
    ore_x = data.get("ore_x", 10)
    ore_y = data.get("ore_y", 10)

    from factorio.zone_manager import ZoneManager
    from factorio.factory_builder import IronLinePlan

    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(ore_x, ore_y))
    entities = plan.compute()
    commands = plan.to_rcon_commands()

    # Execute via command queue
    cmd_id = f"iron_line_{len(commands)}"
    _command_queue.put({"id": cmd_id, "actions": commands})

    return jsonify({
        "queued": True,
        "command_id": cmd_id,
        "entity_count": len(entities),
        "zones": zm.to_dict(),
    })
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge_api.py
git commit -m "feat(factorio): /api/build/iron_line endpoint — one-click litmus test factory"
```

---

### Task 9: Update curriculum for blueprint-first building

**Files:**
- Modify: `fleet/factorio/curricula/phase1_bootstrap.toml`

Replace the freeform "place a furnace" curriculum with blueprint-stamp focused lessons.

- [ ] **Step 1: Update phase 1 curriculum**

```toml
[meta]
phase = 1
name = "Sandbox Building"
description = "Creative mode — learn to use blueprints and layout templates"

[[lessons]]
name = "Body check"
criteria = "player.alive >= 1"
hint = "You should already have a body."
max_attempts = 5

[[lessons]]
name = "Place any entity"
description = "Place any building from inventory"
criteria = "entities.stone-furnace >= 1 OR entities.transport-belt >= 1 OR entities.inserter >= 1"
hint = "Use place action with any entity."
max_attempts = 100

[[lessons]]
name = "Place a blueprint stamp"
description = "Successfully place a multi-entity blueprint"
criteria = "entities.stone-furnace >= 3 OR entities.transport-belt >= 5"
hint = "Use STAMP action to place a pre-built layout."
max_attempts = 200

[[lessons]]
name = "Build a production line"
description = "Place furnaces with belts and inserters"
criteria = "entities.stone-furnace >= 4 AND entities.transport-belt >= 4 AND entities.inserter >= 2"
hint = "Use the smelter_column blueprint or iron_smelter_4x template."
max_attempts = 300

[[lessons]]
name = "Scale the factory"
description = "20+ buildings placed"
criteria = "entities.transport-belt >= 10 AND entities.inserter >= 4 AND entities.stone-furnace >= 4"
hint = "Place more blueprints. Expand the factory."
max_attempts = 500
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/curricula/phase1_bootstrap.toml
git commit -m "feat(factorio): blueprint-first curriculum — STAMP actions over freeform PLACE"
```

---

### Task 10: Run full test suite + manual litmus test

**Files:** None (verification only)

- [ ] **Step 1: Run all Factorio tests**

Run: `python -m pytest tests/factorio/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Restart bridge and run litmus test**

```bash
# After bridge restart:
curl -X POST http://localhost:27016/api/build/iron_line \
  -H "Content-Type: application/json" \
  -d '{"ore_x": 10, "ore_y": 10}'
```

Expected: entities placed, production chain visible in-game

- [ ] **Step 3: Commit any fixups**

```bash
git add -A
git commit -m "test(factorio): litmus test verification and fixups"
```

---

## Summary

| Tier | Tasks | Impact |
|---|---|---|
| 0: Fix Broken | 1-3 | Blueprint placement actually works (was silently failing due to name mismatch) |
| 1: Zone System | 4 | Spatial organization — agents pick zones, not coordinates |
| 2: Deterministic Tools | 5-6 | Layout templates + A* belt routing — agents never invent placements |
| 3: Litmus Test | 7-10 | End-to-end iron plate chain with verified throughput |

### Not in this plan (deferred)
- **RL agent integration** — wiring zone/template selection into the policy network (needs new action types)
- **Power pole auto-placement** — every-N-tiles algorithm
- **Recipe ratio calculator** — how many furnaces per drill
- **Throughput verification tool** — monitor output rate over 60 seconds
- **Multi-chain expansion** — copper plates, gears, circuits after iron works
