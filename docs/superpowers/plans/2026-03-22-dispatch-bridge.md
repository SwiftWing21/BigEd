# Dispatch Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose BigEd's 92-skill fleet as an MCP server via FastMCP so Claude Cowork Dispatch can submit tasks, check status, and respond to HITL gates from the Claude mobile app.

**Architecture:** FastMCP stdio server (`fleet/mcp_server.py`) with 7 tools + 1 resource. Direct DB for writes, HTTP for reads. Intent parser extracted to shared module. CLI fallback via `fleet/dispatch_bridge.py`. Claude Desktop auto-registration from launcher boot.

**Tech Stack:** FastMCP (pip), Python stdlib, existing fleet DB/config/dashboard APIs.

**Spec:** `docs/superpowers/specs/2026-03-22-dispatch-bridge-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `fleet/intent.py` | **Create** | Shared NL intent parser (extracted from lead_client.py) |
| `fleet/mcp_server.py` | **Create** | FastMCP server — 7 tools, 1 resource |
| `fleet/dispatch_bridge.py` | **Create** | CLI fallback for Dispatch-shaped commands |
| `fleet/db.py` | **Edit** (line ~1040) | Add `cancel_task(task_id)` function |
| `fleet/lead_client.py` | **Edit** (lines 34-80) | Replace inline parser with `from intent import parse_intent_with_maintainer` |
| `fleet/fleet.toml` | **Edit** (append after line 397) | Add `[dispatch_bridge]` config section |
| `fleet/requirements.txt` | **Edit** (append) | Add `fastmcp` |
| `fleet/dependency_check.py` | **Edit** (lines 355-376) | Add `check_fastmcp()` to ALL_CHECKS |
| `fleet/mcp_manager.py` | **Edit** (append) | Add `register_claude_desktop()` and `get_claude_desktop_config_path()` |
| `BigEd/launcher/launcher.py` | **Edit** | Add Dispatch bridge registration prompt after boot |
| `BigEd/launcher/ui/settings/mcp.py` | **Edit** | Add Dispatch Bridge toggle section |

---

## Task 1: Foundation — Intent Extraction + DB + Config

**Files:**
- Create: `fleet/intent.py`
- Modify: `fleet/lead_client.py:34-80`
- Modify: `fleet/db.py:~1040`
- Modify: `fleet/fleet.toml:397+`
- Modify: `fleet/requirements.txt`
- Modify: `fleet/dependency_check.py:355-376`

### Step 1.1: Create `fleet/intent.py`

- [ ] Extract `parse_intent_with_maintainer()` from `fleet/lead_client.py` (lines 34-80) and helper `_get_intent_model()` (lines 26-31) into a new `fleet/intent.py` module.

```python
"""Shared NL intent parser — extracts skill + payload from natural language."""

import json
import logging
import re
import urllib.request

_log = logging.getLogger("intent")


def _get_intent_model():
    """Return conductor model name from config, default qwen3:4b."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("models", {}).get("conductor", "qwen3:4b")
    except Exception:
        return "qwen3:4b"


def parse_intent_with_maintainer(text: str) -> tuple:
    """Parse natural language into (skill_name, payload_dict).

    Routes through conductor model (qwen3:4b) via Ollama.
    Falls back to ("summarize", {"description": text}) on failure.
    """
    model = _get_intent_model()
    prompt = (
        "You are an intent parser. Given user text, return JSON with "
        '"skill" (string) and "payload" (object). Available skills include: '
        "summarize, code_review, web_search, security_audit, rag_index, "
        "rag_query, ingest, plan_workload, lead_research, browser_crawl. "
        f"User text: {text}\nReturn ONLY valid JSON."
    )
    try:
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        raw = data.get("response", "")
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            skill = parsed.get("skill", "summarize")
            payload = parsed.get("payload", {"description": text})
            return (skill, payload)
    except Exception:
        _log.warning("Intent parsing failed, falling back to summarize", exc_info=True)
    return ("summarize", {"description": text})
```

- [ ] Commit: `git add fleet/intent.py && git commit -m "feat: extract intent parser to shared fleet/intent.py module"`

### Step 1.2: Refactor `lead_client.py` to use `intent.py`

- [ ] In `fleet/lead_client.py`, replace the inline `_get_intent_model()` (lines 26-31) and `parse_intent_with_maintainer()` (lines 34-80) with imports from `intent.py`:

Replace the function bodies with:
```python
from intent import parse_intent_with_maintainer  # extracted to shared module
```

Keep the import at the top of the file (lead_client.py already has module-level imports, this is not a skill).

- [ ] Commit: `git add fleet/lead_client.py && git commit -m "refactor: lead_client uses shared intent.py parser"`

### Step 1.3: Add `cancel_task()` to `fleet/db.py`

- [ ] Add after `respond_to_agent()` (~line 1040) in `fleet/db.py`:

```python
def cancel_task(task_id):
    """Cancel a PENDING or WAITING_HUMAN task. Returns True if cancelled."""
    def _do():
        with get_conn() as conn:
            row = conn.execute("SELECT status, payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return False
            status = row["status"]
            if status not in ("PENDING", "WAITING_HUMAN"):
                return False
            payload = json.loads(row["payload_json"] or "{}")
            payload["_cancel_reason"] = "user_cancelled"
            conn.execute(
                "UPDATE tasks SET status='FAILED', payload_json=?, updated_at=? WHERE id=?",
                (json.dumps(payload), _now_iso(), task_id),
            )
            return True
    return _retry_write(_do)
```

- [ ] Commit: `git add fleet/db.py && git commit -m "feat: add db.cancel_task() for Dispatch bridge"`

### Step 1.4: Add `[dispatch_bridge]` to `fleet/fleet.toml`

- [ ] Append after the last section (line 397+):

```toml

# ── Dispatch Bridge (Cowork Dispatch → Fleet) ──────────────────────
[dispatch_bridge]
enabled = true
registered_claude_desktop = false
hitl_auto_approve_timeout_min = 0       # 0 = off (wait forever)
hitl_auto_approve_max_cost = 0          # 0 = no cost gate
dashboard_base_url = "http://127.0.0.1:5555"
```

- [ ] Commit: `git add fleet/fleet.toml && git commit -m "config: add [dispatch_bridge] section to fleet.toml"`

### Step 1.5: Add `fastmcp` to `fleet/requirements.txt`

- [ ] Append to `fleet/requirements.txt`:

```
fastmcp>=2.0.0
```

- [ ] Commit: `git add fleet/requirements.txt && git commit -m "deps: add fastmcp to fleet requirements"`

### Step 1.6: Add `check_fastmcp()` to `fleet/dependency_check.py`

- [ ] Add new check function before `ALL_CHECKS` (before line 355):

```python
def check_fastmcp() -> dict:
    """FastMCP — MCP server framework for Dispatch bridge."""
    try:
        import fastmcp
        version = getattr(fastmcp, "__version__", "unknown")
        return {
            "name": "fastmcp",
            "category": "mcp",
            "required": False,
            "found": True,
            "ok": True,
            "version": version,
            "detail": f"FastMCP {version}",
        }
    except ImportError:
        return {
            "name": "fastmcp",
            "category": "mcp",
            "required": False,
            "found": False,
            "ok": False,
            "version": None,
            "detail": "pip install fastmcp",
        }
```

- [ ] Add `check_fastmcp` to the `ALL_CHECKS` list (after `check_playwright_mcp`).

- [ ] Commit: `git add fleet/dependency_check.py && git commit -m "feat: add check_fastmcp() dependency check"`

---

## Task 2: MCP Server — `fleet/mcp_server.py`

**Files:**
- Create: `fleet/mcp_server.py`

**Dependencies:** Needs `fleet/intent.py` and `db.cancel_task()` from Task 1. If running in parallel with Task 1, use lazy imports — they'll resolve at runtime once Task 1 merges.

### Step 2.1: Create `fleet/mcp_server.py` with FastMCP scaffold

- [ ] Create `fleet/mcp_server.py`:

```python
"""BigEd Fleet MCP Server — exposes fleet skills to Claude Desktop / Cowork Dispatch.

Transport: stdio (default for Claude Desktop).
Tools: fleet_task, fleet_dispatch, fleet_status, fleet_catalog,
       fleet_task_result, fleet_hitl_respond, fleet_cancel
Resource: biged://hitl/pending
"""

import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

from fastmcp import FastMCP

_log = logging.getLogger("mcp_server")

# Ensure fleet/ is on sys.path for lazy imports
_FLEET_DIR = Path(__file__).resolve().parent
if str(_FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(_FLEET_DIR))

mcp = FastMCP(
    "BigEd Fleet",
    description="92-skill AI worker fleet — submit tasks, check status, respond to HITL gates",
)


def _get_base_url() -> str:
    """Dashboard base URL from config, default localhost:5555."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("dispatch_bridge", {}).get(
            "dashboard_base_url", "http://127.0.0.1:5555"
        )
    except Exception:
        return "http://127.0.0.1:5555"


def _http_get(path: str) -> dict:
    """GET request to dashboard API. Returns parsed JSON."""
    url = f"{_get_base_url()}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


# ── Tools ──────────────────────────────────────────────────────────


@mcp.tool()
def fleet_task(instruction: str) -> dict:
    """Submit a natural language task to the BigEd fleet.

    The instruction is parsed by the conductor model to determine
    which skill to invoke and what payload to pass.

    Args:
        instruction: Natural language task description
    """
    try:
        from intent import parse_intent_with_maintainer
        import db

        skill, payload = parse_intent_with_maintainer(instruction)
        fallback = skill == "summarize" and "description" in payload
        task_id = db.post_task(skill, json.dumps(payload))
        result = {"task_id": task_id, "skill": skill, "status": "PENDING"}
        if fallback:
            result["fallback"] = True
        return result
    except Exception as e:
        _log.warning("fleet_task failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_dispatch(skill: str, payload: dict | None = None) -> dict:
    """Dispatch an explicit skill with optional payload.

    Args:
        skill: Skill name (e.g. 'code_review', 'summarize', 'web_search')
        payload: Optional dict of skill-specific parameters
    """
    try:
        import db

        payload = payload or {}
        task_id = db.post_task(skill, json.dumps(payload))
        return {"task_id": task_id, "skill": skill, "status": "PENDING"}
    except Exception as e:
        _log.warning("fleet_dispatch failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_status() -> dict:
    """Get current fleet health — agent counts, queue depth, running tasks."""
    try:
        data = _http_get("/api/status")
        return {
            "agents": data.get("agents", 0),
            "queue_depth": data.get("queue_depth", 0),
            "running": data.get("running_tasks", 0),
            "healthy": data.get("healthy", False),
        }
    except Exception as e:
        _log.warning("fleet_status failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_catalog() -> dict:
    """List all available fleet skills with descriptions.

    Scans fleet/skills/*.py on disk — works in offline and air-gap modes.
    """
    try:
        skills_dir = _FLEET_DIR / "skills"
        skills = []
        for py_file in sorted(skills_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            name = None
            desc = None
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
        return {"skills": skills}
    except Exception as e:
        _log.warning("fleet_catalog failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_task_result(task_id: int) -> dict:
    """Check the result of a submitted task.

    Args:
        task_id: The task ID returned by fleet_task or fleet_dispatch
    """
    try:
        import db

        row = db.get_task_result(task_id)
        if not row:
            return {"error": f"Task {task_id} not found"}
        return {
            "task_id": row["id"],
            "status": row["status"],
            "result": row.get("result_json", None),
            "skill": row.get("type", "unknown"),
        }
    except Exception as e:
        _log.warning("fleet_task_result failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_hitl_respond(task_id: int, response: str) -> dict:
    """Respond to a human-in-the-loop gate.

    Args:
        task_id: The task awaiting human input
        response: Your response text (e.g. 'approved', 'rejected — reason', or freeform)
    """
    try:
        import db

        db.respond_to_agent(task_id, response)
        return {"task_id": task_id, "responded": True, "response": response}
    except Exception as e:
        _log.warning("fleet_hitl_respond failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_cancel(task_id: int) -> dict:
    """Cancel a pending or waiting task.

    Args:
        task_id: The task to cancel (must be PENDING or WAITING_HUMAN)
    """
    try:
        import db

        cancelled = db.cancel_task(task_id)
        return {"task_id": task_id, "cancelled": cancelled}
    except Exception as e:
        _log.warning("fleet_cancel failed", exc_info=True)
        return {"error": str(e)}


# ── Resources ──────────────────────────────────────────────────────


@mcp.resource("biged://hitl/pending")
def pending_hitl() -> str:
    """Tasks currently waiting for human approval or input."""
    try:
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
                "task_id": row["id"],
                "skill": row["type"],
                "agent": row["assigned_to"],
                "question": payload.get("_human_question", "Approval required"),
                "context": payload.get("_human_context", ""),
                "created_at": row["created_at"],
            })
        return json.dumps(tasks, indent=2)
    except Exception as e:
        _log.warning("pending_hitl resource failed", exc_info=True)
        return json.dumps({"error": str(e)})


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
```

- [ ] Commit: `git add fleet/mcp_server.py && git commit -m "feat: BigEd Fleet MCP server — 7 tools + HITL resource via FastMCP"`

---

## Task 3: CLI Fallback — `fleet/dispatch_bridge.py`

**Files:**
- Create: `fleet/dispatch_bridge.py`

### Step 3.1: Create `fleet/dispatch_bridge.py`

- [ ] Create `fleet/dispatch_bridge.py`:

```python
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
import os
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
```

- [ ] Commit: `git add fleet/dispatch_bridge.py && git commit -m "feat: dispatch_bridge.py CLI fallback for Cowork Dispatch"`

---

## Task 4: Claude Desktop Registration — `mcp_manager.py` + Launcher

**Files:**
- Modify: `fleet/mcp_manager.py` (append new functions)
- Modify: `BigEd/launcher/launcher.py` (add registration call in boot)

### Step 4.1: Add registration helpers to `mcp_manager.py`

- [ ] Append to `fleet/mcp_manager.py` (after last function, ~line 247):

```python
def get_claude_desktop_config_path() -> Path:
    """Return platform-specific path to Claude Desktop config."""
    import sys as _sys
    if _sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif _sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def is_registered_claude_desktop() -> bool:
    """Check if biged-fleet is registered in Claude Desktop config."""
    cfg_path = get_claude_desktop_config_path()
    if not cfg_path.exists():
        return False
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return "biged-fleet" in data.get("mcpServers", {})
    except Exception:
        return False


def register_claude_desktop() -> bool:
    """Register biged-fleet MCP server in Claude Desktop config.

    Returns True if registered successfully, False on error.
    """
    cfg_path = get_claude_desktop_config_path()
    mcp_server_path = str(Path(__file__).resolve().parent / "mcp_server.py")

    # Load existing config or create new
    data = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    servers = data.setdefault("mcpServers", {})
    servers["biged-fleet"] = {
        "command": "python",
        "args": [mcp_server_path],
        "env": {},
    }

    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _log.info("Registered biged-fleet in Claude Desktop config: %s", cfg_path)
        return True
    except Exception:
        _log.warning("Failed to register biged-fleet in Claude Desktop", exc_info=True)
        return False


def unregister_claude_desktop() -> bool:
    """Remove biged-fleet from Claude Desktop config."""
    cfg_path = get_claude_desktop_config_path()
    if not cfg_path.exists():
        return True
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        if "biged-fleet" in servers:
            del servers["biged-fleet"]
            cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        _log.warning("Failed to unregister biged-fleet", exc_info=True)
        return False
```

- [ ] Commit: `git add fleet/mcp_manager.py && git commit -m "feat: Claude Desktop registration helpers in mcp_manager.py"`

### Step 4.2: Add Dispatch bridge registration prompt to launcher boot

- [ ] In `BigEd/launcher/launcher.py`, find where the boot sequence completes (after all stages finish, typically where a success message is shown). Add a post-boot check:

```python
# After boot completes successfully — check Dispatch bridge registration
self._check_dispatch_bridge_registration()
```

Add the method to the launcher class:

```python
def _check_dispatch_bridge_registration(self):
    """Prompt to register with Claude Desktop if Dispatch bridge enabled."""
    try:
        from config import load_config
        cfg = load_config()
        bridge = cfg.get("dispatch_bridge", {})
        if not bridge.get("enabled", False):
            return
        if bridge.get("registered_claude_desktop", False):
            return

        import mcp_manager
        if mcp_manager.is_registered_claude_desktop():
            return

        # Show registration prompt
        import customtkinter as ctk
        result = ctk.CTkInputDialog(
            text="BigEd can connect to Claude Desktop for mobile Dispatch.\nRegister now?",
            title="Dispatch Bridge",
        )
        # Use a simple messagebox instead
        from tkinter import messagebox
        answer = messagebox.askyesno(
            "Dispatch Bridge",
            "BigEd can connect to Claude Desktop for mobile Dispatch.\n\n"
            "This lets you send tasks to your fleet from the Claude mobile app.\n\n"
            "Register now?",
        )
        if answer:
            if mcp_manager.register_claude_desktop():
                messagebox.showinfo("Dispatch Bridge", "Registered with Claude Desktop!")
            else:
                messagebox.showwarning("Dispatch Bridge", "Registration failed — check logs.")
    except Exception:
        pass  # Non-critical, don't block boot
```

- [ ] Commit: `git add BigEd/launcher/launcher.py && git commit -m "feat: Dispatch bridge registration prompt on launcher boot"`

---

## Task 5: Launcher UI — Settings Toggle

**Files:**
- Modify: `BigEd/launcher/ui/settings/mcp.py`

### Step 5.1: Add Dispatch Bridge section to MCP settings panel

- [ ] In `BigEd/launcher/ui/settings/mcp.py`, find the `McpPanelMixin` class and its `_build_mcp_panel()` method. Add a new section at the top of the panel (before "Connected Servers"):

```python
# ── Dispatch Bridge section ────────────────────────────────
dispatch_frame = ctk.CTkFrame(parent, fg_color="transparent")
dispatch_frame.pack(fill="x", padx=12, pady=(8, 4))

ctk.CTkLabel(
    dispatch_frame, text="Dispatch Bridge",
    font=FONT_BOLD, anchor="w",
).pack(fill="x")

ctk.CTkLabel(
    dispatch_frame,
    text="Connect to Claude Desktop for mobile Dispatch",
    font=FONT_XS, text_color="gray60", anchor="w",
).pack(fill="x")

# Enable/disable toggle
self._dispatch_enabled_var = ctk.BooleanVar(value=self._get_dispatch_enabled())
dispatch_toggle = ctk.CTkSwitch(
    dispatch_frame,
    text="Enable Dispatch Bridge",
    variable=self._dispatch_enabled_var,
    command=self._toggle_dispatch_bridge,
    font=FONT_SM,
)
dispatch_toggle.pack(anchor="w", pady=(4, 0))

# Registration status
self._dispatch_status_label = ctk.CTkLabel(
    dispatch_frame,
    text=self._get_dispatch_status_text(),
    font=FONT_XS, text_color="gray60", anchor="w",
)
self._dispatch_status_label.pack(fill="x", pady=(2, 0))

# Register/unregister button
self._dispatch_reg_btn = ctk.CTkButton(
    dispatch_frame,
    text="Register with Claude Desktop",
    command=self._register_dispatch,
    font=FONT_SM, width=200, height=28,
)
self._dispatch_reg_btn.pack(anchor="w", pady=(4, 8))
```

- [ ] Add the helper methods:

```python
def _get_dispatch_enabled(self) -> bool:
    try:
        from config import load_config
        return load_config().get("dispatch_bridge", {}).get("enabled", False)
    except Exception:
        return False

def _get_dispatch_status_text(self) -> str:
    try:
        import mcp_manager
        if mcp_manager.is_registered_claude_desktop():
            return "Status: Registered with Claude Desktop"
        return "Status: Not registered"
    except Exception:
        return "Status: Unknown"

def _toggle_dispatch_bridge(self):
    # Update fleet.toml dispatch_bridge.enabled
    try:
        import toml
        toml_path = Path(__file__).resolve().parents[3] / "fleet" / "fleet.toml"
        data = toml.loads(toml_path.read_text(encoding="utf-8"))
        data.setdefault("dispatch_bridge", {})["enabled"] = self._dispatch_enabled_var.get()
        toml_path.write_text(toml.dumps(data), encoding="utf-8")
    except Exception:
        _log.warning("Failed to toggle dispatch bridge", exc_info=True)

def _register_dispatch(self):
    try:
        import mcp_manager
        from tkinter import messagebox
        if mcp_manager.is_registered_claude_desktop():
            if messagebox.askyesno("Dispatch Bridge", "Already registered. Unregister?"):
                mcp_manager.unregister_claude_desktop()
        else:
            if mcp_manager.register_claude_desktop():
                messagebox.showinfo("Dispatch Bridge", "Registered!")
            else:
                messagebox.showwarning("Dispatch Bridge", "Failed — check logs.")
        self._dispatch_status_label.configure(text=self._get_dispatch_status_text())
    except Exception:
        _log.warning("Dispatch registration failed", exc_info=True)
```

- [ ] Commit: `git add BigEd/launcher/ui/settings/mcp.py && git commit -m "feat: Dispatch Bridge toggle in MCP settings panel"`

---

## Parallel Agent Assignment

| Agent | Task | Files | Independent? |
|---|---|---|---|
| Agent 1 | Task 1: Foundation | intent.py, lead_client.py, db.py, fleet.toml, requirements.txt, dependency_check.py | Yes |
| Agent 2 | Task 2: MCP Server | mcp_server.py | Yes (lazy imports) |
| Agent 3 | Task 3: CLI Fallback | dispatch_bridge.py | Yes (lazy imports) |
| Agent 4 | Task 4: Registration | mcp_manager.py, launcher.py | Yes |
| Agent 5 | Task 5: Launcher UI | settings/mcp.py | Yes |

All 5 tasks can run in parallel. Tasks 2-5 use lazy imports for `intent`, `db`, and `config`, so they don't need Task 1 to complete first. Git merge handles any overlapping edits.
