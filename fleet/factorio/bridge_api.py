# fleet/factorio/bridge_api.py
"""Localhost-only Flask API for fleet skills to access the bridge."""
import logging
import queue
import threading
from flask import Flask, jsonify, request

log = logging.getLogger("biged.factorio.api")

_lock = threading.Lock()
_world_model = None
_command_queue: "queue.Queue | None" = None
_result_store: dict = {}  # command_id -> result (Python 3.7+ insertion order)
_bridge_status: dict = {"running": False, "tick": 0, "cadence": "adaptive"}
_brain = None


def create_api(world_model, command_queue, brain=None) -> Flask:
    global _world_model, _command_queue, _brain
    _world_model = world_model
    _command_queue = command_queue
    _brain = brain

    app = Flask("factorio_bridge_api")

    @app.route("/api/status")
    def api_status():
        return jsonify(_bridge_status)

    @app.route("/api/state")
    def api_state():
        if _world_model is None:
            return jsonify({"error": "WorldModel not initialized"}), 503
        return jsonify(_world_model.get_snapshot())

    @app.route("/api/command", methods=["POST"])
    def api_command():
        if _command_queue is None:
            return jsonify({"error": "CommandQueue not available"}), 503
        data = request.get_json(silent=True)
        if not data or "actions" not in data:
            return jsonify({"error": "Missing 'actions' in request body"}), 400
        cmd_id = f"cmd_{_bridge_status.get('tick', 0)}_{id(data)}"
        _command_queue.put({"id": cmd_id, "actions": data["actions"]})
        return jsonify({"queued": True, "command_id": cmd_id})

    @app.route("/api/result/<cmd_id>")
    def api_result(cmd_id):
        with _lock:
            result = _result_store.get(cmd_id)
        if result is None:
            return jsonify({"pending": True})
        return jsonify(result)

    @app.route("/api/plan")
    def api_plan():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        plan_status = _brain.get_plan_status()
        progress = _brain.curriculum.get_progress()
        return jsonify({**plan_status, "progress": progress})

    return app


def update_status(running: bool, tick: int, cadence: str) -> None:
    with _lock:
        _bridge_status["running"] = running
        _bridge_status["tick"] = tick
        _bridge_status["cadence"] = cadence


def store_result(cmd_id: str, result: dict) -> None:
    with _lock:
        _result_store[cmd_id] = result
        while len(_result_store) > 100:
            oldest = next(iter(_result_store))
            del _result_store[oldest]
