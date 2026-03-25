# fleet/skills/_http.py
"""HTTP probing helpers with timeout and latency tracking."""
import json
import time
import urllib.error
import urllib.request


def probe_url(url: str, method: str = "GET", timeout: int = 10,
              headers: dict = None) -> dict:
    """Probe a URL, return {status, code, latency_ms, body, error}."""
    req = urllib.request.Request(url, method=method, headers=headers or {})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.monotonic() - start) * 1000
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "status": "ok",
                "code": resp.status,
                "latency_ms": round(elapsed, 1),
                "body": body,
            }
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "http_error",
            "code": e.code,
            "latency_ms": round(elapsed, 1),
            "error": str(e.reason),
        }
    except urllib.error.URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "unreachable",
            "code": 0,
            "latency_ms": round(elapsed, 1),
            "error": str(e.reason),
        }
    except Exception as e:
        return {"status": "error", "code": 0, "latency_ms": 0, "error": str(e)}


def fetch_json(url: str, timeout: int = 10, headers: dict = None) -> dict | None:
    """Fetch and parse JSON from a URL. Returns None on failure."""
    result = probe_url(url, timeout=timeout, headers=headers)
    if result["status"] == "ok":
        try:
            return json.loads(result["body"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None
