#!/usr/bin/env python3
"""
BigEd CC -- Web Launcher (SaaS foundation).
Serves the fleet dashboard + management API for browser-based access.
Replaces the desktop GUI for cloud/container deployments.

Usage:
    python web_app.py              # http://localhost:5555
    python web_app.py --port 8080  # custom port

v0.160.00b -- Platform & SaaS foundation.
"""
import argparse
import json
import os
import sys
from pathlib import Path

FLEET_DIR = Path(__file__).parent


def create_web_app():
    """Create Flask app for web-based fleet management.

    Imports the existing dashboard app and extends it with web launcher
    routes. Returns None if Flask/dashboard is not available.
    """
    try:
        from dashboard import app

        @app.route("/web")
        def web_launcher():
            """Web launcher landing page."""
            return (
                "<h1>BigEd CC Web Launcher</h1>"
                "<p>Platform & SaaS foundation -- v0.160.00b</p>"
                "<ul>"
                '<li><a href="/api/fleet/health">Fleet Health</a></li>'
                '<li><a href="/api/web/config">Web Config</a></li>'
                "</ul>"
            )

        @app.route("/api/web/config")
        def web_config():
            """Web launcher configuration endpoint."""
            return json.dumps({
                "mode": "web",
                "version": "0.170.00b",
                "features": ["dashboard", "fleet-control", "module-hub", "mcp-management"],
                "production": os.environ.get("BIGED_PRODUCTION") == "1",
                "web_mode": os.environ.get("BIGED_WEB_MODE") == "1",
            })

        # ── MCP Management API (Phase 4) ─────────────────────────────────

        @app.route("/api/mcp/servers")
        def mcp_servers():
            """List all MCP servers with status."""
            from mcp_manager import (
                get_all_server_status, MCP_DEFAULTS, MCP_INTEGRATIONS,
            )
            configured = get_all_server_status()
            # Include unconfigured defaults and integrations
            configured_names = {s["name"] for s in configured}
            available = []
            for name, meta in MCP_DEFAULTS.items():
                if name not in configured_names:
                    available.append({
                        "name": name, "status": "available",
                        "category": "default", "description": meta["description"],
                        "type": meta["type"],
                    })
            for name, meta in MCP_INTEGRATIONS.items():
                if name not in configured_names:
                    available.append({
                        "name": name, "status": "available",
                        "category": "integration", "description": meta["description"],
                        "type": meta["type"],
                        "requires_key": meta.get("requires_key"),
                    })
            return json.dumps({
                "configured": configured,
                "available": available,
            })

        @app.route("/api/mcp/enable/<name>", methods=["POST"])
        def mcp_enable(name):
            """Enable a default or integration MCP server."""
            from mcp_manager import enable_default, MCP_INTEGRATIONS, add_server
            from flask import request
            # Try as default first
            if enable_default(name):
                return json.dumps({"ok": True, "name": name, "action": "enabled"})
            # Try as integration (may need API key)
            if name in MCP_INTEGRATIONS:
                meta = MCP_INTEGRATIONS[name]
                data = request.get_json(silent=True) or {}
                api_key = data.get("api_key", "")
                if meta.get("requires_key") and not api_key:
                    return json.dumps({
                        "ok": False, "error": f"API key required: {meta['requires_key']}",
                    }), 400
                config = {"type": meta["type"]}
                if meta["type"] == "stdio":
                    config["command"] = meta["command"]
                    config["args"] = list(meta["args"])
                    if api_key:
                        config.setdefault("env", {})[meta["requires_key"]] = api_key
                add_server(name, config)
                return json.dumps({"ok": True, "name": name, "action": "enabled"})
            return json.dumps({"ok": False, "error": "Unknown server"}), 404

        @app.route("/api/mcp/disable/<name>", methods=["POST"])
        def mcp_disable(name):
            """Disable (remove) an MCP server."""
            from mcp_manager import disable_server
            ok = disable_server(name)
            return json.dumps({"ok": ok, "name": name, "action": "disabled"})

        @app.route("/api/mcp/add", methods=["POST"])
        def mcp_add_custom():
            """Add a custom MCP server."""
            from mcp_manager import add_server
            from flask import request
            data = request.get_json(silent=True) or {}
            name = data.get("name", "").strip()
            if not name:
                return json.dumps({"ok": False, "error": "name required"}), 400
            server_type = data.get("type", "stdio")
            config = {"type": server_type}
            if server_type == "http":
                url = data.get("url", "").strip()
                if not url:
                    return json.dumps({"ok": False, "error": "url required"}), 400
                config["url"] = url
            elif server_type == "stdio":
                command = data.get("command", "").strip()
                if not command:
                    return json.dumps({"ok": False, "error": "command required"}), 400
                config["command"] = command
                config["args"] = data.get("args", [])
                if data.get("env"):
                    config["env"] = data["env"]
            add_server(name, config)
            return json.dumps({"ok": True, "name": name, "action": "added"})

        @app.route("/api/mcp/probe/<name>", methods=["POST"])
        def mcp_probe(name):
            """Probe an MCP server's health."""
            from mcp_manager import load_mcp_json, probe_server
            mcp_data = load_mcp_json()
            cfg = mcp_data.get("mcpServers", {}).get(name)
            if not cfg:
                return json.dumps({"ok": False, "error": "Not configured"}), 404
            result = probe_server(name, cfg)
            return json.dumps(result)

        return app
    except ImportError:
        return None


def main():
    """Entry point for standalone web launcher."""
    parser = argparse.ArgumentParser(description="BigEd CC Web Launcher")
    parser.add_argument("--port", type=int, default=5555, help="Port (default: 5555)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    app = create_web_app()
    if app is None:
        print("ERROR: Could not create web app -- dashboard or Flask not available.")
        sys.exit(1)

    print(f"BigEd CC Web Launcher starting on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
