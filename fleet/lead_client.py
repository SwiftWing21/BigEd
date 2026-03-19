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


if __name__ == "__main__":
    main()