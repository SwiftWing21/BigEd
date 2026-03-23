"""Geo-Distributed Fleets + Auto-Scaling Infrastructure (v0.400.00b).

Manages geographic fleet placement, latency-based routing, per-region
auto-scaling (backed by the ML predictive scaler), and CDN distribution
points for skill/model packages.

DB tables: fleet_regions, scaling_config, scaling_events, cdn_endpoints.

Usage:
    from geo_fleet import register_region, list_regions, get_nearest_region
    register_region("us-east-1", "https://fleet-east.example.com:5555", {"max_agents": 20})
    regions = list_regions()
    best = get_nearest_region("10.10.1.42")
"""

import json
import logging
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("geo_fleet")

FLEET_DIR = Path(__file__).parent

# ── DB Schema ────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fleet_regions (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    capacity_json   TEXT,
    status          TEXT DEFAULT 'active',
    created_at      REAL,
    last_heartbeat  REAL
);

CREATE TABLE IF NOT EXISTS scaling_config (
    region_id           TEXT PRIMARY KEY,
    min_agents          INTEGER DEFAULT 2,
    max_agents          INTEGER DEFAULT 20,
    target_utilization  REAL DEFAULT 0.7,
    FOREIGN KEY (region_id) REFERENCES fleet_regions(id)
);

CREATE TABLE IF NOT EXISTS scaling_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id   TEXT NOT NULL,
    ts          REAL NOT NULL,
    action      TEXT NOT NULL,
    prev_agents INTEGER,
    new_agents  INTEGER,
    reason      TEXT,
    FOREIGN KEY (region_id) REFERENCES fleet_regions(id)
);
CREATE INDEX IF NOT EXISTS idx_se_region_ts ON scaling_events(region_id, ts);

CREATE TABLE IF NOT EXISTS cdn_endpoints (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    region_id   TEXT NOT NULL,
    status      TEXT DEFAULT 'active',
    created_at  REAL,
    FOREIGN KEY (region_id) REFERENCES fleet_regions(id)
);
"""

_schema_lock = threading.Lock()
_schema_ready = False


def _ensure_schema():
    """Create geo-fleet tables if they don't exist."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        import db
        def _do():
            with db.get_conn() as conn:
                conn.executescript(_SCHEMA_SQL)
        db._retry_write(_do)
        _schema_ready = True


# ── Config ───────────────────────────────────────────────────────────────────

def _load_geo_config() -> dict:
    """Load [geo] section from fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("geo", {})
    except Exception:
        log.warning("geo_fleet: failed to load config", exc_info=True)
        return {}


# ── Simple IP-prefix to region mapping ───────────────────────────────────────
# Production deployments override via fleet.toml [geo.ip_prefixes] or an
# external GeoIP database.  This built-in mapping covers common RFC-1918
# ranges and a handful of well-known public prefixes for demo purposes.

_DEFAULT_IP_PREFIXES: dict[str, str] = {
    "10.": "local",
    "172.": "local",
    "192.168.": "local",
    "127.": "local",
}


def _get_ip_prefix_map() -> dict[str, str]:
    """Return IP-prefix -> region mapping from config (or defaults)."""
    geo = _load_geo_config()
    custom = geo.get("ip_prefixes", {})
    if custom:
        return {str(k): str(v) for k, v in custom.items()}
    return dict(_DEFAULT_IP_PREFIXES)


# ── Region Management ────────────────────────────────────────────────────────

def register_region(name: str, endpoint: str, capacity: dict | None = None) -> dict:
    """Register a new fleet region (or update an existing one by name).

    Args:
        name: human-readable region name (e.g. "us-east-1")
        endpoint: base URL of the fleet dashboard in that region
        capacity: optional dict with max_agents, current_agents, etc.

    Returns:
        Dict with region id, name, endpoint, status.
    """
    _ensure_schema()
    import db

    region_id = None
    # Check for existing region with same name
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM fleet_regions WHERE name = ?", (name,)
        ).fetchone()
        if row:
            region_id = row[0] if isinstance(row, (list, tuple)) else row["id"]

    if region_id is None:
        region_id = str(uuid.uuid4())[:12]

    now = time.time()
    cap_json = json.dumps(capacity or {})

    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO fleet_regions (id, name, endpoint, capacity_json, status, created_at, last_heartbeat) "
                "VALUES (?, ?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET endpoint=excluded.endpoint, "
                "capacity_json=excluded.capacity_json, last_heartbeat=excluded.last_heartbeat",
                (region_id, name, endpoint, cap_json, now, now),
            )
    db._retry_write(_do)

    log.info("geo_fleet: registered region %s (%s) at %s", region_id, name, endpoint)
    return {"id": region_id, "name": name, "endpoint": endpoint, "status": "active"}


def list_regions() -> list[dict]:
    """Return all registered regions with health/capacity info."""
    _ensure_schema()
    import db

    now = time.time()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT r.id, r.name, r.endpoint, r.capacity_json, r.status, "
            "r.created_at, r.last_heartbeat, "
            "s.min_agents, s.max_agents, s.target_utilization "
            "FROM fleet_regions r "
            "LEFT JOIN scaling_config s ON r.id = s.region_id "
            "ORDER BY r.name"
        ).fetchall()

    regions = []
    for row in rows:
        r = dict(row)
        try:
            r["capacity"] = json.loads(r.pop("capacity_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            r["capacity"] = {}
        heartbeat = r.get("last_heartbeat") or 0
        r["online"] = (now - heartbeat) < 120
        r["heartbeat_age_s"] = round(now - heartbeat, 1) if heartbeat else None
        regions.append(r)
    return regions


def get_region(region_id: str) -> dict | None:
    """Get a single region by ID."""
    _ensure_schema()
    import db

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, endpoint, capacity_json, status, created_at, last_heartbeat "
            "FROM fleet_regions WHERE id = ?", (region_id,)
        ).fetchone()
    if not row:
        return None
    r = dict(row)
    try:
        r["capacity"] = json.loads(r.pop("capacity_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        r["capacity"] = {}
    now = time.time()
    heartbeat = r.get("last_heartbeat") or 0
    r["online"] = (now - heartbeat) < 120
    return r


def update_region_heartbeat(region_id: str, capacity: dict | None = None):
    """Update heartbeat timestamp (and optionally capacity) for a region."""
    _ensure_schema()
    import db

    now = time.time()
    def _do():
        with db.get_conn() as conn:
            if capacity is not None:
                conn.execute(
                    "UPDATE fleet_regions SET last_heartbeat = ?, capacity_json = ? WHERE id = ?",
                    (now, json.dumps(capacity), region_id),
                )
            else:
                conn.execute(
                    "UPDATE fleet_regions SET last_heartbeat = ? WHERE id = ?",
                    (now, region_id),
                )
    db._retry_write(_do)


def get_nearest_region(client_ip: str) -> str:
    """Return the best region for a given client IP using prefix mapping.

    Falls back to the default_region from [geo] config when no prefix matches.
    """
    _ensure_schema()
    prefix_map = _get_ip_prefix_map()

    # Longest-prefix match
    best_match = ""
    best_region = ""
    for prefix, region_name in prefix_map.items():
        if client_ip.startswith(prefix) and len(prefix) > len(best_match):
            best_match = prefix
            best_region = region_name

    if best_region:
        return best_region

    geo = _load_geo_config()
    return geo.get("default_region", "local")


def route_to_region(task: dict, preferred_region: str) -> dict:
    """Route a task to a specific region's fleet endpoint.

    Args:
        task: dict with type, payload, priority keys
        preferred_region: region name or ID to route to

    Returns:
        Dict with ok, task_id (or error), region info.
    """
    _ensure_schema()
    import db

    # Find the region
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, endpoint, status FROM fleet_regions "
            "WHERE id = ? OR name = ?", (preferred_region, preferred_region)
        ).fetchone()

    if not row:
        return {"ok": False, "error": f"Region not found: {preferred_region}"}

    region = dict(row)
    if region["status"] != "active":
        return {"ok": False, "error": f"Region {region['name']} is {region['status']}"}

    endpoint = region["endpoint"].rstrip("/")
    body = json.dumps({
        "type": task.get("type", ""),
        "payload": task.get("payload", {}),
        "priority": task.get("priority", 5),
        "source": "geo_routing",
    }).encode()

    try:
        import urllib.request
        req = urllib.request.Request(
            f"{endpoint}/api/trigger",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return {
            "ok": True,
            "task_id": result.get("task_id"),
            "region": region["name"],
            "endpoint": endpoint,
        }
    except Exception as exc:
        log.warning("geo_fleet: failed to route to region %s: %s", region["name"], exc)
        return {"ok": False, "error": str(exc), "region": region["name"]}


# ── Auto-Scaling ─────────────────────────────────────────────────────────────

def get_scaling_recommendation() -> dict:
    """Per-region scaling advice using the ML predictive scaler.

    Queries each active region's capacity and returns a recommendation
    dict keyed by region ID.
    """
    _ensure_schema()
    import db

    regions = list_regions()
    recommendations = {}

    for region in regions:
        if region.get("status") != "active":
            continue

        rid = region["id"]
        cap = region.get("capacity", {})
        current_agents = cap.get("current_agents", 0)
        pending = cap.get("pending_tasks", 0)

        # Use predictive scaler for recommendation
        try:
            from predictive_scaler import predict_optimal_agents
            optimal = predict_optimal_agents(pending, current_agents)
        except Exception:
            log.debug("geo_fleet: ML predictor unavailable, using heuristic")
            optimal = max(2, current_agents + (pending // 2))

        # Clamp to HPA bounds
        with db.get_conn() as conn:
            sc = conn.execute(
                "SELECT min_agents, max_agents, target_utilization FROM scaling_config WHERE region_id = ?",
                (rid,),
            ).fetchone()

        min_a = sc["min_agents"] if sc else 2
        max_a = sc["max_agents"] if sc else 20
        target_util = sc["target_utilization"] if sc else 0.7
        clamped = max(min_a, min(max_a, optimal))

        if clamped > current_agents:
            action = "scale_up"
        elif clamped < current_agents:
            action = "scale_down"
        else:
            action = "hold"

        recommendations[rid] = {
            "region": region["name"],
            "current_agents": current_agents,
            "optimal_agents": optimal,
            "clamped_target": clamped,
            "action": action,
            "min_agents": min_a,
            "max_agents": max_a,
            "target_utilization": target_util,
        }

    return recommendations


def apply_auto_scale(region: str, target_agents: int) -> dict:
    """Adjust a region's capacity to the target agent count.

    Sends a scaling request to the region's endpoint and records the event.

    Args:
        region: region name or ID
        target_agents: desired agent count

    Returns:
        Dict with ok, previous/new agent counts, region.
    """
    _ensure_schema()
    import db

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, endpoint, capacity_json FROM fleet_regions "
            "WHERE id = ? OR name = ?", (region, region)
        ).fetchone()

    if not row:
        return {"ok": False, "error": f"Region not found: {region}"}

    r = dict(row)
    rid = r["id"]
    try:
        cap = json.loads(r.get("capacity_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        cap = {}
    prev_agents = cap.get("current_agents", 0)

    # Clamp to HPA bounds
    with db.get_conn() as conn:
        sc = conn.execute(
            "SELECT min_agents, max_agents FROM scaling_config WHERE region_id = ?",
            (rid,),
        ).fetchone()
    min_a = sc["min_agents"] if sc else 2
    max_a = sc["max_agents"] if sc else 20
    clamped = max(min_a, min(max_a, target_agents))

    # Send scaling command to region endpoint
    endpoint = r["endpoint"].rstrip("/")
    body = json.dumps({"target_agents": clamped}).encode()
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{endpoint}/api/fleet/scale",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:
        log.warning("geo_fleet: scale command to %s failed: %s", r["name"], exc)
        # Record the attempt anyway — remote may be unreachable

    # Record scaling event
    now = time.time()
    reason = f"auto_scale: {prev_agents} -> {clamped}"

    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO scaling_events (region_id, ts, action, prev_agents, new_agents, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rid, now, "scale_up" if clamped > prev_agents else "scale_down", prev_agents, clamped, reason),
            )
    db._retry_write(_do)

    # Update local capacity record
    cap["current_agents"] = clamped
    def _update():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE fleet_regions SET capacity_json = ? WHERE id = ?",
                (json.dumps(cap), rid),
            )
    db._retry_write(_update)

    log.info("geo_fleet: scaled region %s from %d to %d agents", r["name"], prev_agents, clamped)
    return {"ok": True, "region": r["name"], "prev_agents": prev_agents, "new_agents": clamped}


def get_scaling_history(region: str, hours: int = 24) -> list[dict]:
    """Return scaling events for a region within the given time window.

    Args:
        region: region name or ID
        hours: lookback window (default 24h)

    Returns:
        List of scaling event dicts, newest first.
    """
    _ensure_schema()
    import db

    cutoff = time.time() - (hours * 3600)

    with db.get_conn() as conn:
        # Resolve region name to ID
        rid_row = conn.execute(
            "SELECT id FROM fleet_regions WHERE id = ? OR name = ?",
            (region, region),
        ).fetchone()
        if not rid_row:
            return []
        rid = rid_row[0] if isinstance(rid_row, (list, tuple)) else rid_row["id"]

        rows = conn.execute(
            "SELECT id, region_id, ts, action, prev_agents, new_agents, reason "
            "FROM scaling_events WHERE region_id = ? AND ts >= ? ORDER BY ts DESC",
            (rid, cutoff),
        ).fetchall()

    return [dict(r) for r in rows]


def configure_hpa(region: str, min_agents: int, max_agents: int,
                  target_utilization: float = 0.7) -> dict:
    """Set auto-scale bounds for a region.

    Args:
        region: region name or ID
        min_agents: minimum agent count (floor)
        max_agents: maximum agent count (ceiling)
        target_utilization: target utilization ratio (0.0-1.0)

    Returns:
        Dict with ok and the applied config.
    """
    _ensure_schema()
    import db

    with db.get_conn() as conn:
        rid_row = conn.execute(
            "SELECT id, name FROM fleet_regions WHERE id = ? OR name = ?",
            (region, region),
        ).fetchone()
    if not rid_row:
        return {"ok": False, "error": f"Region not found: {region}"}

    rid = rid_row["id"]
    rname = rid_row["name"]

    # Validate
    min_agents = max(1, int(min_agents))
    max_agents = max(min_agents, int(max_agents))
    target_utilization = max(0.1, min(1.0, float(target_utilization)))

    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO scaling_config (region_id, min_agents, max_agents, target_utilization) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(region_id) DO UPDATE SET min_agents=excluded.min_agents, "
                "max_agents=excluded.max_agents, target_utilization=excluded.target_utilization",
                (rid, min_agents, max_agents, target_utilization),
            )
    db._retry_write(_do)

    log.info("geo_fleet: HPA configured for %s: min=%d max=%d util=%.2f",
             rname, min_agents, max_agents, target_utilization)
    return {
        "ok": True,
        "region": rname,
        "min_agents": min_agents,
        "max_agents": max_agents,
        "target_utilization": target_utilization,
    }


# ── CDN for Skills / Models ─────────────────────────────────────────────────

def register_cdn_endpoint(url: str, region: str) -> dict:
    """Register a CDN distribution point for a region.

    Args:
        url: base URL of the CDN node
        region: region name or ID the CDN serves

    Returns:
        Dict with cdn endpoint id, url, region.
    """
    _ensure_schema()
    import db

    # Resolve region
    with db.get_conn() as conn:
        rid_row = conn.execute(
            "SELECT id, name FROM fleet_regions WHERE id = ? OR name = ?",
            (region, region),
        ).fetchone()
    if not rid_row:
        return {"ok": False, "error": f"Region not found: {region}"}

    rid = rid_row["id"]
    cdn_id = str(uuid.uuid4())[:12]
    now = time.time()

    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO cdn_endpoints (id, url, region_id, status, created_at) "
                "VALUES (?, ?, ?, 'active', ?)",
                (cdn_id, url, rid, now),
            )
    db._retry_write(_do)

    log.info("geo_fleet: CDN endpoint registered: %s -> %s", url, rid_row["name"])
    return {"ok": True, "id": cdn_id, "url": url, "region": rid_row["name"]}


def list_cdn_endpoints() -> list[dict]:
    """Return all CDN endpoints with region info."""
    _ensure_schema()
    import db

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.url, c.region_id, c.status, c.created_at, r.name as region_name "
            "FROM cdn_endpoints c "
            "LEFT JOIN fleet_regions r ON c.region_id = r.id "
            "ORDER BY r.name, c.url"
        ).fetchall()

    return [dict(r) for r in rows]


def get_skill_download_url(skill_name: str, region: str) -> str:
    """Return the nearest CDN URL for downloading a skill package.

    Args:
        skill_name: name of the skill to download
        region: target region name or ID

    Returns:
        Full URL to the skill package, or empty string if no CDN available.
    """
    _ensure_schema()
    import db

    with db.get_conn() as conn:
        # Try exact region first
        row = conn.execute(
            "SELECT c.url FROM cdn_endpoints c "
            "JOIN fleet_regions r ON c.region_id = r.id "
            "WHERE (r.id = ? OR r.name = ?) AND c.status = 'active' "
            "LIMIT 1",
            (region, region),
        ).fetchone()

    if row:
        base = row["url"].rstrip("/")
        return f"{base}/skills/{skill_name}.tar.gz"

    # Fallback: any active CDN
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT url FROM cdn_endpoints WHERE status = 'active' LIMIT 1"
        ).fetchone()

    if row:
        base = row["url"].rstrip("/")
        return f"{base}/skills/{skill_name}.tar.gz"

    return ""


def sync_skills_to_cdn(region: str) -> dict:
    """Push local skill packages to a region's CDN endpoints.

    Packages each skill in fleet/skills/ and POSTs to the CDN upload endpoint.
    Skips __pycache__ and non-Python files.

    Args:
        region: region name or ID

    Returns:
        Dict with ok, synced count, errors.
    """
    _ensure_schema()
    import db

    # Resolve CDN endpoints for region
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT c.url FROM cdn_endpoints c "
            "JOIN fleet_regions r ON c.region_id = r.id "
            "WHERE (r.id = ? OR r.name = ?) AND c.status = 'active'",
            (region, region),
        ).fetchall()

    if not rows:
        return {"ok": False, "error": f"No active CDN endpoints for region: {region}", "synced": 0}

    cdn_urls = [r["url"] for r in rows]

    # Enumerate skills
    skills_dir = FLEET_DIR / "skills"
    if not skills_dir.exists():
        return {"ok": False, "error": "Skills directory not found", "synced": 0}

    skill_files = [f for f in skills_dir.glob("*.py")
                   if f.name != "__init__.py" and not f.name.startswith("_")]

    synced = 0
    errors = []

    for skill_path in skill_files:
        skill_name = skill_path.stem
        content = skill_path.read_bytes()

        for cdn_url in cdn_urls:
            try:
                import urllib.request
                upload_url = f"{cdn_url.rstrip('/')}/upload/skills/{skill_name}"
                req = urllib.request.Request(
                    upload_url,
                    data=content,
                    method="PUT",
                    headers={"Content-Type": "application/octet-stream"},
                )
                urllib.request.urlopen(req, timeout=30)
                synced += 1
            except Exception as exc:
                errors.append(f"{skill_name}->{cdn_url}: {exc}")
                log.debug("geo_fleet: CDN sync failed for %s: %s", skill_name, exc)

    log.info("geo_fleet: synced %d skill(s) to region %s (%d errors)",
             synced, region, len(errors))
    return {"ok": len(errors) == 0, "synced": synced, "errors": errors}


# ── Region Health ────────────────────────────────────────────────────────────

def get_region_health(region_id: str) -> dict:
    """Return health summary for a single region.

    Includes heartbeat freshness, capacity, scaling config, and recent events.
    """
    region = get_region(region_id)
    if not region:
        return {"ok": False, "error": f"Region not found: {region_id}"}

    events = get_scaling_history(region_id, hours=1)
    recommendations = get_scaling_recommendation()
    rec = recommendations.get(region_id, {})

    return {
        "ok": True,
        "region": region,
        "recommendation": rec,
        "recent_events": events[:10],
        "scaling_events_1h": len(events),
    }
