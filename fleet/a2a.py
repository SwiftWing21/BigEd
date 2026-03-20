"""
A2A (Agent-to-Agent) Protocol adapter for BigEd CC fleet.
Implements Google's A2A protocol for cross-framework agent interoperability.
Ref: https://google.github.io/A2A/

Supports:
- Agent Card discovery (/.well-known/agent.json)
- Task send/receive via A2A JSON-RPC
- Status reporting
"""
import json
from pathlib import Path
from flask import Blueprint, jsonify, request

a2a_bp = Blueprint('a2a', __name__)

FLEET_DIR = Path(__file__).parent
A2A_VERSION = "0.1"


def _get_agent_card() -> dict:
    """Generate A2A-compliant Agent Card for the fleet."""
    return {
        "name": "BigEd CC Fleet",
        "description": "Multi-agent AI worker fleet with 55 skills",
        "url": "http://localhost:5555",
        "version": A2A_VERSION,
        "protocol": "a2a",
        "capabilities": {
            "skills": _list_skills(),
            "streaming": True,
            "human_in_the_loop": True,
            "review_gate": True,
        },
        "authentication": {
            "type": "none",  # local deployment, no auth required
        },
        "endpoints": {
            "task_send": "/a2a/task/send",
            "task_status": "/a2a/task/status",
            "agent_card": "/.well-known/agent.json",
        }
    }


def _list_skills() -> list:
    """List available skills from fleet/skills/ directory."""
    skills_dir = FLEET_DIR / "skills"
    skills = []
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        skills.append(f.stem)
    return skills


@a2a_bp.route("/.well-known/agent.json")
def agent_card():
    """A2A Agent Card discovery endpoint."""
    return jsonify(_get_agent_card())


@a2a_bp.route("/a2a/task/send", methods=["POST"])
def task_send():
    """A2A task submission — receive a task from an external agent."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        skill = data.get("skill") or data.get("type")
        payload = data.get("payload", {})
        priority = data.get("priority", 5)
        callback_url = data.get("callback_url")  # optional webhook for completion

        if not skill:
            return jsonify({"error": "Missing 'skill' field"}), 400

        # Dispatch to fleet task queue
        import db
        task_id = db.post_task(skill, json.dumps(payload), priority=priority)

        result = {
            "task_id": task_id,
            "status": "accepted",
            "protocol": "a2a",
            "version": A2A_VERSION,
        }

        # Store callback URL in task payload for post-completion webhook
        if callback_url:
            try:
                existing = json.loads(db.get_task_result(task_id).get("payload_json", "{}"))
                existing["_a2a_callback"] = callback_url
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE tasks SET payload_json=? WHERE id=?",
                        (json.dumps(existing), task_id)
                    )
                    # commit happens automatically with context manager
            except Exception:
                pass

        return jsonify(result), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@a2a_bp.route("/a2a/task/status/<int:task_id>")
def task_status(task_id):
    """A2A task status query."""
    try:
        import db
        task = db.get_task_result(task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # Map internal status to A2A status
        status_map = {
            "PENDING": "queued",
            "RUNNING": "in_progress",
            "DONE": "completed",
            "FAILED": "failed",
            "REVIEW": "in_progress",
            "WAITING": "queued",
            "WAITING_HUMAN": "requires_input",
        }

        return jsonify({
            "task_id": task_id,
            "status": status_map.get(task["status"], task["status"]),
            "internal_status": task["status"],
            "result": task.get("result_json"),
            "error": task.get("error"),
            "protocol": "a2a",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@a2a_bp.route("/a2a/capabilities")
def capabilities():
    """A2A capabilities listing."""
    return jsonify({
        "protocols": ["a2a"],
        "version": A2A_VERSION,
        "skills": _list_skills(),
        "task_types": ["dispatch", "chain", "review"],
        "communication": {
            "channels": ["sup", "agent", "fleet", "pool"],
            "messaging": True,
            "notes": True,
        },
    })


# ── Federation (v0.30.00) ──────────────────────────────────────────────────

def _get_federation_config() -> dict:
    """Load federation config from fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("federation", {})
    except Exception:
        return {}


def _get_peer_list() -> list:
    """Get configured peer fleet URLs."""
    fed_cfg = _get_federation_config()
    if not fed_cfg.get("enabled", False):
        return []
    return fed_cfg.get("peers", [])


def _probe_peer(peer_url: str, timeout: int = 5) -> dict:
    """Health-check a peer fleet via its A2A agent card."""
    import urllib.request
    try:
        url = f"{peer_url.rstrip('/')}/.well-known/agent.json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return {
                "url": peer_url,
                "status": "online",
                "name": data.get("name", "unknown"),
                "skills": len(data.get("capabilities", {}).get("skills", [])),
                "version": data.get("version", "?"),
            }
    except Exception as e:
        return {
            "url": peer_url,
            "status": "offline",
            "error": str(e),
        }


def forward_to_peer(peer_url: str, skill: str, payload: dict, priority: int = 5) -> dict:
    """Forward a task to a peer fleet via A2A task/send."""
    import urllib.request
    try:
        url = f"{peer_url.rstrip('/')}/a2a/task/send"
        body = json.dumps({
            "skill": skill,
            "payload": payload,
            "priority": priority,
            "callback_url": None,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        fed_cfg = _get_federation_config()
        timeout = fed_cfg.get("peer_timeout_secs", 5)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            result["forwarded_to"] = peer_url
            return result
    except Exception as e:
        return {"error": str(e), "peer": peer_url}


@a2a_bp.route("/a2a/federation/peers")
def federation_peers():
    """List configured peers and their online status."""
    fed_cfg = _get_federation_config()
    if not fed_cfg.get("enabled", False):
        return jsonify({"enabled": False, "peers": [], "message": "Federation disabled in fleet.toml"}), 200

    peers = _get_peer_list()
    timeout = fed_cfg.get("peer_timeout_secs", 5)
    results = [_probe_peer(p, timeout=timeout) for p in peers]

    return jsonify({
        "enabled": True,
        "overflow_threshold": fed_cfg.get("overflow_threshold", 0.85),
        "peers": results,
        "total": len(results),
        "online": sum(1 for r in results if r["status"] == "online"),
    })


@a2a_bp.route("/a2a/federation/status")
def federation_status():
    """Federation overview — local capacity + peer availability."""
    fed_cfg = _get_federation_config()

    # Get local queue stats
    local_stats = {"pending": 0, "running": 0, "capacity": 1.0}
    try:
        import db
        with db.get_conn() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()[0]
            running = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='RUNNING'").fetchone()[0]
            from config import load_config
            cfg = load_config()
            max_w = cfg.get("fleet", {}).get("max_workers", 10)
            utilization = running / max(max_w, 1)
            local_stats = {"pending": pending, "running": running, "utilization": round(utilization, 2)}
    except Exception:
        pass

    threshold = fed_cfg.get("overflow_threshold", 0.85)
    should_forward = local_stats.get("utilization", 0) > threshold

    return jsonify({
        "enabled": fed_cfg.get("enabled", False),
        "local": local_stats,
        "overflow_threshold": threshold,
        "should_forward": should_forward,
        "peers_configured": len(fed_cfg.get("peers", [])),
    })
