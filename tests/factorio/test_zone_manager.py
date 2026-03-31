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
    assert result is None

def test_allocate_next_zone():
    zm = ZoneManager()
    zone = zm.allocate(ZoneType.SMELTING, width=20, height=10)
    assert zone is not None
    zone2 = zm.allocate(ZoneType.ASSEMBLY, width=20, height=10)
    assert zone2 is not None
    assert not zm.overlaps(zone, zone2)

def test_get_zone_center():
    zm = ZoneManager()
    zone = zm.create_zone("test", ZoneType.MINING, x=10, y=20, width=8, height=6)
    cx, cy = zone.center
    assert cx == 14
    assert cy == 23

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
