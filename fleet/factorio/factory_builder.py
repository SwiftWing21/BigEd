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
            log.warning("IronLinePlan: create_zone failed for iron_mining — falling back to allocate()")
            mining_zone = self.zm.allocate(ZoneType.MINING, width=14, height=5)

        # 2. Place 4 drills
        drill_template = get_template("drill_array_4x")
        if drill_template and mining_zone:
            drills = drill_template.at_position(mining_zone.x, mining_zone.y)
            self._entities.extend(drills)
        else:
            log.warning("IronLinePlan: could not place drills — template=%s, zone=%s",
                        drill_template, mining_zone)

        # 3. Allocate smelting zone below mining
        smelt_x = mining_zone.x if mining_zone else self.ore_x
        smelt_y = (mining_zone.y + mining_zone.height + 3) if mining_zone else self.ore_y + 8
        smelting_zone = self.zm.create_zone(
            "iron_smelting", ZoneType.SMELTING,
            x=smelt_x, y=smelt_y, width=8, height=10,
        )
        if not smelting_zone:
            log.warning("IronLinePlan: create_zone failed for iron_smelting — falling back to allocate()")
            smelting_zone = self.zm.allocate(ZoneType.SMELTING, width=8, height=10)

        # 4. Place smelter line
        smelter_template = get_template("iron_smelter_4x")
        if smelter_template and smelting_zone:
            smelters = smelter_template.at_position(smelting_zone.x, smelting_zone.y)
            self._entities.extend(smelters)
        else:
            log.warning("IronLinePlan: could not place smelters — template=%s, zone=%s",
                        smelter_template, smelting_zone)

        # 5. Route belts from drill output to smelter input
        if mining_zone and smelting_zone:
            belt_start = (mining_zone.x + 6, mining_zone.y + mining_zone.height)
            belt_end = (smelting_zone.x, smelting_zone.y)
            occupied = {(int(e["position"]["x"]), int(e["position"]["y"]))
                        for e in self._entities}
            try:
                belt_route = route_belt(belt_start, belt_end, occupied)
            except Exception:
                log.warning("IronLinePlan: belt routing raised an exception", exc_info=True)
                belt_route = None
            if belt_route:
                self._entities.extend(belt_route.belts)
            else:
                log.warning("IronLinePlan: no belt route found from %s to %s",
                            belt_start, belt_end)
        else:
            log.warning("IronLinePlan: skipping belt routing — mining_zone=%s, smelting_zone=%s",
                        mining_zone, smelting_zone)

        # 6. Place output chest after smelter
        chest_template = get_template("output_chest")
        if chest_template and smelting_zone:
            chest_pos_x = smelting_zone.x + 4  # after output belt column
            chest_pos_y = smelting_zone.y + smelting_zone.height
            chests = chest_template.at_position(chest_pos_x, chest_pos_y)
            self._entities.extend(chests)
        else:
            log.warning("IronLinePlan: could not place output chest — template=%s, zone=%s",
                        chest_template, smelting_zone)

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
