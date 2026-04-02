"""BigEd Fleet MCP Server — exposes fleet skills to Claude Desktop / Cowork Dispatch.

Transport: stdio (default for Claude Desktop).
Tools: fleet_task, fleet_dispatch, fleet_status, fleet_catalog,
       fleet_task_result, fleet_hitl_respond, fleet_cancel
Resource: biged://hitl/pending
"""

import json
import logging
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
    instructions="92-skill AI worker fleet — submit tasks, check status, respond to HITL gates",
)


_PORT_FILE = _FLEET_DIR / ".port"
_FALLBACK_PORTS = [5555, 5556, 5557]


def _discover_port() -> int:
    """Read active dashboard port from .port file, or probe fallbacks."""
    if _PORT_FILE.exists():
        try:
            return int(_PORT_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    for port in _FALLBACK_PORTS:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            return port
        except Exception:
            continue
    return _FALLBACK_PORTS[0]


def _get_base_url() -> str:
    """Dashboard base URL with port discovery."""
    return f"http://127.0.0.1:{_discover_port()}"


def _http_get(path: str) -> dict:
    """GET request to dashboard API. Returns parsed JSON."""
    url = f"{_get_base_url()}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _http_post(path: str, body: dict | None = None) -> dict:
    """POST request to dashboard API. Returns parsed JSON."""
    url = f"{_get_base_url()}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
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
def fleet_hitl_respond(task_id: int, response: str, source: str = "local") -> dict:
    """Respond to a human-in-the-loop gate.

    Auto-routes to the correct fleet based on the source field.
    Use source="local" for local tasks, source="peer:http://..." for remote tasks.

    Args:
        task_id: The task awaiting human input
        response: Your response text (e.g. 'approved', 'rejected — reason', or freeform)
        source: Task source — "local" or "peer:<url>" (from biged://hitl/pending)
    """
    try:
        if source and source.startswith("peer:"):
            # Route to remote peer
            peer_url = source[5:]  # strip "peer:" prefix
            from federation_hitl import respond_to_remote_hitl
            result = respond_to_remote_hitl(peer_url, task_id, response)
            if "error" in result:
                return result
            return {"task_id": task_id, "responded": True, "response": response, "routed_to": peer_url}

        # Local response
        import db
        db.respond_to_agent(task_id, response)
        return {"task_id": task_id, "responded": True, "response": response, "routed_to": "local"}
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


@mcp.tool()
def fleet_chat(message: str) -> dict:
    """Send a chat message to the fleet. Creates a task for an agent to respond.

    Args:
        message: The message to send to the fleet agents.
    """
    try:
        return _http_post("/api/comms/chat", {"message": message})
    except Exception as e:
        _log.warning("fleet_chat failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_comms(channel: str = "fleet", limit: int = 20) -> str:
    """Read recent messages and notes from a fleet communication channel.

    Args:
        channel: Channel to read — 'chat', 'fleet', 'agent', 'sup', or 'pool'.
        limit: Number of recent items to return. Default 20.
    """
    try:
        if channel not in ("chat", "fleet", "agent", "sup", "pool"):
            return f"Invalid channel '{channel}'. Use: chat, fleet, agent, sup, pool"
        data = _http_get(f"/api/comms/history/{channel}?limit={limit}")
        items = []
        for msg in data.get("messages", []):
            body = msg.get("body_json", "")
            try:
                body = json.loads(body).get("text", body)
            except Exception:
                pass
            items.append(f"[msg] {msg.get('from_agent', '?')} → {msg.get('to_agent', '*')}: {body} ({msg.get('created_at', '')})")
        for note in data.get("notes", []):
            body = note.get("body_json", "")
            try:
                body = json.loads(body).get("text", body)
            except Exception:
                pass
            items.append(f"[note] {note.get('from_agent', '?')}: {body} ({note.get('created_at', '')})")
        return "\n".join(items) if items else f"No activity in '{channel}' channel."
    except Exception as e:
        _log.warning("fleet_comms failed", exc_info=True)
        return f"Error: {e}"


@mcp.tool()
def fleet_send(message: str, channel: str = "fleet") -> dict:
    """Send a note to a fleet communication channel.

    Args:
        message: The message text to send.
        channel: Target channel — 'fleet', 'agent', 'sup', or 'pool'. Default 'fleet'.
    """
    try:
        return _http_post("/api/comms/send", {"channel": channel, "body": message})
    except Exception as e:
        _log.warning("fleet_send failed", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
def fleet_ignition(action: str) -> str:
    """Start or stop the fleet.

    Args:
        action: 'start' to resume queue and start workers, 'stop' to pause and stop.
    """
    try:
        if action == "start":
            r1 = _http_post("/api/queue/resume")
            r2 = _http_post("/api/fleet/start")
            return "Fleet started — queue resumed, workers launching."
        elif action == "stop":
            r1 = _http_post("/api/queue/pause")
            r2 = _http_post("/api/fleet/stop")
            return "Fleet stopped — queue paused, workers shutting down."
        return f"Invalid action '{action}'. Use 'start' or 'stop'."
    except Exception as e:
        _log.warning("fleet_ignition failed", exc_info=True)
        return f"Error: {e}"


# ── Resources ──────────────────────────────────────────────────────


@mcp.resource("biged://hitl/pending")
def pending_hitl() -> str:
    """Tasks currently waiting for human approval or input.

    Includes tasks from local fleet and all federation peers (when enabled).
    Each task has a 'source' field: "local" or "peer:http://..." to route responses.
    """
    try:
        import db

        # Local tasks
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
                "source": "local",
            })

        # Remote tasks (federation)
        try:
            from federation_hitl import get_federation_hitl_config, _fetch_peer_hitl
            cfg = get_federation_hitl_config()
            if cfg["enabled"] and cfg["aggregate_remote"]:
                for peer_url in cfg["peers"]:
                    try:
                        remote = _fetch_peer_hitl(peer_url, timeout=cfg["peer_timeout_secs"])
                        for rt in remote:
                            tasks.append({
                                "task_id": rt.get("id"),
                                "skill": rt.get("type", ""),
                                "agent": rt.get("assigned_to", rt.get("agent", "")),
                                "question": rt.get("question", "Approval required"),
                                "context": rt.get("context", ""),
                                "created_at": rt.get("created_at", ""),
                                "source": f"peer:{peer_url}",
                            })
                    except Exception:
                        _log.debug("Skipping unreachable peer %s for HITL resource", peer_url)
        except ImportError:
            _log.debug("federation_hitl not available — showing local tasks only")

        return json.dumps(tasks, indent=2)
    except Exception as e:
        _log.warning("pending_hitl resource failed", exc_info=True)
        return json.dumps({"error": str(e)})


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
