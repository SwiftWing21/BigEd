"""Audit Sanitizer — strips sensitive data from fleet audit output.

Deep-walks all dicts/lists in audit results and removes or redacts:
  - Absolute file paths (replaced with relative)
  - Keys matching sensitive patterns (password, secret, token, etc.)
  - OS/system fingerprints (hostname, IP addresses, platform, pid)

Preserves: scores, grades, dimension names, findings categories/counts,
timestamps, before/after deltas.

Configurable via a dict or optional JSON config file.
"""
import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("audit_sanitizer")

# ── Default Configuration ────────────────────────────────────────────────

_DEFAULT_CONFIG: dict = {
    # Keys whose values get stripped entirely (case-insensitive substring match)
    "strip_keys": [
        "password", "secret", "key", "token", "api_key",
        "endpoint", "hostname", "username", "env", "pid", "port",
        "ip_address", "home_dir", "user_home",
    ],
    # Keys that are always preserved even if they match strip_keys
    "keep_keys": [
        "api_health", "api_key_count",
    ],
    # Path prefixes that indicate absolute paths needing sanitization
    "path_prefixes": [
        "/home/", "/c/Users/", "C:\\Users\\", "C:/Users/",
        "/opt/", "/usr/", "/var/", "/tmp/",
        "/root/", "/etc/",
    ],
    # Regex for Windows drive letters (D:\, E:\, etc.)
    "windows_drive_re": r"[A-Za-z]:[\\\/]",
    # Regex for IP addresses (v4)
    "ipv4_re": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    # OS fingerprint keys to strip
    "os_fingerprint_keys": [
        "os.name", "os_name", "platform", "sys.platform",
        "hostname", "machine", "node", "release", "version",
    ],
    # Preserve these keys unconditionally (scores, grades, etc.)
    "preserve_keys": [
        "score", "auto_score", "grade", "auto_grade", "manual_grade",
        "overall_score", "overall_grade", "s_tier_grade", "s_tier_eligible",
        "dimension", "dimensions", "tier", "confidence",
        "weight", "part", "type", "message",
        "timestamp", "count", "total", "ok",
        "passed", "failed", "stale", "found", "missing",
        "divergence", "divergences", "acknowledged",
        "gaps", "issues", "evidence",
        "ratchet_grade", "ratchet_score", "ratchet_violations",
        "context_avg", "output_avg",
        "under_1500", "total_checked",
        "ruff_clean", "no_raw_sqlite", "no_bare_excepts",
        "rbac_ok", "rbac_roles", "path_traversal_blocked",
        "page_score", "feedback_score",
        "sse_template", "audit_log",
        "dimensions_scored",
    ],
}

# Compiled regexes (built once at import time)
_IPV4_RE = re.compile(_DEFAULT_CONFIG["ipv4_re"])
_WIN_DRIVE_RE = re.compile(_DEFAULT_CONFIG["windows_drive_re"])


# ── Config Loader ────────────────────────────────────────────────────────

def load_sanitizer_config(config_path: Path | None = None) -> dict:
    """Load sanitizer config from JSON file, falling back to defaults.

    Args:
        config_path: optional path to a JSON config file. Keys in the file
                     are merged over the defaults.

    Returns:
        Merged config dict.
    """
    config = copy.deepcopy(_DEFAULT_CONFIG)
    if config_path and config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            for key in ("strip_keys", "keep_keys", "path_prefixes",
                        "os_fingerprint_keys", "preserve_keys"):
                if key in overrides:
                    config[key] = overrides[key]
            for key in ("windows_drive_re", "ipv4_re"):
                if key in overrides:
                    config[key] = overrides[key]
        except Exception:
            log.warning("Failed to load sanitizer config from %s", config_path,
                        exc_info=True)
    return config


# ── Path Sanitization ────────────────────────────────────────────────────

def _sanitize_path(value: str, config: dict) -> str:
    """Replace absolute paths with relative equivalents.

    Detects common prefixes (/home/user/..., C:\\Users\\..., /opt/..., etc.)
    and strips them down to the project-relative portion.
    """
    result = value

    # Handle Windows-style paths: C:\Users\max\Projects\Education\fleet\foo
    # Convert to forward slashes first for uniform processing
    for prefix in config.get("path_prefixes", []):
        if prefix in result:
            # Find the path segment after common project markers
            idx = result.find(prefix)
            path_portion = result[idx:]
            # Normalize to forward slashes
            normalized = path_portion.replace("\\", "/")
            # Try to find a project-relative anchor
            for anchor in ("fleet/", "BigEd/", "Education/fleet/",
                           "Education/BigEd/", "autoresearch/"):
                anchor_idx = normalized.find(anchor)
                if anchor_idx >= 0:
                    relative = normalized[anchor_idx:]
                    result = result[:idx] + relative
                    break
            else:
                # No anchor found — just strip the home/system prefix
                # Keep the last 2 path components
                parts = normalized.rstrip("/").split("/")
                if len(parts) > 2:
                    result = result[:idx] + "/".join(parts[-2:])

    # Handle bare Windows drive letters not caught by prefix list
    if _WIN_DRIVE_RE.search(result):
        normalized = result.replace("\\", "/")
        for anchor in ("fleet/", "BigEd/", "Education/"):
            anchor_idx = normalized.find(anchor)
            if anchor_idx >= 0:
                result = normalized[anchor_idx:]
                break

    return result


# ── Value Sanitization ───────────────────────────────────────────────────

def _sanitize_value(value: Any, config: dict) -> Any:
    """Sanitize a single value — redact IPs, paths, OS fingerprints."""
    if isinstance(value, str):
        # Replace IP addresses with placeholder
        sanitized = _IPV4_RE.sub("[REDACTED_IP]", value)
        # Sanitize absolute paths
        sanitized = _sanitize_path(sanitized, config)
        return sanitized
    return value


def _is_strip_key(key: str, config: dict) -> bool:
    """Check if a key matches the strip list but not the keep list."""
    key_lower = key.lower()

    # Check keep list first (exact match or substring)
    for keep in config.get("keep_keys", []):
        if keep.lower() == key_lower or keep.lower() in key_lower:
            return False

    # Check preserve list (exact match)
    if key_lower in [k.lower() for k in config.get("preserve_keys", [])]:
        return False

    # Check strip list (substring match)
    for strip in config.get("strip_keys", []):
        if strip.lower() in key_lower:
            return True

    # Check OS fingerprint keys (exact match)
    for fp_key in config.get("os_fingerprint_keys", []):
        if fp_key.lower() == key_lower:
            return True

    return False


# ── Deep Walk ────────────────────────────────────────────────────────────

def _sanitize_dict(data: dict, config: dict) -> dict:
    """Deep-walk a dict, stripping sensitive keys and sanitizing values."""
    result = {}
    for key, value in data.items():
        if _is_strip_key(key, config):
            result[key] = "[REDACTED]"
            continue
        result[key] = _sanitize_any(value, config)
    return result


def _sanitize_list(data: list, config: dict) -> list:
    """Deep-walk a list, sanitizing each element."""
    return [_sanitize_any(item, config) for item in data]


def _sanitize_any(data: Any, config: dict) -> Any:
    """Dispatch to the appropriate sanitizer based on type."""
    if isinstance(data, dict):
        return _sanitize_dict(data, config)
    if isinstance(data, list):
        return _sanitize_list(data, config)
    if isinstance(data, str):
        return _sanitize_value(data, config)
    # int, float, bool, None — pass through
    return data


# ── Public API ───────────────────────────────────────────────────────────

def sanitize(data: Any, config: dict | None = None,
             config_path: Path | None = None) -> Any:
    """Sanitize audit output data, stripping sensitive information.

    Deep-walks all dicts/lists and:
      - Replaces absolute paths with relative
      - Strips keys matching sensitive patterns
      - Redacts IP addresses
      - Preserves scores, grades, dimensions, timestamps, findings

    Args:
        data:        The audit data to sanitize (dict, list, or primitive).
        config:      Optional config dict (merged over defaults).
        config_path: Optional path to a JSON config file.

    Returns:
        A deep copy of the data with sensitive information removed.
    """
    effective_config = load_sanitizer_config(config_path)
    if config:
        for key, value in config.items():
            effective_config[key] = value

    # Work on a deep copy to avoid mutating the original
    return _sanitize_any(copy.deepcopy(data), effective_config)


def sanitize_scores(scores: list[dict], config: dict | None = None) -> list[dict]:
    """Convenience: sanitize a list of score dicts from get_latest_scores().

    Preserves the score/grade/dimension structure while stripping
    sensitive details from auto_detail JSON blobs.
    """
    sanitized = []
    effective_config = load_sanitizer_config()
    if config:
        for key, value in config.items():
            effective_config[key] = value

    for score_row in scores:
        row = dict(score_row)
        # Parse and sanitize auto_detail if it's a JSON string
        if isinstance(row.get("auto_detail"), str):
            try:
                detail = json.loads(row["auto_detail"])
                row["auto_detail"] = json.dumps(
                    _sanitize_any(detail, effective_config)
                )
            except (json.JSONDecodeError, TypeError):
                row["auto_detail"] = _sanitize_value(
                    row["auto_detail"], effective_config
                )
        elif isinstance(row.get("auto_detail"), dict):
            row["auto_detail"] = _sanitize_any(
                row["auto_detail"], effective_config
            )
        sanitized.append(row)
    return sanitized
