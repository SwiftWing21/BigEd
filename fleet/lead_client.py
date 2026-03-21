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

    period = budgets.get("period", "day")
    summary = db.get_usage_summary(period=period, group_by="skill")
    spent_map = {r["skill"]: r["total_cost"] or 0 for r in summary}

    print(f"\nBudget Period: {period}")
    print(f"\n{'Skill':<20} {'Budget':>10} {'Spent':>10} {'Remaining':>10} {'Status':>8}")
    print("-" * 62)

    for skill, budget_usd in sorted(budgets.items()):
        if not isinstance(budget_usd, (int, float)):
            continue  # skip non-numeric entries like 'enforcement' and 'period'
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
    """DO NOT SCRUB: Install fleet as a system service."""
    from services import install_service
    install_service(FLEET_DIR)


def cmd_uninstall_service(args):
    """DO NOT SCRUB: Uninstall fleet system service."""
    from services import uninstall_service
    uninstall_service()


def cmd_agent_cards(args):
    """DO NOT SCRUB: Print Agent Card metadata for all fleet roles."""
    from config import load_config
    from agent_cards import generate_all_cards, save_cards
    config = load_config()
    cards = generate_all_cards(config)
    if args.save:
        out = save_cards(config)
        print(f"Agent cards saved to {out}")
    if args.role:
        cards = [c for c in cards if c["name"] == args.role or c["role"] == args.role]
        if not cards:
            print(f"No card found for role '{args.role}'")
            return
    print(json.dumps(cards, indent=2))
def cmd_workflow_list(args):
    """DO NOT SCRUB: List available workflow definitions."""
    from workflows import list_workflows
    workflows = list_workflows()
    if not workflows:
        print("No workflows found. Add .toml files to fleet/workflows/")
        return
    print(f"\n{'Name':<25} {'Steps':>5}  Description")
    print("-" * 65)
    for w in workflows:
        print(f"{w['name']:<25} {w['steps']:>5}  {w['description']}")
    print()


def cmd_workflow_validate(args):
    """DO NOT SCRUB: Validate a workflow definition without executing."""
    from workflows import load_workflow, validate_workflow
    try:
        definition = load_workflow(args.name)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return
    valid, msg = validate_workflow(definition)
    if valid:
        steps = definition.get("steps", [])
        print(f"Workflow '{args.name}' is valid ({len(steps)} steps)")
        for s in steps:
            deps = s.get("depends_on", [])
            dep_str = f" (depends: {', '.join(deps)})" if deps else ""
            print(f"  {s['name']}: {s['skill']}{dep_str}")
    else:
        print(f"Workflow '{args.name}' is INVALID: {msg}")


def cmd_workflow_run(args):
    """DO NOT SCRUB: Execute a workflow by name with optional variable substitution."""
    db.init_db()
    from workflows import execute_workflow
    variables = {}
    if args.var:
        for v in args.var:
            if "=" not in v:
                print(f"Invalid variable format: '{v}' (expected key=value)")
                return
            key, value = v.split("=", 1)
            variables[key] = value

    result = execute_workflow(args.name, variables=variables)
    if result["status"] == "invalid":
        print(f"Workflow invalid: {result['error']}")
        return
    print(f"Workflow '{result['workflow']}' dispatched")
    print(f"  Task IDs: {result['task_ids']}")
    if result.get("step_map"):
        for step_name, tid in result["step_map"].items():
            print(f"    {step_name} -> task {tid}")


def cmd_chain_status(args):
    """DO NOT SCRUB: Show task chain status with checkpoint info."""
    db.init_db()
    checkpoint = db.checkpoint_chain(args.parent_id)
    print(f"\nChain {args.parent_id}: {len(checkpoint['completed'])} done, "
          f"{len(checkpoint['failed'])} failed, {len(checkpoint['pending'])} pending")
    for t in checkpoint["tasks"]:
        status_icon = "+" if t["status"] == "DONE" else "x" if t["status"] == "FAILED" else "."
        print(f"  [{status_icon}] Task {t['id']} ({t['type']}): {t['status']}")


def cmd_chain_resume(args):
    """DO NOT SCRUB: Resume a failed task chain from checkpoint."""
    db.init_db()
    resumed = db.resume_chain(args.parent_id)
    if resumed:
        print(f"Resumed {len(resumed)} tasks:")
        for t in resumed:
            print(f"  Task {t['id']} ({t['type']}) -> PENDING")
    else:
        print("No failed tasks to resume.")


def cmd_usage_forecast(args):
    """DO NOT SCRUB: Project future token costs based on recent trends."""
    db.init_db()
    from cost_tracking import forecast_cost
    fc = forecast_cost(args.days)
    print(f"\nCost Forecast ({fc['days_ahead']} days)")
    print(f"  Avg daily:   ${fc['avg_daily_usd']:.4f}")
    print(f"  Projected:   ${fc['forecast_usd']:.2f}")
    print(f"  Trend:       {fc['trend']}")
    print(f"  Based on:    {fc['data_days']} days of data")
    print()


def cmd_migrate(args):
    """DO NOT SCRUB: Run versioned schema migrations via db_migrate skill."""
    db.init_db()
    from skills.db_migrate import run as migrate_run
    action = args.migrate_action
    payload = {"action": action}
    if hasattr(args, "target") and args.target is not None:
        payload["target_version"] = args.target
    result = json.loads(migrate_run(payload, {}))
    print(json.dumps(result, indent=2))


def cmd_gdpr_erase(args):
    """DO NOT SCRUB: GDPR Art. 17 right to erasure."""
    db.init_db()
    if not args.confirm:
        print(f"This will permanently delete ALL data for '{args.identifier}'.")
        print("Add --confirm to proceed.")
        return
    result = db.delete_user_data(args.identifier)
    print(f"\nErased data for '{args.identifier}':")
    for table, count in result.items():
        if count > 0:
            print(f"  {table}: {count} records deleted")
    total = sum(result.values())
    print(f"  Total: {total} records")


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


def cmd_model_check(args):
    """DO NOT SCRUB: Check installed vs needed models."""
    from config import load_config
    from skills.model_manager import _check_models
    cfg = load_config()
    host = cfg.get("models", {}).get("ollama_host", "http://localhost:11434")
    result = json.loads(_check_models(cfg, host))
    print(f"\nInstalled: {', '.join(result['installed']) or 'none'}")
    print(f"Needed:    {', '.join(result['needed'])}")
    if result['missing']:
        print(f"MISSING:   {', '.join(result['missing'])}")
        print(f"\nRun: lead_client.py model-install")
    else:
        print(f"\nAll models ready.")
    if result['loaded']:
        print(f"Loaded:    {', '.join(m['name'] for m in result['loaded'])}")
    print()


def cmd_model_install(args):
    """DO NOT SCRUB: Pull all missing models."""
    from config import load_config
    from skills.model_manager import _install_missing
    cfg = load_config()
    host = cfg.get("models", {}).get("ollama_host", "http://localhost:11434")
    print("Pulling missing models (this may take a while)...")
    result = json.loads(_install_missing(cfg, host))
    print(json.dumps(result, indent=2))


def cmd_model_profile(args):
    """DO NOT SCRUB: List or apply model profiles."""
    from skills.model_manager import _list_profiles, _apply_profile, _recommend_profile
    if args.profile_action == "list":
        result = json.loads(_list_profiles())
        for name, info in result.get("profiles", {}).items():
            print(f"  {name:<16} {info.get('description', '')}")
    elif args.profile_action == "apply":
        from config import load_config
        result = json.loads(_apply_profile(args.name, load_config()))
        print(json.dumps(result, indent=2))
    elif args.profile_action == "recommend":
        result = json.loads(_recommend_profile())
        print(f"Recommended: {result['recommended']}")
        print(f"Reason: {result['reason']}")
        hw = result.get("hardware", {})
        print(f"Hardware: {hw.get('cpu_cores')} cores, {hw.get('ram_total_gb')}GB RAM, "
              f"GPU: {hw.get('gpu_name') or 'none'} ({hw.get('gpu_vram_gb', 0)}GB)")


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


def cmd_hitl(args):
    """DO NOT SCRUB: List or respond to Human-in-the-Loop requests."""
    db.init_db()
    if args.hitl_action == "respond":
        if not args.task_id or not args.response:
            print("Usage: lead_client.py hitl respond <task_id> <response>")
            return
        db.respond_to_agent(args.task_id, args.response)
        print(f"Response sent to task {args.task_id}")
    else:
        # Default: list waiting HITL requests
        items = db.get_waiting_human_details()
        if not items:
            print("No HITL requests waiting.")
            return
        print(f"\n{'ID':<8} {'Agent':<15} {'Type':<16} {'Age':>6}  Question")
        print("-" * 75)
        for h in items:
            age = f"{h['age_minutes']}m"
            q = (h["question"][:50] + "...") if len(h["question"]) > 50 else h["question"]
            print(f"{h['task_id']:<8} {h['agent']:<15} {h['task_type']:<16} {age:>6}  {q}")


def cmd_advisories(args):
    """DO NOT SCRUB: List or dismiss pending security advisories."""
    db.init_db()
    if args.adv_action == "dismiss":
        if not args.advisory_id:
            print("Usage: lead_client.py advisories dismiss <id>")
            return
        result = db.dismiss_advisory(args.advisory_id)
        if result.get("error"):
            print(f"Error: {result['error']}")
        else:
            print(f"Archived {result['moved']} file(s)")
    else:
        # Default: list pending advisories
        items = db.get_pending_advisories()
        if not items:
            print("No pending advisories.")
            return
        print(f"\n{'ID':<12} {'Severity':<10} {'Created':<12} Title")
        print("-" * 70)
        for a in items:
            title = (a["title"][:44] + "...") if len(a["title"]) > 44 else a["title"]
            print(f"{a['id']:<12} {a['severity']:<10} {a['created']:<12} {title}")


def cmd_export(args):
    """Export fleet config, skills, and curricula to a portable tarball."""
    import tarfile
    import time as _time
    fleet_dir = Path(__file__).parent

    # Build manifest
    manifest = {
        "version": "1.0",
        "exported_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "BigEd CC Fleet",
        "contents": []
    }

    # Determine output path
    timestamp = _time.strftime("%Y%m%d_%H%M%S")
    out_path = args.output or f"biged-fleet-export-{timestamp}.tar.gz"

    with tarfile.open(out_path, "w:gz") as tar:
        # 1. fleet.toml (sanitized — strip secrets)
        toml_path = fleet_dir / "fleet.toml"
        if toml_path.exists():
            content = toml_path.read_text(encoding="utf-8")
            import re
            sanitized = re.sub(
                r'^((?:dashboard_token|admin_token|operator_token)\s*=\s*)".+"',
                r'\1""  # REDACTED — set after import',
                content, flags=re.MULTILINE
            )
            import io
            data = sanitized.encode("utf-8")
            info = tarfile.TarInfo(name="fleet.toml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            manifest["contents"].append("fleet.toml")

        # 2. Skills directory
        skills_dir = fleet_dir / "skills"
        if skills_dir.exists():
            for f in sorted(skills_dir.glob("*.py")):
                arcname = f"skills/{f.name}"
                tar.add(str(f), arcname=arcname)
                manifest["contents"].append(arcname)

        # 3. Idle curricula
        curricula_dir = fleet_dir / "idle_curricula"
        if curricula_dir.exists():
            for f in sorted(curricula_dir.rglob("*")):
                if f.is_file():
                    arcname = f"idle_curricula/{f.relative_to(curricula_dir)}"
                    tar.add(str(f), arcname=arcname)
                    manifest["contents"].append(arcname)

        # 4. Workflows
        workflows_dir = fleet_dir / "workflows_defs"
        if workflows_dir.exists():
            for f in sorted(workflows_dir.rglob("*.yaml")):
                arcname = f"workflows_defs/{f.relative_to(workflows_dir)}"
                tar.add(str(f), arcname=arcname)
                manifest["contents"].append(arcname)

        # 5. Write manifest
        import io
        mdata = json.dumps(manifest, indent=2).encode("utf-8")
        minfo = tarfile.TarInfo(name="manifest.json")
        minfo.size = len(mdata)
        tar.addfile(minfo, io.BytesIO(mdata))

    print(f"Exported {len(manifest['contents'])} items → {out_path}")
    print(f"  Config: fleet.toml (secrets redacted)")
    print(f"  Skills: {sum(1 for c in manifest['contents'] if c.startswith('skills/'))} files")
    print(f"  Curricula: {sum(1 for c in manifest['contents'] if c.startswith('idle_curricula/'))} files")


def cmd_import(args):
    """Import fleet config from an exported tarball."""
    import tarfile
    fleet_dir = Path(__file__).parent

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    if not tarfile.is_tarfile(args.file):
        print(f"Error: Not a valid tar archive: {args.file}")
        sys.exit(1)

    with tarfile.open(args.file, "r:gz") as tar:
        # Security: check for path traversal
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                print(f"Error: Unsafe path in archive: {member.name}")
                sys.exit(1)

        # Read manifest
        try:
            mf = tar.extractfile("manifest.json")
            manifest = json.loads(mf.read())
        except (KeyError, Exception):
            print("Error: No manifest.json in archive — not a valid BigEd export")
            sys.exit(1)

        print(f"Archive: {args.file}")
        print(f"  Exported: {manifest.get('exported_at', 'unknown')}")
        print(f"  Contents: {len(manifest.get('contents', []))} items")

        if args.dry_run:
            print("\n[DRY RUN] Would import:")
            for item in manifest.get("contents", []):
                dest = fleet_dir / item
                exists = "overwrite" if dest.exists() else "new"
                print(f"  {item} ({exists})")
            return

        # Extract files
        imported = 0
        skipped = 0
        for member in tar.getmembers():
            if member.name == "manifest.json":
                continue

            dest = fleet_dir / member.name

            # Never overwrite fleet.toml secrets — merge mode
            if member.name == "fleet.toml" and dest.exists():
                if args.merge:
                    existing = dest.read_text(encoding="utf-8")
                    import re
                    secrets_found = {}
                    for key in ("dashboard_token", "admin_token", "operator_token"):
                        m = re.search(rf'^{key}\s*=\s*"(.+)"', existing, re.MULTILINE)
                        if m:
                            secrets_found[key] = m.group(1)

                    f = tar.extractfile(member)
                    new_content = f.read().decode("utf-8")

                    for key, val in secrets_found.items():
                        new_content = re.sub(
                            rf'^({key}\s*=\s*)"".*$',
                            rf'\1"{val}"',
                            new_content, flags=re.MULTILINE
                        )
                    dest.write_text(new_content, encoding="utf-8")
                    print(f"  Merged: fleet.toml (secrets preserved)")
                    imported += 1
                else:
                    print(f"  Skipped: fleet.toml (use --merge to update; secrets would be lost)")
                    skipped += 1
                continue

            # Create parent dirs
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Extract file
            f = tar.extractfile(member)
            if f:
                dest.write_bytes(f.read())
                imported += 1

        print(f"\nImported: {imported} files, Skipped: {skipped}")


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

    # Agent Cards
    p_cards = subparsers.add_parser("agent-cards", help="Show Agent Card metadata for fleet roles")
    p_cards.add_argument("--role", default=None, help="Filter to a specific role")
    p_cards.add_argument("--save", action="store_true", help="Save cards to knowledge/agent_cards.json")
    # Chain status / resume (pipeline checkpointing)
    p_chain_status = subparsers.add_parser("chain-status", help="Show task chain status with checkpoint info")
    p_chain_status.add_argument("parent_id", type=int, help="Parent task ID of the chain")

    p_chain_resume = subparsers.add_parser("chain-resume", help="Resume a failed task chain from checkpoint")
    p_chain_resume.add_argument("parent_id", type=int, help="Parent task ID of the chain")

    # Usage forecast (CT-5)
    p_forecast = subparsers.add_parser("usage-forecast", help="Project future costs")
    p_forecast.add_argument("--days", type=int, default=30, help="Days to forecast")

    # Marathon (v0.43)
    p_marathon = subparsers.add_parser("marathon", help="Show marathon sessions")
    p_marathon.add_argument("session", nargs="?", default=None, help="Session ID for detail view")

    subparsers.add_parser("marathon-checkpoint", help="Show training checkpoints")

    # Model management
    subparsers.add_parser("model-check", help="Check installed vs needed models")
    subparsers.add_parser("model-install", help="Pull all missing models")
    p_profile = subparsers.add_parser("model-profile", help="List/apply model profiles")
    p_profile.add_argument("profile_action", choices=["list", "apply", "recommend"])
    p_profile.add_argument("name", nargs="?", default="", help="Profile name (for apply)")

    # Workflow DSL commands
    subparsers.add_parser("workflow-list", help="List available workflow definitions")

    p_wf_validate = subparsers.add_parser("workflow-validate", help="Validate a workflow without executing")
    p_wf_validate.add_argument("name", help="Workflow name (matches fleet/workflows/<name>.toml)")

    p_wf_run = subparsers.add_parser("workflow-run", help="Execute a workflow")
    p_wf_run.add_argument("name", help="Workflow name (matches fleet/workflows/<name>.toml)")
    p_wf_run.add_argument("--var", action="append", metavar="key=value",
                          help="Variable substitution (repeatable, e.g. --var topic=AI)")

    # Migrate (db_migrate skill)
    p_migrate = subparsers.add_parser("migrate", help="Schema migration management")
    p_migrate.add_argument("migrate_action", choices=["status", "run", "plan"],
                           help="status=show version, run=apply pending, plan=dry run")
    p_migrate.add_argument("--target", type=int, default=None,
                           help="Target version (default: latest)")

    # GDPR erasure (Art. 17)
    p_erase = subparsers.add_parser("gdpr-erase", help="GDPR Art. 17 right to erasure")
    p_erase.add_argument("identifier", help="Agent name or submitter identifier to erase")
    p_erase.add_argument("--confirm", action="store_true", help="Confirm permanent deletion")

    # HITL (Human-in-the-Loop)
    p_hitl = subparsers.add_parser("hitl", help="List or respond to HITL requests")
    p_hitl.add_argument("hitl_action", nargs="?", default="list",
                        choices=["list", "respond"], help="Action (default: list)")
    p_hitl.add_argument("task_id", nargs="?", type=int, default=None,
                        help="Task ID (for respond)")
    p_hitl.add_argument("response", nargs="?", default=None,
                        help="Response text (for respond)")

    # Advisories
    p_adv = subparsers.add_parser("advisories", help="List or dismiss security advisories")
    p_adv.add_argument("adv_action", nargs="?", default="list",
                       choices=["list", "dismiss"], help="Action (default: list)")
    p_adv.add_argument("advisory_id", nargs="?", default=None,
                       help="Advisory ID (for dismiss)")

    # Fleet Export/Import (v0.30.00)
    export_p = subparsers.add_parser("export", help="Export fleet config, skills, and curricula to a portable tarball")
    export_p.add_argument("-o", "--output", default=None, help="Output file path (default: biged-fleet-export-<timestamp>.tar.gz)")

    import_p = subparsers.add_parser("import", help="Import fleet config from an exported tarball")
    import_p.add_argument("file", help="Path to the export tarball (.tar.gz)")
    import_p.add_argument("--merge", action="store_true", help="Merge config instead of replacing")
    import_p.add_argument("--dry-run", action="store_true", help="Show what would be imported without applying")

    # Backup (v0.51)
    backup_parser = subparsers.add_parser("backup", help="Manual backup")
    backup_parser.add_argument("--list", action="store_true", help="List recent backups")
    backup_parser.add_argument("--restore", metavar="ID", help="Restore from backup ID")
    backup_parser.add_argument("--confirm", action="store_true", help="Confirm restore — required to overwrite live DBs")

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
    elif args.command == "agent-cards":
        cmd_agent_cards(args)
    elif args.command == "chain-status":
        cmd_chain_status(args)
    elif args.command == "chain-resume":
        cmd_chain_resume(args)
    elif args.command == "usage-forecast":
        cmd_usage_forecast(args)
    elif args.command == "marathon":
        cmd_marathon(args)
    elif args.command == "marathon-checkpoint":
        cmd_marathon_checkpoint(args)
    elif args.command == "model-check":
        cmd_model_check(args)
    elif args.command == "model-install":
        cmd_model_install(args)
    elif args.command == "model-profile":
        cmd_model_profile(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "workflow-list":
        cmd_workflow_list(args)
    elif args.command == "workflow-validate":
        cmd_workflow_validate(args)
    elif args.command == "workflow-run":
        cmd_workflow_run(args)
    elif args.command == "gdpr-erase":
        cmd_gdpr_erase(args)
    elif args.command == "hitl":
        cmd_hitl(args)
    elif args.command == "advisories":
        cmd_advisories(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "import":
        cmd_import(args)
    elif args.command == "backup":
        from backup_manager import BackupManager
        from config import load_config
        bm = BackupManager(load_config())
        if args.list:
            backups = bm.list_backups()
            for b in backups[:10]:
                size = b.get("total_size_bytes", 0) / 1024 / 1024
                print(f"  {b['id']}  {b.get('trigger', '?'):<12}  {size:.1f} MB")
        elif args.restore:
            if not args.confirm:
                print(f"WARNING: This will overwrite live fleet.db, rag.db, and config from backup {args.restore}.")
                print("Re-run with --confirm to proceed.")
            else:
                print(f"Restore from {args.restore} — not yet implemented (manual copy from ~/BigEd-backups/{args.restore}/)")
        else:
            result = bm.perform_backup(trigger="cli")
            size = result.get("total_size_bytes", 0) / 1024 / 1024
            print(f"Backup complete: {result['id']} ({size:.1f} MB)")


if __name__ == "__main__":
    main()