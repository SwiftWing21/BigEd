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
                "version": "0.160.00b",
                "features": ["dashboard", "fleet-control", "module-hub"],
                "production": os.environ.get("BIGED_PRODUCTION") == "1",
                "web_mode": os.environ.get("BIGED_WEB_MODE") == "1",
            })

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
