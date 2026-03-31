"""Settings and configuration endpoints — theme, TOML editor, schema.

Extracted from dashboard.py (Phase 4 of dashboard decomposition).
All /api/settings/* routes live here.
"""
import logging
import re

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    FLEET_DIR, _load_config, _require_role,
    _check_rate_limit, _safe_error,
)

log = logging.getLogger("dashboard.settings")

settings_bp = Blueprint("settings", __name__)


# ── Theme ──────────────────────────────────────────────────────────────────

_VALID_THEMES = {"classic", "modern", "figma"}


@settings_bp.route("/api/settings/theme", methods=["GET"])
def api_settings_theme_get():
    """Return current dashboard theme from fleet.toml."""
    try:
        cfg = _load_config()
        theme = cfg.get("dashboard", {}).get("theme", "figma")
        if theme not in _VALID_THEMES:
            theme = "figma"
        return jsonify({"theme": theme})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@settings_bp.route("/api/settings/theme", methods=["POST"])
def api_settings_theme_set():
    """Update dashboard theme in fleet.toml.

    Accepts JSON body: {"theme": "classic"|"modern"|"figma"}
    Writes to [dashboard] theme key using tomlkit to preserve formatting.
    """
    deny = _require_role("operator")
    if deny:
        return deny
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "").strip().lower()
    if theme not in _VALID_THEMES:
        return jsonify({"error": f"Invalid theme '{theme}'. Valid: {sorted(_VALID_THEMES)}"}), 400

    try:
        import tomlkit
        toml_path = FLEET_DIR / "fleet.toml"
        doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
        if "dashboard" not in doc:
            doc["dashboard"] = tomlkit.table()
        doc["dashboard"]["theme"] = theme
        toml_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        return jsonify({"ok": True, "theme": theme})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Settings Editor ────────────────────────────────────────────────────────

# Sections that can be edited via the API (safety: never expose security tokens)
_EDITABLE_SECTIONS = {
    "fleet", "models", "thermal", "training", "dashboard", "workers",
    "idle", "backup", "review", "gpu", "naming", "affinity", "context",
    "budgets", "triggers", "schedules", "assistant", "boot", "ollama",
}

# Sections that are read-only via the API
_READONLY_SECTIONS = {
    "security", "ditl", "walkthrough", "enterprise", "filesystem",
}

# Schema descriptions for the settings editor UI
_SETTINGS_SCHEMA = {
    "fleet": {
        "eco_mode": {"type": "bool", "description": "Reduce resource usage"},
        "idle_enabled": {"type": "bool", "description": "Workers self-improve when idle"},
        "idle_timeout_secs": {"type": "int", "description": "Seconds before idle mode activates"},
        "max_workers": {"type": "int", "description": "Maximum active workers at boot"},
        "offline_mode": {"type": "bool", "description": "Disable external API calls"},
        "hitl_evolution": {"type": "bool", "description": "Require human approval for evolution"},
    },
    "models": {
        "local": {"type": "str", "description": "Default local model (Ollama)"},
        "complex": {"type": "str", "description": "Complex task model"},
        "complex_provider": {"type": "str", "description": "Provider for complex tasks: claude | gemini | local"},
        "conductor_model": {"type": "str", "description": "CPU-pinned chat model"},
        "keep_alive_mins": {"type": "int", "description": "Minutes to keep models loaded"},
    },
    "thermal": {
        "gpu_max_sustained_c": {"type": "int", "description": "Max sustained GPU temp (C)"},
        "gpu_max_burst_c": {"type": "int", "description": "Hard GPU temp ceiling (C)"},
        "cpu_max_sustained_c": {"type": "int", "description": "Max sustained CPU temp (C)"},
        "cooldown_target_c": {"type": "int", "description": "Resume GPU below this temp (C)"},
        "poll_interval_secs": {"type": "int", "description": "Temp check interval (seconds)"},
    },
    "dashboard": {
        "enabled": {"type": "bool", "description": "Enable web dashboard"},
        "port": {"type": "int", "description": "Dashboard port"},
        "auto_open": {"type": "bool", "description": "Open browser on fleet boot"},
        "bind_address": {"type": "str", "description": "Listen address (127.0.0.1 or 0.0.0.0)"},
    },
    "workers": {
        "nice_level": {"type": "int", "description": "OS priority level"},
        "cpu_limit_percent": {"type": "int", "description": "CPU limit per worker (%)"},
        "coder_count": {"type": "int", "description": "Number of coder instances"},
        "memory_limit_mb": {"type": "int", "description": "Max memory per worker (MB)"},
    },
    "backup": {
        "enabled": {"type": "bool", "description": "Enable auto-save backups"},
        "interval_secs": {"type": "int", "description": "Backup interval (seconds, min 180)"},
        "depth": {"type": "int", "description": "Max backups to keep"},
        "location": {"type": "str", "description": "Backup directory path"},
    },
    "training": {
        "exclusive_lock": {"type": "bool", "description": "Only 1 training process at a time"},
        "auto_pause_gpu_tasks": {"type": "bool", "description": "Pause GPU skills during training"},
        "default_profile": {"type": "str", "description": "Training profile: conservative | aggressive | exploratory"},
    },
    "review": {
        "enabled": {"type": "bool", "description": "Enable evaluator-optimizer review pass"},
        "max_rounds": {"type": "int", "description": "Max review-reject cycles per task"},
        "provider": {"type": "str", "description": "Review provider: api | subscription | local"},
    },
    "idle": {
        "enabled": {"type": "bool", "description": "Enable idle self-improvement"},
        "threshold_polls": {"type": "int", "description": "Idle polls before activation"},
        "cooldown_secs": {"type": "int", "description": "Min seconds between idle runs"},
    },
    "gpu": {
        "mode": {"type": "str", "description": "GPU mode: eco | full"},
        "multi_gpu": {"type": "bool", "description": "Enable multi-GPU splitting"},
    },
    "ollama": {
        "flash_attention": {"type": "str", "description": "Flash attention: auto | on | off"},
        "kv_cache_type": {"type": "str", "description": "KV cache quantization: auto | f16 | q8_0 | q4_0 | tq"},
        "num_parallel": {"type": "str", "description": "Concurrent requests: auto | 1-8"},
        "max_loaded_models": {"type": "str", "description": "Max models in VRAM: auto | 1-8"},
    },
    "context": {
        "max_turns": {"type": "int", "description": "Sliding window context turns"},
        "max_tokens": {"type": "int", "description": "Token budget for context"},
        "stale_hours": {"type": "int", "description": "Clear contexts older than this"},
    },
}


@settings_bp.route("/api/settings")
def api_settings():
    """Return fleet.toml as JSON with read-only sections marked."""
    try:
        cfg = _load_config()
        result = {}
        for section, values in cfg.items():
            if isinstance(values, dict):
                result[section] = {
                    "values": values,
                    "readonly": section in _READONLY_SECTIONS,
                    "editable": section in _EDITABLE_SECTIONS,
                }
            else:
                # Top-level scalar (rare in fleet.toml but handle gracefully)
                result[section] = {
                    "values": values,
                    "readonly": True,
                    "editable": False,
                }
        return jsonify({"status": "ok", "settings": result})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@settings_bp.route("/api/settings/<section>", methods=["PUT"])
def api_settings_update(section):
    """Update a TOML section. Body: {key: value, ...}

    Only editable sections can be modified. Security/DITL/enterprise are read-only.
    """
    deny = _require_role("operator")
    if deny:
        return deny
    if not _check_rate_limit("settings_update", max_per_min=10):
        return jsonify({"error": "Rate limited"}), 429

    if section not in _EDITABLE_SECTIONS:
        return jsonify({"error": f"Section '{section}' is read-only or does not exist"}), 403

    # Validate section name format
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', section):
        return jsonify({"error": "Invalid section name"}), 400

    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Body must be a JSON object with key-value pairs"}), 400

        # Read current TOML
        toml_path = FLEET_DIR / "fleet.toml"
        content = toml_path.read_text(encoding="utf-8")

        # Apply updates line by line within the section
        updated_keys = []
        for key, value in data.items():
            # Validate key format
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', key):
                continue

            # Format value for TOML
            if isinstance(value, bool):
                toml_val = "true" if value else "false"
            elif isinstance(value, int):
                toml_val = str(value)
            elif isinstance(value, float):
                toml_val = str(value)
            elif isinstance(value, str):
                toml_val = f'"{value}"'
            elif isinstance(value, list):
                # Format list items
                items = []
                for item in value:
                    if isinstance(item, str):
                        items.append(f'"{item}"')
                    else:
                        items.append(str(item))
                toml_val = "[" + ", ".join(items) + "]"
            else:
                continue  # Skip unsupported types

            # Try to replace existing key in the content
            pattern = rf'^({re.escape(key)}\s*=\s*).*$'
            new_line = f'{key} = {toml_val}'
            new_content = re.sub(pattern, new_line, content, count=1, flags=re.MULTILINE)

            if new_content != content:
                content = new_content
                updated_keys.append(key)

        if updated_keys:
            toml_path.write_text(content, encoding="utf-8")

            # Reload config cache
            try:
                from config import reload_config
                reload_config()
            except Exception:
                pass

        return jsonify({
            "status": "ok",
            "section": section,
            "updated_keys": updated_keys,
            "total_updated": len(updated_keys),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@settings_bp.route("/api/settings/schema")
def api_settings_schema():
    """Return editable sections with types and descriptions for UI form generation."""
    return jsonify({
        "status": "ok",
        "schema": _SETTINGS_SCHEMA,
        "editable_sections": sorted(_EDITABLE_SECTIONS),
        "readonly_sections": sorted(_READONLY_SECTIONS),
    })
