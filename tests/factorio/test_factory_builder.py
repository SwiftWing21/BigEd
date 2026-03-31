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


def test_iron_line_rcon_commands_are_valid_action_dicts():
    """Verify RCON commands can be translated by action_translator."""
    from factorio.action_translator import translate_action
    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(10, 10))
    plan.compute()
    commands = plan.to_rcon_commands()
    for cmd in commands:
        translated = translate_action(cmd)
        assert translated.action_type == "place"
        assert translated.rcon_command is not None


def test_iron_line_endpoint_integration():
    """Simulate the /api/build/iron_line endpoint logic (no Flask needed)."""
    import queue
    from factorio.zone_manager import ZoneManager
    from factorio.factory_builder import IronLinePlan

    command_queue = queue.Queue()
    ore_x, ore_y = 15, 20

    zm = ZoneManager()
    plan = IronLinePlan(zone_manager=zm, ore_position=(ore_x, ore_y))
    entities = plan.compute()
    commands = plan.to_rcon_commands()

    cmd_id = f"iron_line_{len(commands)}"
    command_queue.put({"id": cmd_id, "actions": commands})

    # Verify the queue has the expected item
    item = command_queue.get_nowait()
    assert item["id"] == cmd_id
    assert len(item["actions"]) > 0
    assert all(a["action"] == "place" for a in item["actions"])

    # Verify zones were allocated
    zones_dict = zm.to_dict()
    assert len(zones_dict.get("zones", [])) >= 2  # mining + smelting


def test_iron_line_different_ore_positions():
    """Plan works at various ore positions."""
    for ox, oy in [(0, 0), (50, -30), (-10, 10)]:
        zm = ZoneManager()
        plan = IronLinePlan(zone_manager=zm, ore_position=(ox, oy))
        entities = plan.compute()
        assert len(entities) > 0, f"Failed at ore position ({ox}, {oy})"
