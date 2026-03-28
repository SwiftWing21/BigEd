# Factorio Sandbox Module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a BigEd module that trains fleet agents to play Factorio autonomously through a 4-phase curriculum, with RCON bridge, adaptive cadence, and multi-PC support.

**Architecture:** A long-running bridge process (`fleet/factorio/bridge.py`) talks to Factorio via RCON, maintains a persistent WorldModel, and exposes a localhost API. Fleet skills wrap the bridge for workers. A launcher module (`mod_factorio.py`) provides the tab UI with status, cadence slider, curriculum progress, and setup wizard.

**Tech Stack:** Python 3.10+, asyncio (RCON client), Flask (bridge API), customtkinter (launcher UI), TOML (config + curricula)

**Spec:** `docs/superpowers/specs/2026-03-27-factorio-sandbox-design.md`

---

## File Map

### New Files — Fleet Side (`fleet/factorio/`)

| File | Responsibility |
|------|---------------|
| `fleet/factorio/__init__.py` | Package init, version constant |
| `fleet/factorio/bridge.py` | Main bridge process: tick loop, RCON polling, CommandQueue drain |
| `fleet/factorio/rcon_client.py` | Async RCON protocol client (Source RCON v1) |
| `fleet/factorio/state_parser.py` | Parse raw JSON from Lua mod → `GameState` dataclass |
| `fleet/factorio/action_translator.py` | Translate agent action dicts → RCON command strings |
| `fleet/factorio/bridge_config.py` | `BridgeConfig` dataclass loaded from fleet.toml `[factorio]` |
| `fleet/factorio/world_model.py` | Persistent in-memory state with diffing + event detection |
| `fleet/factorio/cadence.py` | CadenceController: 4 modes, adaptive boost + decay |
| `fleet/factorio/curriculum.py` | Curriculum engine: load TOML, evaluate criteria, graduate |
| `fleet/factorio/lua_installer.py` | Detect Factorio path, copy Lua mod (manual/assisted) |
| `fleet/factorio/bridge_api.py` | Flask app: localhost-only `/api/state`, `/api/command`, `/api/status` |
| `fleet/factorio/lua_mod/info.json` | Factorio mod manifest |
| `fleet/factorio/lua_mod/control.lua` | Lua state serializer + command executor (from draft) |

### New Files — Skills (`fleet/skills/`)

| File | Responsibility |
|------|---------------|
| `fleet/skills/factorio_observe.py` | Read WorldModel via bridge API, return markdown |
| `fleet/skills/factorio_plan.py` | Strategic planning: state → next actions (LLM call) |
| `fleet/skills/factorio_act.py` | Translate plan → actions, submit to bridge CommandQueue |
| `fleet/skills/factorio_train.py` | Curriculum evaluation: check success criteria, track progress |

### New Files — Curricula (`fleet/idle_curricula/`)

| File | Responsibility |
|------|---------------|
| `fleet/idle_curricula/factorio_01_bootstrap.toml` | Phase 1: hand-craft, smelt, power, automate mining |
| `fleet/idle_curricula/factorio_02_goals.toml` | Phase 2: automate red science, green science, rails |
| `fleet/idle_curricula/factorio_03_kpis.toml` | Phase 3: throughput targets, idle rate, efficiency |
| `fleet/idle_curricula/factorio_04_survival.toml` | Phase 4: biters on, defense, expansion |

### New Files — Launcher (`BigEd/launcher/modules/`)

| File | Responsibility |
|------|---------------|
| `BigEd/launcher/modules/mod_factorio.py` | Launcher tab: status panel, cadence slider, curriculum view, setup wizard |

### New Files — Tests

| File | Responsibility |
|------|---------------|
| `tests/test_rcon_client.py` | RCON packet framing, connect/disconnect, command round-trip |
| `tests/test_state_parser.py` | JSON → GameState parsing, edge cases |
| `tests/test_action_translator.py` | Action dict → RCON command string translation |
| `tests/test_world_model.py` | State diffing, event detection, entity lifecycle |
| `tests/test_cadence.py` | Mode switching, adaptive boost/decay timing |
| `tests/test_curriculum.py` | Criteria parsing, lesson pass/fail, graduation |
| `tests/test_bridge_config.py` | Config loading from fleet.toml, defaults |
| `tests/test_lua_installer.py` | Path detection, copy logic |

### Modified Files

| File | Change |
|------|--------|
| `fleet/fleet.toml` | Add `[factorio]` config section |
| `fleet/process_manager.py` | Add `start_factorio_bridge()` / `check_alive` / shutdown for bridge process |
| `fleet/dashboard.py` | Register `factorio_api` blueprint, add sandbox mode banner |
| `fleet/providers.py` | Add factorio skills to `SKILL_COMPLEXITY` dict |

---

## Task 1: RCON Client

The async RCON client handles packet framing per the Source RCON protocol. Everything else depends on this.

**Files:**
- Create: `fleet/factorio/__init__.py`
- Create: `fleet/factorio/rcon_client.py`
- Test: `tests/test_rcon_client.py`

- [ ] **Step 1: Create package init**

```python
# fleet/factorio/__init__.py
"""Factorio Sandbox Module — BigEd agent bridge to Factorio."""
__version__ = "0.1.0"
```

- [ ] **Step 2: Write failing tests for RCON packet framing**

```python
# tests/test_rcon_client.py
"""Tests for Factorio RCON client — packet framing + command encoding."""
import struct
import pytest

# Source RCON packet format:
# [4 bytes size][4 bytes id][4 bytes type][payload bytes][2 null bytes]
# Types: 3 = SERVERDATA_AUTH, 2 = SERVERDATA_EXECCOMMAND, 0 = SERVERDATA_RESPONSE_VALUE

def test_encode_auth_packet():
    from factorio.rcon_client import encode_packet
    packet = encode_packet(1, 3, "mypassword")
    size = struct.unpack("<i", packet[:4])[0]
    req_id = struct.unpack("<i", packet[4:8])[0]
    ptype = struct.unpack("<i", packet[8:12])[0]
    body = packet[12:-2]
    assert req_id == 1
    assert ptype == 3  # AUTH
    assert body == b"mypassword"
    assert packet[-2:] == b"\x00\x00"
    assert size == len(packet) - 4  # size field doesn't include itself

def test_encode_command_packet():
    from factorio.rcon_client import encode_packet
    packet = encode_packet(42, 2, "/biged-state")
    req_id = struct.unpack("<i", packet[4:8])[0]
    ptype = struct.unpack("<i", packet[8:12])[0]
    body = packet[12:-2]
    assert req_id == 42
    assert ptype == 2  # EXECCOMMAND
    assert body == b"/biged-state"

def test_decode_response_packet():
    from factorio.rcon_client import encode_packet, decode_packet
    # Build a fake response
    body = b'{"tick": 100}'
    payload = struct.pack("<ii", 42, 0) + body + b"\x00\x00"
    raw = struct.pack("<i", len(payload)) + payload
    req_id, ptype, data = decode_packet(raw)
    assert req_id == 42
    assert ptype == 0  # RESPONSE_VALUE
    assert data == '{"tick": 100}'

def test_decode_empty_response():
    from factorio.rcon_client import encode_packet, decode_packet
    payload = struct.pack("<ii", 1, 0) + b"\x00\x00"
    raw = struct.pack("<i", len(payload)) + payload
    req_id, ptype, data = decode_packet(raw)
    assert data == ""
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_rcon_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'factorio'`

- [ ] **Step 4: Implement RCON packet encoding/decoding**

```python
# fleet/factorio/rcon_client.py
"""Async RCON client for Factorio headless server (Source RCON v1 protocol)."""
import asyncio
import struct
import logging

log = logging.getLogger("biged.factorio.rcon")

# Source RCON packet types
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


def encode_packet(request_id: int, packet_type: int, body: str) -> bytes:
    """Encode a Source RCON packet."""
    body_bytes = body.encode("utf-8")
    # payload = id(4) + type(4) + body + null(1) + null(1)
    payload = struct.pack("<ii", request_id, packet_type) + body_bytes + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def decode_packet(data: bytes) -> tuple[int, int, str]:
    """Decode a Source RCON packet. Returns (request_id, packet_type, body)."""
    if len(data) < 14:
        raise ValueError(f"Packet too short: {len(data)} bytes")
    size = struct.unpack("<i", data[:4])[0]
    request_id = struct.unpack("<i", data[4:8])[0]
    packet_type = struct.unpack("<i", data[8:12])[0]
    body = data[12:12 + size - 10]  # size includes id+type+2 nulls = 10 bytes
    return request_id, packet_type, body.decode("utf-8", errors="replace")


class RCONClient:
    """Async RCON client with reconnection and timeout support."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._connected = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(self) -> None:
        """Connect and authenticate with the RCON server."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        # Authenticate
        auth_id = self._next_id()
        self._writer.write(encode_packet(auth_id, SERVERDATA_AUTH, self.password))
        await self._writer.drain()
        response = await self._read_packet()
        if response[0] == -1:
            raise ConnectionRefusedError("RCON authentication failed")
        self._connected = True
        log.info(f"RCON connected to {self.host}:{self.port}")

    async def disconnect(self) -> None:
        """Close the RCON connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
        self._reader = None
        self._writer = None

    async def command(self, cmd: str) -> str:
        """Send a command and return the response body."""
        if not self._connected:
            raise ConnectionError("Not connected to RCON server")
        req_id = self._next_id()
        self._writer.write(encode_packet(req_id, SERVERDATA_EXECCOMMAND, cmd))
        await self._writer.drain()
        resp_id, resp_type, body = await self._read_packet()
        return body

    async def _read_packet(self) -> tuple[int, int, str]:
        """Read one RCON packet from the stream."""
        size_data = await asyncio.wait_for(
            self._reader.readexactly(4), timeout=self.timeout
        )
        size = struct.unpack("<i", size_data)[0]
        payload = await asyncio.wait_for(
            self._reader.readexactly(size), timeout=self.timeout
        )
        return decode_packet(size_data + payload)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_rcon_client.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/__init__.py fleet/factorio/rcon_client.py tests/test_rcon_client.py
git commit -m "feat(factorio): add async RCON client with packet framing"
```

---

## Task 2: Bridge Config

Load `[factorio]` section from fleet.toml into a typed dataclass.

**Files:**
- Create: `fleet/factorio/bridge_config.py`
- Modify: `fleet/fleet.toml` — add `[factorio]` section
- Test: `tests/test_bridge_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_bridge_config.py
"""Tests for Factorio bridge config loading."""
import pytest

def test_default_config():
    from factorio.bridge_config import BridgeConfig
    cfg = BridgeConfig()
    assert cfg.enabled is False
    assert cfg.role == "host"
    assert cfg.rcon_port == 27015
    assert cfg.cadence == "adaptive"
    assert cfg.sandbox_mode is True
    assert cfg.reserved_workers == 0
    assert cfg.max_actions_per_step == 20
    assert cfg.rcon_timeout_secs == 5
    assert cfg.rcon_max_retries == 3
    assert cfg.adaptive_boost_hold_secs == 30

def test_load_from_dict():
    from factorio.bridge_config import BridgeConfig
    raw = {
        "enabled": True,
        "rcon_port": 27020,
        "rcon_password": "test123",
        "cadence": "fast",
        "sandbox_mode": False,
        "reserved_workers": 2,
    }
    cfg = BridgeConfig.from_dict(raw)
    assert cfg.enabled is True
    assert cfg.rcon_port == 27020
    assert cfg.rcon_password == "test123"
    assert cfg.cadence == "fast"
    assert cfg.sandbox_mode is False
    assert cfg.reserved_workers == 2

def test_load_from_fleet_toml():
    from factorio.bridge_config import load_factorio_config
    cfg = load_factorio_config()
    # Should load from fleet.toml [factorio] section
    assert isinstance(cfg.rcon_port, int)
    assert cfg.cadence in ("fast", "medium", "slow", "adaptive")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_bridge_config.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement BridgeConfig**

```python
# fleet/factorio/bridge_config.py
"""Bridge configuration — loads from fleet.toml [factorio] section."""
import logging
from dataclasses import dataclass, field

log = logging.getLogger("biged.factorio.config")


@dataclass
class BridgeConfig:
    """Typed config for the Factorio bridge."""
    enabled: bool = False
    role: str = "host"                      # host | compute
    host_fleet_id: str = ""
    bridge_port: int = 27016

    # Server
    rcon_host: str = "localhost"
    rcon_port: int = 27015
    rcon_password: str = ""
    server_mode: str = "headless"           # headless | client
    factorio_path: str = ""
    headless_path: str = ""
    save_file: str = "sandbox.zip"
    spectator_enabled: bool = True

    # Cadence
    cadence: str = "adaptive"               # fast | medium | slow | adaptive
    cadence_fast_ms: int = 1000
    cadence_medium_ms: int = 5000
    cadence_slow_ms: int = 30000
    adaptive_boost_ms: int = 1500
    adaptive_boost_hold_secs: int = 30
    adaptive_events: list = field(default_factory=lambda: [
        "resource_depleted", "entity_destroyed", "research_complete",
        "power_outage", "idle_assemblers",
    ])

    # RCON resilience
    rcon_timeout_secs: int = 5
    rcon_max_retries: int = 3
    rcon_circuit_breaker_secs: int = 30

    # Agent
    max_actions_per_step: int = 20
    sandbox_mode: bool = True
    reserved_workers: int = 0

    # Training
    current_phase: int = 1
    auto_advance: bool = True

    # Lua mod
    lua_install_mode: str = "manual"        # manual | assisted

    # Files
    state_file: str = "fleet/factorio/factory-state.md"
    log_dir: str = "fleet/factorio/logs"
    curriculum_dir: str = "fleet/idle_curricula"

    @classmethod
    def from_dict(cls, d: dict) -> "BridgeConfig":
        """Build config from a dict (e.g., fleet.toml [factorio] section)."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


def load_factorio_config() -> BridgeConfig:
    """Load [factorio] config from fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        section = cfg.get("factorio", {})
        return BridgeConfig.from_dict(section)
    except Exception:
        log.warning("Could not load [factorio] from fleet.toml, using defaults")
        return BridgeConfig()
```

- [ ] **Step 4: Add `[factorio]` section to fleet.toml**

Append to `fleet/fleet.toml`:

```toml
[factorio]
enabled = false
role = "host"
host_fleet_id = ""
bridge_port = 27016
rcon_host = "localhost"
rcon_port = 27015
rcon_password = ""
server_mode = "headless"
factorio_path = ""
headless_path = ""
save_file = "sandbox.zip"
spectator_enabled = true
cadence = "adaptive"
cadence_fast_ms = 1000
cadence_medium_ms = 5000
cadence_slow_ms = 30000
adaptive_boost_ms = 1500
adaptive_boost_hold_secs = 30
adaptive_events = ["resource_depleted", "entity_destroyed", "research_complete", "power_outage", "idle_assemblers"]
rcon_timeout_secs = 5
rcon_max_retries = 3
rcon_circuit_breaker_secs = 30
max_actions_per_step = 20
sandbox_mode = true
reserved_workers = 0
current_phase = 1
auto_advance = true
lua_install_mode = "manual"
state_file = "fleet/factorio/factory-state.md"
log_dir = "fleet/factorio/logs"
curriculum_dir = "fleet/idle_curricula"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_bridge_config.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/bridge_config.py fleet/fleet.toml tests/test_bridge_config.py
git commit -m "feat(factorio): add bridge config with fleet.toml integration"
```

---

## Task 3: State Parser + GameState Model

Parse raw JSON from Factorio's `/biged-state` and `/biged-metrics` RCON responses into typed dataclasses.

**Files:**
- Create: `fleet/factorio/state_parser.py`
- Test: `tests/test_state_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_state_parser.py
"""Tests for Factorio state parser — JSON → GameState dataclass."""
import json
import pytest

SAMPLE_STATE = {
    "tick": 5400,
    "time_of_day": 0.5,
    "player": {"position": {"x": 10.0, "y": -5.0}, "health": 250},
    "inventory": {"iron-plate": 50, "copper-plate": 30, "stone-furnace": 2},
    "entities": [
        {"name": "stone-furnace", "type": "furnace", "position": {"x": 5, "y": 0},
         "direction": 0, "health": 200, "unit_number": 1,
         "recipe": "iron-plate", "crafting_progress": 0.5, "is_crafting": True,
         "input": {"iron-ore": 10}, "output": {"iron-plate": 3}},
        {"name": "transport-belt", "type": "transport-belt",
         "position": {"x": 6, "y": 0}, "direction": 4, "health": 150,
         "unit_number": 2, "belt_contents": {"iron-plate": 2}},
    ],
    "entity_count": 2,
    "resources": [{"name": "iron-ore", "patches": 15, "total_amount": 48000}],
    "research": {"name": "automation", "progress": 0.35},
    "map_explored_chunks": 12,
}

SAMPLE_METRICS = {
    "tick": 5400,
    "total_produced": {"iron-plate": 200, "copper-plate": 50},
    "total_consumed": {"iron-plate": 80},
    "flow_per_minute": {"iron-plate": 12.5, "copper-plate": 3.0},
    "electric": {"capacity_mw": 2, "satisfaction": "ok", "entity_count": 3},
    "research": {"completed": ["automation"], "current": "logistics", "progress": 0.1},
}

def test_parse_state_basic():
    from factorio.state_parser import parse_state
    state = parse_state(json.dumps(SAMPLE_STATE))
    assert state.tick == 5400
    assert state.player_position == {"x": 10.0, "y": -5.0}
    assert state.inventory["iron-plate"] == 50
    assert len(state.entities) == 2
    assert state.entities[0].name == "stone-furnace"
    assert state.entities[0].recipe == "iron-plate"

def test_parse_state_resources():
    from factorio.state_parser import parse_state
    state = parse_state(json.dumps(SAMPLE_STATE))
    assert len(state.resources) == 1
    assert state.resources[0]["name"] == "iron-ore"
    assert state.resources[0]["total_amount"] == 48000

def test_parse_state_research():
    from factorio.state_parser import parse_state
    state = parse_state(json.dumps(SAMPLE_STATE))
    assert state.research_name == "automation"
    assert state.research_progress == 0.35

def test_parse_metrics():
    from factorio.state_parser import parse_metrics
    metrics = parse_metrics(json.dumps(SAMPLE_METRICS))
    assert metrics.flow_per_minute["iron-plate"] == 12.5
    assert "automation" in metrics.completed_research
    assert metrics.electric_satisfaction == "ok"

def test_parse_invalid_json_returns_empty():
    from factorio.state_parser import parse_state
    state = parse_state("not json at all")
    assert state.tick == 0
    assert len(state.entities) == 0

def test_state_to_markdown():
    from factorio.state_parser import parse_state, parse_metrics, state_to_markdown
    state = parse_state(json.dumps(SAMPLE_STATE))
    metrics = parse_metrics(json.dumps(SAMPLE_METRICS))
    md = state_to_markdown(state, metrics)
    assert "## Inventory" in md
    assert "iron-plate" in md
    assert "## Entities" in md
    assert "stone-furnace" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_state_parser.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement state parser**

```python
# fleet/factorio/state_parser.py
"""Parse Factorio RCON JSON responses into typed dataclasses."""
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("biged.factorio.state")


@dataclass
class Entity:
    """A single Factorio entity."""
    name: str = ""
    type: str = ""
    position: dict = field(default_factory=dict)
    direction: int = 0
    health: float = 0
    unit_number: int = 0
    recipe: str = ""
    crafting_progress: float = 0.0
    is_crafting: bool = False
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    belt_contents: dict = field(default_factory=dict)
    held_item: dict | None = None
    mining_target: str = ""
    energy: float = 0.0
    status: str = ""


@dataclass
class GameState:
    """Parsed game state from /biged-state."""
    tick: int = 0
    time_of_day: float = 0.0
    player_position: dict = field(default_factory=dict)
    player_health: float = 0
    inventory: dict = field(default_factory=dict)
    entities: list[Entity] = field(default_factory=list)
    entity_count: int = 0
    resources: list[dict] = field(default_factory=list)
    research_name: str = ""
    research_progress: float = 0.0
    map_explored_chunks: int = 0


@dataclass
class GameMetrics:
    """Parsed metrics from /biged-metrics."""
    tick: int = 0
    total_produced: dict = field(default_factory=dict)
    total_consumed: dict = field(default_factory=dict)
    flow_per_minute: dict = field(default_factory=dict)
    electric_satisfaction: str = ""
    electric_capacity_mw: float = 0.0
    electric_entity_count: int = 0
    completed_research: list[str] = field(default_factory=list)
    current_research: str = ""
    current_research_progress: float = 0.0


def parse_state(raw_json: str) -> GameState:
    """Parse raw /biged-state JSON into GameState."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Failed to parse state JSON")
        return GameState()

    entities = []
    for e in data.get("entities", []):
        entities.append(Entity(
            name=e.get("name", ""),
            type=e.get("type", ""),
            position=e.get("position", {}),
            direction=e.get("direction", 0),
            health=e.get("health", 0),
            unit_number=e.get("unit_number", 0),
            recipe=e.get("recipe", ""),
            crafting_progress=e.get("crafting_progress", 0.0),
            is_crafting=e.get("is_crafting", False),
            input=e.get("input", {}),
            output=e.get("output", {}),
            belt_contents=e.get("belt_contents", {}),
            held_item=e.get("held_item"),
            mining_target=e.get("mining_target", ""),
            energy=e.get("energy", 0.0),
            status=e.get("status", ""),
        ))

    player = data.get("player", {})
    research = data.get("research") or {}

    return GameState(
        tick=data.get("tick", 0),
        time_of_day=data.get("time_of_day", 0.0),
        player_position=player.get("position", {}),
        player_health=player.get("health", 0),
        inventory=data.get("inventory", {}),
        entities=entities,
        entity_count=data.get("entity_count", 0),
        resources=data.get("resources", []),
        research_name=research.get("name", ""),
        research_progress=research.get("progress", 0.0),
        map_explored_chunks=data.get("map_explored_chunks", 0),
    )


def parse_metrics(raw_json: str) -> GameMetrics:
    """Parse raw /biged-metrics JSON into GameMetrics."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Failed to parse metrics JSON")
        return GameMetrics()

    electric = data.get("electric") or {}
    research = data.get("research") or {}

    return GameMetrics(
        tick=data.get("tick", 0),
        total_produced=data.get("total_produced", {}),
        total_consumed=data.get("total_consumed", {}),
        flow_per_minute=data.get("flow_per_minute", {}),
        electric_satisfaction=electric.get("satisfaction", ""),
        electric_capacity_mw=electric.get("capacity_mw", 0.0),
        electric_entity_count=electric.get("entity_count", 0),
        completed_research=research.get("completed", []),
        current_research=research.get("current", ""),
        current_research_progress=research.get("progress", 0.0),
    )


def state_to_markdown(state: GameState, metrics: GameMetrics | None = None) -> str:
    """Render game state as markdown for the LLM agent."""
    lines = [f"# Factory State (tick {state.tick})\n"]

    # Player
    pos = state.player_position
    lines.append(f"**Position:** ({pos.get('x', 0)}, {pos.get('y', 0)})  ")
    lines.append(f"**Health:** {state.player_health}\n")

    # Inventory
    lines.append("## Inventory")
    if state.inventory:
        for item, count in sorted(state.inventory.items()):
            lines.append(f"- {item}: {count}")
    else:
        lines.append("- (empty)")
    lines.append("")

    # Research
    if state.research_name:
        pct = int(state.research_progress * 100)
        lines.append(f"## Research\n- {state.research_name}: {pct}%\n")

    # Resources
    if state.resources:
        lines.append("## Resources Nearby")
        for r in state.resources:
            lines.append(f"- {r['name']}: {r['total_amount']:,} ({r['patches']} patches)")
        lines.append("")

    # Entities
    lines.append(f"## Entities ({state.entity_count} total)")
    # Group by type
    by_type: dict[str, list] = {}
    for e in state.entities:
        by_type.setdefault(e.type, []).append(e)
    for etype, ents in sorted(by_type.items()):
        lines.append(f"\n### {etype} ({len(ents)})")
        for e in ents[:20]:  # cap per type to avoid huge output
            pos_str = f"({e.position.get('x', 0)}, {e.position.get('y', 0)})"
            detail = f"- **{e.name}** at {pos_str}"
            if e.recipe:
                detail += f" recipe={e.recipe}"
            if e.is_crafting:
                detail += f" crafting={int(e.crafting_progress * 100)}%"
            if e.belt_contents:
                items = ", ".join(f"{k}:{v}" for k, v in e.belt_contents.items())
                detail += f" carrying=[{items}]"
            lines.append(detail)

    # Metrics
    if metrics and metrics.flow_per_minute:
        lines.append("\n## Production Flow (items/min)")
        for item, rate in sorted(metrics.flow_per_minute.items(), key=lambda x: -x[1]):
            lines.append(f"- {item}: {rate}/min")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_state_parser.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/state_parser.py tests/test_state_parser.py
git commit -m "feat(factorio): add state parser with GameState/GameMetrics dataclasses"
```

---

## Task 4: Action Translator

Convert agent action dicts (from LLM output) into RCON command strings.

**Files:**
- Create: `fleet/factorio/action_translator.py`
- Test: `tests/test_action_translator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_action_translator.py
"""Tests for action translation — agent dict → RCON command string."""
import json
import pytest

def test_translate_place():
    from factorio.action_translator import translate_action
    action = {"action": "place", "entity": "stone-furnace",
              "position": {"x": 5, "y": -3}, "direction": "south"}
    result = translate_action(action)
    assert result.rcon_command.startswith("/biged-cmd")
    payload = json.loads(result.rcon_command.split(" ", 1)[1])
    assert payload["action"] == "place"
    assert payload["entity"] == "stone-furnace"
    assert payload["direction"] == 4  # south = 4

def test_translate_craft():
    from factorio.action_translator import translate_action
    action = {"action": "craft", "recipe": "iron-gear-wheel", "count": 5}
    result = translate_action(action)
    payload = json.loads(result.rcon_command.split(" ", 1)[1])
    assert payload["recipe"] == "iron-gear-wheel"
    assert payload["count"] == 5

def test_translate_direction_names():
    from factorio.action_translator import _direction_to_int
    assert _direction_to_int("north") == 0
    assert _direction_to_int("east") == 2
    assert _direction_to_int("south") == 4
    assert _direction_to_int("west") == 6
    assert _direction_to_int(4) == 4  # pass-through int
    assert _direction_to_int(None) == 0  # default north

def test_translate_wait():
    from factorio.action_translator import translate_action
    action = {"action": "wait", "ticks": 120}
    result = translate_action(action)
    assert result.action_type == "wait"
    assert result.rcon_command is None  # wait is handled by bridge, not RCON

def test_translate_batch():
    from factorio.action_translator import translate_batch
    actions = [
        {"action": "craft", "recipe": "iron-gear-wheel", "count": 2},
        {"action": "place", "entity": "stone-furnace", "position": {"x": 0, "y": 0}},
    ]
    results = translate_batch(actions)
    assert len(results) == 2
    assert results[0].action_type == "craft"
    assert results[1].action_type == "place"

def test_translate_unknown_action():
    from factorio.action_translator import translate_action
    action = {"action": "fly_to_moon"}
    result = translate_action(action)
    assert result.action_type == "unknown"
    assert result.rcon_command is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_action_translator.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement action translator**

```python
# fleet/factorio/action_translator.py
"""Translate agent action dicts into RCON command strings."""
import json
import logging
from dataclasses import dataclass

log = logging.getLogger("biged.factorio.actions")

DIRECTION_MAP = {"north": 0, "east": 2, "south": 4, "west": 6}
KNOWN_ACTIONS = {"place", "remove", "set_recipe", "craft", "research",
                 "move", "connect", "observe", "wait"}


def _direction_to_int(d) -> int:
    """Convert direction name or int to Factorio direction int."""
    if d is None:
        return 0
    if isinstance(d, int):
        return d
    return DIRECTION_MAP.get(str(d).lower(), 0)


@dataclass
class TranslatedAction:
    """An action ready for RCON execution."""
    action_type: str
    rcon_command: str | None
    description: str


def translate_action(action: dict) -> TranslatedAction:
    """Translate a single agent action dict into RCON command."""
    action_type = action.get("action", "unknown")

    if action_type not in KNOWN_ACTIONS:
        log.warning(f"Unknown action type: {action_type}")
        return TranslatedAction(action_type="unknown", rcon_command=None,
                                description=f"Unknown: {action_type}")

    if action_type == "wait":
        ticks = action.get("ticks", 60)
        return TranslatedAction(action_type="wait", rcon_command=None,
                                description=f"Wait {ticks} ticks")

    # Build RCON payload — copy action, normalize direction
    payload = dict(action)
    if "direction" in payload:
        payload["direction"] = _direction_to_int(payload["direction"])

    # Position normalization — ensure integer grid
    for key in ("position", "from", "to"):
        if key in payload and isinstance(payload[key], dict):
            pos = payload[key]
            pos["x"] = int(round(pos.get("x", 0)))
            pos["y"] = int(round(pos.get("y", 0)))

    cmd_json = json.dumps(payload, separators=(",", ":"))
    desc = _describe_action(action_type, action)

    return TranslatedAction(
        action_type=action_type,
        rcon_command=f"/biged-cmd {cmd_json}",
        description=desc,
    )


def translate_batch(actions: list[dict]) -> list[TranslatedAction]:
    """Translate a list of agent actions."""
    return [translate_action(a) for a in actions]


def _describe_action(action_type: str, action: dict) -> str:
    """Human-readable description of an action."""
    if action_type == "place":
        ent = action.get("entity", "?")
        pos = action.get("position", {})
        return f"Place {ent} at ({pos.get('x', 0)}, {pos.get('y', 0)})"
    if action_type == "craft":
        return f"Craft {action.get('count', 1)}x {action.get('recipe', '?')}"
    if action_type == "research":
        return f"Research {action.get('technology', '?')}"
    if action_type == "move":
        pos = action.get("position", {})
        return f"Move to ({pos.get('x', 0)}, {pos.get('y', 0)})"
    if action_type == "remove":
        return f"Remove entity {action.get('unit_number', action.get('position', '?'))}"
    if action_type == "set_recipe":
        return f"Set recipe {action.get('recipe', '?')} on #{action.get('unit_number', '?')}"
    if action_type == "connect":
        return f"Connect {action.get('entity', 'belt')} from {action.get('from', '?')} to {action.get('to', '?')}"
    return action_type
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_action_translator.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/action_translator.py tests/test_action_translator.py
git commit -m "feat(factorio): add action translator with direction normalization"
```

---

## Task 5: World Model + Event Detector

Persistent in-memory state with diffing and typed event detection.

**Files:**
- Create: `fleet/factorio/world_model.py`
- Test: `tests/test_world_model.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_world_model.py
"""Tests for WorldModel — state diffing + event detection."""
import pytest
from factorio.state_parser import GameState, GameMetrics, Entity


def _make_state(tick=100, entities=None, resources=None, research_name="", inventory=None):
    return GameState(
        tick=tick,
        entities=entities or [],
        resources=resources or [],
        research_name=research_name,
        inventory=inventory or {},
    )


def test_update_tracks_entity_count():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    state = _make_state(entities=[
        Entity(name="furnace", unit_number=1),
        Entity(name="belt", unit_number=2),
    ])
    wm.update(state)
    assert wm.entity_count == 2


def test_update_detects_entity_destroyed():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    s1 = _make_state(tick=100, entities=[Entity(name="wall", unit_number=1)])
    s2 = _make_state(tick=200, entities=[])
    wm.update(s1)
    events = wm.update(s2)
    event_types = [e.event_type for e in events]
    assert "entity_destroyed" in event_types


def test_update_detects_research_complete():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    s1 = _make_state(tick=100, research_name="automation")
    wm.update(s1)
    s2 = _make_state(tick=200, research_name="logistics")
    events = wm.update(s2)
    event_types = [e.event_type for e in events]
    assert "research_complete" in event_types


def test_update_detects_resource_depleted():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    s1 = _make_state(tick=100, resources=[{"name": "iron-ore", "total_amount": 1000, "patches": 5}])
    wm.update(s1)
    s2 = _make_state(tick=200, resources=[{"name": "iron-ore", "total_amount": 200, "patches": 5}])
    events = wm.update(s2)
    event_types = [e.event_type for e in events]
    assert "resource_depleted" in event_types


def test_no_events_on_first_update():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    state = _make_state(tick=100, entities=[Entity(name="belt", unit_number=1)])
    events = wm.update(state)
    assert len(events) == 0


def test_get_snapshot_returns_copy():
    from factorio.world_model import WorldModel
    wm = WorldModel()
    wm.update(_make_state(tick=100, inventory={"iron-plate": 50}))
    snap = wm.get_snapshot()
    assert snap["tick"] == 100
    assert snap["inventory"]["iron-plate"] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_world_model.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement WorldModel**

```python
# fleet/factorio/world_model.py
"""Persistent in-memory world state with diffing and event detection."""
import logging
import threading
from dataclasses import dataclass, field

from factorio.state_parser import GameState, GameMetrics

log = logging.getLogger("biged.factorio.world")

RESOURCE_DEPLETION_THRESHOLD = 0.3  # trigger event when resource drops below 30% of first seen


@dataclass
class GameEvent:
    """A detected game event."""
    event_type: str          # resource_depleted, entity_destroyed, research_complete, etc.
    tick: int = 0
    detail: str = ""


class WorldModel:
    """Thread-safe persistent game state with diff-based event detection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: GameState | None = None
        self._prev_state: GameState | None = None
        self._metrics: GameMetrics | None = None
        self._entity_ids: set[int] = set()
        self._resource_baselines: dict[str, int] = {}  # name → first-seen amount

    @property
    def entity_count(self) -> int:
        with self._lock:
            return self._state.entity_count if self._state else 0

    def update(self, state: GameState, metrics: GameMetrics | None = None) -> list[GameEvent]:
        """Update world state, return detected events."""
        with self._lock:
            self._prev_state = self._state
            self._state = state
            if metrics:
                self._metrics = metrics

            if self._prev_state is None:
                # First update — set baselines, no events
                self._entity_ids = {e.unit_number for e in state.entities if e.unit_number}
                for r in state.resources:
                    self._resource_baselines[r["name"]] = r["total_amount"]
                return []

            events = []
            events.extend(self._detect_entity_events(state))
            events.extend(self._detect_research_events(state))
            events.extend(self._detect_resource_events(state))

            # Update entity tracking
            self._entity_ids = {e.unit_number for e in state.entities if e.unit_number}

            return events

    def _detect_entity_events(self, state: GameState) -> list[GameEvent]:
        events = []
        new_ids = {e.unit_number for e in state.entities if e.unit_number}
        destroyed = self._entity_ids - new_ids
        if destroyed:
            events.append(GameEvent(
                event_type="entity_destroyed",
                tick=state.tick,
                detail=f"{len(destroyed)} entities destroyed",
            ))
        # Detect idle assemblers
        idle_count = sum(
            1 for e in state.entities
            if e.type in ("assembling-machine", "furnace") and not e.is_crafting
        )
        if idle_count > 0:
            events.append(GameEvent(
                event_type="idle_assemblers",
                tick=state.tick,
                detail=f"{idle_count} idle",
            ))
        return events

    def _detect_research_events(self, state: GameState) -> list[GameEvent]:
        if (self._prev_state.research_name
                and state.research_name != self._prev_state.research_name):
            return [GameEvent(
                event_type="research_complete",
                tick=state.tick,
                detail=f"Completed: {self._prev_state.research_name}",
            )]
        return []

    def _detect_resource_events(self, state: GameState) -> list[GameEvent]:
        events = []
        for r in state.resources:
            name = r["name"]
            amount = r["total_amount"]
            baseline = self._resource_baselines.get(name)
            if baseline and amount < baseline * RESOURCE_DEPLETION_THRESHOLD:
                events.append(GameEvent(
                    event_type="resource_depleted",
                    tick=state.tick,
                    detail=f"{name}: {amount}/{baseline}",
                ))
            # Update baseline if this is a new resource
            if name not in self._resource_baselines:
                self._resource_baselines[name] = amount
        return events

    def get_snapshot(self) -> dict:
        """Return a serializable snapshot of current state for remote workers."""
        with self._lock:
            if not self._state:
                return {"tick": 0, "entities": [], "inventory": {}, "resources": []}
            return {
                "tick": self._state.tick,
                "player_position": self._state.player_position,
                "inventory": dict(self._state.inventory),
                "entity_count": self._state.entity_count,
                "entities": [
                    {"name": e.name, "type": e.type, "position": e.position,
                     "unit_number": e.unit_number, "recipe": e.recipe,
                     "is_crafting": e.is_crafting}
                    for e in self._state.entities
                ],
                "resources": list(self._state.resources),
                "research_name": self._state.research_name,
                "research_progress": self._state.research_progress,
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_world_model.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/world_model.py tests/test_world_model.py
git commit -m "feat(factorio): add WorldModel with diff-based event detection"
```

---

## Task 6: Cadence Controller

Manages tick interval with 4 modes and adaptive boost/decay.

**Files:**
- Create: `fleet/factorio/cadence.py`
- Test: `tests/test_cadence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cadence.py
"""Tests for CadenceController — mode switching + adaptive boost."""
import time
import pytest


def test_fixed_modes():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    cc.set_mode("fast")
    assert cc.get_interval_secs() == 1.0
    cc.set_mode("medium")
    assert cc.get_interval_secs() == 5.0
    cc.set_mode("slow")
    assert cc.get_interval_secs() == 30.0


def test_adaptive_default_is_slow():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    cc.set_mode("adaptive")
    assert cc.get_interval_secs() == 30.0  # baseline = slow


def test_adaptive_boost_on_event():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    cc.set_mode("adaptive")
    cc.on_event("entity_destroyed")
    assert cc.get_interval_secs() == 1.5  # boosted


def test_adaptive_ignores_unknown_events():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30,
                           adaptive_events=["research_complete"])
    cc.set_mode("adaptive")
    cc.on_event("entity_destroyed")  # not in allowed list
    assert cc.get_interval_secs() == 30.0  # no boost


def test_mode_rejects_invalid():
    from factorio.cadence import CadenceController
    cc = CadenceController(fast_ms=1000, medium_ms=5000, slow_ms=30000,
                           boost_ms=1500, boost_hold_secs=30)
    with pytest.raises(ValueError):
        cc.set_mode("turbo")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_cadence.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement CadenceController**

```python
# fleet/factorio/cadence.py
"""Cadence controller — manages tick interval with 4 modes."""
import time
import logging

log = logging.getLogger("biged.factorio.cadence")

VALID_MODES = {"fast", "medium", "slow", "adaptive"}
DEFAULT_ADAPTIVE_EVENTS = [
    "resource_depleted", "entity_destroyed", "research_complete",
    "power_outage", "idle_assemblers",
]


class CadenceController:
    """Controls the tick polling interval with fixed and adaptive modes."""

    def __init__(self, fast_ms: int = 1000, medium_ms: int = 5000,
                 slow_ms: int = 30000, boost_ms: int = 1500,
                 boost_hold_secs: int = 30,
                 adaptive_events: list[str] | None = None):
        self._fast = fast_ms / 1000.0
        self._medium = medium_ms / 1000.0
        self._slow = slow_ms / 1000.0
        self._boost = boost_ms / 1000.0
        self._boost_hold = boost_hold_secs
        self._adaptive_events = set(adaptive_events or DEFAULT_ADAPTIVE_EVENTS)
        self._mode = "adaptive"
        self._boost_until: float = 0.0  # timestamp when boost expires
        self._decay_until: float = 0.0  # timestamp when decay to slow

    def set_mode(self, mode: str) -> None:
        """Set cadence mode. Raises ValueError for invalid modes."""
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid cadence mode: {mode}. Must be one of {VALID_MODES}")
        self._mode = mode
        log.info(f"Cadence mode set to: {mode}")

    def get_interval_secs(self) -> float:
        """Get the current tick interval in seconds."""
        if self._mode == "fast":
            return self._fast
        if self._mode == "medium":
            return self._medium
        if self._mode == "slow":
            return self._slow

        # Adaptive mode
        now = time.monotonic()
        if now < self._boost_until:
            return self._boost
        if now < self._decay_until:
            return self._medium  # decay step: medium before returning to slow
        return self._slow

    def on_event(self, event_type: str) -> None:
        """Signal an event — may trigger cadence boost in adaptive mode."""
        if self._mode != "adaptive":
            return
        if event_type not in self._adaptive_events:
            return
        now = time.monotonic()
        self._boost_until = now + self._boost_hold
        self._decay_until = self._boost_until + self._boost_hold  # medium phase
        log.info(f"Adaptive boost triggered by {event_type}")

    @property
    def mode(self) -> str:
        return self._mode
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_cadence.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/cadence.py tests/test_cadence.py
git commit -m "feat(factorio): add CadenceController with adaptive boost/decay"
```

---

## Task 7: Curriculum Engine

Load curriculum TOMLs, evaluate success criteria, track lesson progress, handle graduation.

**Files:**
- Create: `fleet/factorio/curriculum.py`
- Create: `fleet/idle_curricula/factorio_01_bootstrap.toml`
- Test: `tests/test_curriculum.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_curriculum.py
"""Tests for curriculum engine — criteria parsing, lesson tracking, graduation."""
import pytest


def test_evaluate_simple_criteria():
    from factorio.curriculum import evaluate_criteria
    state = {"inventory": {"iron-gear-wheel": 15}, "flow": {}, "entities": {}, "production": {}}
    assert evaluate_criteria("inventory.iron-gear-wheel >= 10", state) is True
    assert evaluate_criteria("inventory.iron-gear-wheel >= 20", state) is False


def test_evaluate_and_criteria():
    from factorio.curriculum import evaluate_criteria
    state = {"inventory": {"iron-plate": 50}, "flow": {"iron-plate": 12.5},
             "entities": {}, "production": {}}
    assert evaluate_criteria("inventory.iron-plate >= 20 AND flow.iron-plate > 5", state) is True
    assert evaluate_criteria("inventory.iron-plate >= 100 AND flow.iron-plate > 5", state) is False


def test_evaluate_missing_key_returns_false():
    from factorio.curriculum import evaluate_criteria
    state = {"inventory": {}, "flow": {}, "entities": {}, "production": {}}
    assert evaluate_criteria("inventory.iron-plate >= 10", state) is False


def test_load_curriculum():
    from factorio.curriculum import load_curriculum
    curriculum = load_curriculum("factorio_01_bootstrap")
    assert curriculum is not None
    assert len(curriculum["tasks"]) > 0
    assert curriculum["tasks"][0]["type"] == "factorio"


def test_lesson_tracker():
    from factorio.curriculum import LessonTracker
    tracker = LessonTracker(total_lessons=3)
    assert tracker.current_index == 0
    assert not tracker.all_passed
    tracker.mark_passed(0)
    assert tracker.current_index == 1
    tracker.mark_passed(1)
    tracker.mark_passed(2)
    assert tracker.all_passed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_curriculum.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Create the first curriculum TOML**

```toml
# fleet/idle_curricula/factorio_01_bootstrap.toml
[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "hand_craft"
instruction = "Craft 10 iron gear wheels from your starting inventory"
success_criteria = "inventory.iron-gear-wheel >= 10"
max_steps = 20
complexity = "simple"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "place_and_smelt"
instruction = "Place a stone furnace and smelt at least 20 iron plates from iron ore"
success_criteria = "production.iron-plate >= 20"
max_steps = 50
complexity = "simple"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "build_power"
instruction = "Build a power setup: place an offshore-pump, connect it to a boiler, connect the boiler to a steam-engine"
success_criteria = "entities.steam-engine >= 1"
max_steps = 80
complexity = "simple"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "automate_mining"
instruction = "Place at least 2 electric mining drills on an iron ore patch and belt the ore to furnaces. You need power first."
success_criteria = "entities.electric-mining-drill >= 2 AND flow.iron-plate > 0"
max_steps = 100
complexity = "simple"
```

- [ ] **Step 4: Implement curriculum engine**

```python
# fleet/factorio/curriculum.py
"""Curriculum engine — load TOMLs, evaluate criteria, track progress."""
import logging
import re
from pathlib import Path

log = logging.getLogger("biged.factorio.curriculum")

# Safe criteria parser — supports: dotted.path >= N, AND, OR
_COMPARISON_RE = re.compile(
    r"([\w.\-]+)\s*(>=|<=|>|<|==)\s*([\d.]+)"
)


def evaluate_criteria(criteria: str, state: dict) -> bool:
    """Evaluate a success criteria string against game state.

    Supports: dotted.path >= N, AND, OR connectors.
    State dict expected keys: inventory, flow, entities, production.
    """
    # Split on OR first (lower precedence)
    or_parts = [p.strip() for p in criteria.split(" OR ")]
    for or_part in or_parts:
        # Each OR branch: all AND clauses must be true
        and_parts = [p.strip() for p in or_part.split(" AND ")]
        all_true = True
        for clause in and_parts:
            if not _eval_comparison(clause, state):
                all_true = False
                break
        if all_true:
            return True
    return False


def _eval_comparison(clause: str, state: dict) -> bool:
    """Evaluate a single comparison like 'inventory.iron-plate >= 10'."""
    match = _COMPARISON_RE.match(clause.strip())
    if not match:
        log.warning(f"Could not parse criteria clause: {clause}")
        return False

    path, op, threshold_str = match.groups()
    threshold = float(threshold_str)

    # Resolve dotted path against state dict
    value = _resolve_path(path, state)
    if value is None:
        return False

    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == "==":
        return value == threshold
    return False


def _resolve_path(path: str, state: dict):
    """Resolve 'inventory.iron-plate' against state dict."""
    parts = path.split(".", 1)
    if len(parts) == 1:
        return state.get(parts[0])

    section, key = parts
    sub = state.get(section)
    if isinstance(sub, dict):
        return sub.get(key, 0)
    return None


def load_curriculum(name: str, curriculum_dir: str = "fleet/idle_curricula") -> dict | None:
    """Load a curriculum TOML by name (without .toml extension)."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    path = Path(curriculum_dir) / f"{name}.toml"
    if not path.exists():
        log.warning(f"Curriculum not found: {path}")
        return None

    with open(path, "rb") as f:
        return tomllib.load(f)


class LessonTracker:
    """Track lesson pass/fail state for a curriculum phase."""

    def __init__(self, total_lessons: int):
        self._passed = [False] * total_lessons
        self._attempts = [0] * total_lessons

    @property
    def current_index(self) -> int:
        """Index of the next unfinished lesson."""
        for i, p in enumerate(self._passed):
            if not p:
                return i
        return len(self._passed)

    @property
    def all_passed(self) -> bool:
        return all(self._passed)

    def mark_passed(self, index: int) -> None:
        if 0 <= index < len(self._passed):
            self._passed[index] = True

    def mark_attempt(self, index: int) -> None:
        if 0 <= index < len(self._attempts):
            self._attempts[index] += 1

    def get_progress(self) -> dict:
        return {
            "total": len(self._passed),
            "completed": sum(self._passed),
            "current": self.current_index,
            "attempts": list(self._attempts),
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_curriculum.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/factorio/curriculum.py fleet/idle_curricula/factorio_01_bootstrap.toml tests/test_curriculum.py
git commit -m "feat(factorio): add curriculum engine with safe criteria parser"
```

---

## Task 8: Bridge API (localhost Flask)

The localhost-only Flask app that skills call to access the bridge.

**Files:**
- Create: `fleet/factorio/bridge_api.py`

- [ ] **Step 1: Implement bridge API**

```python
# fleet/factorio/bridge_api.py
"""Localhost-only Flask API for fleet skills to access the bridge."""
import json
import logging
import queue
from flask import Flask, jsonify, request

log = logging.getLogger("biged.factorio.api")

# Shared state — set by bridge.py before starting the API
_world_model = None
_command_queue: queue.Queue | None = None
_result_store: dict = {}  # command_id → result
_bridge_status: dict = {"running": False, "tick": 0, "cadence": "adaptive"}


def create_api(world_model, command_queue) -> Flask:
    """Create the bridge API Flask app."""
    global _world_model, _command_queue
    _world_model = world_model
    _command_queue = command_queue

    app = Flask("factorio_bridge_api")

    @app.route("/api/status")
    def api_status():
        return jsonify(_bridge_status)

    @app.route("/api/state")
    def api_state():
        if _world_model is None:
            return jsonify({"error": "WorldModel not initialized"}), 503
        return jsonify(_world_model.get_snapshot())

    @app.route("/api/command", methods=["POST"])
    def api_command():
        if _command_queue is None:
            return jsonify({"error": "CommandQueue not available"}), 503
        data = request.get_json(silent=True)
        if not data or "actions" not in data:
            return jsonify({"error": "Missing 'actions' in request body"}), 400
        cmd_id = f"cmd_{_bridge_status.get('tick', 0)}_{id(data)}"
        _command_queue.put({"id": cmd_id, "actions": data["actions"]})
        return jsonify({"queued": True, "command_id": cmd_id})

    @app.route("/api/result/<cmd_id>")
    def api_result(cmd_id):
        result = _result_store.get(cmd_id)
        if result is None:
            return jsonify({"pending": True})
        return jsonify(result)

    return app


def update_status(running: bool, tick: int, cadence: str) -> None:
    """Update bridge status (called by bridge tick loop)."""
    _bridge_status["running"] = running
    _bridge_status["tick"] = tick
    _bridge_status["cadence"] = cadence


def store_result(cmd_id: str, result: dict) -> None:
    """Store command execution result."""
    _result_store[cmd_id] = result
    # Keep only last 100 results
    if len(_result_store) > 100:
        oldest = list(_result_store.keys())[0]
        del _result_store[oldest]
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge_api.py
git commit -m "feat(factorio): add localhost Flask API for skill access"
```

---

## Task 9: Bridge Main Process

The main bridge tick loop — ties RCON, WorldModel, Cadence, CommandQueue, and API together.

**Files:**
- Create: `fleet/factorio/bridge.py`

- [ ] **Step 1: Implement the bridge**

```python
# fleet/factorio/bridge.py
"""Main Factorio bridge process — tick loop + RCON + API server."""
import asyncio
import json
import logging
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from factorio.bridge_config import load_factorio_config, BridgeConfig
from factorio.rcon_client import RCONClient
from factorio.state_parser import parse_state, parse_metrics, state_to_markdown
from factorio.action_translator import translate_batch
from factorio.world_model import WorldModel
from factorio.cadence import CadenceController
from factorio.bridge_api import create_api, update_status, store_result

log = logging.getLogger("biged.factorio.bridge")


class FactorioBridge:
    """Main bridge between BigEd fleet and Factorio headless server."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.rcon = RCONClient(
            config.rcon_host, config.rcon_port, config.rcon_password,
            timeout=config.rcon_timeout_secs,
        )
        self.world_model = WorldModel()
        self.cadence = CadenceController(
            fast_ms=config.cadence_fast_ms,
            medium_ms=config.cadence_medium_ms,
            slow_ms=config.cadence_slow_ms,
            boost_ms=config.adaptive_boost_ms,
            boost_hold_secs=config.adaptive_boost_hold_secs,
            adaptive_events=config.adaptive_events,
        )
        self.cadence.set_mode(config.cadence)
        self.command_queue: queue.Queue = queue.Queue()
        self._running = False
        self._consecutive_failures = 0
        self._tick_count = 0

    async def connect_with_retry(self) -> bool:
        """Connect to RCON with exponential backoff."""
        delay = 1.0
        max_delay = 30.0
        while self._running:
            try:
                await self.rcon.connect()
                self._consecutive_failures = 0
                return True
            except Exception as e:
                log.warning(f"RCON connect failed: {e}. Retrying in {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
        return False

    async def tick(self) -> None:
        """Run a single perception → action tick."""
        self._tick_count += 1

        # 1. Get state
        try:
            state_raw = await self.rcon.command("/biged-state")
            state = parse_state(state_raw)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            log.warning(f"RCON state fetch failed ({self._consecutive_failures}): {e}")
            if self._consecutive_failures >= self.config.rcon_max_retries:
                log.error("Circuit breaker tripped — pausing ticks")
                await asyncio.sleep(self.config.rcon_circuit_breaker_secs)
                self._consecutive_failures = 0
            return

        # 2. Get metrics (every 5th tick)
        metrics = None
        if self._tick_count % 5 == 0:
            try:
                metrics_raw = await self.rcon.command("/biged-metrics")
                metrics = parse_metrics(metrics_raw)
            except Exception:
                log.warning("Metrics fetch failed, skipping")

        # 3. Update world model + detect events
        events = self.world_model.update(state, metrics)
        for event in events:
            self.cadence.on_event(event.event_type)
            log.info(f"Event: {event.event_type} — {event.detail}")

        # 4. Write state file (debug/dashboard)
        try:
            md = state_to_markdown(state, metrics)
            state_path = Path(self.config.state_file)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(md, encoding="utf-8")
        except Exception:
            log.warning("Failed to write state file", exc_info=True)

        # 5. Drain command queue
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                actions = cmd.get("actions", [])
                translated = translate_batch(actions)
                results = []
                for ta in translated:
                    if ta.action_type == "wait":
                        ticks = 60
                        await asyncio.sleep(ticks / 60.0)
                        results.append({"action": "wait", "success": True})
                        continue
                    if not ta.rcon_command:
                        continue
                    try:
                        resp = await self.rcon.command(ta.rcon_command)
                        result = json.loads(resp)
                    except json.JSONDecodeError:
                        result = {"raw": resp}
                    except Exception as e:
                        result = {"error": str(e), "success": False}
                    result["description"] = ta.description
                    results.append(result)
                store_result(cmd["id"], {"results": results})
            except queue.Empty:
                break

        # 6. Update bridge status
        update_status(True, state.tick, self.cadence.mode)

    async def run(self) -> None:
        """Main loop — connect, then tick at cadence interval."""
        self._running = True
        log.info("Factorio bridge starting...")

        if not await self.connect_with_retry():
            log.error("Failed to connect to RCON, exiting")
            return

        log.info("Bridge connected, entering tick loop")
        while self._running:
            await self.tick()
            interval = self.cadence.get_interval_secs()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        """Signal the bridge to stop."""
        self._running = False


def _run_api_server(app, port: int) -> None:
    """Run Flask API in a daemon thread."""
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True,
            use_reloader=False)


def main():
    """Entry point — load config, start API thread, run bridge loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_factorio_config()
    if not config.enabled:
        log.info("Factorio bridge is disabled in fleet.toml")
        return
    if config.role != "host":
        log.info("This node is a compute-only role, not starting bridge")
        return

    bridge = FactorioBridge(config)

    # Start localhost API server
    api_app = create_api(bridge.world_model, bridge.command_queue)
    api_thread = threading.Thread(
        target=_run_api_server, args=(api_app, config.bridge_port),
        daemon=True, name="factorio-api",
    )
    api_thread.start()
    log.info(f"Bridge API running on http://127.0.0.1:{config.bridge_port}")

    # Run bridge loop
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        log.info("Bridge interrupted")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add fleet/factorio/bridge.py
git commit -m "feat(factorio): add main bridge process with tick loop and API"
```

---

## Task 10: Lua Mod Files

Copy the draft Lua mod into the package. This is the Factorio-side code.

**Files:**
- Create: `fleet/factorio/lua_mod/info.json`
- Create: `fleet/factorio/lua_mod/control.lua`

- [ ] **Step 1: Create info.json**

```json
{
    "name": "biged-bridge",
    "version": "0.1.0",
    "title": "BigEd Agent Bridge",
    "author": "BigEd",
    "description": "State serializer and command executor for BigEd AI agent bridge",
    "factorio_version": "2.0",
    "dependencies": ["base >= 2.0"]
}
```

- [ ] **Step 2: Copy control.lua from draft**

Copy the `control.lua` provided in the spec documents (the full Lua file with `/biged-state`, `/biged-cmd`, `/biged-metrics`, `/biged-observe` commands). This is the file from the user's other Claude session — copy it verbatim.

- [ ] **Step 3: Commit**

```bash
git add fleet/factorio/lua_mod/
git commit -m "feat(factorio): add Lua mod for Factorio state serialization"
```

---

## Task 11: Lua Installer

Detect Factorio install path, copy the Lua mod (manual/assisted modes).

**Files:**
- Create: `fleet/factorio/lua_installer.py`
- Test: `tests/test_lua_installer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_lua_installer.py
"""Tests for Lua mod installer — path detection + copy."""
import sys
import pytest
from pathlib import Path
from unittest.mock import patch


def test_detect_mods_dir_windows():
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    from factorio.lua_installer import detect_mods_dir
    # Should find %APPDATA%/Factorio/mods/ if Factorio is installed
    result = detect_mods_dir()
    # May be None if Factorio not installed — that's OK
    if result:
        assert "Factorio" in str(result)
        assert "mods" in str(result)


def test_detect_factorio_path_returns_none_when_not_found():
    from factorio.lua_installer import detect_factorio_path
    with patch("factorio.lua_installer._SEARCH_PATHS", []):
        result = detect_factorio_path()
        assert result is None


def test_get_lua_mod_source():
    from factorio.lua_installer import get_lua_mod_source
    src = get_lua_mod_source()
    assert src.exists()
    assert (src / "control.lua").exists()
    assert (src / "info.json").exists()


def test_install_mode_manual_returns_instructions():
    from factorio.lua_installer import install_lua_mod
    result = install_lua_mod(mode="manual")
    assert result["mode"] == "manual"
    assert "instructions" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd fleet && python -m pytest ../tests/test_lua_installer.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement Lua installer**

```python
# fleet/factorio/lua_installer.py
"""Detect Factorio install and copy the Lua mod."""
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("biged.factorio.lua_install")

_LUA_MOD_DIR = Path(__file__).parent / "lua_mod"

# Search paths for Factorio installation
_SEARCH_PATHS: list[Path] = []

if sys.platform == "win32":
    _SEARCH_PATHS = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
        / "Steam" / "steamapps" / "common" / "Factorio",
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Factorio",
    ]
    _MODS_CANDIDATES = [
        Path(os.environ.get("APPDATA", "")) / "Factorio" / "mods",
    ]
elif sys.platform == "darwin":
    _SEARCH_PATHS = [
        Path.home() / "Library" / "Application Support" / "factorio",
    ]
    _MODS_CANDIDATES = [_SEARCH_PATHS[0] / "mods"] if _SEARCH_PATHS else []
else:  # Linux
    _SEARCH_PATHS = [
        Path.home() / ".factorio",
        Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Factorio",
    ]
    _MODS_CANDIDATES = [Path.home() / ".factorio" / "mods"]


def get_lua_mod_source() -> Path:
    """Return path to the bundled Lua mod directory."""
    return _LUA_MOD_DIR


def detect_factorio_path() -> Path | None:
    """Auto-detect Factorio installation directory."""
    for p in _SEARCH_PATHS:
        if p.exists() and p.is_dir():
            return p
    return None


def detect_mods_dir() -> Path | None:
    """Auto-detect Factorio mods directory."""
    for p in _MODS_CANDIDATES:
        if p.exists() and p.is_dir():
            return p
    return None


def install_lua_mod(mode: str = "manual", mods_dir: str | None = None) -> dict:
    """Install the BigEd Lua mod into Factorio.

    Args:
        mode: "manual" (instructions only) or "assisted" (auto-copy)
        mods_dir: Override mods directory path

    Returns:
        dict with status, mode, and details
    """
    source = get_lua_mod_source()
    if not source.exists():
        return {"mode": mode, "error": "Lua mod source not found", "success": False}

    if mode == "manual":
        return {
            "mode": "manual",
            "success": True,
            "source": str(source),
            "instructions": (
                f"Copy the folder '{source}' into your Factorio mods directory.\n"
                f"Typical locations:\n"
                f"  Windows: %APPDATA%\\Factorio\\mods\\biged-bridge\\\n"
                f"  Linux:   ~/.factorio/mods/biged-bridge/\n"
                f"  macOS:   ~/Library/Application Support/factorio/mods/biged-bridge/\n"
                f"Then restart Factorio."
            ),
        }

    if mode == "assisted":
        target_dir = Path(mods_dir) if mods_dir else detect_mods_dir()
        if not target_dir:
            return {
                "mode": "assisted",
                "success": False,
                "error": "Could not detect Factorio mods directory. Set it manually.",
            }

        dest = target_dir / "biged-bridge"
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)
            log.info(f"Lua mod installed to {dest}")
            return {
                "mode": "assisted",
                "success": True,
                "installed_to": str(dest),
            }
        except Exception as e:
            return {
                "mode": "assisted",
                "success": False,
                "error": f"Copy failed: {e}",
            }

    return {"mode": mode, "error": f"Unknown mode: {mode}", "success": False}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd fleet && python -m pytest ../tests/test_lua_installer.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/factorio/lua_installer.py tests/test_lua_installer.py
git commit -m "feat(factorio): add Lua mod installer with manual/assisted modes"
```

---

## Task 12: Fleet Skills (4 skills)

Create the four skills that follow the standard BigEd skill contract.

**Files:**
- Create: `fleet/skills/factorio_observe.py`
- Create: `fleet/skills/factorio_plan.py`
- Create: `fleet/skills/factorio_act.py`
- Create: `fleet/skills/factorio_train.py`
- Modify: `fleet/providers.py` — add skills to `SKILL_COMPLEXITY` dict

- [ ] **Step 1: Create factorio_observe**

```python
# fleet/skills/factorio_observe.py
"""Read Factorio WorldModel via bridge API, return markdown observation."""
SKILL_NAME = "factorio_observe"
DESCRIPTION = "Fetch current Factorio game state from the bridge and return a markdown summary"
VERSION = "0.1.0"
REQUIRES_NETWORK = False
COMPLEXITY = "simple"
TAGS = ["factorio", "sandbox"]


def run(payload, config):
    import urllib.request
    import json
    import logging

    log = logging.getLogger("biged.skill.factorio_observe")
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    url = f"http://127.0.0.1:{bridge_port}/api/state"

    try:
        resp = urllib.request.urlopen(url, timeout=5)
        state = json.loads(resp.read())
    except Exception as e:
        log.warning(f"Bridge API unreachable: {e}")
        return {"error": f"Bridge API unreachable at {url}: {e}"}

    return {"status": "ok", "state": state, "tick": state.get("tick", 0)}
```

- [ ] **Step 2: Create factorio_plan**

```python
# fleet/skills/factorio_plan.py
"""Strategic planning for Factorio — state + context → action plan."""
SKILL_NAME = "factorio_plan"
DESCRIPTION = "Analyze Factorio game state and produce a strategic plan with concrete next actions"
VERSION = "0.1.0"
REQUIRES_NETWORK = True
COMPLEXITY = "complex"
TAGS = ["factorio", "sandbox", "planning"]


def run(payload, config):
    """Plan next actions based on current state snapshot.

    payload keys:
        state: dict — WorldModel snapshot (from factorio_observe or task payload)
        task: str — current objective/instruction
        history: list — recent action results (optional)
    """
    state = payload.get("state", {})
    task = payload.get("task", "Build a factory")
    history = payload.get("history", [])

    if not state:
        return {"error": "No game state provided in payload"}

    # Build context for LLM — the actual LLM call is handled by the worker
    # This skill prepares the prompt and returns it for the worker to execute
    return {
        "status": "ok",
        "plan_context": {
            "state": state,
            "task": task,
            "history": history[-5:],  # last 5 steps
        },
    }
```

- [ ] **Step 3: Create factorio_act**

```python
# fleet/skills/factorio_act.py
"""Submit actions to the Factorio bridge CommandQueue."""
SKILL_NAME = "factorio_act"
DESCRIPTION = "Translate a plan into Factorio actions and submit to the bridge for execution"
VERSION = "0.1.0"
REQUIRES_NETWORK = True
COMPLEXITY = "medium"
TAGS = ["factorio", "sandbox"]


def run(payload, config):
    import urllib.request
    import json
    import logging

    log = logging.getLogger("biged.skill.factorio_act")
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    actions = payload.get("actions", [])

    if not actions:
        return {"error": "No actions provided in payload"}

    url = f"http://127.0.0.1:{bridge_port}/api/command"
    body = json.dumps({"actions": actions}).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        return {"status": "ok", "queued": True, "command_id": result.get("command_id")}
    except Exception as e:
        log.warning(f"Failed to submit actions: {e}")
        return {"error": f"Bridge API error: {e}"}
```

- [ ] **Step 4: Create factorio_train**

```python
# fleet/skills/factorio_train.py
"""Curriculum evaluation — check success criteria against game state."""
SKILL_NAME = "factorio_train"
DESCRIPTION = "Evaluate Factorio curriculum lesson criteria against current game state"
VERSION = "0.1.0"
REQUIRES_NETWORK = True
COMPLEXITY = "medium"
TAGS = ["factorio", "sandbox", "training"]


def run(payload, config):
    import logging
    log = logging.getLogger("biged.skill.factorio_train")

    state = payload.get("state", {})
    criteria = payload.get("success_criteria", "")
    instruction = payload.get("instruction", "")
    name = payload.get("name", "unknown")

    if not state or not criteria:
        return {"error": "Missing state or success_criteria in payload"}

    try:
        from factorio.curriculum import evaluate_criteria
        passed = evaluate_criteria(criteria, state)
    except Exception as e:
        log.warning(f"Criteria evaluation failed: {e}")
        return {"error": f"Criteria evaluation failed: {e}"}

    return {
        "status": "ok",
        "lesson": name,
        "instruction": instruction,
        "criteria": criteria,
        "passed": passed,
    }
```

- [ ] **Step 5: Add skills to providers.py SKILL_COMPLEXITY dict**

Find the `SKILL_COMPLEXITY` dict in `fleet/providers.py` and add:
```python
"factorio_observe": "simple",
"factorio_plan": "complex",
"factorio_act": "medium",
"factorio_train": "medium",
```

- [ ] **Step 6: Commit**

```bash
git add fleet/skills/factorio_observe.py fleet/skills/factorio_plan.py \
      fleet/skills/factorio_act.py fleet/skills/factorio_train.py \
      fleet/providers.py
git commit -m "feat(factorio): add 4 fleet skills (observe, plan, act, train)"
```

---

## Task 13: Supervisor Integration

Add bridge process management to the existing process_manager.py.

**Files:**
- Modify: `fleet/process_manager.py` — add start/monitor/shutdown for factorio bridge

- [ ] **Step 1: Add bridge spawn to process_manager.py**

In `process_manager.py`, following the Dr. Ders pattern at line ~326:

```python
# Add class variable alongside hw_supervisor_proc
factorio_bridge_proc: subprocess.Popen | None = None

def start_factorio_bridge(self) -> None:
    """Start Factorio bridge if enabled in fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        factorio_cfg = cfg.get("factorio", {})
        if not factorio_cfg.get("enabled", False):
            return
        if factorio_cfg.get("role", "host") != "host":
            return
    except Exception:
        return

    log.info("Starting Factorio bridge")
    self.factorio_bridge_proc = subprocess.Popen(
        [PYTHON, str(FLEET_DIR / "factorio" / "bridge.py")],
        cwd=str(FLEET_DIR),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
```

- [ ] **Step 2: Add check_alive for bridge**

In the existing `check_alive()` method, add alongside the Dr. Ders check:

```python
if self.factorio_bridge_proc and self.factorio_bridge_proc.poll() is not None:
    log.warning("Factorio bridge crashed — respawning")
    self.start_factorio_bridge()
```

- [ ] **Step 3: Add shutdown for bridge**

In the existing `shutdown_all()` method, add alongside the Dr. Ders shutdown:

```python
if self.factorio_bridge_proc and self.factorio_bridge_proc.poll() is None:
    self.factorio_bridge_proc.terminate()
    try:
        self.factorio_bridge_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        self.factorio_bridge_proc.kill()
```

- [ ] **Step 4: Commit**

```bash
git add fleet/process_manager.py
git commit -m "feat(factorio): integrate bridge process into supervisor lifecycle"
```

---

## Task 14: Remaining Curricula

Create the Phase 2-4 curriculum TOMLs.

**Files:**
- Create: `fleet/idle_curricula/factorio_02_goals.toml`
- Create: `fleet/idle_curricula/factorio_03_kpis.toml`
- Create: `fleet/idle_curricula/factorio_04_survival.toml`

- [ ] **Step 1: Create Phase 2 goals curriculum**

```toml
# fleet/idle_curricula/factorio_02_goals.toml
[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "automate_red_science"
instruction = "Build a complete automated red science production line: mine iron+copper, smelt, assemble gears+science packs, feed to lab, start automation research"
success_criteria = "flow.automation-science-pack > 0"
max_steps = 200
complexity = "medium"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "green_science"
instruction = "Add green science (logistic-science-pack) production. Requires electronic circuits (copper wire + iron plate) and transport belts."
success_criteria = "flow.logistic-science-pack > 0"
max_steps = 300
complexity = "medium"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "main_bus"
instruction = "Organize your factory with a main bus: parallel belts of iron plates, copper plates, gears, and green circuits. Tap off with splitters to feed assemblers."
success_criteria = "entities.transport-belt >= 50 AND entities.splitter >= 4"
max_steps = 250
complexity = "medium"
```

- [ ] **Step 2: Create Phase 3 KPIs curriculum**

```toml
# fleet/idle_curricula/factorio_03_kpis.toml
[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "iron_throughput"
instruction = "Scale iron plate production to at least 30 plates per minute. Add more miners, furnaces, and belts as needed."
success_criteria = "flow.iron-plate >= 30"
max_steps = 200
complexity = "medium"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "science_throughput"
instruction = "Produce at least 15 automation science packs per minute. Optimize your production chain — check for bottlenecks."
success_criteria = "flow.automation-science-pack >= 15"
max_steps = 300
complexity = "medium"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "minimize_idle"
instruction = "Ensure all assemblers and furnaces are actively working. Fix any stalled machines by improving input belts or balancing ratios."
success_criteria = "flow.iron-plate >= 20 AND flow.copper-plate >= 10"
max_steps = 200
complexity = "medium"
```

- [ ] **Step 3: Create Phase 4 survival curriculum**

```toml
# fleet/idle_curricula/factorio_04_survival.toml
[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "defense_perimeter"
instruction = "Build a defensive perimeter around your factory: walls + gun turrets at chokepoints. Keep turrets supplied with ammunition."
success_criteria = "entities.gun-turret >= 4 AND entities.stone-wall >= 20"
max_steps = 200
complexity = "complex"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "power_resilience"
instruction = "Ensure power stability: build redundant steam engines, maintain coal supply, add electric poles to cover all machines."
success_criteria = "entities.steam-engine >= 4 AND flow.iron-plate > 0"
max_steps = 200
complexity = "complex"

[[tasks]]
type = "factorio"
skill = "factorio_train"

[tasks.payload]
name = "expand_resources"
instruction = "Your nearby iron is running low. Scout for a new iron ore patch, build miners there, and belt or train the ore back to your smelting area."
success_criteria = "entities.electric-mining-drill >= 8 AND flow.iron-plate >= 30"
max_steps = 400
complexity = "complex"
```

- [ ] **Step 4: Commit**

```bash
git add fleet/idle_curricula/factorio_02_goals.toml \
      fleet/idle_curricula/factorio_03_kpis.toml \
      fleet/idle_curricula/factorio_04_survival.toml
git commit -m "feat(factorio): add Phase 2-4 curriculum files (goals, KPIs, survival)"
```

---

## Task 15: Launcher Module Tab

The customtkinter tab UI for the Factorio module.

**Files:**
- Create: `BigEd/launcher/modules/mod_factorio.py`

- [ ] **Step 1: Implement the launcher module**

```python
# BigEd/launcher/modules/mod_factorio.py
"""Factorio Sandbox Module — launcher tab with status, cadence, curriculum."""
import json
import logging
import threading
import urllib.request

import customtkinter as ctk

log = logging.getLogger("biged.module.factorio")

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = FONT_STAT = FONT_BOLD = FONT_XS = ("Segoe UI", 10)
FLEET_DIR = None


class Module:
    NAME = "factorio"
    LABEL = "Factorio"
    VERSION = "0.1.0"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._status_lbl = None
        self._tick_lbl = None
        self._cadence_var = None
        self._phase_lbl = None

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED
        global FONT_SM, FONT_STAT, FONT_BOLD, FONT_XS, FLEET_DIR
        try:
            from ui.theme import (BG as _BG, BG2 as _BG2, BG3 as _BG3,
                                  ACCENT as _ACC, ACCENT_H as _AH, GOLD as _GOLD,
                                  TEXT as _TEXT, DIM as _DIM, GREEN as _GR,
                                  ORANGE as _OR, RED as _RED,
                                  FONT_SM as _FSM, FONT_STAT as _FST,
                                  FONT_BOLD as _FB, FONT_XS as _FXS)
            BG = _BG; BG2 = _BG2; BG3 = _BG3
            ACCENT = _ACC; ACCENT_H = _AH; GOLD = _GOLD
            TEXT = _TEXT; DIM = _DIM; GREEN = _GR; ORANGE = _OR; RED = _RED
            FONT_SM = _FSM; FONT_STAT = _FST; FONT_BOLD = _FB; FONT_XS = _FXS
        except Exception:
            pass

    def build_tab(self, parent):
        """Build the Factorio tab UI."""
        frame = ctk.CTkFrame(parent, fg_color=BG)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Header
        ctk.CTkLabel(frame, text="Factorio Sandbox", font=FONT_BOLD,
                     text_color=GOLD).pack(anchor="w", padx=10, pady=(10, 5))

        # Status panel
        status_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        status_frame.pack(fill="x", padx=10, pady=5)

        self._status_lbl = ctk.CTkLabel(status_frame, text="Bridge: Not Running",
                                        font=FONT_SM, text_color=DIM)
        self._status_lbl.pack(anchor="w", padx=10, pady=5)

        self._tick_lbl = ctk.CTkLabel(status_frame, text="Tick: —",
                                      font=FONT_XS, text_color=DIM)
        self._tick_lbl.pack(anchor="w", padx=10, pady=(0, 5))

        # Cadence control
        cadence_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        cadence_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(cadence_frame, text="Cadence", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=10, pady=(10, 0))

        self._cadence_var = ctk.StringVar(value="adaptive")
        cadence_menu = ctk.CTkOptionMenu(
            cadence_frame, values=["fast", "medium", "slow", "adaptive"],
            variable=self._cadence_var, font=FONT_SM,
            fg_color=BG3, button_color=ACCENT,
        )
        cadence_menu.pack(anchor="w", padx=10, pady=10)

        # Curriculum progress
        curriculum_frame = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=8)
        curriculum_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(curriculum_frame, text="Training Progress", font=FONT_BOLD,
                     text_color=TEXT).pack(anchor="w", padx=10, pady=(10, 0))

        self._phase_lbl = ctk.CTkLabel(curriculum_frame,
                                       text="Phase 1: Curriculum — Not started",
                                       font=FONT_SM, text_color=DIM)
        self._phase_lbl.pack(anchor="w", padx=10, pady=10)

    def on_refresh(self):
        """Poll bridge API for status updates."""
        try:
            import config as fleet_config
            cfg = fleet_config.load_config()
            port = cfg.get("factorio", {}).get("bridge_port", 27016)
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=2
            )
            data = json.loads(resp.read())
            if self._status_lbl:
                running = data.get("running", False)
                color = GREEN if running else RED
                self._status_lbl.configure(
                    text=f"Bridge: {'Running' if running else 'Stopped'}",
                    text_color=color,
                )
            if self._tick_lbl:
                self._tick_lbl.configure(text=f"Tick: {data.get('tick', '—')}")
        except Exception:
            if self._status_lbl:
                self._status_lbl.configure(text="Bridge: Not Running",
                                           text_color=DIM)

    def on_close(self):
        """Clean up."""
        pass
```

- [ ] **Step 2: Commit**

```bash
git add BigEd/launcher/modules/mod_factorio.py
git commit -m "feat(factorio): add launcher tab module with status and cadence UI"
```

---

## Task 16: Smoke Test

Add a basic integration check to verify the module loads and the bridge config parses.

**Files:**
- No new files — run existing test suite + manual verification

- [ ] **Step 1: Run all Factorio tests**

```bash
cd fleet && python -m pytest ../tests/test_rcon_client.py ../tests/test_bridge_config.py \
  ../tests/test_state_parser.py ../tests/test_action_translator.py \
  ../tests/test_world_model.py ../tests/test_cadence.py \
  ../tests/test_curriculum.py ../tests/test_lua_installer.py -v
```

Expected: All tests PASS

- [ ] **Step 2: Run existing smoke tests to ensure no regressions**

```bash
cd fleet && python smoke_test.py --fast
```

Expected: 33/33 PASS (or current baseline)

- [ ] **Step 3: Verify skill contract compliance**

```bash
cd fleet && python -c "
import importlib, sys
sys.path.insert(0, '.')
for skill in ['factorio_observe', 'factorio_plan', 'factorio_act', 'factorio_train']:
    mod = importlib.import_module(f'skills.{skill}')
    from skills._contract import validate_skill
    warnings = validate_skill(mod)
    status = 'PASS' if not warnings else f'FAIL: {warnings}'
    print(f'{skill}: {status}')
"
```

Expected: All 4 skills PASS

- [ ] **Step 4: Verify fleet.toml parses with new [factorio] section**

```bash
cd fleet && python -c "from config import load_config; c = load_config(); print('factorio enabled:', c.get('factorio', {}).get('enabled', 'MISSING'))"
```

Expected: `factorio enabled: False`

- [ ] **Step 5: Final commit with all files**

```bash
git add -A
git status  # verify no unintended files
git commit -m "feat(factorio): complete Factorio sandbox module v0.1.0

16 new files: RCON client, state parser, action translator, world model,
cadence controller, curriculum engine, bridge process, bridge API,
Lua mod, Lua installer, 4 fleet skills, launcher tab module,
4 curriculum phases. All tests passing."
```
