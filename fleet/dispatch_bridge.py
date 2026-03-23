"""CLI fallback for Dispatch Bridge — submit tasks, check status, respond to HITL.

Usage:
    python fleet/dispatch_bridge.py submit "review worker code"
    python fleet/dispatch_bridge.py status [task_id]
    python fleet/dispatch_bridge.py catalog
    python fleet/dispatch_bridge.py pending-hitl
    python fleet/dispatch_bridge.py respond <task_id> "approved"
    python fleet/dispatch_bridge.py watch
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

_FLEET_DIR = Path(__file__).resolve().parent
if str(_FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(_FLEET_DIR))


def _get_base_url(args) -> str:
    if getattr(args, "url", None):
        return args.url.rstrip("/")
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("dispatch_bridge", {}).get(
            "dashboard_base_url", "http://127.0.0.1:5555"
        )
    except Exception:
        return "http://127.0.0.1:5555"


def _http(method: str, url: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _out(obj, pretty=False):
    if pretty:
        print(json.dumps(obj, indent=2))
    else:
        print(json.dumps(obj))


def cmd_submit(args):
    """Submit a natural language task."""
    from intent import parse_intent_with_maintainer
    import db

    skill, payload = parse_intent_with_maintainer(args.instruction)
    task_id = db.post_task(skill, json.dumps(payload))
    _out({"task_id": task_id, "skill": skill, "status": "PENDING"}, args.pretty)


def cmd_status(args):
    """Get fleet or task status."""
    base = _get_base_url(args)
    if args.task_id:
        import db
        row = db.get_task_result(int(args.task_id))
        if row:
            _out({
                "task_id": row["id"], "status": row["status"],
                "skill": row.get("type"), "result": row.get("result_json"),
            }, args.pretty)
        else:
            _out({"error": f"Task {args.task_id} not found"}, args.pretty)
    else:
        data = _http("GET", f"{base}/api/status")
        _out(data, args.pretty)


def cmd_catalog(args):
    """List available skills."""
    skills_dir = _FLEET_DIR / "skills"
    skills = []
    for py_file in sorted(skills_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        name, desc = None, None
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if line.startswith("SKILL_NAME"):
                    name = line.split("=", 1)[1].strip().strip("\"'")
                elif line.startswith("DESCRIPTION"):
                    desc = line.split("=", 1)[1].strip().strip("\"'")
                if name and desc:
                    break
        except Exception:
            continue
        if name:
            skills.append({"name": name, "description": desc or ""})
    _out({"skills": skills}, args.pretty)


def cmd_pending_hitl(args):
    """List tasks waiting for human input."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, type, payload_json, assigned_to, created_at "
            "FROM tasks WHERE status='WAITING_HUMAN' ORDER BY created_at"
        ).fetchall()
    tasks = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        tasks.append({
            "task_id": row["id"], "skill": row["type"],
            "agent": row["assigned_to"],
            "question": payload.get("_human_question", "Approval required"),
            "created_at": row["created_at"],
        })
    _out(tasks, args.pretty)


def cmd_respond(args):
    """Respond to a HITL gate."""
    import db
    db.respond_to_agent(int(args.task_id), args.response)
    _out({"task_id": int(args.task_id), "responded": True, "response": args.response}, args.pretty)


def cmd_watch(args):
    """Tail SSE stream for real-time updates."""
    base = _get_base_url(args)
    url = f"{base}/api/stream"
    req = urllib.request.Request(url)
    print(f"Watching {url} (Ctrl+C to stop)...", file=sys.stderr)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line_bytes in resp:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    try:
                        event = json.loads(data)
                        etype = event.get("type", "")
                        if etype in ("task_update", "hitl_waiting", "alert"):
                            _out(event, args.pretty)
                    except json.JSONDecodeError:
                        print(data)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    except Exception as e:
        print(f"Watch error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description="BigEd Dispatch Bridge CLI")
    p.add_argument("--url", help="Dashboard base URL override")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    s_submit = sub.add_parser("submit", help="Submit a natural language task")
    s_submit.add_argument("instruction", help="Task instruction in plain English")

    s_status = sub.add_parser("status", help="Fleet or task status")
    s_status.add_argument("task_id", nargs="?", help="Optional task ID")

    sub.add_parser("catalog", help="List available skills")
    sub.add_parser("pending-hitl", help="List HITL-waiting tasks")

    s_respond = sub.add_parser("respond", help="Respond to HITL gate")
    s_respond.add_argument("task_id", help="Task ID")
    s_respond.add_argument("response", help="Response text")

    sub.add_parser("watch", help="Tail SSE stream for live updates")

    args = p.parse_args()
    cmds = {
        "submit": cmd_submit, "status": cmd_status, "catalog": cmd_catalog,
        "pending-hitl": cmd_pending_hitl, "respond": cmd_respond, "watch": cmd_watch,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
