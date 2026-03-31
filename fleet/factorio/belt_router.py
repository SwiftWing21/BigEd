"""A* belt routing between two points on the Factorio grid."""
import heapq
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DIR_NORTH = 0
DIR_EAST = 4
DIR_SOUTH = 8
DIR_WEST = 12

_NEIGHBORS = [(0, -1, DIR_NORTH), (1, 0, DIR_EAST), (0, 1, DIR_SOUTH), (-1, 0, DIR_WEST)]


@dataclass
class BeltRoute:
    belts: list[dict]
    length: int


def route_belt(start: tuple[int, int], end: tuple[int, int],
               obstacles: set[tuple[int, int]], belt_type: str = "transport-belt",
               max_search: int = 2000) -> BeltRoute | None:
    sx, sy = int(start[0]), int(start[1])
    ex, ey = int(end[0]), int(end[1])
    open_set: list[tuple[float, int, int]] = []
    heapq.heappush(open_set, (0, sx, sy))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {(sx, sy): 0}
    visited = 0

    while open_set and visited < max_search:
        _, cx, cy = heapq.heappop(open_set)
        visited += 1
        if (cx, cy) == (ex, ey):
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
                f = new_g + abs(nx - ex) + abs(ny - ey)
                heapq.heappush(open_set, (f, nx, ny))
                came_from[(nx, ny)] = (cx, cy)
    return None


def _path_to_belts(path: list[tuple[int, int]], belt_type: str) -> BeltRoute:
    belts = []
    for i, (x, y) in enumerate(path):
        if i < len(path) - 1:
            nx, ny = path[i + 1]
            direction = _delta_to_direction(nx - x, ny - y)
        else:
            direction = belts[-1]["direction"] if belts else DIR_SOUTH
        belts.append({"name": belt_type, "position": {"x": x, "y": y}, "direction": direction})
    return BeltRoute(belts=belts, length=len(belts))


def _delta_to_direction(dx: int, dy: int) -> int:
    if dx > 0: return DIR_EAST
    if dx < 0: return DIR_WEST
    if dy > 0: return DIR_SOUTH
    return DIR_NORTH
