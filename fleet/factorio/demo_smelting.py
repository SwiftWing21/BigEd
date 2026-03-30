"""Submit a smelting demonstration to the Factorio RL agent.

Queries the spatial map for real ore positions, then builds and submits
a mine→craft→place→fuel→smelt sequence. Run after bridge is started.

Usage: python fleet/factorio/demo_smelting.py
"""
import json
import sys
import urllib.request


BRIDGE_PORT = 27016
BASE = f"http://127.0.0.1:{BRIDGE_PORT}"


def api(path, method="GET", body=None):
    opts = {"method": method}
    data = None
    headers = {}
    if body:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, **opts)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def find_ore_positions(resources, ore_name, count=6):
    """Find `count` resource positions of the given type near spawn."""
    matches = [r for r in resources if r["name"] == ore_name]
    # Sort by distance from origin
    matches.sort(key=lambda r: r["x"] ** 2 + r["y"] ** 2)
    return matches[:count]


def main():
    # 1. Query spatial map for real ore positions
    try:
        spatial = api("/api/spatial-map")
    except Exception as e:
        print(f"Bridge not reachable: {e}")
        sys.exit(1)

    resources = spatial.get("resources", [])
    player = spatial.get("player", {"x": 0, "y": 0})
    print(f"Player at ({player['x']:.0f}, {player['y']:.0f})")
    print(f"Total resources: {len(resources)}")

    coal_pos = find_ore_positions(resources, "coal", 8)
    stone_pos = find_ore_positions(resources, "stone", 6)
    iron_pos = find_ore_positions(resources, "iron-ore", 10)

    if not coal_pos or not stone_pos or not iron_pos:
        print("Not enough ore near spawn — wait for spatial memory to populate")
        sys.exit(1)

    print(f"Coal: {len(coal_pos)} patches, nearest at ({coal_pos[0]['x']}, {coal_pos[0]['y']})")
    print(f"Stone: {len(stone_pos)} patches, nearest at ({stone_pos[0]['x']}, {stone_pos[0]['y']})")
    print(f"Iron: {len(iron_pos)} patches, nearest at ({iron_pos[0]['x']}, {iron_pos[0]['y']})")

    actions = []

    # 2. Mine coal (need fuel for furnaces)
    actions.append({"action": "move", "position": {"x": coal_pos[0]["x"], "y": coal_pos[0]["y"]}})
    for cp in coal_pos:
        actions.append({"action": "mine", "position": {"x": cp["x"], "y": cp["y"]}})

    # 3. Mine stone (need 10 for 2 furnaces)
    actions.append({"action": "move", "position": {"x": stone_pos[0]["x"], "y": stone_pos[0]["y"]}})
    for sp in stone_pos:
        actions.append({"action": "mine", "position": {"x": sp["x"], "y": sp["y"]}})

    # 4. Mine iron ore
    actions.append({"action": "move", "position": {"x": iron_pos[0]["x"], "y": iron_pos[0]["y"]}})
    for ip in iron_pos:
        actions.append({"action": "mine", "position": {"x": ip["x"], "y": ip["y"]}})

    # 5. Craft 2 furnaces
    actions.append({"action": "craft", "recipe": "stone-furnace", "count": 2})

    # 6. Place furnaces on clear ground near spawn (offset from ore)
    # Find a spot that's not on ore — slightly offset from iron
    fx, fy = 0, -8  # north of spawn, likely clear
    actions.append({"action": "move", "position": {"x": fx, "y": fy}})
    actions.append({"action": "place", "entity": "stone-furnace", "position": {"x": fx, "y": fy}, "direction": 0})
    actions.append({"action": "place", "entity": "stone-furnace", "position": {"x": fx + 2, "y": fy}, "direction": 0})

    # 7. Fuel both furnaces with coal
    actions.append({"action": "insert", "position": {"x": fx, "y": fy}, "item": "coal", "count": 10})
    actions.append({"action": "insert", "position": {"x": fx + 2, "y": fy}, "item": "coal", "count": 10})

    # 8. Feed iron ore into both furnaces
    actions.append({"action": "insert", "position": {"x": fx, "y": fy}, "item": "iron-ore", "count": 5})
    actions.append({"action": "insert", "position": {"x": fx + 2, "y": fy}, "item": "iron-ore", "count": 5})

    # 9. Wait for smelting
    actions.append({"action": "wait", "ticks": 300})

    print(f"\nSubmitting {len(actions)} actions...")
    result = api("/api/command", method="POST", body={"actions": actions})
    print(f"Queued: {result.get('command_id', '?')}")
    print("Done — furnaces should appear and start smelting within seconds.")


if __name__ == "__main__":
    main()
