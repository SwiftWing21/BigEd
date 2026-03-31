"""Zone system — rectangular area reservations for factory organization."""
import logging
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)

_ZONE_GAP = 3
_ZONE_ORIGIN_X = -5
_ZONE_ORIGIN_Y = 15
_ZONES_PER_ROW = 3


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
        return not (x >= self.x + self.width or x + w <= self.x
                    or y >= self.y + self.height or y + h <= self.y)


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

    def allocate(self, zone_type: ZoneType, width: int = 20, height: int = 10) -> Zone | None:
        for _ in range(50):
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
        return {"zones": [{"name": z.name, "type": z.zone_type.value,
                           "x": z.x, "y": z.y, "width": z.width, "height": z.height}
                          for z in self.zones]}

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneManager":
        zm = cls()
        for zd in data.get("zones", []):
            zm.create_zone(zd["name"], ZoneType(zd["type"]),
                           zd["x"], zd["y"], zd["width"], zd["height"])
        return zm
