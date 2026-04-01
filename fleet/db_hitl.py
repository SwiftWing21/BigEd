"""Human-in-the-Loop task management — split from db.py (TD-04)."""
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def request_human_input(task_id, agent_name, question):
    """Agent pauses task and requests operator input. Sets status to WAITING_HUMAN.

    Also notifies federation peers (if enabled) so operators on any
    connected dashboard see the HITL task immediately.
    """
    from db import get_conn, _retry_write, utc_to_local

    def _do():
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='WAITING_HUMAN' WHERE id=?", (task_id,))
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json, channel)
                VALUES (?, 'operator', ?, 'fleet')
            """, (agent_name, json.dumps({
                "type": "human_input_request",
                "task_id": task_id,
                "question": question,
            })))
    _retry_write(_do)

    # Notify federation peers about the new HITL task (fire-and-forget)
    try:
        from federation_hitl import forward_hitl_notification, get_federation_hitl_config
        cfg = get_federation_hitl_config()
        if cfg["enabled"] and cfg["forward_notifications"] and cfg["peers"]:
            import threading
            task_info = {
                "task_id": task_id,
                "agent": agent_name,
                "question": question,
            }
            threading.Thread(
                target=forward_hitl_notification,
                args=(cfg["peers"], task_info),
                daemon=True,
            ).start()
    except Exception:
        pass

    # Broadcast SSE so dashboard updates HITL badge immediately
    try:
        from dashboard_utils import _broadcast_sse
        _broadcast_sse({"type": "hitl_new", "task_id": task_id})
    except Exception:
        pass


def respond_to_agent(task_id, response):
    """Operator responds to agent question. Resumes task to RUNNING."""
    from db import get_conn, _retry_write

    def _do():
        with get_conn() as conn:
            row = conn.execute(
                "SELECT assigned_to, payload_json FROM tasks WHERE id=?",
                (task_id,)).fetchone()
            if not row:
                return
            agent = row["assigned_to"]
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            payload["_human_response"] = response
            conn.execute("""
                UPDATE tasks SET status='PENDING', payload_json=?
                WHERE id=? AND status='WAITING_HUMAN'
            """, (json.dumps(payload), task_id))
            if agent:
                conn.execute("""
                    INSERT INTO messages (from_agent, to_agent, body_json, channel)
                    VALUES ('operator', ?, ?, 'fleet')
                """, (agent, json.dumps({
                    "type": "human_response",
                    "task_id": task_id,
                    "response": response,
                })))
    _retry_write(_do)


def get_waiting_human_tasks():
    """Get all tasks awaiting human input, with the agent's question."""
    from db import get_conn

    with get_conn() as conn:
        tasks = conn.execute("""
            SELECT t.id, t.type, t.assigned_to, t.created_at, t.payload_json
            FROM tasks t
            WHERE t.status = 'WAITING_HUMAN'
            ORDER BY t.created_at ASC
        """).fetchall()
        result = []
        for t in tasks:
            task_dict = dict(t)
            msg = conn.execute("""
                SELECT body_json FROM messages
                WHERE from_agent = ? AND to_agent = 'operator'
                AND body_json LIKE '%human_input_request%'
                AND body_json LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (t["assigned_to"] or "", f'%"task_id": {t["id"]}%')).fetchone()
            if msg:
                try:
                    body = json.loads(msg["body_json"])
                    task_dict["question"] = body.get("question", "")
                except Exception:
                    task_dict["question"] = ""
            else:
                task_dict["question"] = ""
            result.append(task_dict)
        return result


def get_waiting_human_details():
    """Return detailed HITL requests with agent info, question, task type, and age.

    Richer version of get_waiting_human_tasks() — includes age_minutes and
    waiting_since for the launcher UI's HITL panel.
    """
    from db import get_conn, utc_to_local

    with get_conn() as conn:
        tasks = conn.execute("""
            SELECT t.id, t.type, t.assigned_to, t.created_at, t.payload_json
            FROM tasks t
            WHERE t.status = 'WAITING_HUMAN'
            ORDER BY t.created_at ASC
        """).fetchall()
        result = []
        now = datetime.now(timezone.utc)
        for t in tasks:
            question = ""
            waiting_since = t["created_at"] or ""
            msg = conn.execute("""
                SELECT body_json, created_at FROM messages
                WHERE from_agent = ? AND to_agent = 'operator'
                AND body_json LIKE '%human_input_request%'
                AND body_json LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (t["assigned_to"] or "", f'%"task_id": {t["id"]}%')).fetchone()
            if msg:
                try:
                    body = json.loads(msg["body_json"])
                    question = body.get("question", "")
                except Exception:
                    pass
                if msg["created_at"]:
                    waiting_since = msg["created_at"]

            age_minutes = 0
            if waiting_since:
                try:
                    dt = datetime.fromisoformat(waiting_since).replace(tzinfo=timezone.utc)
                    age_minutes = int((now - dt).total_seconds() / 60)
                except Exception:
                    pass

            result.append({
                "task_id": t["id"],
                "agent": t["assigned_to"] or "unknown",
                "question": question,
                "task_type": t["type"] or "",
                "waiting_since": utc_to_local(waiting_since),
                "age_minutes": age_minutes,
            })
        return result
