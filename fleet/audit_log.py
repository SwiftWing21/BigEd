"""0.09.00: Centralized audit log — tamper-evident JSON event trail with HMAC signing."""
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FLEET_DIR = Path(__file__).parent
AUDIT_LOG = FLEET_DIR / "logs" / "audit.jsonl"
_lock = threading.Lock()
_HMAC_KEY = os.environ.get("BIGED_AUDIT_KEY", "biged-default-audit-key").encode()


def log_event(event_type: str, source: str, data: dict = None, severity: str = "info"):
    """Append a signed audit event to the centralized log.

    Args:
        event_type: e.g., "task_complete", "dlp_alert", "quarantine", "budget_exceeded"
        source: e.g., "supervisor", "watchdog", "worker:coder_1"
        data: arbitrary event payload
        severity: info | warning | error | critical
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "source": source,
        "severity": severity,
        "data": data or {},
    }
    # HMAC signature for tamper evidence
    payload = json.dumps(event, sort_keys=True)
    sig = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()
    event["_hmac"] = sig

    line = json.dumps(event) + "\n"
    with _lock:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)


def verify_event(event: dict) -> bool:
    """Verify HMAC signature of an audit event."""
    sig = event.pop("_hmac", "")
    payload = json.dumps(event, sort_keys=True)
    expected = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()
    event["_hmac"] = sig  # restore
    return hmac.compare_digest(sig, expected)


def read_events(last_n: int = 100, event_type: str = None, since: str = None) -> list:
    """Read recent audit events with optional filtering."""
    if not AUDIT_LOG.exists():
        return []
    events = []
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if event_type and event.get("type") != event_type:
                continue
            if since and event.get("timestamp", "") < since:
                continue
            events.append(event)
        except json.JSONDecodeError:
            continue
    return events[-last_n:]


def get_audit_summary() -> dict:
    """Summary stats for dashboard display."""
    events = read_events(last_n=1000)
    by_type = {}
    by_severity = {}
    for e in events:
        by_type[e.get("type", "unknown")] = by_type.get(e.get("type"), 0) + 1
        by_severity[e.get("severity", "info")] = by_severity.get(e.get("severity"), 0) + 1
    return {
        "total_events": len(events),
        "by_type": by_type,
        "by_severity": by_severity,
        "verified": sum(1 for e in events[-10:] if verify_event(e)),
    }
