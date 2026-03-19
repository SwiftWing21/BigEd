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
                db.get_conn().execute(
                    "UPDATE tasks SET payload_json=? WHERE id=?",
                    (json.dumps(existing), task_id)
                )
                db.get_conn().commit()
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
