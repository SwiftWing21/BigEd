"""
UniFi network management — query UniFi Controller API for clients, devices, alerts.

Requires: UNIFI_HOST, UNIFI_USER, UNIFI_PASS in ~/.secrets
Default site: "default" (override via payload.site)

Actions:
  list_clients  — connected clients with IP, MAC, hostname, signal
  list_devices   — APs, switches, gateways with status + firmware
  list_alerts    — recent controller alerts/events
  get_firewall   — current firewall rules
  get_dpi        — deep packet inspection app stats

Returns: {action, data: [...], count, site}
"""
import json
import os
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
NETWORK_DIR = KNOWLEDGE_DIR / "network"
REQUIRES_NETWORK = True


def _unifi_session(host, user, password, site="default"):
    """Authenticate to UniFi Controller, return opener + base URL."""
    # UniFi uses self-signed certs — skip verification for local controller
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    cookie_handler = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        cookie_handler,
    )

    # Login
    login_url = f"{host}/api/login"
    body = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        login_url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener.open(req, timeout=10)
    return opener, f"{host}/api/s/{site}"


def _api_get(opener, url):
    """GET a UniFi API endpoint, return parsed JSON data."""
    req = urllib.request.Request(url, method="GET")
    with opener.open(req, timeout=15) as r:
        data = json.loads(r.read())
        return data.get("data", data)


def run(payload, config):
    host = os.environ.get("UNIFI_HOST", "https://192.168.1.1:8443")
    user = os.environ.get("UNIFI_USER", "")
    password = os.environ.get("UNIFI_PASS", "")
    site = payload.get("site", "default")
    action = payload.get("action", "list_clients")

    if not user or not password:
        return {"error": "UNIFI_USER and UNIFI_PASS required in ~/.secrets"}

    try:
        opener, base = _unifi_session(host, user, password, site)
    except Exception as e:
        return {"error": f"UniFi login failed: {e}", "host": host}

    try:
        if action == "list_clients":
            clients = _api_get(opener, f"{base}/stat/sta")
            result = [{
                "hostname": c.get("hostname", c.get("name", "?")),
                "ip": c.get("ip", "?"),
                "mac": c.get("mac", "?"),
                "signal_dbm": c.get("signal", ""),
                "rx_bytes": c.get("rx_bytes", 0),
                "tx_bytes": c.get("tx_bytes", 0),
                "network": c.get("network", ""),
            } for c in clients]

        elif action == "list_devices":
            devices = _api_get(opener, f"{base}/stat/device")
            result = [{
                "name": d.get("name", d.get("model", "?")),
                "model": d.get("model", "?"),
                "type": d.get("type", "?"),
                "ip": d.get("ip", "?"),
                "mac": d.get("mac", "?"),
                "status": "online" if d.get("state", 0) == 1 else "offline",
                "version": d.get("version", "?"),
                "uptime_hours": round(d.get("uptime", 0) / 3600, 1),
            } for d in devices]

        elif action == "list_alerts":
            alerts = _api_get(opener, f"{base}/stat/alarm")
            result = [{
                "msg": a.get("msg", "?"),
                "key": a.get("key", "?"),
                "severity": a.get("severity", "?"),
                "time": datetime.fromtimestamp(a.get("time", 0) / 1000).isoformat(),
            } for a in alerts[:20]]

        elif action == "get_firewall":
            rules = _api_get(opener, f"{base}/rest/firewallrule")
            result = [{
                "name": r.get("name", "?"),
                "action": r.get("action", "?"),
                "src": r.get("src_address", r.get("src_networkconf_id", "any")),
                "dst": r.get("dst_address", r.get("dst_networkconf_id", "any")),
                "protocol": r.get("protocol", "all"),
                "enabled": r.get("enabled", True),
            } for r in rules]

        elif action == "get_dpi":
            dpi = _api_get(opener, f"{base}/stat/dpi")
            result = [{
                "app": d.get("app", "?"),
                "cat": d.get("cat", "?"),
                "rx_bytes": d.get("rx_bytes", 0),
                "tx_bytes": d.get("tx_bytes", 0),
            } for d in dpi[:30]]

        else:
            return {"error": f"Unknown action: {action}"}

        # Save to knowledge
        NETWORK_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = NETWORK_DIR / f"unifi_{action}_{ts}.json"
        out_file.write_text(json.dumps(result, indent=2))

        return {"action": action, "count": len(result), "data": result[:50], "site": site}

    except Exception as e:
        return {"error": f"UniFi API error: {e}", "action": action}
