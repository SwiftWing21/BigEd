"""
API health probe skill — probes configured API endpoints for availability,
records response status and latency, generates a health report.
"""
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SKILL_NAME = "api_health_probe"
DESCRIPTION = "Probe configured API endpoints for health, record status and latency."
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
HEALTH_DIR = KNOWLEDGE_DIR / "health"

# Default endpoints when none configured in payload or fleet.toml
DEFAULT_ENDPOINTS = [
    {"name": "Ollama", "url": "http://localhost:11434/api/tags", "method": "GET"},
    {"name": "Dashboard", "url": "http://localhost:5555/api/health", "method": "GET"},
]


def _probe_endpoint(ep, timeout):
    """Probe a single endpoint, return status dict."""
    import time

    name = ep.get("name", ep.get("url", "unknown"))
    url = ep.get("url", "")
    method = ep.get("method", "GET").upper()

    if not url:
        return {
            "name": name, "url": url, "status": "error",
            "code": 0, "latency_ms": 0, "detail": "No URL configured",
        }

    start = time.monotonic()
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.monotonic() - start) * 1000
            return {
                "name": name, "url": url, "status": "healthy",
                "code": resp.status, "latency_ms": round(elapsed, 1),
                "detail": "",
            }
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "name": name, "url": url, "status": "degraded",
            "code": e.code, "latency_ms": round(elapsed, 1),
            "detail": str(e.reason),
        }
    except urllib.error.URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "name": name, "url": url, "status": "down",
            "code": 0, "latency_ms": round(elapsed, 1),
            "detail": str(e.reason),
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "name": name, "url": url, "status": "error",
            "code": 0, "latency_ms": round(elapsed, 1),
            "detail": str(e),
        }


def run(payload, config):
    """Probe API endpoints and generate a health report."""
    import logging

    log = logging.getLogger(SKILL_NAME)
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    timeout = payload.get("timeout", 10)
    endpoints = payload.get("endpoints", None)

    # Load endpoints from fleet.toml if not in payload
    if not endpoints:
        try:
            from config import load_config
            cfg = load_config()
            endpoints = cfg.get("health_probe", {}).get("endpoints", None)
        except Exception:
            log.warning("Could not load fleet.toml health_probe config", exc_info=True)

    if not endpoints:
        endpoints = DEFAULT_ENDPOINTS

    # Probe all endpoints
    results = []
    for ep in endpoints:
        try:
            result = _probe_endpoint(ep, timeout)
            results.append(result)
        except Exception:
            log.warning("Probe failed for %s", ep.get("name", "unknown"),
                        exc_info=True)
            results.append({
                "name": ep.get("name", "unknown"), "url": ep.get("url", ""),
                "status": "error", "code": 0, "latency_ms": 0,
                "detail": "Probe exception",
            })

    healthy = [r for r in results if r["status"] == "healthy"]
    degraded = [r for r in results if r["status"] == "degraded"]
    down = [r for r in results if r["status"] in ("down", "error")]

    # Build report
    today = date.today().isoformat()
    overall = "HEALTHY" if not down and not degraded else (
        "DEGRADED" if not down else "UNHEALTHY"
    )
    avg_latency = (
        round(sum(r["latency_ms"] for r in results) / max(len(results), 1), 1)
    )

    md = [
        f"# API Health Report — {today}",
        "",
        f"**Overall Status:** {overall} | **Endpoints:** {len(results)} | "
        f"**Avg Latency:** {avg_latency}ms",
        f"**Healthy:** {len(healthy)} | **Degraded:** {len(degraded)} "
        f"| **Down:** {len(down)}",
        "",
        "---",
        "",
        "## Endpoint Status",
        "",
        "| Endpoint | URL | Status | Code | Latency | Detail |",
        "|----------|-----|--------|------|---------|--------|",
    ]

    for r in results:
        status_icon = {
            "healthy": "OK", "degraded": "WARN", "down": "DOWN", "error": "ERR",
        }.get(r["status"], "?")
        detail = r["detail"][:60] if r["detail"] else "-"
        md.append(
            f"| {r['name']} | `{r['url']}` | {status_icon} "
            f"| {r['code']} | {r['latency_ms']}ms | {detail} |"
        )
    md.append("")

    if down:
        md.append("## Down / Error Endpoints")
        md.append("")
        for r in down:
            md.append(f"- **{r['name']}** (`{r['url']}`): {r['detail']}")
        md.append("")

    md.append("## Latency Summary")
    md.append("")
    if results:
        latencies = [r["latency_ms"] for r in results]
        md.append(f"- **Min:** {min(latencies)}ms")
        md.append(f"- **Max:** {max(latencies)}ms")
        md.append(f"- **Avg:** {avg_latency}ms")
    md.append("")

    report = "\n".join(md)
    out_file = HEALTH_DIR / f"api_health_{today}.md"
    out_file.write_text(report, encoding="utf-8")

    # Save result to DB if possible
    try:
        import db

        def _save():
            with db.get_conn() as conn:
                conn.execute("""
                    INSERT INTO tasks (type, status, result, created_at)
                    VALUES (?, 'DONE', ?, datetime('now'))
                """, (SKILL_NAME, f"healthy={len(healthy)} down={len(down)}"))

        db._retry_write(_save)
    except Exception:
        log.warning("Could not save health probe result to DB", exc_info=True)

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "overall": overall,
        "endpoints_checked": len(results),
        "healthy": len(healthy),
        "degraded": len(degraded),
        "down": len(down),
        "avg_latency_ms": avg_latency,
    }
