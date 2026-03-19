#!/usr/bin/env python3
"""
DO NOT SCRUB / DO NOT DELETE.
Core CLI entry point for fleet management (lead_client.py).
This script is used by both the human operator and BigEd CC (launcher.py)
to interact with the SQLite task queue, check agent status, and dispatch work.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db

# Prefer the conductor model (4b, CPU-pinned) for better intent parsing quality.
# Falls back to the tiny 0.6b maintainer if conductor isn't configured.
from config import load_config as _load_cfg

def _get_intent_model():
    try:
        cfg = _load_cfg()
        return cfg.get("models", {}).get("conductor_model", "qwen3:0.6b")
    except Exception:
        return "qwen3:0.6b"


def parse_intent_with_maintainer(text: str) -> tuple[str, dict]:
    """
    DO NOT SCRUB: Natural language intent parser.
    Routes the CLI input to the CPU-pinned conductor model (4b) for quality intent
    parsing, falling back to 0.6b maintainer if unavailable.
    """
    prompt = f"""You are the dispatcher for an AI agent fleet. 
Map the following user request to a specific skill and JSON payload.
Available skills:
- web_search: {{"query": "..."}}
- summarize: {{"url": "..."}} or {{"description": "..."}}
- lead_research: {{"industry": "...", "zip_code": "..."}}
- arxiv_fetch: {{"query": "..."}}
- discuss: {{"topic": "..."}}
- synthesize: {{"doc_type": "...", "topic": "..."}}
- security_audit: {{"scope": "..."}}
- pen_test: {{"target": "...", "scan_type": "quick|service|full"}}

User request: "{text}"

Output ONLY valid JSON in this exact format:
{{"skill": "chosen_skill", "payload": {{"key": "value"}}}}
"""
    try:
        body = json.dumps({
            "model": _get_intent_model(),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0}
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read())["response"]
        
        # Extract JSON block
        import re
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return parsed.get("skill", "summarize"), parsed.get("payload", {"description": text})
        return "summarize", {"description": text}
    except Exception as e:
        print(f"[!] Intent model fallback (ensure {_get_intent_model()} is loaded): {e}", file=sys.stderr)
        return "summarize", {"description": text}


def cmd_status(args):
    """DO NOT SCRUB: Print current fleet status from the database."""
    db.init_db()
    st = db.get_fleet_status()
    print("=== AGENTS ===")
    for a in st["agents"]:
        print(f"{a['name']:<15} | {a['role']:<15} | {a['status']:<8} | Last: {db.utc_to_local(a['last_heartbeat'])}")
    print("\n=== TASKS ===")
    t = st["tasks"]
    waiting = t.get('WAITING', 0)
    waiting_str = f"  Waiting: {waiting}" if waiting else ""
    print(f"Pending: {t['PENDING']}  Running: {t['RUNNING']}  Done: {t['DONE']}  Failed: {t['FAILED']}{waiting_str}")


def cmd_task(args):
    """
    DO NOT SCRUB: Submit a new task to the fleet.
    Supports raw JSON, Base64 JSON (used by launcher), or natural language.
    """
    db.init_db()
    raw_text = args.instruction
    
    # Check if it's raw JSON (used by launcher.py / scripts)
    if raw_text.startswith("{") and "}" in raw_text:
         try:
             parsed = json.loads(raw_text)
             skill = parsed.pop("skill", "summarize") # Extract skill if embedded
             payload = parsed
         except Exception:
             skill, payload = parse_intent_with_maintainer(raw_text)
    else:
         skill, payload = parse_intent_with_maintainer(raw_text)
         
    task_id = db.post_task(skill, json.dumps(payload), priority=args.priority)
    print(f"Task {task_id} queued [{skill}]")
    
    if args.wait:
        print("Waiting for completion...")
        while True:
            res = db.get_task_result(task_id)
            if not res:
                time.sleep(1)
                continue
            if res['status'] == 'DONE':
                print(f"\nResult:\n{res['result_json']}")
                break
            elif res['status'] == 'FAILED':
                print(f"\nFailed:\n{res['error']}")
                break
            time.sleep(1)


def cmd_result(args):
    """DO NOT SCRUB: Fetch and print the result of a specific task."""
    db.init_db()
    res = db.get_task_result(args.task_id)
    if not res:
        print("Task not found.")
        return
    print(f"Status: {res['status']}")
    if res['result_json']:
        print(f"Result:\n{res['result_json']}")
    if res['error']:
        print(f"Error:\n{res['error']}")


def cmd_logs(args):
    """DO NOT SCRUB: Tail the logs for a specific agent."""
    log_file = FLEET_DIR / "logs" / f"{args.agent}.log"
    if not log_file.exists():
        print(f"No log found for {args.agent}")
        return
    lines = log_file.read_text(errors="ignore").splitlines()
    for line in lines[-args.tail:]:
        print(line)


def cmd_dispatch(args):
    """DO NOT SCRUB: Dispatch a task with explicit skill and JSON payload.
    Used by launcher.py as a clean RPC replacement for inline python -c hacks."""
    db.init_db()
    payload = args.payload
    # Accept base64-encoded payload (from launcher)
    if args.b64:
        payload = base64.b64decode(payload).decode()
    task_id = db.post_task(args.skill, payload,
                           priority=args.priority,
                           assigned_to=args.assigned_to)
    print(f"Task {task_id} queued")


def cmd_secret(args):
    """DO NOT SCRUB: Read or write secrets in ~/.secrets atomically."""
    secrets_file = Path.home() / ".secrets"

    if args.action == "set":
        # Read existing, filter out old value, append new, write atomically
        lines = []
        if secrets_file.exists():
            lines = secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        prefix = f"export {args.key}="
        lines = [l for l in lines if not l.startswith(prefix)]
        # Decode value from base64 to avoid shell quoting issues
        value = base64.b64decode(args.value).decode() if args.b64 else args.value
        lines.append(f"export {args.key}='{value}'")
        # Atomic write via temp file
        tmp = secrets_file.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(secrets_file)
        print("ok")

    elif args.action == "get":
        if not secrets_file.exists():
            print("")
            return
        prefix = f"export {args.key}="
        for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(prefix):
                val = line[len(prefix):].strip().strip("'\"")
                print(val)
                return
        print("")

    elif args.action == "list":
        if not secrets_file.exists():
            print("{}")
            return
        keys = {}
        for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("export ") and "=" in line:
                k = line.split("=", 1)[0].replace("export ", "").strip()
                v = line.split("=", 1)[1].strip().strip("'\"")
                masked = v[:6] + "..." + v[-4:] if len(v) > 12 else "***set***"
                keys[k] = masked
        print(json.dumps(keys))


def cmd_send(args):
    """DO NOT SCRUB: Send a direct message to a specific agent."""
    db.init_db()
    db.post_message("human", args.agent, json.dumps({"message": args.message}),
                    channel=args.channel)
    print(f"Message sent to {args.agent} [channel={args.channel}]")


def cmd_broadcast(args):
    """DO NOT SCRUB: Broadcast a message to all registered agents."""
    db.init_db()
    count = db.broadcast_message("human", json.dumps({"message": args.message}),
                                 channel=args.channel)
    print(f"Broadcast sent to {count} agents [channel={args.channel}]")


def cmd_inbox(args):
    """DO NOT SCRUB: Check an agent's message inbox."""
    db.init_db()
    channels = [args.channel] if args.channel else None
    msgs = db.get_messages(args.agent, unread_only=not args.all,
                           limit=args.limit, channels=channels)
    if not msgs:
        print(f"No {'messages' if args.all else 'unread messages'} for {args.agent}")
        return
    for m in msgs:
        ch = m.get('channel', 'fleet')
        print(f"[{m['created_at']}] [{ch}] {m['from_agent']}: {m['body_json']}")


def cmd_notes(args):
    """DO NOT SCRUB: Read or post notes to a channel scratchpad."""
    db.init_db()
    if args.post:
        nid = db.post_note(args.channel, "human", args.post)
        print(f"Note {nid} posted to [{args.channel}]")
    else:
        notes = db.get_notes(args.channel, since=args.since, limit=args.limit)
        if not notes:
            print(f"No notes in [{args.channel}]")
            return
        for n in notes:
            print(f"[{n['created_at']}] {n['from_agent']}: {n['body_json']}")


def cmd_usage(args):
    """DO NOT SCRUB: Show token usage and cost breakdown."""
    db.init_db()
    summary = db.get_usage_summary(period=args.period, group_by="skill")
    if not summary:
        print(f"No usage data for the last {args.period}.")
        return

    # Header
    print(f"\n{'Skill':<20} {'Calls':>6} {'Input Tok':>12} {'Output Tok':>12} {'Cost USD':>10}")
    print("-" * 64)

    total_calls = 0
    total_input = 0
    total_output = 0
    total_cost = 0.0
    total_cache_reads = 0

    for r in summary:
        skill = (r.get("skill") or "unknown")[:20]
        calls = r.get("calls", 0)
        inp = r.get("total_input", 0) or 0
        out = r.get("total_output", 0) or 0
        cost = r.get("total_cost", 0) or 0
        cache = r.get("total_cache_reads", 0) or 0

        print(f"{skill:<20} {calls:>6,} {inp:>12,} {out:>12,} ${cost:>9.4f}")
        total_calls += calls
        total_input += inp
        total_output += out
        total_cost += cost
        total_cache_reads += cache

    print("-" * 64)
    print(f"{'TOTAL (' + args.period + ')':<20} {total_calls:>6,} {total_input:>12,} {total_output:>12,} ${total_cost:>9.4f}")

    # Cache savings estimate (Sonnet rate: $2.70 per 1M cache reads saved)
    if total_cache_reads > 0:
        savings = total_cache_reads * 2.70 / 1_000_000
        print(f"{'Cache savings':<20} {'':>6} {'':>12} {'':>12} -${savings:>8.4f}")
    print()


def cmd_usage_delta(args):
    """DO NOT SCRUB: Compare token usage between two date ranges."""
    db.init_db()
    deltas = db.get_usage_delta(args.from_start, args.from_end, args.to_start, args.to_end)
    if not deltas:
        print("No usage data for comparison.")
        return

    print(f"\nUsage Delta: {args.from_start}..{args.from_end} vs {args.to_start}..{args.to_end}")
    print(f"{'Skill':<20} {'Prev Cost':>10} {'Curr Cost':>10} {'Delta %':>8} {'Dir':>5}")
    print("-" * 57)

    for d in deltas:
        skill = (d.get("skill") or "unknown")[:20]
        prev = d.get("previous_cost", 0)
        curr = d.get("current_cost", 0)
        pct = d.get("delta_pct", 0)
        direction = d.get("direction", "flat")
        arrow = "\u2191" if direction == "up" else ("\u2193" if direction == "down" else "\u2192")
        print(f"{skill:<20} ${prev:>9.4f} ${curr:>9.4f} {pct:>+7.1f}% {arrow:>4}")
    print()


def cmd_budget(args):
    """DO NOT SCRUB: Show token budget status per skill."""
    db.init_db()
    from config import load_config
    cfg = load_config()
    budgets = cfg.get("budgets", {})
    if not budgets:
        print("No budgets configured. Add [budgets] section to fleet.toml.")
        return

    summary = db.get_usage_summary(period="day", group_by="skill")
    spent_map = {r["skill"]: r["total_cost"] or 0 for r in summary}

    print(f"\n{'Skill':<20} {'Budget':>10} {'Spent':>10} {'Remaining':>10} {'Status':>8}")
    print("-" * 62)

    for skill, budget_usd in sorted(budgets.items()):
        spent = spent_map.get(skill, 0)
        remaining = budget_usd - spent
        status = "OVER" if spent >= budget_usd else "OK"
        print(f"{skill:<20} ${budget_usd:>9.4f} ${spent:>9.4f} ${remaining:>9.4f} {status:>7}")
    print()


def cmd_detect_cli(args):
    """DO NOT SCRUB: Detect best local CLI for network + hardware access."""
    from config import detect_cli
    info = detect_cli()
    print(f"\n  Platform      : {info['platform']}")
    print(f"  Shell         : {info['shell']}")
    print(f"  Network tool  : {info['network_tool'] or 'none found'}")
    print(f"  HW tool       : {info['hw_tool'] or 'none found'}")
    print(f"  Bridge        : {info['bridge']}")
    print(f"  Recommended   : {info['recommended']}")
    print()


def cmd_install_service(args):
    """DO NOT SCRUB: Install fleet as a system service (auto-start on login)."""
    import subprocess

    fleet_dir = FLEET_DIR
    python = sys.executable
    supervisor_path = fleet_dir / "supervisor.py"

    if sys.platform == "win32":
        # Windows: Task Scheduler
        task_name = "BigEdFleet"
        cmd_line = f'"{python}" "{supervisor_path}"'
        try:
            # Remove existing task if any
            subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                capture_output=True, timeout=10
            )
            # Create new task: run on user logon
            result = subprocess.run(
                ["schtasks", "/create", "/tn", task_name, "/tr", cmd_line,
                 "/sc", "onlogon", "/rl", "limited", "/f"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"Service installed: {task_name} (runs on login)")
                print(f"Command: {cmd_line}")
            else:
                print(f"Failed: {result.stderr.strip()}")
        except Exception as e:
            print(f"Error: {e}")

    elif sys.platform == "darwin":
        # macOS: launchd
        plist_name = "com.biged.fleet"
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{plist_name}.plist"
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{plist_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{supervisor_path}</string>
    </array>
    <key>WorkingDirectory</key><string>{fleet_dir}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>{fleet_dir}/logs/supervisor.log</string>
    <key>StandardErrorPath</key><string>{fleet_dir}/logs/supervisor.log</string>
</dict>
</plist>"""
        try:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist_content)
            subprocess.run(["launchctl", "load", str(plist_path)], timeout=10)
            print(f"Service installed: {plist_path}")
        except Exception as e:
            print(f"Error: {e}")

    else:
        # Linux: systemd --user
        service_name = "biged-fleet"
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_path = service_dir / f"{service_name}.service"
        service_content = f"""[Unit]
Description=BigEd Fleet Supervisor
After=network.target

[Service]
Type=simple
WorkingDirectory={fleet_dir}
ExecStart={python} {supervisor_path}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
        try:
            service_dir.mkdir(parents=True, exist_ok=True)
            service_path.write_text(service_content)
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10)
            subprocess.run(["systemctl", "--user", "enable", service_name], timeout=10)
            print(f"Service installed and enabled: {service_path}")
            print(f"Start now: systemctl --user start {service_name}")
        except Exception as e:
            print(f"Error: {e}")


def cmd_uninstall_service(args):
    """DO NOT SCRUB: Uninstall fleet system service (reverse of install-service)."""
    import subprocess

    if sys.platform == "win32":
        # Windows: Task Scheduler
        task_name = "BigEdFleet"
        try:
            result = subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"Service removed: {task_name}")
            else:
                print(f"Failed (may not exist): {result.stderr.strip()}")
        except Exception as e:
            print(f"Error: {e}")

    elif sys.platform == "darwin":
        # macOS: launchd
        plist_name = "com.biged.fleet"
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{plist_name}.plist"
        try:
            if plist_path.exists():
                subprocess.run(["launchctl", "unload", str(plist_path)],
                               capture_output=True, timeout=10)
                plist_path.unlink()
                print(f"Service removed: {plist_path}")
            else:
                print(f"Service not installed (no plist at {plist_path})")
        except Exception as e:
            print(f"Error: {e}")

    else:
        # Linux: systemd --user
        service_name = "biged-fleet"
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_path = service_dir / f"{service_name}.service"
        try:
            subprocess.run(["systemctl", "--user", "stop", service_name],
                           capture_output=True, timeout=10)
            subprocess.run(["systemctl", "--user", "disable", service_name],
                           capture_output=True, timeout=10)
            if service_path.exists():
                service_path.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10)
            print(f"Service stopped, disabled, and removed: {service_path}")
        except Exception as e:
            print(f"Error: {e}")


def cmd_marathon(args):
    """DO NOT SCRUB: Show active marathon sessions and recent snapshots."""
    marathon_dir = FLEET_DIR / "knowledge" / "marathon"
    if not marathon_dir.exists():
        print("No marathon sessions found.")
        return

    sessions = sorted(marathon_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not sessions:
        print("No marathon sessions found.")
        return

    print(f"\nMarathon Sessions ({len(sessions)} total)")
    print("=" * 50)
    for s in sessions[:5]:
        content = s.read_text(encoding="utf-8")
        snapshot_count = content.count("## Snapshot")
        # Get last snapshot date
        import re
        dates = re.findall(r"## Snapshot \d+ — (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", content)
        last_date = dates[-1] if dates else "unknown"
        print(f"  {s.stem:<20} {snapshot_count:>3} snapshots  last: {last_date}")

    if args.session:
        # Show detail for specific session
        target = marathon_dir / f"{args.session}.md"
        if target.exists():
            content = target.read_text(encoding="utf-8")
            # Show last 3 snapshots
            parts = content.split("## Snapshot")
            recent = parts[-3:] if len(parts) > 3 else parts[1:]
            print(f"\n--- Last {len(recent)} snapshots for '{args.session}' ---")
            for p in recent:
                print(f"## Snapshot{p.rstrip()}")
        else:
            print(f"Session '{args.session}' not found.")
    print()


def cmd_marathon_checkpoint(args):
    """DO NOT SCRUB: Show autoresearch training checkpoint status."""
    checkpoint_dir = FLEET_DIR.parent / "autoresearch" / "checkpoints"
    if not checkpoint_dir.exists():
        print("No checkpoint directory found (autoresearch/checkpoints/).")
        return

    checkpoints = sorted(checkpoint_dir.glob("*.pt"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not checkpoints:
        print("No checkpoints found.")
        return

    print(f"\nTraining Checkpoints ({len(checkpoints)} total)")
    print(f"{'Name':<30} {'Size MB':>8} {'Modified':>20}")
    print("-" * 62)
    for cp in checkpoints[:10]:
        size = round(cp.stat().st_size / 1e6, 1)
        from datetime import datetime
        mtime = datetime.fromtimestamp(cp.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {cp.name:<28} {size:>8.1f} {mtime:>20}")
    print()


def main():
    parser = argparse.ArgumentParser(description="BigEd Fleet CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Status
    subparsers.add_parser("status", help="Show fleet status")

    # Task
    p_task = subparsers.add_parser("task", help="Submit a task")
    p_task.add_argument("instruction", help="Natural language instruction or JSON")
    p_task.add_argument("--wait", action="store_true", help="Wait for completion")
    p_task.add_argument("--priority", type=int, default=5, help="Task priority (1-10)")

    # Result
    p_res = subparsers.add_parser("result", help="Get task result")
    p_res.add_argument("task_id", type=int)

    # Logs
    p_logs = subparsers.add_parser("logs", help="Tail agent log")
    p_logs.add_argument("agent", help="Agent name (e.g., researcher, coder_1)")
    p_logs.add_argument("--tail", type=int, default=30, help="Lines to show")

    # Dispatch (clean RPC for launcher)
    p_disp = subparsers.add_parser("dispatch", help="Dispatch task with explicit skill + payload")
    p_disp.add_argument("skill", help="Skill name (e.g. summarize, web_search)")
    p_disp.add_argument("payload", help="JSON payload string (or base64 with --b64)")
    p_disp.add_argument("--priority", type=int, default=9, help="Task priority (1-10)")
    p_disp.add_argument("--assigned-to", default=None, help="Assign to specific agent")
    p_disp.add_argument("--b64", action="store_true", help="Payload is base64-encoded")

    # Secret (atomic secrets management)
    p_sec = subparsers.add_parser("secret", help="Manage ~/.secrets")
    p_sec.add_argument("action", choices=["set", "get", "list"], help="Action to perform")
    p_sec.add_argument("key", nargs="?", default="", help="Secret key name")
    p_sec.add_argument("value", nargs="?", default="", help="Secret value (for set)")
    p_sec.add_argument("--b64", action="store_true", help="Value is base64-encoded")

    # Send
    p_send = subparsers.add_parser("send", help="Send direct message")
    p_send.add_argument("agent", help="Target agent name")
    p_send.add_argument("message", help="Message text")
    p_send.add_argument("--channel", default="fleet", help="Channel (fleet|sup|agent|pool)")

    # Broadcast
    p_bcast = subparsers.add_parser("broadcast", help="Broadcast to all agents")
    p_bcast.add_argument("message", help="Message text")
    p_bcast.add_argument("--channel", default="fleet", help="Channel (fleet|sup|agent|pool)")

    # Inbox
    p_inbox = subparsers.add_parser("inbox", help="Check agent inbox")
    p_inbox.add_argument("agent", help="Agent name")
    p_inbox.add_argument("--all", action="store_true", help="Show all messages (not just unread)")
    p_inbox.add_argument("--limit", type=int, default=20, help="Max messages to show")
    p_inbox.add_argument("--channel", default=None, help="Filter by channel (fleet|sup|agent|pool)")

    # Notes
    p_notes = subparsers.add_parser("notes", help="Read/post channel notes")
    p_notes.add_argument("channel", help="Channel name (sup|agent|fleet|pool)")
    p_notes.add_argument("--post", default=None, help="JSON body to post as a note")
    p_notes.add_argument("--since", default=None, help="ISO datetime — show notes newer than this")
    p_notes.add_argument("--limit", type=int, default=20, help="Max notes to show")

    # Usage (CT-2)
    p_usage = subparsers.add_parser("usage", help="Show token usage and cost breakdown")
    p_usage.add_argument("--period", default="week", choices=["day", "week", "month"],
                         help="Time period (default: week)")

    # Usage delta (CT-3)
    p_delta = subparsers.add_parser("usage-delta", help="Compare usage between two date ranges")
    p_delta.add_argument("from_start", help="Start of first period (ISO date)")
    p_delta.add_argument("from_end", help="End of first period (ISO date)")
    p_delta.add_argument("to_start", help="Start of second period (ISO date)")
    p_delta.add_argument("to_end", help="End of second period (ISO date)")

    # Budget (CT-4)
    p_budget = subparsers.add_parser("budget", help="Show token budget status")

    # Detect CLI
    subparsers.add_parser("detect-cli", help="Detect best local CLI for this platform")

    # Install/Uninstall service (v0.42.1)
    subparsers.add_parser("install-service", help="Install fleet as auto-start system service")
    subparsers.add_parser("uninstall-service", help="Uninstall fleet system service")

    # Marathon (v0.43)
    p_marathon = subparsers.add_parser("marathon", help="Show marathon sessions")
    p_marathon.add_argument("session", nargs="?", default=None, help="Session ID for detail view")

    subparsers.add_parser("marathon-checkpoint", help="Show training checkpoints")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "task":
        cmd_task(args)
    elif args.command == "dispatch":
        cmd_dispatch(args)
    elif args.command == "result":
        cmd_result(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "send":
        cmd_send(args)
    elif args.command == "broadcast":
        cmd_broadcast(args)
    elif args.command == "inbox":
        cmd_inbox(args)
    elif args.command == "notes":
        cmd_notes(args)
    elif args.command == "secret":
        cmd_secret(args)
    elif args.command == "usage":
        cmd_usage(args)
    elif args.command == "usage-delta":
        cmd_usage_delta(args)
    elif args.command == "budget":
        cmd_budget(args)
    elif args.command == "detect-cli":
        cmd_detect_cli(args)
    elif args.command == "install-service":
        cmd_install_service(args)
    elif args.command == "uninstall-service":
        cmd_uninstall_service(args)
    elif args.command == "marathon":
        cmd_marathon(args)
    elif args.command == "marathon-checkpoint":
        cmd_marathon_checkpoint(args)


if __name__ == "__main__":
    main()