#!/usr/bin/env python3
"""Fleet Mesh Auto-Discovery — automatic peer detection on local network.

Two discovery mechanisms:
1. UDP Broadcast — sends JSON beacons every 30s on port 5556
2. mDNS/DNS-SD  — registers _biged-fleet._tcp.local. (optional, requires zeroconf)

Peers are auto-discovered and merged with manually configured peers from fleet.toml.
Stale peers (no beacon for 90s) are automatically removed.

Usage:
    from discovery import start_discovery, stop_discovery, get_all_peers
    start_discovery(port=5555)
    peers = get_all_peers()
    stop_discovery()
"""

import hashlib
import json
import logging
import platform
import socket
import threading
import time
from pathlib import Path

log = logging.getLogger("discovery")

FLEET_DIR = Path(__file__).parent

# Defaults (overridden by fleet.toml [federation])
DISCOVERY_PORT = 5556
BROADCAST_INTERVAL = 30   # seconds between beacon sends
PEER_TTL = 90             # remove peer if no beacon for this long
BEACON_MAGIC = "BIGED-FLEET-V1"

# Module-level state
_discovered_peers: dict[str, dict] = {}
_peers_lock = threading.Lock()
_broadcast_thread: threading.Thread | None = None
_listener_thread: threading.Thread | None = None
_mdns_registered = False
_running = False
_fleet_id: str = ""
_local_port: int = 5555
_discovery_port: int = DISCOVERY_PORT


def _generate_fleet_id() -> str:
    """Generate a stable fleet_id from hostname + fleet.toml hash.

    Stable across restarts but unique per machine/config combination.
    """
    hostname = platform.node() or "unknown"
    toml_path = FLEET_DIR / "fleet.toml"
    toml_hash = ""
    try:
        content = toml_path.read_bytes()
        toml_hash = hashlib.sha256(content).hexdigest()[:12]
    except Exception:
        toml_hash = "no-config"
    return f"{hostname}-{toml_hash}"


def _build_beacon(port: int) -> bytes:
    """Build the UDP beacon payload."""
    agent_count = 0
    pending_count = 0
    try:
        import db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM agents WHERE status != 'OFFLINE'"
            ).fetchone()
            agent_count = row["n"] if row else 0
            row = conn.execute(
                "SELECT COUNT(*) as n FROM tasks WHERE status = 'PENDING'"
            ).fetchone()
            pending_count = row["n"] if row else 0
    except Exception:
        pass

    beacon = {
        "magic": BEACON_MAGIC,
        "fleet_id": _fleet_id,
        "host": _get_local_ip(),
        "port": port,
        "version": "0.100.00b",
        "agents": agent_count,
        "pending": pending_count,
        "capacity": {
            "max_workers": _get_max_workers(),
        },
        "timestamp": time.time(),
    }
    return json.dumps(beacon).encode("utf-8")


def _get_local_ip() -> str:
    """Get the best local IP address for peer communication."""
    try:
        # Connect to a public DNS to find our LAN-facing IP (no data sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_max_workers() -> int:
    """Read max_workers from config."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("fleet", {}).get("max_workers", 10)
    except Exception:
        return 10


def _broadcast_loop(port: int, discovery_port: int) -> None:
    """Send UDP broadcast beacons at regular intervals."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(2)
    except Exception:
        log.warning("Discovery: failed to create broadcast socket", exc_info=True)
        return

    log.info(f"Discovery: broadcast sender started on UDP port {discovery_port}")
    try:
        while _running:
            try:
                beacon = _build_beacon(port)
                sock.sendto(beacon, ("<broadcast>", discovery_port))
                log.debug("Discovery: beacon sent")
            except Exception:
                log.debug("Discovery: beacon send failed", exc_info=True)

            # Sleep in short intervals so we can stop quickly
            for _ in range(BROADCAST_INTERVAL * 2):
                if not _running:
                    break
                time.sleep(0.5)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _listener_loop(discovery_port: int) -> None:
    """Listen for UDP broadcast beacons from other fleets."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEPORT not available on Windows
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except Exception:
                pass
        sock.bind(("", discovery_port))
        sock.settimeout(2)  # non-blocking with timeout
    except Exception:
        log.warning(f"Discovery: failed to bind listener on UDP port {discovery_port}", exc_info=True)
        return

    log.info(f"Discovery: listener started on UDP port {discovery_port}")
    try:
        while _running:
            try:
                data, addr = sock.recvfrom(4096)
                _handle_beacon(data, addr)
            except socket.timeout:
                # Expected — lets us check _running flag
                pass
            except Exception:
                log.debug("Discovery: listener recv error", exc_info=True)

            # Prune stale peers
            _prune_stale_peers()
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _handle_beacon(data: bytes, addr: tuple) -> None:
    """Process a received beacon from another fleet."""
    try:
        beacon = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    # Validate beacon format
    if beacon.get("magic") != BEACON_MAGIC:
        return

    peer_id = beacon.get("fleet_id", "")
    if not peer_id:
        return

    # Ignore our own beacons
    if peer_id == _fleet_id:
        return

    host = beacon.get("host", addr[0])
    port = beacon.get("port", 5555)

    peer_info = {
        "fleet_id": peer_id,
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "version": beacon.get("version", "unknown"),
        "agents": beacon.get("agents", 0),
        "pending": beacon.get("pending", 0),
        "capacity": beacon.get("capacity", {}),
        "last_seen": time.time(),
        "source": "broadcast",
    }

    with _peers_lock:
        existing = _discovered_peers.get(peer_id)
        if existing:
            log.debug(f"Discovery: updated peer {peer_id} at {host}:{port}")
        else:
            log.info(f"Discovery: new peer found — {peer_id} at {host}:{port}")
        _discovered_peers[peer_id] = peer_info


def _prune_stale_peers() -> None:
    """Remove peers that haven't sent a beacon within TTL."""
    now = time.time()
    with _peers_lock:
        stale = [pid for pid, info in _discovered_peers.items()
                 if now - info.get("last_seen", 0) > PEER_TTL]
        for pid in stale:
            log.info(f"Discovery: peer {pid} timed out (no beacon for {PEER_TTL}s)")
            del _discovered_peers[pid]


# ── mDNS/DNS-SD Discovery (optional) ─────────────────────────────────────────

def _start_mdns(port: int) -> bool:
    """Register and browse for _biged-fleet._tcp.local. services.

    Returns True if mDNS started successfully, False if zeroconf unavailable.
    """
    global _mdns_registered
    try:
        from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
    except ImportError:
        log.info("Discovery: zeroconf not installed — mDNS disabled (pip install zeroconf)")
        return False

    try:
        zc = Zeroconf()
        local_ip = _get_local_ip()

        # Register our service
        info = ServiceInfo(
            "_biged-fleet._tcp.local.",
            f"{_fleet_id}._biged-fleet._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties={
                "fleet_id": _fleet_id,
                "version": "0.100.00b",
            },
        )
        zc.register_service(info)
        _mdns_registered = True
        log.info(f"Discovery: mDNS service registered — {_fleet_id} at {local_ip}:{port}")

        class _Listener:
            def add_service(self, zc_ref, type_, name):
                try:
                    sinfo = zc_ref.get_service_info(type_, name)
                    if sinfo is None:
                        return
                    props = {k.decode(): v.decode() if isinstance(v, bytes) else v
                             for k, v in sinfo.properties.items()}
                    peer_id = props.get("fleet_id", name)
                    if peer_id == _fleet_id:
                        return
                    addresses = sinfo.parsed_addresses()
                    if not addresses:
                        return
                    host = addresses[0]
                    peer_port = sinfo.port
                    peer_info = {
                        "fleet_id": peer_id,
                        "host": host,
                        "port": peer_port,
                        "url": f"http://{host}:{peer_port}",
                        "version": props.get("version", "unknown"),
                        "agents": 0,
                        "pending": 0,
                        "capacity": {},
                        "last_seen": time.time(),
                        "source": "mdns",
                    }
                    with _peers_lock:
                        if peer_id not in _discovered_peers:
                            log.info(f"Discovery: mDNS peer found — {peer_id} at {host}:{peer_port}")
                        _discovered_peers[peer_id] = peer_info
                except Exception:
                    log.debug("Discovery: mDNS add_service error", exc_info=True)

            def remove_service(self, zc_ref, type_, name):
                # Let TTL handle removal — mDNS remove can be premature
                pass

            def update_service(self, zc_ref, type_, name):
                self.add_service(zc_ref, type_, name)

        ServiceBrowser(zc, "_biged-fleet._tcp.local.", _Listener())
        log.info("Discovery: mDNS browser started")

        # Store zc reference for cleanup
        _start_mdns._zc = zc
        _start_mdns._info = info
        return True
    except Exception:
        log.warning("Discovery: mDNS registration failed", exc_info=True)
        return False


def _stop_mdns() -> None:
    """Unregister mDNS service and close zeroconf."""
    global _mdns_registered
    if not _mdns_registered:
        return
    try:
        zc = getattr(_start_mdns, "_zc", None)
        info = getattr(_start_mdns, "_info", None)
        if zc and info:
            zc.unregister_service(info)
            zc.close()
        _mdns_registered = False
        log.info("Discovery: mDNS service unregistered")
    except Exception:
        log.debug("Discovery: mDNS cleanup error", exc_info=True)


# ── Public API ────────────────────────────────────────────────────────────────

def start_discovery(port: int = 5555) -> None:
    """Start auto-discovery (broadcast + optional mDNS).

    Args:
        port: The dashboard/API port this fleet listens on (advertised to peers).
    """
    global _running, _broadcast_thread, _listener_thread, _fleet_id
    global _local_port, _discovery_port

    if _running:
        log.warning("Discovery: already running")
        return

    _fleet_id = _generate_fleet_id()
    _local_port = port

    # Load discovery config from fleet.toml
    try:
        from config import load_config
        cfg = load_config()
        fed_cfg = cfg.get("federation", {})
        _discovery_port = fed_cfg.get("discovery_port", DISCOVERY_PORT)
        method = fed_cfg.get("discovery_method", "broadcast")
    except Exception:
        _discovery_port = DISCOVERY_PORT
        method = "broadcast"

    _running = True
    log.info(f"Discovery: starting (fleet_id={_fleet_id}, method={method}, "
             f"discovery_port={_discovery_port}, fleet_port={port})")

    # Always start UDP broadcast (primary mechanism)
    if method in ("broadcast", "both"):
        _broadcast_thread = threading.Thread(
            target=_broadcast_loop, args=(port, _discovery_port),
            name="discovery-broadcast", daemon=True)
        _broadcast_thread.start()

        _listener_thread = threading.Thread(
            target=_listener_loop, args=(_discovery_port,),
            name="discovery-listener", daemon=True)
        _listener_thread.start()

    # Optionally start mDNS
    if method in ("mdns", "both"):
        _start_mdns(port)


def stop_discovery() -> None:
    """Stop all discovery mechanisms and clean up."""
    global _running, _broadcast_thread, _listener_thread

    if not _running:
        return

    log.info("Discovery: stopping...")
    _running = False

    # Stop mDNS
    _stop_mdns()

    # Threads are daemon threads — they'll stop when _running=False
    # Wait briefly for clean exit
    if _broadcast_thread and _broadcast_thread.is_alive():
        _broadcast_thread.join(timeout=3)
    if _listener_thread and _listener_thread.is_alive():
        _listener_thread.join(timeout=3)

    _broadcast_thread = None
    _listener_thread = None

    with _peers_lock:
        _discovered_peers.clear()

    log.info("Discovery: stopped")


def get_discovered_peers() -> list[dict]:
    """Return currently discovered peers (auto-discovered only).

    Each peer dict contains:
        fleet_id, host, port, url, version, agents, pending, capacity,
        last_seen, source ("broadcast" or "mdns"), online (bool)
    """
    now = time.time()
    with _peers_lock:
        return [
            {**info, "online": now - info.get("last_seen", 0) < PEER_TTL}
            for info in _discovered_peers.values()
        ]


def get_all_peers() -> list[dict]:
    """Return all peers: auto-discovered + manually configured from fleet.toml.

    Manually configured peers are included with source="config".
    Deduplication: if a discovered peer matches a configured URL, the discovered
    info takes precedence (richer data).
    """
    # Start with discovered peers
    peers_by_url: dict[str, dict] = {}
    for peer in get_discovered_peers():
        peers_by_url[peer["url"]] = peer

    # Add manually configured peers (don't overwrite discovered)
    try:
        from config import load_config
        cfg = load_config()
        manual_peers = cfg.get("federation", {}).get("peers", [])
        for peer_url in manual_peers:
            url = peer_url.rstrip("/")
            if url not in peers_by_url:
                peers_by_url[url] = {
                    "fleet_id": "manual",
                    "host": _extract_host(url),
                    "port": _extract_port(url),
                    "url": url,
                    "version": "unknown",
                    "agents": 0,
                    "pending": 0,
                    "capacity": {},
                    "last_seen": 0,
                    "source": "config",
                    "online": False,  # unknown until heartbeat received
                }
    except Exception:
        log.debug("Discovery: failed to load manual peers from config", exc_info=True)

    return list(peers_by_url.values())


def _extract_host(url: str) -> str:
    """Extract host from a URL like http://192.168.1.50:5555."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname or "unknown"
    except Exception:
        return "unknown"


def _extract_port(url: str) -> int:
    """Extract port from a URL like http://192.168.1.50:5555."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.port or 5555
    except Exception:
        return 5555
