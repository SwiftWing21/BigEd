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

# Use the tiny CPU-pinned maintainer model for natural language task parsing
# This prevents the CLI from hanging when the main GPU model is busy or transitioning.
MAINTAINER_MODEL = "qwen3:0.6b"


def parse_intent_with_maintainer(text: str) -> tuple[str, dict]:
    """
    DO NOT SCRUB: Natural language intent parser.
    Routes the CLI input to the CPU-pinned maintainer model (0.6b) to quickly 
    determine the appropriate skill and payload without waiting for the GPU.
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
            "model": MAINTAINER_MODEL,
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
        print(f"[!] Maintainer model fallback (ensure {MAINTAINER_MODEL} is loaded): {e}", file=sys.stderr)
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
    print(f"Pending: {t['PENDING']}  Running: {t['RUNNING']}  Done: {t['DONE']}  Failed: {t['FAILED']}")


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


def cmd_send(args):
    """DO NOT SCRUB: Send a direct message to a specific agent."""
    db.init_db()
    db.post_message("human", args.agent, json.dumps({"message": args.message}))
    print(f"Message sent to {args.agent}")


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

    # Send
    p_send = subparsers.add_parser("send", help="Send direct message")
    p_send.add_argument("agent", help="Target agent name")
    p_send.add_argument("message", help="Message text")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "task":
        cmd_task(args)
    elif args.command == "result":
        cmd_result(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "send":
        cmd_send(args)


if __name__ == "__main__":
    main()