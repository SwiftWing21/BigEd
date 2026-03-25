"""
Webhook dispatch — sends HTTP POST notifications for fleet events to
configured webhook URLs from fleet.toml.

Payload:
  event_type  str    Event type (e.g. "task_complete", "alert", "drift_detected")
  event_data  dict   Event payload to POST as JSON body
  urls        list   Override webhook URLs (optional — defaults to fleet.toml config)

Records delivery status per URL.
Output: knowledge/webhooks/webhook_YYYY-MM-DD.jsonl (append)
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, date
from pathlib import Path

SKILL_NAME = "webhook_dispatch"
DESCRIPTION = "Dispatch webhook notifications for fleet events via HTTP POST."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
WEBHOOK_DIR = KNOWLEDGE_DIR / "webhooks"

log = logging.getLogger(__name__)


def _load_webhook_urls():
    """Load webhook URLs from fleet.toml triggers section."""
    try:
        from config import load_config
        cfg = load_config()
        triggers = cfg.get("triggers", {})
        urls = triggers.get("webhook_urls", [])
        if isinstance(urls, str):
            urls = [urls] if urls else []
        return urls
    except Exception:
        log.warning("Failed to load webhook URLs from config", exc_info=True)
        return []


def _send_webhook(url, event_type, event_data):
    """Send a single webhook POST. Returns (success, status_code, error)."""
    body = json.dumps({
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "data": event_data,
    }, default=str).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "BigEd-Fleet-Webhook/1.0",
            "X-Fleet-Event": event_type,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, resp.status, None
    except urllib.error.HTTPError as e:
        return False, e.code, str(e)
    except urllib.error.URLError as e:
        return False, 0, str(e.reason)
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {e}"


def _append_log(log_path, entry):
    """Append a JSONL entry to the delivery log."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        log.warning("Failed to write webhook log entry", exc_info=True)


def run(payload, config):
    """Dispatch webhook notifications for a fleet event."""
    WEBHOOK_DIR.mkdir(parents=True, exist_ok=True)

    event_type = payload.get("event_type", "unknown")
    event_data = payload.get("event_data", {})
    urls = payload.get("urls", [])

    # Fall back to configured URLs if none provided
    if not urls:
        urls = _load_webhook_urls()

    if not urls:
        return {
            "status": "skip",
            "message": "No webhook URLs configured or provided",
        }

    today = date.today().isoformat()
    log_path = WEBHOOK_DIR / f"webhook_{today}.jsonl"

    deliveries = []
    success_count = 0
    fail_count = 0

    for url in urls:
        if not isinstance(url, str) or not url.startswith("http"):
            deliveries.append({
                "url": str(url),
                "success": False,
                "status_code": 0,
                "error": "Invalid URL",
            })
            fail_count += 1
            continue

        success, status_code, error = _send_webhook(url, event_type, event_data)

        delivery = {
            "url": url,
            "success": success,
            "status_code": status_code,
            "error": error,
        }
        deliveries.append(delivery)

        # Append to JSONL log
        _append_log(log_path, {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "url": url,
            "success": success,
            "status_code": status_code,
            "error": error,
        })

        if success:
            success_count += 1
        else:
            fail_count += 1
            log.warning("Webhook delivery failed for %s: %s", url, error)

    return {
        "status": "ok" if fail_count == 0 else "partial",
        "event_type": event_type,
        "log_file": str(log_path),
        "total_urls": len(urls),
        "delivered": success_count,
        "failed": fail_count,
        "deliveries": deliveries,
    }
