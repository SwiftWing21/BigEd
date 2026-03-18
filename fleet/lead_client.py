#!/usr/bin/env python3
"""Lead client — dispatch tasks to the fleet and read results.

Usage (from WSL in fleet dir):
    uv run python lead_client.py status
    uv run python lead_client.py task "summarize arxiv:2501.00001" --wait
    uv run python lead_client.py result 42
    uv run python lead_client.py logs analyst --tail 30
    uv run python lead_client.py send researcher "index the autoresearch directory"
"""

import argparse
import json
import sys
import time
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db

# Map keywords in natural language to skill types
SKILL_MAP = {
    "summarize": "summarize",
    "summary": "summarize",
    "arxiv": "arxiv_fetch",
    "paper": "arxiv_fetch",
    "search": "web_search",
    "google": "web_search",
    "flashcard": "flashcard",
    "flashcards": "flashcard",
    "index": "code_index",
    "analyze": "analyze_results",
    "results": "analyze_results",
    "train": "analyze_results",
    "security_audit": "security_audit",
    "audit": "security_audit",
    "security_apply": "security_apply",
    "pen_test": "pen_test",
    "pentest": "pen_test",
    "scan": "pen_test",
    "discuss": "discuss",
    "synthesize": "synthesize",
    "lead_research": "lead_research",
    "leads": "lead_research",
    "key_manager": "key_manager",
    "keys": "key_manager",
    "api keys": "key_manager",
    "plan_workload": "plan_workload",
    "plan": "plan_workload",
    "workload": "plan_workload",
    "queue": "plan_workload",
    "code_discuss": "code_discuss",
    "code discuss": "code_discuss",
    "coding team": "code_discuss",
}


def infer_skill(description):
    """Infer skill from description. Also extracts advisory_id for security_apply."""
    lower = description.lower()
    for keyword, skill in SKILL_MAP.items():
        if keyword in lower:
            return skill
    return "summarize"  # default


def build_payload(description, skill):
    """Build task payload, handling skill-specific argument parsing."""
    import re
    if skill == "security_apply":
        # Extract advisory ID: "security_apply abc123de" or "apply abc123de"
        m = re.search(r'(?:security_apply|apply)\s+([a-f0-9]{8})', description, re.I)
        if m:
            return json.dumps({"advisory_id": m.group(1), "source": "lead"})
    if skill == "pen_test":
        # Allow "pen_test 192.168.1.0/24 full" style
        m = re.search(r'(?:pen_test|pentest|scan)\s+([\d./]+)(?:\s+(quick|service|full))?', description, re.I)
        if m:
            return json.dumps({"target": m.group(1), "scan_type": m.group(2) or "service", "source": "lead"})
    if skill == "security_audit":
        return json.dumps({"scope": "on_demand", "source": "lead"})
    if skill == "plan_workload":
        # "plan 20 leads" or "workload research 50" or just "plan"
        m_focus = re.search(r'\b(research|leads|security|business|all)\b', description, re.I)
        m_count = re.search(r'\b(\d+)\b', description)
        p = {}
        if m_focus:
            p["focus"] = m_focus.group(1).lower()
        if m_count:
            p["max_tasks"] = int(m_count.group(1))
        return json.dumps(p)
    return json.dumps({"description": description, "source": "lead"})


def cmd_status(args):
    db.init_db()
    status = db.get_fleet_status()
    print(f"\n{'NAME':<14} {'ROLE':<12} {'STATUS':<8} LAST HEARTBEAT")
    print("─" * 60)
    for a in status["agents"]:
        hb = db.utc_to_local(a.get("last_heartbeat"))
        marker = "●" if a["status"] == "BUSY" else "○"
        print(f"{marker} {a['name']:<13} {a['role']:<12} {a['status']:<8} {hb}")
    t = status["tasks"]
    print(f"\n  pending={t['PENDING']}  running={t['RUNNING']}  done={t['DONE']}  failed={t['FAILED']}\n")


def cmd_task(args):
    db.init_db()
    description = " ".join(args.description)
    skill = infer_skill(description)
    payload = build_payload(description, skill)
    task_id = db.post_task(skill, payload, priority=8)
    print(f"Task {task_id} queued [{skill}]: {description}")

    if args.wait:
        print("Waiting", end="", flush=True)
        for _ in range(120):
            time.sleep(3)
            result = db.get_task_result(task_id)
            if result and result["status"] in ("DONE", "FAILED"):
                print()
                if result["status"] == "DONE":
                    r = json.loads(result["result_json"])
                    print(r.get("summary") or r.get("report") or json.dumps(r, indent=2))
                else:
                    print(f"FAILED: {result['error']}")
                return
            print(".", end="", flush=True)
        print("\nTimeout — check with: result", task_id)


def cmd_result(args):
    db.init_db()
    result = db.get_task_result(args.task_id)
    if not result:
        print(f"Task {args.task_id} not found")
        return
    print(f"Status : {result['status']}")
    print(f"Type   : {result['type']}")
    if result["result_json"]:
        r = json.loads(result["result_json"])
        print(r.get("summary") or r.get("report") or json.dumps(r, indent=2))
    if result["error"]:
        print(f"Error  : {result['error']}")


def cmd_logs(args):
    log_file = FLEET_DIR / "logs" / f"{args.worker}.log"
    if not log_file.exists():
        print(f"No log: {log_file}")
        return
    lines = log_file.read_text().splitlines()
    print("\n".join(lines[-(args.tail or 20):]))


def cmd_send(args):
    """Send a task directly to a specific worker by role."""
    db.init_db()
    description = " ".join(args.description)
    skill = infer_skill(description)
    payload = json.dumps({"description": description, "source": "lead"})
    task_id = db.post_task(skill, payload, priority=9, assigned_to=args.worker)
    print(f"Task {task_id} sent to {args.worker} [{skill}]: {description}")


def main():
    parser = argparse.ArgumentParser(description="Fleet lead client")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show fleet status")

    tp = sub.add_parser("task", help="Dispatch a task (auto-routes to available worker)")
    tp.add_argument("description", nargs="+")
    tp.add_argument("--wait", action="store_true", help="Wait for result")

    rp = sub.add_parser("result", help="Get task result by ID")
    rp.add_argument("task_id", type=int)

    lp = sub.add_parser("logs", help="Show worker logs (e.g. researcher, coder_1, supervisor)")
    lp.add_argument("worker")
    lp.add_argument("--tail", type=int, default=20)

    sp = sub.add_parser("send", help="Send task to a specific worker (e.g. researcher, coder_1)")
    sp.add_argument("worker")
    sp.add_argument("description", nargs="+")

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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
