#!/usr/bin/env python3
"""
Web-accessible fleet launcher — complements the desktop CustomTkinter app.
Provides remote fleet management via browser at http://localhost:8080.
Uses Flask + htmx for reactive updates without JavaScript frameworks.

Usage:
    python web_launcher.py              # http://localhost:8080
    python web_launcher.py --port 9090  # custom port
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

FLEET_API = "http://localhost:5555"  # Fleet dashboard API


def _api(endpoint, method="GET", data=None):
    """Call fleet dashboard API."""
    try:
        url = f"{FLEET_API}{endpoint}"
        if method == "POST":
            body = json.dumps(data or {}).encode()
            req = urllib.request.Request(url, data=body, method="POST",
                                        headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>BigEd CC — Web Launcher</title>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        :root { --bg: #1a1a2e; --bg2: #16213e; --bg3: #0f3460; --text: #e0e0e0;
                --dim: #888; --accent: #4fc3f7; --green: #66bb6a; --red: #ef5350; --gold: #ffd54f; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'RuneScape Plain 12', system-ui, sans-serif; background: var(--bg); color: var(--text); }
        .header { background: var(--bg3); padding: 12px 20px; display: flex; align-items: center; gap: 16px; }
        .header h1 { font-size: 18px; color: var(--gold); }
        .header .status { font-size: 12px; padding: 4px 10px; border-radius: 12px; }
        .online { background: #1b5e20; color: #c8e6c9; }
        .offline { background: #b71c1c; color: #ffcdd2; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 16px; }
        .card { background: var(--bg2); border-radius: 8px; padding: 16px; }
        .card h2 { font-size: 14px; color: var(--accent); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; color: var(--dim); padding: 6px 8px; border-bottom: 1px solid var(--bg3); }
        td { padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
        .dot-green { background: var(--green); }
        .dot-red { background: var(--red); }
        .dot-yellow { background: var(--gold); }
        .btn { padding: 6px 14px; border: none; border-radius: 4px; cursor: pointer; font-size: 12px; color: var(--text); }
        .btn-green { background: #2e7d32; }
        .btn-red { background: #c62828; }
        .btn-blue { background: var(--bg3); }
        .btn:hover { opacity: 0.85; }
        .mono { font-family: 'Consolas', monospace; font-size: 12px; }
        .actions { display: flex; gap: 8px; margin-top: 12px; }
        #toast { position: fixed; bottom: 20px; right: 20px; background: var(--bg3); padding: 10px 16px;
                 border-radius: 6px; display: none; font-size: 13px; border-left: 3px solid var(--accent); }
    </style>
</head>
<body>
    <div class="header">
        <h1>BigEd CC</h1>
        <span id="conn-status" class="status offline"
              hx-get="/api/ping" hx-trigger="every 5s" hx-swap="innerHTML">Checking...</span>
        <span style="flex:1"></span>
        <span class="mono" style="color:var(--dim)">Web Launcher v0.05</span>
    </div>

    <div class="container">
        <div class="grid">
            <!-- Agents Panel -->
            <div class="card">
                <h2>Agents</h2>
                <div hx-get="/partial/agents" hx-trigger="load, every 5s" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- Tasks Panel -->
            <div class="card">
                <h2>Tasks</h2>
                <div hx-get="/partial/tasks" hx-trigger="load, every 5s" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- Fleet Control -->
            <div class="card">
                <h2>Fleet Control</h2>
                <div class="actions">
                    <button class="btn btn-green" hx-post="/action/start" hx-swap="none">Start Fleet</button>
                    <button class="btn btn-red" hx-post="/action/stop" hx-swap="none">Stop Fleet</button>
                    <button class="btn btn-blue" hx-get="/partial/health" hx-target="#health-data">Health Check</button>
                </div>
                <div id="health-data" class="mono" style="margin-top:12px; color:var(--dim)"></div>
            </div>

            <!-- Cost Overview -->
            <div class="card">
                <h2>Cost Intelligence</h2>
                <div hx-get="/partial/cost" hx-trigger="load, every 30s" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- Workflows -->
            <div class="card">
                <h2>Workflows</h2>
                <div hx-get="/partial/workflows" hx-trigger="load" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- Quick Dispatch -->
            <div class="card">
                <h2>Quick Dispatch</h2>
                <form hx-post="/action/dispatch" hx-swap="none">
                    <input type="text" name="instruction" placeholder="Natural language task or /skill_name..."
                           style="width:100%; padding:8px; background:var(--bg); color:var(--text); border:1px solid var(--bg3); border-radius:4px; font-size:13px;">
                    <button type="submit" class="btn btn-blue" style="margin-top:8px">Dispatch</button>
                </form>
            </div>

            <!-- Agent Management -->
            <div class="card">
                <h2>Agent Management</h2>
                <div hx-get="/partial/agents/manage" hx-trigger="load, every 10s" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- Settings -->
            <div class="card">
                <h2>Settings</h2>
                <div hx-get="/partial/settings" hx-trigger="load" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- MCP Servers -->
            <div class="card">
                <h2>MCP Servers</h2>
                <div hx-get="/partial/mcp" hx-trigger="load, every 30s" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>

            <!-- Console -->
            <div class="card">
                <h2>Console</h2>
                <div hx-get="/partial/console" hx-trigger="load, every 5s" hx-swap="innerHTML">
                    Loading...
                </div>
            </div>
        </div>
    </div>

    <div id="toast"></div>

    <script>
        document.body.addEventListener('htmx:afterRequest', function(e) {
            if (e.detail.xhr.status >= 400) {
                showToast('Error: ' + e.detail.xhr.responseText);
            }
        });
        function showToast(msg) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 3000);
        }
    </script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/ping")
def ping():
    health = _api("/api/fleet/health")
    if "error" in health:
        return '<span class="status offline">Dashboard Offline</span>'
    workers = health.get("workers", {})
    return f'<span class="status online">Online — {workers.get("active", 0)}/{workers.get("total", 0)} workers</span>'


@app.route("/partial/agents")
def partial_agents():
    data = _api("/api/status")
    if "error" in data:
        return f'<p style="color:var(--dim)">Dashboard unavailable</p>'
    agents = data.get("agents", [])
    if not agents:
        return '<p style="color:var(--dim)">No agents registered</p>'
    rows = ""
    for a in agents:
        status = a.get("status", "?")
        dot = "dot-green" if status in ("IDLE", "BUSY") else "dot-red" if status in ("OFFLINE", "QUARANTINED") else "dot-yellow"
        rows += f'<tr><td><span class="status-dot {dot}"></span>{a["name"]}</td><td>{a.get("role","")}</td><td>{status}</td></tr>'
    return f'<table><tr><th>Agent</th><th>Role</th><th>Status</th></tr>{rows}</table>'


@app.route("/partial/tasks")
def partial_tasks():
    data = _api("/api/status")
    if "error" in data:
        return '<p style="color:var(--dim)">Dashboard unavailable</p>'
    tasks = data.get("task_counts", data.get("tasks", {}))
    rows = ""
    for status, count in tasks.items():
        color = "var(--green)" if status == "DONE" else "var(--red)" if status == "FAILED" else "var(--accent)"
        rows += f'<tr><td>{status}</td><td style="color:{color};font-weight:bold">{count}</td></tr>'
    return f'<table><tr><th>Status</th><th>Count</th></tr>{rows}</table>'


@app.route("/partial/health")
def partial_health():
    health = _api("/api/fleet/health")
    return f'<pre style="color:var(--text)">{json.dumps(health, indent=2)}</pre>'


@app.route("/partial/cost")
def partial_cost():
    data = _api("/api/usage?period=week&group=skill")
    if "error" in data or not isinstance(data, list):
        return '<p style="color:var(--dim)">No usage data</p>'
    total = sum(r.get("total_cost", 0) or 0 for r in data)
    top = data[:5]
    rows = ""
    for r in top:
        rows += f'<tr><td>{r.get("skill","?")}</td><td>${r.get("total_cost",0):.4f}</td><td>{r.get("calls",0)}</td></tr>'
    return f'<p style="margin-bottom:8px">Weekly total: <b>${total:.4f}</b></p><table><tr><th>Skill</th><th>Cost</th><th>Calls</th></tr>{rows}</table>'


@app.route("/partial/workflows")
def partial_workflows():
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fleet"))
        from workflows import list_workflows
        wfs = list_workflows()
        if not wfs:
            return '<p style="color:var(--dim)">No workflows defined</p>'
        rows = ""
        for w in wfs:
            rows += f'<tr><td>{w["name"]}</td><td>{w["description"]}</td><td>{w["steps"]}</td></tr>'
        return f'<table><tr><th>Name</th><th>Description</th><th>Steps</th></tr>{rows}</table>'
    except Exception as e:
        return f'<p style="color:var(--dim)">{e}</p>'


@app.route("/action/start", methods=["POST"])
def action_start():
    result = _api("/api/fleet/start", method="POST")
    return jsonify(result)


@app.route("/action/stop", methods=["POST"])
def action_stop():
    result = _api("/api/fleet/stop", method="POST")
    return jsonify(result)


@app.route("/action/dispatch", methods=["POST"])
def action_dispatch():
    instruction = request.form.get("instruction", "")
    if not instruction:
        return "No instruction", 400
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fleet"))
        import db
        db.init_db()
        task_id = db.post_task("summarize", json.dumps({"description": instruction}))
        return jsonify({"task_id": task_id, "status": "dispatched"})
    except Exception as e:
        return str(e), 500


@app.route("/partial/agents/manage")
def partial_agents_manage():
    """Agent management view with enable/disable toggles."""
    data = _api("/api/status")
    if "error" in data:
        return '<p style="color:var(--dim)">Dashboard unavailable</p>'
    agents = data.get("agents", [])
    if not agents:
        return '<p style="color:var(--dim)">No agents registered</p>'

    # Read disabled agents from config
    disabled = set()
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fleet"))
        from config import load_config
        cfg = load_config()
        disabled = set(cfg.get("fleet", {}).get("disabled_agents", []))
    except Exception:
        pass

    rows = ""
    for a in agents:
        name = a.get("name", "?")
        status = a.get("status", "?")
        is_disabled = name in disabled
        dot = "dot-red" if is_disabled else "dot-green" if status in ("IDLE", "BUSY") else "dot-yellow"
        state_text = "DISABLED" if is_disabled else status
        if is_disabled:
            btn = f'<button class="btn btn-green" hx-post="/action/agent/{name}/enable" hx-swap="none" hx-on::after-request="htmx.trigger(this.closest(\'div\'), \'htmx:load\')">Enable</button>'
        else:
            btn = f'<button class="btn btn-red" hx-post="/action/agent/{name}/disable" hx-swap="none" hx-on::after-request="htmx.trigger(this.closest(\'div\'), \'htmx:load\')">Disable</button>'
        rows += f'<tr><td><span class="status-dot {dot}"></span>{name}</td><td>{a.get("role","")}</td><td>{state_text}</td><td>{btn}</td></tr>'
    return f'<table><tr><th>Agent</th><th>Role</th><th>Status</th><th>Action</th></tr>{rows}</table>'


@app.route("/action/agent/<name>/disable", methods=["POST"])
def action_agent_disable(name):
    result = _api(f"/api/fleet/worker/{name}/disable", method="POST")
    return jsonify(result)


@app.route("/action/agent/<name>/enable", methods=["POST"])
def action_agent_enable(name):
    result = _api(f"/api/fleet/worker/{name}/enable", method="POST")
    return jsonify(result)


@app.route("/partial/settings")
def partial_settings():
    """Read-only settings view from fleet.toml."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fleet"))
        from config import load_config
        cfg = load_config()

        sections = [
            ("Fleet", cfg.get("fleet", {})),
            ("Models", cfg.get("models", {})),
            ("Dashboard", cfg.get("dashboard", {})),
            ("Security", {k: "***" if "token" in k else v for k, v in cfg.get("security", {}).items()}),
            ("Thermal", cfg.get("thermal", {})),
        ]

        html = ""
        for section_name, section_data in sections:
            rows = ""
            for k, v in section_data.items():
                if isinstance(v, dict):
                    continue  # skip nested tables
                rows += f'<tr><td style="color:var(--accent)">{k}</td><td class="mono">{v}</td></tr>'
            if rows:
                html += f'<h3 style="color:var(--gold);font-size:12px;margin:12px 0 4px;text-transform:uppercase">{section_name}</h3>'
                html += f'<table>{rows}</table>'
        return html or '<p style="color:var(--dim)">No config loaded</p>'
    except Exception as e:
        return f'<p style="color:var(--dim)">{e}</p>'


@app.route("/partial/console")
def partial_console():
    """Recent completed tasks (console view)."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fleet"))
        import db
        db.init_db()
        with db.get_conn() as conn:
            tasks = conn.execute(
                "SELECT id, type, status, assigned_to, created_at "
                "FROM tasks ORDER BY id DESC LIMIT 20"
            ).fetchall()
        if not tasks:
            return '<p style="color:var(--dim)">No tasks yet</p>'
        rows = ""
        for t in tasks:
            status = t["status"]
            color = "var(--green)" if status == "DONE" else "var(--red)" if status == "FAILED" else "var(--accent)"
            rows += f'<tr><td class="mono">#{t["id"]}</td><td>{t["type"]}</td><td style="color:{color}">{status}</td><td>{t["assigned_to"] or ""}</td><td style="color:var(--dim)">{t["created_at"]}</td></tr>'
        return f'<table><tr><th>ID</th><th>Skill</th><th>Status</th><th>Agent</th><th>Created</th></tr>{rows}</table>'
    except Exception as e:
        return f'<p style="color:var(--dim)">{e}</p>'


@app.route("/partial/mcp")
def partial_mcp():
    """MCP server status panel."""
    data = _api("/api/mcp/status")
    if "error" in data:
        return '<p style="color:var(--dim)">MCP status unavailable</p>'
    servers = data.get("servers", [])
    if not servers:
        return '<p style="color:var(--dim)">No MCP servers configured</p>'

    rows = ""
    for s in servers:
        status = s.get("status", "unknown")
        color = "var(--green)" if status == "online" else "var(--gold)" if status == "configured" else "var(--red)"
        dot = "dot-green" if status == "online" else "dot-yellow" if status == "configured" else "dot-red"
        category = s.get("category", "custom")
        badge = f'<span style="color:var(--dim);font-size:10px">({category})</span>'
        rows += f'<tr><td><span class="status-dot {dot}"></span>{s["name"]} {badge}</td><td>{s.get("type","?")}</td><td style="color:{color}">{status.upper()}</td></tr>'

    summary = f'{data.get("online", 0)} online, {data.get("configured", 0)} configured, {data.get("total", 0)} total'
    return f'<p style="margin-bottom:8px;font-size:12px;color:var(--dim)">{summary}</p><table><tr><th>Server</th><th>Type</th><th>Status</th></tr>{rows}</table>'


def main():
    parser = argparse.ArgumentParser(description="BigEd CC Web Launcher")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"Web Launcher: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
