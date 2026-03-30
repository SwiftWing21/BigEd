"""Persistent spatial memory for the Factorio RL agent."""
import math
from dataclasses import dataclass

import numpy as np

__all__ = ["SpatialMemory", "ResourceEntry", "EntityEntry"]


@dataclass
class ResourceEntry:
    name: str
    x: int
    y: int
    amount: int
    last_seen_tick: int


@dataclass
class EntityEntry:
    name: str
    x: float
    y: float
    unit_number: int
    last_seen_tick: int


class SpatialMemory:
    """Sparse persistent map of the Factorio world."""

    _RESOURCE_TYPES = ["iron-ore", "copper-ore", "coal", "stone"]
    _DISTANCE_CAP = 200.0
    _RESOURCE_COUNT_CAP = 100.0
    _ENTITY_COUNT_CAP = 20.0

    def __init__(self):
        self.resources: dict[str, ResourceEntry] = {}
        self.entities: dict[int, EntityEntry] = {}
        self._last_local_entity_ids: set[int] = set()

    # ── Core CRUD ──────────────────────────────────────────────

    def _upsert_resource(self, name: str, x: int, y: int, amount: int, tick: int) -> None:
        key = f"{name}_{x}_{y}"
        self.resources[key] = ResourceEntry(name=name, x=x, y=y, amount=amount, last_seen_tick=tick)

    def _upsert_entity(self, name: str, x: float, y: float, unit_number: int, tick: int) -> None:
        self.entities[unit_number] = EntityEntry(name=name, x=x, y=y, unit_number=unit_number, last_seen_tick=tick)

    def remove_entity(self, unit_number: int) -> None:
        self.entities.pop(unit_number, None)

    # ── Summaries ──────────────────────────────────────────────

    def resource_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.resources.values():
            counts[entry.name] = counts.get(entry.name, 0) + 1
        return counts

    def entity_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entities.values():
            counts[entry.name] = counts.get(entry.name, 0) + 1
        return counts

    # ── Bearing / Distance Math ────────────────────────────────

    @staticmethod
    def _bearing_distance(px: float, py: float, tx: float, ty: float) -> tuple[float, float]:
        dx = tx - px
        dy = ty - py
        distance = math.sqrt(dx * dx + dy * dy)
        bearing = math.atan2(dy, dx)
        if bearing < 0:
            bearing += 2 * math.pi
        return bearing, distance

    def nearest_resource(self, px: float, py: float, resource_name: str) -> tuple[float, float] | None:
        best = None
        best_dist = float("inf")
        for entry in self.resources.values():
            if entry.name != resource_name:
                continue
            _, dist = self._bearing_distance(px, py, entry.x, entry.y)
            if dist < best_dist:
                best_dist = dist
                best = entry
        if best is None:
            return None
        return self._bearing_distance(px, py, best.x, best.y)

    def nearest_entity_by_name(self, px: float, py: float, name: str) -> tuple[float, float] | None:
        best = None
        best_dist = float("inf")
        for entry in self.entities.values():
            if entry.name != name:
                continue
            _, dist = self._bearing_distance(px, py, entry.x, entry.y)
            if dist < best_dist:
                best_dist = dist
                best = entry
        if best is None:
            return None
        return self._bearing_distance(px, py, best.x, best.y)

    # ── Feature Vector ─────────────────────────────────────────

    def get_features(self, px: float, py: float) -> list[float]:
        """Return 16 normalized floats for state encoder injection."""
        features: list[float] = []

        # Bearing/distance to nearest of each resource type (8 floats)
        for rtype in self._RESOURCE_TYPES:
            result = self.nearest_resource(px, py, rtype)
            if result is None:
                features.extend([0.0, 1.0])
            else:
                bearing, distance = result
                features.append(bearing / (2 * math.pi))
                features.append(min(distance, self._DISTANCE_CAP) / self._DISTANCE_CAP)

        # Resource patch counts (4 floats)
        summary = self.resource_summary()
        for rtype in self._RESOURCE_TYPES:
            features.append(min(summary.get(rtype, 0), self._RESOURCE_COUNT_CAP) / self._RESOURCE_COUNT_CAP)

        # Entity counts: furnaces, drills (2 floats)
        esummary = self.entity_summary()
        features.append(min(esummary.get("stone-furnace", 0), self._ENTITY_COUNT_CAP) / self._ENTITY_COUNT_CAP)
        features.append(min(esummary.get("burner-mining-drill", 0), self._ENTITY_COUNT_CAP) / self._ENTITY_COUNT_CAP)

        # Nearest furnace bearing/distance (2 floats)
        furnace = self.nearest_entity_by_name(px, py, "stone-furnace")
        if furnace is None:
            features.extend([0.0, 1.0])
        else:
            bearing, distance = furnace
            features.append(bearing / (2 * math.pi))
            features.append(min(distance, self._DISTANCE_CAP) / self._DISTANCE_CAP)

        return features

    # ── State + Survey Updates ─────────────────────────────────

    def update_from_survey(self, survey_data: list[dict]) -> None:
        """Bulk-update resources from wide-area RCON scan."""
        for entry in survey_data:
            name = entry.get("name", "")
            x = int(entry.get("x", 0))
            y = int(entry.get("y", 0))
            amount = int(entry.get("amount", 0))
            if name:
                self._upsert_resource(name, x, y, amount, tick=0)

    def clear_entities_in_radius(self, center: tuple[float, float], radius: float) -> None:
        """Remove built entities within radius of center (episode reset)."""
        cx, cy = center
        to_remove = []
        for uid, entry in self.entities.items():
            dx = entry.x - cx
            dy = entry.y - cy
            if math.sqrt(dx * dx + dy * dy) <= radius:
                to_remove.append(uid)
        for uid in to_remove:
            del self.entities[uid]

    # ── World Grid (minimap for CNN) ─────────────────────────────────

    _RESOURCE_CHANNEL_IDS = {"iron-ore": 0.25, "copper-ore": 0.5, "coal": 0.75, "stone": 1.0}
    _ENTITY_CHANNEL_IDS = {
        "stone-furnace": 0.2, "burner-mining-drill": 0.4, "electric-mining-drill": 0.4,
        "assembling-machine-1": 0.6, "transport-belt": 0.3, "inserter": 0.5,
        "burner-inserter": 0.5, "lab": 0.8, "small-electric-pole": 0.7,
        "boiler": 0.9, "steam-engine": 1.0,
    }
    _WORLD_GRID_SIZE = 64
    _AMOUNT_CAP = 5_000_000.0

    def render_world_grid(self, px: float, py: float) -> np.ndarray:
        """Render all known spatial data into a 4-channel 64x64 world minimap.

        Channels:
            0 — resource type ID (0.25=iron, 0.5=copper, 0.75=coal, 1.0=stone)
            1 — resource amount (normalized 0-1)
            2 — entity type ID (see _ENTITY_CHANNEL_IDS)
            3 — player position (1.0 at player cell, 0 elsewhere)

        The grid is auto-scaled to fit ALL known points with 10% padding,
        centered on the centroid of known data (not the player).
        """
        g = self._WORLD_GRID_SIZE
        grid = np.zeros((4, g, g), dtype=np.float32)

        if not self.resources and not self.entities:
            # No data — put player at center
            grid[3, g // 2, g // 2] = 1.0
            return grid

        # Compute bounding box of all known points
        all_x: list[float] = [px]
        all_y: list[float] = [py]
        for entry in self.resources.values():
            all_x.append(entry.x)
            all_y.append(entry.y)
        for entry in self.entities.values():
            all_x.append(entry.x)
            all_y.append(entry.y)

        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        range_x = max(max_x - min_x, 20.0)  # minimum 20 tiles
        range_y = max(max_y - min_y, 20.0)
        world_range = max(range_x, range_y) * 1.1  # 10% padding
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0

        def to_grid(wx: float, wy: float) -> tuple[int, int]:
            gx = int(((wx - center_x) / world_range + 0.5) * g)
            gy = int(((wy - center_y) / world_range + 0.5) * g)
            return max(0, min(g - 1, gx)), max(0, min(g - 1, gy))

        # Channel 0-1: resources
        for entry in self.resources.values():
            gx, gy = to_grid(entry.x, entry.y)
            rid = self._RESOURCE_CHANNEL_IDS.get(entry.name, 0.0)
            if rid > 0:
                grid[0, gy, gx] = rid
                grid[1, gy, gx] = min(entry.amount / self._AMOUNT_CAP, 1.0)

        # Channel 2: entities
        for entry in self.entities.values():
            gx, gy = to_grid(entry.x, entry.y)
            eid = self._ENTITY_CHANNEL_IDS.get(entry.name, 0.1)
            grid[2, gy, gx] = eid

        # Channel 3: player position
        pgx, pgy = to_grid(px, py)
        grid[3, pgy, pgx] = 1.0
        # Slight glow around player (3x3) for easier CNN detection
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = pgy + dy, pgx + dx
                if 0 <= ny < g and 0 <= nx < g:
                    grid[3, ny, nx] = max(grid[3, ny, nx], 0.5)
        grid[3, pgy, pgx] = 1.0  # center stays bright

        return grid

    def update_from_state(self, state, current_tick: int) -> None:
        """Upsert from get_state response. Detect removed local entities."""
        # Resources
        for r in getattr(state, "resource_positions", []):
            name = r.get("name", "")
            if not name:
                continue
            x = int(r.get("x", 0))
            y = int(r.get("y", 0))
            amount = int(r.get("amount", 0))
            self._upsert_resource(name, x, y, amount, tick=current_tick)

        # Entities
        current_ids = set()
        for e in getattr(state, "entities", []):
            uid = getattr(e, "unit_number", 0) if hasattr(e, "unit_number") else e.get("unit_number", 0)
            if not uid:
                continue
            name = getattr(e, "name", "") if hasattr(e, "name") else e.get("name", "")
            pos = getattr(e, "position", {}) if hasattr(e, "position") else e.get("position", {})
            x = pos.get("x", 0.0) if isinstance(pos, dict) else 0.0
            y = pos.get("y", 0.0) if isinstance(pos, dict) else 0.0
            self._upsert_entity(name, x, y, uid, tick=current_tick)
            current_ids.add(uid)

        # Detect removed entities (were in local grid last tick, gone now)
        removed = self._last_local_entity_ids - current_ids
        for uid in removed:
            self.remove_entity(uid)
        self._last_local_entity_ids = current_ids
