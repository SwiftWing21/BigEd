"""Pre-computed factory layout templates — exact entity coordinates."""
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class LayoutTemplate:
    name: str
    description: str
    entities: list[dict]
    footprint: tuple[int, int] = (0, 0)

    def at_position(self, ox: int, oy: int) -> list[dict]:
        result = []
        for e in self.entities:
            entry = dict(e)
            entry["position"] = {"x": e["position"]["x"] + ox, "y": e["position"]["y"] + oy}
            result.append(entry)
        return result


# 16-dir: 0=north, 4=east, 8=south, 12=west

_IRON_SMELTER_4X = LayoutTemplate(
    name="iron_smelter_4x",
    description="4 stone furnaces with input/output belts and inserters",
    footprint=(5, 8),
    entities=[
        *[{"name": "transport-belt", "position": {"x": 0, "y": i * 2}, "direction": 8} for i in range(4)],
        *[{"name": "burner-inserter", "position": {"x": 1, "y": i * 2}, "direction": 4} for i in range(4)],
        *[{"name": "stone-furnace", "position": {"x": 2, "y": i * 2}, "direction": 0} for i in range(4)],
        *[{"name": "burner-inserter", "position": {"x": 3, "y": i * 2}, "direction": 4} for i in range(4)],
        *[{"name": "transport-belt", "position": {"x": 4, "y": i * 2}, "direction": 8} for i in range(4)],
    ],
)

_BASIC_POWER = LayoutTemplate(
    name="basic_power",
    description="Offshore pump + boiler + steam engine",
    footprint=(7, 3),
    entities=[
        {"name": "offshore-pump", "position": {"x": 0, "y": 1}, "direction": 4},
        {"name": "boiler", "position": {"x": 2, "y": 1}, "direction": 4},
        {"name": "steam-engine", "position": {"x": 5, "y": 1}, "direction": 4},
        {"name": "small-electric-pole", "position": {"x": 6, "y": 0}, "direction": 0},
    ],
)

_DRILL_ARRAY_4X = LayoutTemplate(
    name="drill_array_4x",
    description="4 electric mining drills in a row",
    footprint=(12, 3),
    entities=[
        *[{"name": "electric-mining-drill", "position": {"x": i * 3, "y": 0}, "direction": 8} for i in range(4)],
    ],
)

_OUTPUT_CHEST = LayoutTemplate(
    name="output_chest",
    description="Iron chest at belt terminus with inserter",
    footprint=(2, 1),
    entities=[
        {"name": "inserter", "position": {"x": 0, "y": 0}, "direction": 4},
        {"name": "iron-chest", "position": {"x": 1, "y": 0}, "direction": 0},
    ],
)


def belt_segment(length: int, direction: int = 8) -> LayoutTemplate:
    if direction in (0, 8):
        entities = [{"name": "transport-belt", "position": {"x": 0, "y": i}, "direction": direction} for i in range(length)]
        footprint = (1, length)
    else:
        entities = [{"name": "transport-belt", "position": {"x": i, "y": 0}, "direction": direction} for i in range(length)]
        footprint = (length, 1)
    return LayoutTemplate(name=f"belt_{length}_{direction}", description=f"{length}-tile belt", footprint=footprint, entities=entities)


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
