#!/usr/bin/env python3
"""Generate Factorio blueprint strings for the action pack system."""
import base64
import json
import zlib
from pathlib import Path


def make_blueprint(label: str, entities: list) -> str:
    """Create a Factorio blueprint string from entity list."""
    bp_data = {
        "blueprint": {
            "item": "blueprint",
            "label": label,
            "entities": entities,
            "version": 562949954076672,  # Factorio 2.0
        }
    }
    json_bytes = json.dumps(bp_data).encode("utf-8")
    compressed = zlib.compress(json_bytes, level=9)
    encoded = base64.b64encode(compressed).decode("ascii")
    return "0" + encoded


def entity(number: int, name: str, x: float, y: float, direction: int = 0) -> dict:
    """Create a blueprint entity entry."""
    e = {
        "entity_number": number,
        "name": name,
        "position": {"x": x, "y": y},
    }
    if direction != 0:
        e["direction"] = direction
    return e


def gen_basic_power_station() -> str:
    """Offshore-pump + boiler + steam-engine with pipe connections."""
    entities = [
        entity(1, "offshore-pump", 0.5, 0.5),
        # Pipe from pump to boiler
        entity(2, "pipe", 1.5, 0.5),
        # Boiler is 2x3, center at (3, 1) facing east
        entity(3, "boiler", 3, 1, direction=4),
        # Pipe from boiler to steam engine
        entity(4, "pipe", 4.5, 0.5),
        # Steam engine is 1x5, center at (5.5, 0.5) facing east
        entity(5, "steam-engine", 6, 1, direction=4),
    ]
    return make_blueprint("Basic Power Station", entities)


def gen_main_bus_starter() -> str:
    """4-lane belt segment (iron/copper bus), 4 rows of 8 belts spaced 2 apart."""
    entities = []
    n = 1
    for lane in range(4):
        y = lane * 2  # rows at y=0, 2, 4, 6
        for col in range(8):
            x = col
            entities.append(entity(n, "transport-belt", x + 0.5, y + 0.5))
            n += 1
    return make_blueprint("Main Bus Starter", entities)


def gen_smelter_column() -> str:
    """8 stone-furnaces in a column with inserter pairs and belts."""
    entities = []
    n = 1
    for i in range(8):
        y = i * 2  # furnaces at y=0,2,4,...,14
        # Input belt (far left, x=-2)
        entities.append(entity(n, "transport-belt", -1.5, y + 0.5))
        n += 1
        entities.append(entity(n, "transport-belt", -1.5, y + 1.5))
        n += 1
        # Input inserter (left side, x=-1, facing east to grab from belt into furnace)
        entities.append(entity(n, "inserter", -0.5, y + 0.5, direction=0))
        n += 1
        # Stone furnace (2x2, center at x=0.5, but 2x2 uses integer pos)
        entities.append(entity(n, "stone-furnace", 1, y + 1))
        n += 1
        # Output inserter (right side, x=2, facing east to push from furnace to belt)
        entities.append(entity(n, "inserter", 2.5, y + 0.5, direction=0))
        n += 1
        # Output belt (far right, x=3)
        entities.append(entity(n, "transport-belt", 3.5, y + 0.5))
        n += 1
        entities.append(entity(n, "transport-belt", 3.5, y + 1.5))
        n += 1
    return make_blueprint("Smelter Column", entities)


def gen_oil_processing_block() -> str:
    """Oil refinery + 2 chemical plants with pipe connections."""
    entities = []
    n = 1
    # Oil refinery (5x5, center at 3,3)
    entities.append(entity(n, "oil-refinery", 3, 3))
    n += 1
    # Chemical plant 1 (3x3, center at 9,2)
    entities.append(entity(n, "chemical-plant", 9, 2))
    n += 1
    # Chemical plant 2 (3x3, center at 9,6)
    entities.append(entity(n, "chemical-plant", 9, 6))
    n += 1
    # Small electric poles for power
    entities.append(entity(n, "small-electric-pole", 0.5, 0.5))
    n += 1
    entities.append(entity(n, "small-electric-pole", 11.5, 0.5))
    n += 1
    # Pipes connecting refinery to chemical plants
    for px in range(6, 8):
        entities.append(entity(n, "pipe", px + 0.5, 2.5))
        n += 1
        entities.append(entity(n, "pipe", px + 0.5, 6.5))
        n += 1
    # Additional pipe runs
    for py in range(1, 8):
        entities.append(entity(n, "pipe", 5.5, py + 0.5))
        n += 1
    for py in range(1, 8):
        entities.append(entity(n, "pipe", 8.5, py + 0.5))
        n += 1
    return make_blueprint("Oil Processing Block", entities)


def gen_solar_field() -> str:
    """4 solar panels (2x2 grid) + 2 accumulators + 1 medium electric pole."""
    entities = []
    n = 1
    # Solar panels are 3x3, positions at grid centers
    for row in range(2):
        for col in range(2):
            x = col * 3 + 2  # centers at x=2, 5
            y = row * 3 + 2  # centers at y=2, 5
            entities.append(entity(n, "solar-panel", x, y))
            n += 1
    # Accumulators (2x2) on the right side
    entities.append(entity(n, "accumulator", 7, 2))
    n += 1
    entities.append(entity(n, "accumulator", 7, 5))
    n += 1
    # Medium electric pole
    entities.append(entity(n, "medium-electric-pole", 4.5, 0.5))
    n += 1
    return make_blueprint("Solar Field", entities)


BLUEPRINTS = {
    "basic_power_station": {
        "name": "basic_power_station",
        "generator": gen_basic_power_station,
        "footprint": [7, 3],
        "phase_required": 1,
        "required_items": {
            "boiler": 1,
            "steam-engine": 1,
            "offshore-pump": 1,
            "pipe": 2,
        },
    },
    "main_bus_starter": {
        "name": "main_bus_starter",
        "generator": gen_main_bus_starter,
        "footprint": [8, 7],
        "phase_required": 2,
        "required_items": {"transport-belt": 32},
    },
    "smelter_column": {
        "name": "smelter_column",
        "generator": gen_smelter_column,
        "footprint": [6, 16],
        "phase_required": 1,
        "required_items": {
            "stone-furnace": 8,
            "inserter": 16,
            "transport-belt": 16,
        },
    },
    "oil_processing_block": {
        "name": "oil_processing_block",
        "generator": gen_oil_processing_block,
        "footprint": [12, 8],
        "phase_required": 3,
        "required_items": {
            "oil-refinery": 1,
            "chemical-plant": 2,
            "pipe": 20,
            "small-electric-pole": 2,
        },
    },
    "solar_field": {
        "name": "solar_field",
        "generator": gen_solar_field,
        "footprint": [8, 6],
        "phase_required": 5,
        "required_items": {
            "solar-panel": 4,
            "accumulator": 2,
            "medium-electric-pole": 1,
        },
    },
}


def main():
    bp_dir = Path(__file__).parent / "packs" / "blueprints"
    bp_dir.mkdir(parents=True, exist_ok=True)

    for key, spec in BLUEPRINTS.items():
        bp_string = spec["generator"]()
        data = {
            "name": spec["name"],
            "blueprint_string": bp_string,
            "footprint": spec["footprint"],
            "phase_required": spec["phase_required"],
            "required_items": spec["required_items"],
        }
        out_path = bp_dir / f"{key}.json"
        out_path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"  wrote {out_path.name} ({len(bp_string)} chars)")

    print(f"Generated {len(BLUEPRINTS)} blueprints in {bp_dir}")


if __name__ == "__main__":
    main()
