"""
Home Assistant management — query HA REST API for entities, automations, backups.

Requires: HA_URL and HA_TOKEN in ~/.secrets
  HA_URL   = http://homeassistant.local:8123  (or IP)
  HA_TOKEN = long-lived access token from HA profile

Actions:
  list_entities     — all entities with state + last_changed
  list_automations  — automation configs with enabled/disabled
  create_backup     — trigger a full backup
  list_backups      — show existing backups with size + date
  get_entity        — single entity detail (payload.entity_id)
  call_service      — call a HA service (payload.domain, payload.service, payload.data)

Returns: {action, data, count}
"""
import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
HA_DIR = KNOWLEDGE_DIR / "home_assistant"
REQUIRES_NETWORK = True


def _validate_entity_id(entity_id):
    """Reject entity_ids that could inject path segments or query params."""
    if not re.match(r'^[a-z_][a-z0-9_]*\.[a-z0-9_]+$', entity_id):
        return False
    return True


def _ha_request(url, token, method="GET", body=None):
    """Make authenticated HA API request."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run(payload, config):
    ha_url = os.environ.get("HA_URL", "http://homeassistant.local:8123")
    token = os.environ.get("HA_TOKEN", "")
    action = payload.get("action", "list_entities")

    if not token:
        return {"error": "HA_TOKEN required in ~/.secrets (long-lived access token)"}

    api = f"{ha_url}/api"

    try:
        if action == "list_entities":
            states = _ha_request(f"{api}/states", token)
            result = [{
                "entity_id": s["entity_id"],
                "state": s["state"],
                "friendly_name": s.get("attributes", {}).get("friendly_name", ""),
                "domain": s["entity_id"].split(".")[0],
                "last_changed": s.get("last_changed", ""),
            } for s in states]

        elif action == "list_automations":
            states = _ha_request(f"{api}/states", token)
            result = [{
                "entity_id": s["entity_id"],
                "state": s["state"],
                "friendly_name": s.get("attributes", {}).get("friendly_name", ""),
                "last_triggered": s.get("attributes", {}).get("last_triggered", ""),
            } for s in states if s["entity_id"].startswith("automation.")]

        elif action == "create_backup":
            resp = _ha_request(f"{api}/services/backup/create", token, method="POST",
                               body=payload.get("data", {}))
            result = [{"status": "backup_triggered", "response": str(resp)[:500]}]

        elif action == "list_backups":
            resp = _ha_request(f"{ha_url}/api/backups", token)
            backups = resp if isinstance(resp, list) else resp.get("backups", [])
            result = [{
                "slug": b.get("slug", "?"),
                "name": b.get("name", "?"),
                "date": b.get("date", "?"),
                "size_mb": round(b.get("size", 0) / (1024 * 1024), 1) if b.get("size") else "?",
                "type": b.get("type", "?"),
            } for b in backups]

        elif action == "get_entity":
            entity_id = payload.get("entity_id", "")
            if not entity_id:
                return {"error": "entity_id required"}
            if not _validate_entity_id(entity_id):
                return {"error": f"Invalid entity_id format: {entity_id}"}
            state = _ha_request(f"{api}/states/{entity_id}", token)
            result = [state]

        elif action == "call_service":
            domain = payload.get("domain", "")
            service = payload.get("service", "")
            data = payload.get("data", {})
            if not domain or not service:
                return {"error": "domain and service required"}
            if not re.match(r'^[a-z_][a-z0-9_]*$', domain):
                return {"error": f"Invalid domain format: {domain}"}
            if not re.match(r'^[a-z_][a-z0-9_]*$', service):
                return {"error": f"Invalid service format: {service}"}
            resp = _ha_request(
                f"{api}/services/{domain}/{service}", token,
                method="POST", body=data,
            )
            result = resp if isinstance(resp, list) else [resp]

        else:
            return {"error": f"Unknown action: {action}"}

        # Save to knowledge
        HA_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = HA_DIR / f"ha_{action}_{ts}.json"
        out_file.write_text(json.dumps(result, indent=2))

        return {"action": action, "count": len(result), "data": result[:100]}

    except Exception as e:
        return {"error": f"Home Assistant API error: {e}", "action": action}
