"""MCP Server Manager (v0.31.00) — discover, probe, and manage MCP server connections."""

import json
import os
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
PROJECT_ROOT = FLEET_DIR.parent

# Default MCP server definitions (bundled with BigEd CC)
MCP_DEFAULTS = {
    "playwright": {
        "type": "http",
        "url": "http://localhost:8931",
        "description": "Browser automation via Playwright",
        "docker_service": "playwright-mcp",
        "skills": ["browser_crawl", "web_search"],
    },
    "filesystem": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", str(PROJECT_ROOT)],
        "description": "File system operations",
        "skills": ["ingest", "rag_index", "code_index"],
    },
    "sequential-thinking": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "description": "Multi-step reasoning chains",
        "skills": ["plan_workload", "lead_research"],
    },
    "memory": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "description": "Persistent cross-session knowledge",
        "skills": ["rag_index"],
    },
}

# One-click add servers (need user API key or config)
MCP_INTEGRATIONS = {
    "github": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "description": "GitHub issues, PRs, code search",
        "requires_key": "GITHUB_TOKEN",
        "skills": ["github_sync", "code_review"],
    },
    "brave-search": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "description": "Brave web search API",
        "requires_key": "BRAVE_API_KEY",
        "skills": ["web_search"],
    },
    "fetch": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-fetch"],
        "description": "HTTP fetch for web crawling",
        "requires_key": None,
        "skills": ["web_crawl"],
    },
    "slack": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "description": "Slack team notifications",
        "requires_key": "SLACK_BOT_TOKEN",
        "skills": [],
    },
    "postgres": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"],
        "description": "PostgreSQL database queries",
        "requires_key": "POSTGRES_URL",
        "skills": ["analyze_results"],
    },
}


def load_mcp_json() -> dict:
    """Load .mcp.json from project root."""
    mcp_path = PROJECT_ROOT / ".mcp.json"
    if mcp_path.exists():
        try:
            return json.loads(mcp_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"mcpServers": {}}


def save_mcp_json(data: dict):
    """Write .mcp.json to project root."""
    mcp_path = PROJECT_ROOT / ".mcp.json"
    mcp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_configured_servers() -> dict:
    """Return all configured MCP servers with their status."""
    mcp_data = load_mcp_json()
    servers = {}
    for name, cfg in mcp_data.get("mcpServers", {}).items():
        servers[name] = {
            "name": name,
            "type": cfg.get("type", "unknown"),
            "url": cfg.get("url", ""),
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []),
            "configured": True,
            "category": _categorize_server(name),
        }
    return servers


def probe_server(name: str, cfg: dict, timeout: int = 3) -> dict:
    """Health-check an MCP server. Returns status dict."""
    server_type = cfg.get("type", "unknown")
    result = {"name": name, "type": server_type, "status": "unknown"}

    if server_type == "http":
        url = cfg.get("url", "")
        if not url:
            result["status"] = "no_url"
            return result
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result["status"] = "online" if resp.status < 400 else "error"
                result["http_status"] = resp.status
        except urllib.error.URLError:
            result["status"] = "offline"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
    elif server_type == "stdio":
        # stdio servers can't be probed without starting them
        # Mark as "configured" — actual health checked at skill dispatch time
        result["status"] = "configured"
    else:
        result["status"] = "unknown_type"

    return result


def get_all_server_status() -> list:
    """Get status of all configured MCP servers."""
    mcp_data = load_mcp_json()
    results = []
    for name, cfg in mcp_data.get("mcpServers", {}).items():
        status = probe_server(name, cfg)
        status["config"] = cfg
        status["category"] = _categorize_server(name)
        results.append(status)
    return results


def add_server(name: str, config: dict):
    """Add or update an MCP server in .mcp.json."""
    data = load_mcp_json()
    data.setdefault("mcpServers", {})[name] = config
    save_mcp_json(data)


def remove_server(name: str):
    """Remove an MCP server from .mcp.json."""
    data = load_mcp_json()
    data.get("mcpServers", {}).pop(name, None)
    save_mcp_json(data)


def get_mcp_url(server_name: str) -> str | None:
    """Get the URL for a configured HTTP MCP server from the user's .mcp.json.

    Returns the URL string if the server is configured and is HTTP type,
    None otherwise. This is the single source of truth for MCP server
    addresses — never hardcode URLs in skills.
    """
    data = load_mcp_json()
    cfg = data.get("mcpServers", {}).get(server_name)
    if not cfg:
        return None
    if cfg.get("type") != "http":
        return None
    return cfg.get("url")


def is_mcp_available(server_name: str, timeout: int = 2) -> tuple[bool, str | None]:
    """Check if an MCP server is configured AND reachable.

    Returns (available, url) — url is None if not configured/reachable.
    """
    url = get_mcp_url(server_name)
    if not url:
        return False, None
    status = probe_server(server_name, {"type": "http", "url": url}, timeout=timeout)
    if status.get("status") in ("online", "configured"):
        return True, url
    return False, None


def enable_default(name: str) -> bool:
    """Enable a bundled default MCP server."""
    if name not in MCP_DEFAULTS:
        return False
    default = MCP_DEFAULTS[name]
    config = {"type": default["type"]}
    if default["type"] == "http":
        config["url"] = default["url"]
    elif default["type"] == "stdio":
        config["command"] = default["command"]
        config["args"] = default["args"]
    add_server(name, config)
    return True


def disable_server(name: str) -> bool:
    """Disable (remove) an MCP server."""
    data = load_mcp_json()
    if name in data.get("mcpServers", {}):
        remove_server(name)
        return True
    return False


def get_skill_mcp_mapping() -> dict:
    """Return skill -> MCP server name mapping from fleet.toml [mcp.routing]."""
    try:
        from config import load_config
        cfg = load_config()
        return dict(cfg.get("mcp", {}).get("routing", {}))
    except Exception:
        return {}


def _categorize_server(name: str) -> str:
    """Categorize a server as default, integration, or custom."""
    if name in MCP_DEFAULTS:
        return "default"
    if name in MCP_INTEGRATIONS:
        return "integration"
    return "custom"


# ─── Claude Desktop registration ─────────────────────────────────────────────

_CLAUDE_DESKTOP_SERVER_NAME = "biged-fleet"


def get_claude_desktop_config_path() -> Path:
    """Return the platform-specific path to claude_desktop_config.json.

    Windows: %APPDATA%/Claude/claude_desktop_config.json
    macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
    Linux:   ~/.config/Claude/claude_desktop_config.json
    """
    import sys as _sys
    if _sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    elif _sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def is_registered_claude_desktop() -> bool:
    """Check if biged-fleet is registered in Claude Desktop's MCP config."""
    config_path = get_claude_desktop_config_path()
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return _CLAUDE_DESKTOP_SERVER_NAME in data.get("mcpServers", {})
    except Exception:
        return False


def register_claude_desktop() -> bool:
    """Register biged-fleet as an MCP server in Claude Desktop's config.

    Writes a stdio entry pointing to fleet/mcp_server.py with the
    dynamically resolved absolute path. Creates the config file and
    parent directories if they don't exist.

    Returns True on success, False on failure.
    """
    config_path = get_claude_desktop_config_path()
    mcp_server_path = str(Path(__file__).resolve().parent / "mcp_server.py")

    try:
        # Load existing config or start fresh
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            data = {}

        data.setdefault("mcpServers", {})
        data["mcpServers"][_CLAUDE_DESKTOP_SERVER_NAME] = {
            "type": "stdio",
            "command": "python",
            "args": [mcp_server_path],
        }

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def unregister_claude_desktop() -> bool:
    """Remove biged-fleet from Claude Desktop's MCP config.

    Returns True if successfully removed, False if not found or on error.
    """
    config_path = get_claude_desktop_config_path()
    if not config_path.exists():
        return False

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        if _CLAUDE_DESKTOP_SERVER_NAME not in servers:
            return False
        del servers[_CLAUDE_DESKTOP_SERVER_NAME]
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False
