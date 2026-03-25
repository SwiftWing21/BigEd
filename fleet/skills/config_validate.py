"""
Config validation skill — validates fleet.toml against expected schema.

Checks:
  - Missing required sections and keys
  - Type mismatches (bool, int, float, str, list)
  - Value range violations (ports 1-65535, percentages 0-100, timeouts > 0)
  - Unknown top-level sections (possible typos or custom extensions)

Actions (via payload ``action`` key):
  validate  (default) — schema validation, output to knowledge/quality/
  drift               — compare fleet.toml against a saved baseline snapshot

Payload (validate):
  strict   bool  treat warnings as errors (default false)

Payload (drift):
  reset    bool  overwrite baseline with current config (default false)

Output (validate): knowledge/quality/config_validate_<date>.md
Output (drift):    knowledge/config/drift_<date>.md
"""
import json
import logging
from datetime import datetime, date
from pathlib import Path

SKILL_NAME = "config_validate"
DESCRIPTION = "Validate fleet.toml schema and detect configuration drift."
VERSION = "2.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "ops"

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
QUALITY_DIR = KNOWLEDGE_DIR / "quality"

# ── Drift detection constants ──────────────────────────────────────────────────
CONFIG_DIR = KNOWLEDGE_DIR / "config"
BASELINE_FILE = CONFIG_DIR / "fleet_toml_baseline.json"

# ── Expected schema ───────────────────────────────────────────────────────────
# Each section maps to {key: {type, required, min?, max?}}.
# Dotted section names (e.g. "models.tiers") traverse nested tables.

EXPECTED_SCHEMA = {
    "fleet": {
        "name": {"type": "str", "required": False},
        "disabled_agents": {"type": "list", "required": False},
        "eco_mode": {"type": "bool", "required": False},
        "idle_enabled": {"type": "bool", "required": False},
        "idle_timeout_secs": {"type": "int", "required": False, "min": 1},
        "max_workers": {"type": "int", "required": False, "min": 1, "max": 32},
        "offline_mode": {"type": "bool", "required": False},
        "air_gap_mode": {"type": "bool", "required": False},
        "api_keys_required": {"type": "bool", "required": False},
        "hitl_evolution": {"type": "bool", "required": False},
    },
    "models": {
        "local": {"type": "str", "required": True},
        "conductor_model": {"type": "str", "required": True},
        "keep_alive_mins": {"type": "int", "required": False, "min": 1, "max": 1440},
        "ollama_host": {"type": "str", "required": False},
        "vision_model": {"type": "str", "required": False},
    },
    "models.tiers": {
        "default": {"type": "str", "required": True},
        "mid": {"type": "str", "required": False},
        "low": {"type": "str", "required": False},
        "critical": {"type": "str", "required": False},
    },
    "workers": {
        "max_workers": {"type": "int", "required": False, "min": 1, "max": 32},
        "memory_limit_mb": {"type": "int", "required": False, "min": 64},
        "nice_level": {"type": "int", "required": False, "min": 0, "max": 19},
        "cpu_limit_percent": {"type": "int", "required": False, "min": 1, "max": 100},
        "coder_count": {"type": "int", "required": False, "min": 1, "max": 16},
    },
    "dashboard": {
        "port": {"type": "int", "required": True, "min": 1, "max": 65535},
        "bind_address": {"type": "str", "required": False},
        "enabled": {"type": "bool", "required": False},
        "auto_open": {"type": "bool", "required": False},
    },
    "thermal": {
        "gpu_max_sustained_c": {"type": "int", "required": False, "min": 50, "max": 100},
        "gpu_max_burst_c": {"type": "int", "required": False, "min": 50, "max": 105},
        "cpu_max_sustained_c": {"type": "int", "required": False, "min": 50, "max": 105},
        "cooldown_target_c": {"type": "int", "required": False, "min": 30, "max": 95},
        "poll_interval_secs": {"type": "int", "required": False, "min": 1},
        "grace_period_secs": {"type": "int", "required": False, "min": 1},
    },
    "thermal.vram": {
        "emergency": {"type": "float", "required": False, "min": 0.5, "max": 1.0},
        "high": {"type": "float", "required": False, "min": 0.5, "max": 1.0},
        "restore": {"type": "float", "required": False, "min": 0.1, "max": 1.0},
    },
    "security": {
        "dashboard_token": {"type": "str", "required": False},
        "admin_token": {"type": "str", "required": False},
        "operator_token": {"type": "str", "required": False},
        "sandbox_enabled": {"type": "bool", "required": False},
        "sandbox_skills": {"type": "list", "required": False},
    },
    "routing": {
        "ml_enabled": {"type": "bool", "required": False},
        "retrain_interval_hours": {"type": "int", "required": False, "min": 1},
        "min_training_samples": {"type": "int", "required": False, "min": 1},
        "fallback_to_iq": {"type": "bool", "required": False},
    },
    "training": {
        "exclusive_lock": {"type": "bool", "required": False},
        "auto_pause_gpu_tasks": {"type": "bool", "required": False},
        "lock_timeout_secs": {"type": "int", "required": False, "min": 60},
    },
    "experiments": {
        "auto_approve": {"type": "bool", "required": False},
        "ab_testing_enabled": {"type": "bool", "required": False},
        "min_samples_per_variant": {"type": "int", "required": False, "min": 1},
        "auto_promote_threshold": {"type": "float", "required": False, "min": 0.0, "max": 1.0},
    },
    "backup": {
        "enabled": {"type": "bool", "required": False},
        "interval_secs": {"type": "int", "required": False, "min": 60},
        "depth": {"type": "int", "required": False, "min": 0, "max": 100},
        "location": {"type": "str", "required": False},
    },
    "federation": {
        "enabled": {"type": "bool", "required": False},
        "peers": {"type": "list", "required": False},
        "overflow_threshold": {"type": "float", "required": False, "min": 0.0, "max": 1.0},
        "peer_timeout_secs": {"type": "int", "required": False, "min": 1},
        "discovery_port": {"type": "int", "required": False, "min": 1, "max": 65535},
    },
    "gpu": {
        "mode": {"type": "str", "required": False},
        "multi_gpu": {"type": "bool", "required": False},
        "gpu_count": {"type": "int", "required": False, "min": 0, "max": 64},
    },
    "review": {
        "enabled": {"type": "bool", "required": False},
        "max_rounds": {"type": "int", "required": False, "min": 1, "max": 10},
        "local_ctx": {"type": "int", "required": False, "min": 1024},
    },
    "context": {
        "max_turns": {"type": "int", "required": False, "min": 1, "max": 100},
        "max_tokens": {"type": "int", "required": False, "min": 256},
        "stale_hours": {"type": "int", "required": False, "min": 0},
    },
    "openclaw": {
        "port": {"type": "int", "required": False, "min": 1, "max": 65535},
    },
    "billing": {
        "enabled": {"type": "bool", "required": False},
        "currency": {"type": "str", "required": False},
    },
    "platform": {
        "enabled": {"type": "bool", "required": False},
        "max_managed_fleets": {"type": "int", "required": False, "min": 1},
        "default_agent_count": {"type": "int", "required": False, "min": 1, "max": 32},
    },
    "self_healing": {
        "enabled": {"type": "bool", "required": False},
        "health_sweep_interval": {"type": "int", "required": False, "min": 10},
        "max_task_retries": {"type": "int", "required": False, "min": 0, "max": 10},
        "agent_stuck_timeout": {"type": "int", "required": False, "min": 30},
        "circuit_breaker_threshold": {"type": "int", "required": False, "min": 1},
        "regression_threshold": {"type": "float", "required": False, "min": 0.0, "max": 1.0},
    },
    # Remaining known sections — validated for presence, key checks added as needed
    "naming": {
        "theme": {"type": "str", "required": False},
        "device_name": {"type": "str", "required": False},
    },
    "ditl": {
        "enabled": {"type": "bool", "required": False},
        "compliance_level": {"type": "str", "required": False},
        "data_retention_days": {"type": "int", "required": False, "min": 1},
    },
    "assistant": {
        "stt_enabled": {"type": "bool", "required": False},
        "tts_enabled": {"type": "bool", "required": False},
    },
    "launcher": {
        "profile": {"type": "str", "required": False},
        "close_behavior": {"type": "str", "required": False},
        "start_minimized": {"type": "bool", "required": False},
    },
    "affinity": {
        "enabled": {"type": "bool", "required": False},
    },
    "integrations": {
        "unifi_enabled": {"type": "bool", "required": False},
        "ha_enabled": {"type": "bool", "required": False},
        "mqtt_enabled": {"type": "bool", "required": False},
    },
    "sso": {
        "enabled": {"type": "bool", "required": False},
        "provider": {"type": "str", "required": False},
        "session_expiry_hours": {"type": "int", "required": False, "min": 1},
    },
    "walkthrough": {
        "completed": {"type": "bool", "required": False},
    },
    "budgets": {
        "period": {"type": "str", "required": False},
        "enforcement": {"type": "str", "required": False},
    },
    "autoboot": {
        "enabled": {"type": "bool", "required": False},
    },
    "idle": {
        "enabled": {"type": "bool", "required": False},
        "threshold_polls": {"type": "int", "required": False, "min": 1},
        "cooldown_secs": {"type": "int", "required": False, "min": 1},
        "skills": {"type": "list", "required": False},
    },
    "integrity": {
        "enabled": {"type": "bool", "required": False},
        "auto_manifest": {"type": "bool", "required": False},
    },
    "github": {
        "owner": {"type": "str", "required": False},
        "repo": {"type": "str", "required": False},
    },
    "modules": {
        "hub_url": {"type": "str", "required": False},
        "auto_update": {"type": "bool", "required": False},
        "verify_checksums": {"type": "bool", "required": False},
    },
    "mcp": {
        "enabled": {"type": "bool", "required": False},
        "config_path": {"type": "str", "required": False},
    },
    "filesystem": {
        "enforce": {"type": "bool", "required": False},
        "deny_by_default": {"type": "bool", "required": False},
        "log_all_access": {"type": "bool", "required": False},
    },
    "manual_mode": {
        "approval_required_threshold": {"type": "float", "required": False, "min": 0.0, "max": 1.0},
    },
    "enterprise": {
        "multi_tenant": {"type": "bool", "required": False},
        "tenant_id": {"type": "str", "required": False},
    },
    "payments": {
        "enabled": {"type": "bool", "required": False},
        "provider": {"type": "str", "required": False},
    },
    "triggers": {
        "file_watch_enabled": {"type": "bool", "required": False},
        "webhook_enabled": {"type": "bool", "required": False},
    },
    "schedules": {},  # dynamic keys — just acknowledge the section exists
    "boot": {
        "model_load_timeout": {"type": "int", "required": False, "min": 10},
    },
    "capacity": {
        "enabled": {"type": "bool", "required": False},
        "provider": {"type": "str", "required": False},
    },
    "dag": {
        "enabled": {"type": "bool", "required": False},
        "max_dag_depth": {"type": "int", "required": False, "min": 1},
        "max_dag_tasks": {"type": "int", "required": False, "min": 1},
    },
    "scaling": {
        "ml_predictor_enabled": {"type": "bool", "required": False},
        "predictor_retrain_hours": {"type": "int", "required": False, "min": 1},
    },
    "dispatch_bridge": {
        "enabled": {"type": "bool", "required": False},
        "dashboard_base_url": {"type": "str", "required": False},
    },
    "geo": {
        "enabled": {"type": "bool", "required": False},
        "default_region": {"type": "str", "required": False},
        "latency_routing": {"type": "bool", "required": False},
    },
    "compliance": {
        "enabled": {"type": "bool", "required": False},
        "schedule": {"type": "str", "required": False},
        "report_types": {"type": "list", "required": False},
        "retention_days": {"type": "int", "required": False, "min": 1},
    },
    "self_service": {
        "enabled": {"type": "bool", "required": False},
        "require_email_verification": {"type": "bool", "required": False},
        "default_plan": {"type": "str", "required": False},
        "max_api_keys_per_tenant": {"type": "int", "required": False, "min": 1},
    },
    "marketplace": {
        "enabled": {"type": "bool", "required": False},
        "require_review_before_publish": {"type": "bool", "required": False},
        "max_package_size_mb": {"type": "int", "required": False, "min": 1},
    },
}

# Type names to Python types for validation
_TYPE_MAP = {
    "str": str,
    "int": int,
    "float": (int, float),  # ints are acceptable where floats expected
    "bool": bool,
    "list": list,
}


def _validate_config(cfg, schema):
    """Validate a parsed config dict against the expected schema.

    Returns a list of (level, message) tuples where level is
    "error", "warning", or "info".
    """
    issues = []

    for section, keys in schema.items():
        # Traverse dotted section path (e.g. "models.tiers" -> cfg["models"]["tiers"])
        parts = section.split(".")
        section_data = cfg
        for p in parts:
            section_data = section_data.get(p, {}) if isinstance(section_data, dict) else {}

        if not section_data and any(v.get("required") for v in keys.values()):
            issues.append(("error", f"Missing required section [{section}]"))
            continue

        if not section_data:
            continue

        for key, spec in keys.items():
            val = section_data.get(key)
            if val is None:
                if spec.get("required"):
                    issues.append(("error", f"[{section}] missing required key '{key}'"))
                continue

            # Type check
            expected_type = spec.get("type")
            if expected_type and expected_type in _TYPE_MAP:
                py_type = _TYPE_MAP[expected_type]
                # bool is a subclass of int in Python — check bool before int
                if expected_type == "int" and isinstance(val, bool):
                    issues.append(("warning", f"[{section}].{key} should be int, got bool"))
                elif expected_type == "float" and isinstance(val, bool):
                    issues.append(("warning", f"[{section}].{key} should be float, got bool"))
                elif not isinstance(val, py_type):
                    issues.append(("warning", f"[{section}].{key} should be {expected_type}, got {type(val).__name__}"))

            # Range check (only for numeric values)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if "min" in spec and val < spec["min"]:
                    issues.append(("error", f"[{section}].{key} = {val} below minimum {spec['min']}"))
                if "max" in spec and val > spec["max"]:
                    issues.append(("error", f"[{section}].{key} = {val} above maximum {spec['max']}"))

    # Warn on completely unknown top-level sections
    known_sections = set(s.split(".")[0] for s in schema)
    for section in cfg:
        if section not in known_sections and isinstance(cfg[section], dict):
            issues.append(("info", f"Unknown section [{section}] — may be a typo or custom extension"))

    return issues


def _save_report(issues, cfg_path):
    """Write a markdown report to knowledge/quality/."""
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = QUALITY_DIR / f"config_validate_{date_str}.md"

    errors = [i for i in issues if i[0] == "error"]
    warnings = [i for i in issues if i[0] == "warning"]
    infos = [i for i in issues if i[0] == "info"]

    lines = [
        f"# Config Validation Report — {date_str}",
        "",
        f"**Source:** `{cfg_path}`",
        f"**Errors:** {len(errors)} | **Warnings:** {len(warnings)} | **Info:** {len(infos)}",
        "",
    ]

    if not issues:
        lines.append("All checks passed — no issues found.")
    else:
        if errors:
            lines.append("## Errors")
            lines.append("")
            for _, msg in errors:
                lines.append(f"- {msg}")
            lines.append("")
        if warnings:
            lines.append("## Warnings")
            lines.append("")
            for _, msg in warnings:
                lines.append(f"- {msg}")
            lines.append("")
        if infos:
            lines.append("## Info")
            lines.append("")
            for _, msg in infos:
                lines.append(f"- {msg}")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


# ── Drift helpers ─────────────────────────────────────────────────────────────

def _flatten(data, prefix=""):
    """Flatten a nested dict into dot-separated key-value pairs."""
    flat = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        else:
            flat[key] = v
    return flat


def _diff(baseline, current):
    """Compute added, removed, and changed keys between two flat dicts."""
    base_keys = set(baseline.keys())
    curr_keys = set(current.keys())

    added = sorted(curr_keys - base_keys)
    removed = sorted(base_keys - curr_keys)
    changed = []
    for k in sorted(base_keys & curr_keys):
        if str(baseline[k]) != str(current[k]):
            changed.append((k, baseline[k], current[k]))

    return added, removed, changed


def _build_drift_report(added, removed, changed, is_first_run):
    """Render the drift report as Markdown."""
    today = date.today().isoformat()
    lines = [f"# Config Drift Report — {today}", ""]

    if is_first_run:
        lines.append("**First run — baseline saved.** No drift to report.")
        lines.append("")
        return "\n".join(lines)

    total = len(added) + len(removed) + len(changed)
    lines.append(
        f"**Total drifts:** {total} | "
        f"Added: {len(added)} | Removed: {len(removed)} | Changed: {len(changed)}"
    )
    lines.append("")

    if not total:
        lines.append("No configuration drift detected. Fleet.toml matches baseline.")
        lines.append("")
        return "\n".join(lines)

    if added:
        lines.extend(["## Added Keys", ""] + [f"- `{k}`" for k in added] + [""])
    if removed:
        lines.extend(["## Removed Keys", ""] + [f"- `{k}`" for k in removed] + [""])
    if changed:
        lines.extend([
            "## Changed Keys", "",
            "| Key | Baseline | Current |",
            "|-----|----------|---------|",
        ] + [f"| `{k}` | `{old}` | `{new}` |" for k, old, new in changed] + [""])

    return "\n".join(lines)


def _run_drift(payload, config):
    """Detect configuration drift — action: drift."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    toml_path = FLEET_DIR / "fleet.toml"
    if not toml_path.exists():
        return {"status": "error", "message": "fleet.toml not found"}

    try:
        import config as cfg_mod
        parsed = cfg_mod.load_config()
    except Exception:
        log.warning("config_validate drift: failed to parse fleet.toml", exc_info=True)
        return {"status": "error", "message": "Failed to parse fleet.toml"}

    current = _flatten(parsed)
    reset = payload.get("reset", False)
    is_first_run = not BASELINE_FILE.exists() or reset

    if is_first_run:
        try:
            BASELINE_FILE.write_text(json.dumps(current, indent=2, default=str), encoding="utf-8")
        except Exception:
            log.warning("config_validate drift: failed to save baseline", exc_info=True)
            return {"status": "error", "message": "Failed to save baseline"}
        report = _build_drift_report([], [], [], is_first_run=True)
        added, removed, changed = [], [], []
    else:
        try:
            baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("config_validate drift: failed to read baseline", exc_info=True)
            return {"status": "error", "message": "Failed to read baseline"}

        added, removed, changed = _diff(baseline, current)
        report = _build_drift_report(added, removed, changed, is_first_run=False)

        # Update baseline after diffing
        try:
            BASELINE_FILE.write_text(json.dumps(current, indent=2, default=str), encoding="utf-8")
        except Exception:
            log.warning("config_validate drift: failed to update baseline", exc_info=True)

    today = date.today().isoformat()
    report_path = CONFIG_DIR / f"drift_{today}.md"
    try:
        report_path.write_text(report, encoding="utf-8")
    except Exception:
        log.warning("config_validate drift: failed to write report", exc_info=True)
        return {"status": "error", "message": "Failed to write drift report"}

    return {
        "status": "ok",
        "saved_to": str(report_path),
        "first_run": is_first_run,
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "total_keys": len(current),
        "result": (
            f"Config drift: {len(added)} added, {len(removed)} removed, "
            f"{len(changed)} changed — report saved to {report_path}"
        ),
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def run(payload: dict, config: dict) -> dict:
    """Validate fleet.toml or detect drift depending on ``action``."""
    action = payload.get("action", "validate")
    if action == "drift":
        return _run_drift(payload, config)

    # Default: schema validation
    import config as cfg_mod  # lazy import

    try:
        cfg = cfg_mod.load_config()
    except Exception:
        log.warning("config_validate: failed to load fleet.toml", exc_info=True)
        return {
            "status": "error",
            "error": "Failed to load fleet.toml — check file exists and is valid TOML",
        }

    strict = payload.get("strict", False)

    issues = _validate_config(cfg, EXPECTED_SCHEMA)

    errors = [i for i in issues if i[0] == "error"]
    warnings = [i for i in issues if i[0] == "warning"]
    infos = [i for i in issues if i[0] == "info"]

    report_path = _save_report(issues, cfg_mod.CONFIG_PATH)

    total_issues = len(errors) + (len(warnings) if strict else 0)
    status = "ok" if total_issues == 0 else "warning" if not errors else "error"

    return {
        "status": status,
        "errors": len(errors),
        "warnings": len(warnings),
        "infos": len(infos),
        "issues": [{"level": level, "message": msg} for level, msg in issues],
        "report": report_path,
        "result": (
            f"Config validation: {len(errors)} errors, {len(warnings)} warnings, "
            f"{len(infos)} info — report saved to {report_path}"
        ),
    }
