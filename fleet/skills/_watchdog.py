"""
Semantic Watchdog — monitors fleet health beyond simple heartbeats.

Called by supervisor every 60s. Not a user-dispatchable skill.

Checks:
1. Failure streaks: agents with 3+ consecutive task failures → quarantine
2. Stuck reviews: tasks in REVIEW for >30min → auto-pass
3. DLP scrubbing: scan recent task results and knowledge/ for leaked secrets

Writes alerts via db.post_message to 'supervisor' for dashboard pickup.
"""
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FLEET_DIR))

# ── Secret patterns for DLP scrubbing ────────────────────────────────────────

# Patterns that indicate a leaked API key or secret
_SECRET_PATTERNS = [
    re.compile(r'sk-[a-zA-Z0-9_-]{20,}'),            # Anthropic / OpenAI keys (allow hyphens/underscores)
    re.compile(r'AIza[a-zA-Z0-9_-]{30,}'),            # Google API keys
    re.compile(r'ghp_[a-zA-Z0-9]{30,}'),              # GitHub PAT
    re.compile(r'gho_[a-zA-Z0-9]{30,}'),              # GitHub OAuth
    re.compile(r'xoxb-[a-zA-Z0-9-]{10,}'),            # Slack bot tokens
    re.compile(r'AKIA[A-Z0-9]{16}'),                   # AWS access keys
    re.compile(r'tvly-[a-zA-Z0-9_-]{15,}'),           # Tavily API keys
    re.compile(r'AZURE[a-zA-Z0-9_-]{20,}'),           # Azure
    re.compile(r'ya29\.[a-zA-Z0-9_-]{30,}'),          # GCP OAuth
    re.compile(r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY'), # Private keys
    re.compile(r'mongodb(\+srv)?://[^\s]+'),           # DB connection URIs
    re.compile(r'postgres(ql)?://[^\s]+'),             # PostgreSQL URIs
    re.compile(r'mysql://[^\s]+'),                     # MySQL URIs
    re.compile(r'redis://[^\s]+'),                     # Redis URIs
]

# Also check env vars loaded from ~/.secrets
_SECRETS_CACHE = None


def _load_secret_values():
    """Cache actual secret values from env for exact-match scrubbing."""
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return _SECRETS_CACHE
    _SECRETS_CACHE = set()
    for key, val in os.environ.items():
        # Only check keys that look like API credentials
        if any(k in key.upper() for k in ("KEY", "TOKEN", "SECRET", "PAT", "PASSWORD")):
            if len(val) >= 10:  # skip short values
                _SECRETS_CACHE.add(val)
    return _SECRETS_CACHE


def _check_base64_secrets(text):
    """Detect base64-encoded API keys that LLMs sometimes produce."""
    # Find base64-looking strings (20+ chars, valid base64 alphabet)
    b64_pattern = re.compile(r'[A-Za-z0-9+/]{20,}={0,2}')
    for match in b64_pattern.finditer(text):
        try:
            decoded = base64.b64decode(match.group()).decode('utf-8', errors='ignore')
            # Check if decoded content matches any known secret pattern
            if any(p.search(decoded) for p in _SECRET_PATTERNS):
                return True, match.group()
        except Exception:
            continue
    return False, None


def _contains_secret(text):
    """Check if text contains any secret patterns or known secret values."""
    if not text or len(text) < 10:
        return False
    # Pattern-based detection
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return True
    # Exact-value detection
    for secret in _load_secret_values():
        if secret in text:
            return True
    # Base64-encoded secret detection
    found, _ = _check_base64_secrets(text)
    if found:
        return True
    return False


def _redact_secrets(text):
    """Replace secrets in text with [REDACTED]."""
    if not text:
        return text
    result = text
    for pat in _SECRET_PATTERNS:
        result = pat.sub("[REDACTED]", result)
    for secret in _load_secret_values():
        if secret in result:
            result = result.replace(secret, "[REDACTED]")
    # Redact base64-encoded secrets
    while True:
        found, b64_match = _check_base64_secrets(result)
        if not found:
            break
        result = result.replace(b64_match, "[REDACTED]")
    return result


# ── PII patterns for input-side guardrails ────────────────────────────────────

_PII_PATTERNS = [
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),  # email
    re.compile(r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b'),  # SSN
    re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),  # credit card
    re.compile(r'\b\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),  # US phone
]


def scan_input(text: str) -> dict:
    """Scan input text for secrets and PII before it reaches the LLM.

    Returns:
        {"clean": bool, "findings": [{"type": "secret"|"pii", "pattern": str}]}
    """
    findings = []

    # Check for API key secrets
    if _contains_secret(text):
        findings.append({"type": "secret", "pattern": "API key pattern detected"})

    # Check for base64-encoded secrets
    has_b64, match = _check_base64_secrets(text)
    if has_b64:
        findings.append({"type": "secret", "pattern": "Base64-encoded secret detected"})

    # Check for PII
    for pattern in _PII_PATTERNS:
        if pattern.search(text):
            findings.append({"type": "pii", "pattern": pattern.pattern[:40] + "..."})

    return {"clean": len(findings) == 0, "findings": findings}


# ── Watchdog checks ──────────────────────────────────────────────────────────

def check_failure_streaks(log_fn=print):
    """Quarantine agents with 3+ consecutive failures."""
    import db
    alerts = []
    try:
        streaks = db.get_failure_streaks(threshold=3)
        for s in streaks:
            agent = s["agent"]
            count = s["fail_count"]
            # Check if already quarantined
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT status FROM agents WHERE name=?", (agent,)).fetchone()
                if row and row["status"] == "QUARANTINED":
                    continue
            reason = f"Failure streak: {count} consecutive failures. Last: {s.get('last_error', '?')[:200]}"
            db.quarantine_agent(agent, reason)
            log_fn(f"[WATCHDOG] Quarantined '{agent}': {reason[:100]}")
            alerts.append({"level": "warning", "message": f"Agent '{agent}' quarantined: {count} failures"})
    except Exception as e:
        log_fn(f"[WATCHDOG] Failure streak check error: {e}")
    return alerts


def check_stuck_reviews(log_fn=print):
    """Auto-pass tasks stuck in REVIEW for >30min."""
    import db
    alerts = []
    try:
        stuck = db.get_stuck_reviews(timeout_minutes=30)
        for t in stuck:
            db.complete_task(t["id"], json.dumps({
                "auto_passed": True,
                "reason": "Review timeout (>30min) — auto-passed by watchdog",
            }))
            log_fn(f"[WATCHDOG] Auto-passed stuck review: task {t['id']} ({t['type']})")
            alerts.append({"level": "info", "message": f"Task {t['id']} auto-passed (review timeout)"})
    except Exception as e:
        log_fn(f"[WATCHDOG] Stuck review check error: {e}")
    return alerts


def scrub_recent_results(log_fn=print):
    """DLP: scan recent DONE task results for leaked secrets, redact in-place."""
    import db
    alerts = []
    try:
        with db.get_conn() as conn:
            # Check last 50 completed tasks
            rows = conn.execute("""
                SELECT id, result_json FROM tasks
                WHERE status='DONE' AND result_json IS NOT NULL
                ORDER BY id DESC LIMIT 50
            """).fetchall()

        redacted_count = 0
        for row in rows:
            if _contains_secret(row["result_json"]):
                cleaned = _redact_secrets(row["result_json"])
                if cleaned != row["result_json"]:
                    def _update(tid=row["id"], val=cleaned):
                        with db.get_conn() as conn:
                            conn.execute(
                                "UPDATE tasks SET result_json=? WHERE id=?",
                                (val, tid))
                    db._retry_write(_update)
                    redacted_count += 1
                    log_fn(f"[WATCHDOG] DLP: redacted secrets in task {row['id']}")

        if redacted_count:
            alerts.append({
                "level": "warning",
                "message": f"DLP: redacted secrets in {redacted_count} task result(s)",
            })
    except Exception as e:
        log_fn(f"[WATCHDOG] DLP scan error: {e}")
    return alerts


def scrub_knowledge_files(log_fn=print):
    """DLP: scan knowledge/ output files for leaked secrets."""
    alerts = []
    knowledge_dir = FLEET_DIR / "knowledge"
    if not knowledge_dir.exists():
        return alerts

    redacted_count = 0
    try:
        for ext in ("*.md", "*.json", "*.jsonl", "*.txt", "*.yaml", "*.yml",
                    "*.sh", "*.py", "*.log", "*.env", "*.cfg", "*.ini"):
            for fpath in knowledge_dir.rglob(ext):
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    if _contains_secret(text):
                        cleaned = _redact_secrets(text)
                        if cleaned != text:
                            fpath.write_text(cleaned, encoding="utf-8")
                            redacted_count += 1
                            log_fn(f"[WATCHDOG] DLP: redacted secrets in {fpath.name}")
                except Exception:
                    pass  # skip binary/locked files

        if redacted_count:
            alerts.append({
                "level": "warning",
                "message": f"DLP: redacted secrets in {redacted_count} knowledge file(s)",
            })
    except Exception as e:
        log_fn(f"[WATCHDOG] DLP knowledge scan error: {e}")
    return alerts


# ── Main entry (called by supervisor) ────────────────────────────────────────

def run_cycle(log_fn=print):
    """Run all watchdog checks. Returns list of alert dicts."""
    alerts = []
    alerts.extend(check_failure_streaks(log_fn))
    alerts.extend(check_stuck_reviews(log_fn))
    alerts.extend(scrub_recent_results(log_fn))
    # Knowledge file scan runs less frequently (caller controls)
    return alerts


def run_full_cycle(log_fn=print):
    """Run all checks including knowledge file scan (heavier, run less often)."""
    alerts = run_cycle(log_fn)
    alerts.extend(scrub_knowledge_files(log_fn))
    return alerts
