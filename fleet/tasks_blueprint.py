"""Task and queue management endpoints.

Extracted from dashboard.py (Phase 4 of dashboard decomposition).
All /api/tasks/* and /api/queue/* routes live here, plus task dispatch.
"""
import json
import logging
import re
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    FLEET_DIR, _require_role, _get_request_role,
    _check_rate_limit, _broadcast_sse, _safe_error,
    get_conn, query,
)

log = logging.getLogger("dashboard.tasks")

tasks_bp = Blueprint("tasks", __name__)

# Queue pause state -- flag file checked by workers across processes
_QUEUE_PAUSE_FILE = Path(__file__).resolve().parent / ".queue_paused"


def _is_queue_paused():
    return _QUEUE_PAUSE_FILE.exists()


# ── HITL Response ──────────────────────────────────────────────────────────


@tasks_bp.route("/api/tasks/waiting-human")
def api_waiting_human():
    """List all tasks awaiting human input.

    Query params:
        include_remote=true -- include HITL tasks from federation peers
                              (default: false, local only for backward compat)
    """
    try:
        include_remote = request.args.get("include_remote", "false").lower() == "true"

        if include_remote:
            from federation_hitl import get_all_hitl_tasks
            all_tasks = get_all_hitl_tasks()
            result = []
            for t in all_tasks:
                result.append({
                    "id": t.get("id"),
                    "type": t.get("type", ""),
                    "question": t.get("question", ""),
                    "agent": t.get("assigned_to", ""),
                    "created_at": t.get("created_at", ""),
                    "source_fleet": t.get("source_fleet", "local"),
                    "source": t.get("source", "local"),
                })
            return jsonify(result)

        # Default: local only (backward compatible)
        import db
        tasks = db.get_waiting_human_tasks()
        result = []
        for t in tasks:
            result.append({
                "id": t["id"],
                "type": t.get("type", ""),
                "question": t.get("question", ""),
                "agent": t.get("assigned_to", ""),
                "created_at": t.get("created_at", ""),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/tasks/<int:task_id>/respond", methods=["POST"])
def api_task_respond(task_id):
    """Submit human response to a WAITING_HUMAN task."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        response_text = data.get("response", "").strip()
        if not response_text:
            return jsonify({"error": "response is required"}), 400

        import db
        db.respond_to_agent(task_id, response_text)

        _broadcast_sse({
            "type": "hitl_response",
            "data": {"task_id": task_id, "responded": True},
        })
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/tasks/<int:task_id>/question")
def api_task_question(task_id):
    """Get the question asked by an agent for a specific task."""
    try:
        row = query(
            "SELECT id, type, assigned_to, status, payload_json, created_at "
            "FROM tasks WHERE id=?", (task_id,)
        )
        if not row:
            return jsonify({"error": "Task not found"}), 404
        task = row[0]
        # Extract question from the agent's message to operator
        question = ""
        try:
            msgs = query(
                "SELECT body_json FROM messages "
                "WHERE from_agent=? AND to_agent='operator' "
                "AND body_json LIKE '%human_input_request%' "
                "AND body_json LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (task.get("assigned_to") or "", f'%"task_id": {task_id}%'),
            )
            if msgs:
                body = json.loads(msgs[0]["body_json"])
                question = body.get("question", "")
        except Exception:
            pass
        return jsonify({
            "task_id": task_id,
            "type": task.get("type", ""),
            "agent": task.get("assigned_to", ""),
            "status": task.get("status", ""),
            "question": question,
            "created_at": task.get("created_at", ""),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Queue Listing ──────────────────────────────────────────────────────────


@tasks_bp.route("/api/tasks/recent")
def api_tasks_recent():
    """Recent tasks -- all statuses, newest first. Used by Pipeline -> Swimlane."""
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        tasks = query(
            "SELECT id, type, status, priority, assigned_to, created_at "
            "FROM tasks ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return jsonify({"tasks": tasks})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/tasks/queue")
def api_task_queue():
    """List pending and running tasks with priority and order."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        total_row = query(
            "SELECT COUNT(*) as n FROM tasks WHERE status IN ('PENDING','RUNNING')"
        )
        total = total_row[0]["n"] if total_row else 0

        tasks = query(
            "SELECT id, type, status, priority, assigned_to, created_at, payload_json "
            "FROM tasks WHERE status IN ('PENDING','RUNNING') "
            "ORDER BY priority DESC, created_at ASC "
            "LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        return jsonify({
            "tasks": tasks,
            "page": page,
            "per_page": per_page,
            "total": total,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Task Priority / Cancel / Requeue ───────────────────────────────────────


@tasks_bp.route("/api/tasks/<int:task_id>/priority", methods=["PUT"])
def api_task_priority(task_id):
    """Change task priority (1-10). Only PENDING tasks can be re-prioritised."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        new_priority = data.get("priority", 5)
        try:
            new_priority = int(new_priority)
        except (TypeError, ValueError):
            return jsonify({"error": "priority must be an integer"}), 400
        if not 1 <= new_priority <= 10:
            return jsonify({"error": "priority must be between 1 and 10"}), 400

        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "PENDING":
            return jsonify({"error": "Only PENDING tasks can be re-prioritised"}), 409

        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET priority=? WHERE id=? AND status='PENDING'",
                (new_priority, task_id),
            )

        _broadcast_sse({
            "type": "task_priority",
            "data": {"task_id": task_id, "priority": new_priority},
        })
        return jsonify({"ok": True, "task_id": task_id, "priority": new_priority})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_task_cancel(task_id):
    """Cancel a pending task."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "PENDING":
            return jsonify({"error": "Only PENDING tasks can be cancelled"}), 409

        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='FAILED', result_json=? WHERE id=? AND status='PENDING'",
                (json.dumps({"error": "Cancelled by operator"}), task_id),
            )

        _broadcast_sse({
            "type": "task_cancelled",
            "data": {"task_id": task_id},
        })
        return jsonify({"ok": True, "task_id": task_id, "status": "FAILED"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/tasks/<int:task_id>/requeue", methods=["POST"])
def api_task_requeue(task_id):
    """Requeue a failed task -- resets it to PENDING."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "FAILED":
            return jsonify({"error": "Only FAILED tasks can be requeued"}), 409

        import db
        db.requeue_task(task_id)

        _broadcast_sse({
            "type": "task_requeued",
            "data": {"task_id": task_id},
        })
        return jsonify({"ok": True, "task_id": task_id, "status": "PENDING"})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Task Dispatch ──────────────────────────────────────────────────────────


@tasks_bp.route("/api/tasks/dispatch", methods=["POST"])
def api_task_dispatch():
    """Submit a task to the fleet queue.

    Body JSON:
        skill (str):       required -- skill name (e.g. "summarize", "code_review")
        payload (dict):    optional -- JSON payload for the skill
        priority (int):    optional -- 1-10, default 5
        assigned_to (str): optional -- target agent name
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        if not _check_rate_limit("task_dispatch", max_per_min=30):
            return jsonify({"error": "Rate limited"}), 429

        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        skill = (data.get("skill") or "").strip()
        if not skill:
            return jsonify({"error": "skill is required"}), 400

        # Validate skill name format
        if not re.match(r'^[a-zA-Z0-9_]{1,64}$', skill):
            return jsonify({"error": "Invalid skill name format"}), 400

        payload = data.get("payload", {})
        priority = data.get("priority", 5)
        assigned_to = (data.get("assigned_to") or "").strip() or None

        try:
            priority = max(1, min(10, int(priority)))
        except (TypeError, ValueError):
            priority = 5

        payload_json = json.dumps(payload) if isinstance(payload, dict) else str(payload)

        import db
        task_id = db.post_task(
            type_=skill,
            payload_json=payload_json,
            priority=priority,
            assigned_to=assigned_to,
        )

        _broadcast_sse({
            "type": "task_dispatched",
            "data": {"task_id": task_id, "skill": skill, "priority": priority},
        })

        return jsonify({
            "status": "ok",
            "task_id": task_id,
            "skill": skill,
            "priority": priority,
            "assigned_to": assigned_to,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


# ── Extended Queue Management ──────────────────────────────────────────────


@tasks_bp.route("/api/queue")
def api_queue():
    """Full pending/running queue with ordering and pause state."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 100))))
        offset = (page - 1) * per_page

        total_row = query(
            "SELECT COUNT(*) as n FROM tasks WHERE status IN ('PENDING','RUNNING','WAITING')"
        )
        total = total_row[0]["n"] if total_row else 0

        tasks = query(
            "SELECT id, type, status, priority, assigned_to, created_at, payload_json "
            "FROM tasks WHERE status IN ('PENDING','RUNNING','WAITING') "
            "ORDER BY "
            "  CASE status WHEN 'RUNNING' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END, "
            "  priority DESC, created_at ASC "
            "LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        return jsonify({
            "tasks": tasks,
            "page": page,
            "per_page": per_page,
            "total": total,
            "paused": _is_queue_paused(),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/queue/reorder", methods=["POST"])
def api_queue_reorder():
    """Reorder queue by setting priorities based on position.

    Body JSON:
        task_ids (list[int]): ordered list of task IDs -- first = highest priority
    """
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        data = request.get_json(silent=True) or {}
        task_ids = data.get("task_ids", [])
        if not task_ids or not isinstance(task_ids, list):
            return jsonify({"error": "task_ids must be a non-empty list of integers"}), 400

        # Validate all are integers
        try:
            task_ids = [int(tid) for tid in task_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "All task_ids must be integers"}), 400

        if len(task_ids) > 200:
            return jsonify({"error": "Maximum 200 tasks per reorder"}), 400

        # Assign decreasing priorities: first = 10, last = 1
        updated = []

        import db

        def _do():
            with db.get_conn() as conn:
                for i, tid in enumerate(task_ids):
                    prio = max(1, 10 - int(i * 9 / max(len(task_ids) - 1, 1)))
                    result = conn.execute(
                        "UPDATE tasks SET priority=? WHERE id=? AND status='PENDING'",
                        (prio, tid),
                    )
                    if result.rowcount > 0:
                        updated.append({"task_id": tid, "priority": prio})
        db._retry_write(_do)

        _broadcast_sse({
            "type": "queue_reordered",
            "data": {"updated_count": len(updated)},
        })

        return jsonify({
            "status": "ok",
            "updated": updated,
            "total_updated": len(updated),
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/queue/<int:task_id>", methods=["DELETE"])
def api_queue_remove(task_id):
    """Remove a task from the queue -- cancels a PENDING task."""
    deny = _require_role("operator")
    if deny:
        return deny
    try:
        rows = query("SELECT status FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return jsonify({"error": "Task not found"}), 404
        if rows[0]["status"] != "PENDING":
            return jsonify({"error": "Only PENDING tasks can be removed from queue"}), 409

        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='FAILED', result_json=? WHERE id=? AND status='PENDING'",
                (json.dumps({"error": "Removed from queue by operator"}), task_id),
            )

        _broadcast_sse({
            "type": "queue_removed",
            "data": {"task_id": task_id},
        })
        return jsonify({"status": "ok", "task_id": task_id, "removed": True})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@tasks_bp.route("/api/queue/pause", methods=["POST"])
def api_queue_pause():
    """Pause queue processing -- workers stop claiming new tasks."""
    _QUEUE_PAUSE_FILE.write_text("paused", encoding="utf-8")
    _broadcast_sse({"type": "queue_paused", "data": {"paused": True}})
    return jsonify({"status": "ok", "paused": True})


@tasks_bp.route("/api/queue/resume", methods=["POST"])
def api_queue_resume():
    """Resume queue processing after a pause."""
    try:
        _QUEUE_PAUSE_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    _broadcast_sse({"type": "queue_resumed", "data": {"paused": False}})
    return jsonify({"status": "ok", "paused": False})


@tasks_bp.route("/api/queue/status")
def api_queue_status():
    """Return queue processing state (paused/active)."""
    return jsonify({"paused": _is_queue_paused()})
