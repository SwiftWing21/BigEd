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
_training_status: dict = {}  # updated by bridge.py after each ML tick
_brain = None
_rcon_client = None  # set by bridge.py for status reporting
_spatial_memory = None  # set by bridge.py for spatial map API
_reward_history: list[dict] = []  # rolling window of recent rewards


def create_api(world_model, command_queue, brain=None, rcon=None, spatial_memory=None) -> Flask:
    global _world_model, _command_queue, _brain, _rcon_client, _spatial_memory
    _world_model = world_model
    _command_queue = command_queue
    _brain = brain
    _rcon_client = rcon
    _spatial_memory = spatial_memory

    app = Flask("factorio_bridge_api")

    @app.route("/api/status")
    def api_status():
        import time as _t
        status = {**_bridge_status, "paused": _brain.is_paused if _brain else False}
        # Mark as not running if no tick update for 30 seconds (stuck loop)
        last_ts = _bridge_status.get("last_tick_ts", 0)
        if status["running"] and (_t.time() - last_ts) > 30:
            status["running"] = False
            status["stale"] = True
        # Component health for dashboard status lights
        status["components"] = {
            "bridge": True,  # if we're responding, bridge API is up
            "rcon": bool(_rcon_client and _rcon_client._connected),
            "headless": bool(_rcon_client and _rcon_client._connected and status.get("running")),
        }
        return jsonify(status)

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

    @app.route("/api/pause", methods=["POST"])
    def api_pause():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        _brain.pause()
        return jsonify({"paused": True})

    @app.route("/api/resume", methods=["POST"])
    def api_resume():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        _brain.resume()
        return jsonify({"paused": False})

    @app.route("/api/directive", methods=["POST"])
    def api_add_directive():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        if not text:
            return jsonify({"error": "Missing 'text'"}), 400
        sticky = data.get("sticky", False)
        plans = data.get("plans", 1)
        did = _brain.add_directive(text, sticky=sticky, plans=plans)
        return jsonify({"id": did})

    @app.route("/api/directive/<directive_id>", methods=["DELETE"])
    def api_remove_directive(directive_id):
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        removed = _brain.remove_directive(directive_id)
        return jsonify({"removed": removed})

    @app.route("/api/directives", methods=["GET"])
    def api_list_directives():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        return jsonify(_brain.get_directives())

    @app.route("/api/directives", methods=["DELETE"])
    def api_clear_directives():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        count = _brain.clear_directives()
        return jsonify({"cleared": count})

    @app.route("/api/presets", methods=["GET"])
    def api_list_presets():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        return jsonify(_brain.get_presets())

    @app.route("/api/preset", methods=["POST"])
    def api_add_preset():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        data = request.get_json(silent=True) or {}
        label = data.get("label", "")
        text = data.get("text", "")
        if not label or not text:
            return jsonify({"error": "Missing 'label' or 'text'"}), 400
        sticky = data.get("sticky", False)
        plans = data.get("plans", 1)
        pid = _brain.add_preset(label, text, sticky=sticky, plans=plans)
        return jsonify({"id": pid})

    @app.route("/api/preset/<preset_id>", methods=["DELETE"])
    def api_remove_preset(preset_id):
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        removed = _brain.remove_preset(preset_id)
        return jsonify({"removed": removed})

    @app.route("/api/plan/submit", methods=["POST"])
    def plan_submit():
        data = request.get_json(force=True)
        required = ["actions", "priority", "source", "source_type", "rationale", "confidence"]
        missing = [f for f in required if f not in data]
        if missing:
            return jsonify({"error": f"Missing fields: {missing}"}), 400
        try:
            from factorio.agent_brain import PlanSubmission
            ps = PlanSubmission(
                actions=data["actions"],
                priority=int(data["priority"]),
                source=str(data["source"]),
                source_type=str(data["source_type"]),
                rationale=str(data["rationale"]),
                confidence=float(data["confidence"]),
            )
        except (ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400
        result = _brain.submit_plan(ps)
        status_code = 200 if result["status"] != "rejected" else 409
        return jsonify(result), status_code

    @app.route("/api/plan/queue", methods=["GET"])
    def plan_queue():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        with _brain._lock:
            current = None
            if _brain._plan:
                current = {
                    "actions": _brain._plan,
                    "index": _brain._plan_index,
                    "total": len(_brain._plan),
                    "priority": _brain._current_priority,
                }
            queued = []
            for neg_pri, seq, ps in sorted(_brain._plan_queue):
                queued.append({
                    "plan_id": ps.plan_id,
                    "actions": ps.actions,
                    "priority": ps.priority,
                    "source": ps.source,
                    "source_type": ps.source_type,
                    "rationale": ps.rationale,
                    "confidence": ps.confidence,
                })
            shelved = None
            if _brain._shelved_plan:
                sp = _brain._shelved_plan
                shelved = {"plan_id": sp.plan_id, "actions": sp.actions,
                           "priority": sp.priority}
        return jsonify({"current": current, "queued": queued, "shelved": shelved})

    @app.route("/api/plan/history", methods=["GET"])
    def plan_history():
        if _brain is None:
            return jsonify({"error": "AgentBrain not initialized"}), 503
        with _brain._lock:
            history = list(_brain._plan_history)
        return jsonify({"history": history})

    @app.route("/api/directive/submit", methods=["POST"])
    def directive_submit():
        data = request.get_json(force=True)
        text = data.get("text", "")
        if not text:
            return jsonify({"error": "Missing 'text' field"}), 400
        d_id = _brain.add_directive(
            text=text,
            sticky=data.get("sticky", False),
            plans=data.get("max_plans", 3),
            priority=data.get("priority", 50),
            source=data.get("source", "worker"),
        )
        return jsonify({"id": d_id, "status": "accepted"})

    @app.route("/api/training/status")
    def api_training_status():
        """ML training metrics — polled by launcher, factorio_observe, factorio_train."""
        with _lock:
            if _training_status:
                return jsonify(_training_status)
        # Fallback: assemble from brain curriculum (LLM mode)
        if _brain is not None:
            progress = _brain.curriculum.get_progress()
            return jsonify({
                "mode": "llm",
                "episode": 0,
                "step": 0,
                "phase": progress.get("phase", 1),
                "phase_name": progress.get("phase_name", ""),
                "lessons_completed": progress.get("completed", 0),
                "lessons_total": progress.get("total_lessons", 0),
                "last_reward": None,
                "policy_loss": None,
                "value_loss": None,
                "entropy": None,
            })
        return jsonify({"error": "Training not active"}), 503

    @app.route("/api/training/diagnose")
    def api_training_diagnose():
        """Lightweight diagnostics for factorio_analyze skill."""
        with _lock:
            status = dict(_training_status) if _training_status else {}
        status["bridge_running"] = _bridge_status.get("running", False)
        status["tick"] = _bridge_status.get("tick", 0)
        return jsonify(status)

    @app.route("/api/shutdown", methods=["POST"])
    def api_shutdown():
        """Gracefully stop the bridge process."""
        import os
        log.info("Shutdown requested via API")
        update_status(False, _bridge_status.get("tick", 0), "shutdown")
        # Schedule exit after response is sent
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return jsonify({"shutting_down": True})

    @app.route("/api/focus", methods=["GET"])
    def focus_state():
        import os, json
        focus_file = os.path.join(os.path.dirname(__file__), '..', '.factorio_focus.json')
        try:
            with open(focus_file) as f:
                return jsonify(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return jsonify({"on": False, "workers": []})

    @app.route("/api/spatial-map")
    def api_spatial_map():
        """Spatial memory data for dashboard map visualization."""
        if _spatial_memory is None:
            return jsonify({"error": "Spatial memory not active (ML mode only)"}), 503
        # Player position from latest bridge status
        px = _bridge_status.get("player_x", 0.0)
        py = _bridge_status.get("player_y", 0.0)

        resources = []
        for entry in _spatial_memory.resources.values():
            resources.append({
                "name": entry.name, "x": entry.x, "y": entry.y,
                "amount": entry.amount,
            })

        entities = []
        for entry in _spatial_memory.entities.values():
            entities.append({
                "name": entry.name, "x": entry.x, "y": entry.y,
            })

        return jsonify({
            "player": {"x": px, "y": py},
            "resources": resources,
            "entities": entities,
            "summary": {
                "resources": _spatial_memory.resource_summary(),
                "entities": _spatial_memory.entity_summary(),
            },
        })

    @app.route("/api/reward-history")
    def api_reward_history():
        """Rolling window of recent per-step rewards for chart."""
        with _lock:
            return jsonify({"rewards": list(_reward_history)})

    return app


def update_status(running: bool, tick: int, cadence: str) -> None:
    import time as _t
    with _lock:
        _bridge_status["running"] = running
        _bridge_status["tick"] = tick
        _bridge_status["cadence"] = cadence
        _bridge_status["last_tick_ts"] = _t.time()


def store_result(cmd_id: str, result: dict) -> None:
    with _lock:
        _result_store[cmd_id] = result
        while len(_result_store) > 100:
            oldest = next(iter(_result_store))
            del _result_store[oldest]


def update_training_status(status: dict) -> None:
    """Called by bridge.py after each ML tick to push training metrics."""
    with _lock:
        _training_status.update(status)
        # Track reward history for chart (rolling 500-step window)
        if "last_reward" in status and status["last_reward"] is not None:
            _reward_history.append({
                "step": status.get("step", 0),
                "reward": status["last_reward"],
                "episode": status.get("episode", 0),
            })
            if len(_reward_history) > 500:
                del _reward_history[:len(_reward_history) - 500]


def update_player_position(x: float, y: float) -> None:
    """Called by bridge.py each tick to track player position for spatial map."""
    with _lock:
        _bridge_status["player_x"] = x
        _bridge_status["player_y"] = y
