"""Probe and validate MCP server configurations — reads .mcp.json from
project root, validates each server entry (required fields, command existence),
and reports configuration health.

Does NOT start any MCP servers — validation only.
Saves report to knowledge/mcp/mcp_probe_YYYY-MM-DD.md.
"""
import json
import logging
import shutil
from datetime import date
from pathlib import Path

SKILL_NAME = "mcp_probe"
DESCRIPTION = (
    "Probe and validate MCP server configurations from .mcp.json — "
    "checks required fields and command availability without starting servers."
)
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
PROJECT_ROOT = FLEET_DIR.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
MCP_DIR = KNOWLEDGE_DIR / "mcp"

# Required fields per server type
REQUIRED_FIELDS = {
    "stdio": ["command", "args"],
    "http": ["url"],
}


def _find_command(command):
    """Check if a command is available on PATH or as an absolute path."""
    if not command:
        return False, "empty command"

    # Absolute path check
    cmd_path = Path(command)
    if cmd_path.is_absolute():
        if cmd_path.exists():
            return True, str(cmd_path)
        return False, f"absolute path not found: {command}"

    # PATH lookup via shutil.which
    found = shutil.which(command)
    if found:
        return True, found

    # Windows: also check common locations for npx
    import sys
    if sys.platform == "win32" and command in ("npx", "npx.cmd", "node"):
        common_dirs = [
            Path.home() / "AppData" / "Roaming" / "npm",
            Path("C:/Program Files/nodejs"),
        ]
        for d in common_dirs:
            candidate = d / (command + ".cmd") if not command.endswith(".cmd") else d / command
            if candidate.exists():
                return True, str(candidate)

    return False, f"not found on PATH: {command}"


def _validate_server(name, entry):
    """Validate a single MCP server entry. Returns a dict with validation results."""
    result = {
        "name": name,
        "valid": True,
        "warnings": [],
        "errors": [],
        "info": {},
    }

    if not isinstance(entry, dict):
        result["valid"] = False
        result["errors"].append("Server entry is not a dictionary")
        return result

    # Determine server type
    server_type = entry.get("type", "")
    if not server_type:
        # Infer type from fields
        if "command" in entry:
            server_type = "stdio"
        elif "url" in entry:
            server_type = "http"
        else:
            result["valid"] = False
            result["errors"].append(
                "Cannot determine server type — no 'type', 'command', or 'url' field"
            )
            return result

    result["info"]["type"] = server_type

    # Check required fields
    required = REQUIRED_FIELDS.get(server_type, [])
    for field in required:
        if field not in entry:
            result["valid"] = False
            result["errors"].append(f"Missing required field: '{field}'")

    # Type-specific validation
    if server_type == "stdio":
        command = entry.get("command", "")
        args = entry.get("args", [])

        result["info"]["command"] = command
        result["info"]["args"] = args if isinstance(args, list) else []

        if not isinstance(args, list):
            result["warnings"].append(
                f"'args' should be a list, got {type(args).__name__}"
            )

        if command:
            found, location = _find_command(command)
            result["info"]["command_found"] = found
            result["info"]["command_location"] = location
            if not found:
                result["warnings"].append(f"Command not found: {location}")

        # Check for env vars that may be required
        env = entry.get("env", {})
        if env and isinstance(env, dict):
            import os
            missing_env = []
            for key, val in env.items():
                if val and val.startswith("$"):
                    env_name = val.lstrip("$")
                    if not os.environ.get(env_name):
                        missing_env.append(env_name)
            if missing_env:
                result["warnings"].append(
                    f"Environment variables not set: {', '.join(missing_env)}"
                )

    elif server_type == "http":
        url = entry.get("url", "")
        result["info"]["url"] = url
        if url and not url.startswith(("http://", "https://")):
            result["warnings"].append(f"URL does not start with http(s)://: {url}")

    # Optional metadata
    if "description" in entry:
        result["info"]["description"] = entry["description"]
    if "skills" in entry:
        result["info"]["skills"] = entry["skills"]

    return result


def _build_report(config_path, server_results, raw_config):
    """Build a Markdown report from validation results."""
    today = date.today().isoformat()
    total = len(server_results)
    valid = sum(1 for r in server_results if r["valid"])
    with_warnings = sum(1 for r in server_results if r["warnings"])
    invalid = total - valid

    lines = [
        f"# MCP Configuration Probe Report — {today}",
        "",
        f"**Config file:** `{config_path}`",
        f"**Servers defined:** {total}",
        f"**Valid:** {valid} | **Invalid:** {invalid} | **Warnings:** {with_warnings}",
        "",
    ]

    # Health status
    if invalid == 0 and with_warnings == 0:
        lines.append("## Status: HEALTHY")
        lines.append("")
        lines.append("All MCP server configurations are valid with no warnings.")
    elif invalid == 0:
        lines.append("## Status: OK (with warnings)")
        lines.append("")
        lines.append("All configurations are structurally valid, but some have warnings.")
    else:
        lines.append("## Status: ISSUES FOUND")
        lines.append("")
        lines.append(f"{invalid} server(s) have configuration errors.")
    lines.append("")

    # Per-server details
    lines.append("## Server Details")
    lines.append("")

    for r in server_results:
        status_icon = "PASS" if r["valid"] else "FAIL"
        lines.append(f"### {r['name']} [{status_icon}]")
        lines.append("")

        info = r.get("info", {})
        if "type" in info:
            lines.append(f"- **Type:** {info['type']}")
        if "command" in info:
            lines.append(f"- **Command:** `{info['command']}`")
        if "args" in info:
            args_str = " ".join(str(a) for a in info["args"])
            lines.append(f"- **Args:** `{args_str}`")
        if "url" in info:
            lines.append(f"- **URL:** {info['url']}")
        if "command_found" in info:
            found_label = "Yes" if info["command_found"] else "No"
            lines.append(
                f"- **Command on PATH:** {found_label} "
                f"({info.get('command_location', 'N/A')})"
            )
        if "description" in info:
            lines.append(f"- **Description:** {info['description']}")
        if "skills" in info:
            lines.append(f"- **Skills:** {', '.join(info['skills'])}")

        if r["errors"]:
            lines.append("")
            lines.append("**Errors:**")
            for e in r["errors"]:
                lines.append(f"- {e}")

        if r["warnings"]:
            lines.append("")
            lines.append("**Warnings:**")
            for w in r["warnings"]:
                lines.append(f"- {w}")

        lines.append("")

    return "\n".join(lines)


def run(payload, config):
    """Probe and validate MCP server configurations."""
    import db

    MCP_DIR.mkdir(parents=True, exist_ok=True)

    # Locate .mcp.json — check project root and fleet dir
    config_path = payload.get("config_path")
    if config_path:
        mcp_file = Path(config_path)
    else:
        mcp_file = PROJECT_ROOT / ".mcp.json"
        if not mcp_file.exists():
            mcp_file = FLEET_DIR / ".mcp.json"

    if not mcp_file.exists():
        return {
            "status": "skip",
            "message": f"No .mcp.json found at {PROJECT_ROOT / '.mcp.json'}",
        }

    # Parse config
    try:
        raw_text = mcp_file.read_text(encoding="utf-8")
        raw_config = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "message": f"Invalid JSON in {mcp_file}: {exc}",
        }
    except Exception:
        log.warning("Failed to read MCP config at %s", mcp_file, exc_info=True)
        return {
            "status": "error",
            "message": f"Could not read {mcp_file}",
        }

    # Extract server definitions — support both flat and nested formats
    # Format 1: {"mcpServers": {"name": {...}}}
    # Format 2: {"name": {...}}
    servers = raw_config.get("mcpServers", raw_config)
    if not isinstance(servers, dict):
        return {
            "status": "error",
            "message": "MCP config does not contain a valid server dictionary",
        }

    # Filter out non-server keys (like metadata)
    server_entries = {}
    for key, val in servers.items():
        if isinstance(val, dict):
            server_entries[key] = val

    if not server_entries:
        return {
            "status": "skip",
            "message": "No server entries found in .mcp.json",
        }

    # Validate each server
    results = []
    for name, entry in sorted(server_entries.items()):
        try:
            result = _validate_server(name, entry)
            results.append(result)
        except Exception:
            log.warning("Validation failed for server %s", name, exc_info=True)
            results.append({
                "name": name,
                "valid": False,
                "warnings": [],
                "errors": ["Validation crashed unexpectedly"],
                "info": {},
            })

    # Build and save report
    report = _build_report(str(mcp_file), results, raw_config)
    out_file = MCP_DIR / f"mcp_probe_{date.today().isoformat()}.md"
    out_file.write_text(report, encoding="utf-8")

    total = len(results)
    valid = sum(1 for r in results if r["valid"])
    with_warnings = sum(1 for r in results if r["warnings"])

    # Save summary to DB for tracking
    summary = {
        "config_path": str(mcp_file),
        "total_servers": total,
        "valid": valid,
        "invalid": total - valid,
        "with_warnings": with_warnings,
        "servers": {r["name"]: r["valid"] for r in results},
    }

    try:
        def _save():
            def _do():
                with db.get_conn() as conn:
                    conn.execute(
                        "INSERT INTO tasks (type, status, payload_json, result_json) "
                        "VALUES (?, 'DONE', ?, ?)",
                        (
                            SKILL_NAME,
                            json.dumps({"config_path": str(mcp_file)}),
                            json.dumps(summary),
                        ),
                    )
            db._retry_write(_do)
        _save()
    except Exception:
        log.warning("Failed to save MCP probe summary to DB", exc_info=True)

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "config_path": str(mcp_file),
        "total_servers": total,
        "valid": valid,
        "invalid": total - valid,
        "with_warnings": with_warnings,
        "servers": {r["name"]: r["valid"] for r in results},
    }
