from factorio.belt_router import route_belt, DIR_EAST, DIR_SOUTH

def test_straight_horizontal():
    route = route_belt(start=(0, 0), end=(5, 0), obstacles=set())
    assert route is not None
    assert len(route.belts) == 6

def test_straight_vertical():
    route = route_belt(start=(0, 0), end=(0, 5), obstacles=set())
    assert route is not None
    assert len(route.belts) == 6

def test_route_around_obstacle():
    obstacles = {(2, 0), (2, 1)}
    route = route_belt(start=(0, 0), end=(4, 0), obstacles=obstacles)
    assert route is not None
    belt_positions = {(b["position"]["x"], b["position"]["y"]) for b in route.belts}
    assert belt_positions.isdisjoint(obstacles)

def test_no_route_possible():
    obstacles = {(1, 0), (-1, 0), (0, 1), (0, -1)}
    route = route_belt(start=(0, 0), end=(5, 5), obstacles=obstacles)
    assert route is None

def test_belt_directions_east():
    route = route_belt(start=(0, 0), end=(3, 0), obstacles=set())
    assert route is not None
    for b in route.belts:
        assert b["direction"] == DIR_EAST
