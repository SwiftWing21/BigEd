from factorio.factory_builder import IronLinePlan
from factorio.zone_manager import ZoneManager, ZoneType


def test_iron_line_plan_generates_entities():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    entities = plan.compute()
    assert len(entities) > 0
    names = {e["name"] for e in entities}
    assert "electric-mining-drill" in names
    assert "stone-furnace" in names
    assert "transport-belt" in names
    assert "iron-chest" in names


def test_iron_line_plan_allocates_zones():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    plan.compute()
    assert len(zm.zones_by_type(ZoneType.MINING)) >= 1
    assert len(zm.zones_by_type(ZoneType.SMELTING)) >= 1


def test_iron_line_no_overlaps():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    entities = plan.compute()
    positions = [(e["position"]["x"], e["position"]["y"]) for e in entities]
    assert len(positions) == len(set(positions)), "Overlapping entity positions"


def test_iron_line_to_rcon_commands():
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    plan.compute()
    commands = plan.to_rcon_commands()
    assert len(commands) > 0
    for cmd in commands:
        assert cmd["action"] == "place"
        assert "entity" in cmd
        assert "position" in cmd
