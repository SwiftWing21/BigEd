"""
TECH_DEBT 4.3: Fleet REST API helpers — single module for all HTTP calls
to the fleet dashboard and Ollama APIs.

Usage:
    from fleet_api import fleet_api, ollama_tags, ollama_ps, ollama_keepalive

    health = fleet_api("/api/fleet/health")
    tags   = ollama_tags()
    models = ollama_ps()
"""
import json
import urllib.request

# ─── Fleet Dashboard API ─────────────────────────────────────────────────────

FLEET_PORT = 5555
OLLAMA_HOST = "http://localhost:11434"


def fleet_api(endpoint: str, method: str = "GET",
              json_data: dict = None, port: int = FLEET_PORT,
              timeout: int = 5) -> dict | None:
    """Call fleet dashboard REST API. Returns parsed JSON dict or None on failure.

    Args:
        endpoint: API path, e.g. "/api/fleet/health"
        method:   "GET" or "POST"
        json_data: body payload for POST requests
        port:     dashboard port (default 5555)
        timeout:  request timeout in seconds
    """
    url = f"http://localhost:{port}{endpoint}"
    try:
        if method == "POST":
            data = json.dumps(json_data or {}).encode()
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def fleet_health(port: int = FLEET_PORT) -> dict | None:
    """Quick fleet health check via REST API."""
    return fleet_api("/api/fleet/health", port=port)


def fleet_stop(port: int = FLEET_PORT) -> dict | None:
    """Send fleet stop signal via REST API."""
    return fleet_api("/api/fleet/stop", method="POST", port=port)


# ─── Ollama API ──────────────────────────────────────────────────────────────

def ollama_tags(host: str = OLLAMA_HOST, timeout: int = 3) -> dict | None:
    """Get Ollama model tags (/api/tags). Returns parsed JSON or None."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ollama_ps(host: str = OLLAMA_HOST, timeout: int = 2) -> dict | None:
    """Get currently loaded Ollama models (/api/ps). Returns parsed JSON or None."""
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ollama_is_running(host: str = OLLAMA_HOST) -> bool:
    """Check if Ollama is reachable via HTTP API."""
    return ollama_tags(host=host, timeout=2) is not None


def ollama_keepalive(model: str, host: str = OLLAMA_HOST, timeout: int = 5):
    """Ping Ollama with keep_alive=-1 to prevent model unload."""
    try:
        body = json.dumps({
            "model": model, "prompt": "", "keep_alive": "-1",
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except Exception:
        pass
