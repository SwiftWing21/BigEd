"""Analyze Factorio game state and submit plans or directives to the brain."""

SKILL_NAME = "factorio_analyze"
DESCRIPTION = "Analyze Factorio game state and submit plans or directives to the brain"
REQUIRES_NETWORK = True
COMPLEXITY = "complex"
TAGS = ["factorio", "sandbox", "analysis"]


def run(payload, config):
    import json
    import urllib.request
    import logging

    log = logging.getLogger(SKILL_NAME)
    bridge_port = config.get("factorio", {}).get("bridge_port", 27016)
    base = f"http://localhost:{bridge_port}"

    # 1. Fetch game state
    try:
        resp = urllib.request.urlopen(f"{base}/api/state", timeout=15)
        state = json.loads(resp.read())
    except Exception:
        log.warning("Failed to fetch bridge state", exc_info=True)
        return {"status": "error", "error": "Bridge unreachable"}

    # 2. Fetch brain's current plan + objectives
    try:
        resp = urllib.request.urlopen(f"{base}/api/plan/queue", timeout=15)
        plan_info = json.loads(resp.read())
    except Exception:
        plan_info = {"current": None, "queued": []}

    # 3. Call provider for analysis via _models.call_complex
    try:
        from skills._models import call_complex
        system = (
            "You are a Factorio strategy advisor. Analyze the game state and "
            "suggest the best next actions. Return JSON with 'confidence' (0-1), "
            "'actions' (list of action dicts), and 'rationale' (string). "
            "If confidence < 0.7, return 'directive' (string) instead of actions."
        )
        user = _build_analysis_prompt(state, plan_info)
        result = call_complex(
            system, user, config,
            max_tokens=1024,
            skill_name=SKILL_NAME,
            agent_name=payload.get("worker_name"),
            purpose="task",
        )
        # Strip markdown fences if present
        text = result.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()
        analysis = json.loads(text)
    except Exception:
        log.warning("LLM analysis failed", exc_info=True)
        return {"status": "error", "error": "LLM call failed"}

    # 4. Confidence gate
    confidence = float(analysis.get("confidence", 0.5))
    if confidence >= 0.7 and "actions" in analysis:
        # High confidence — submit full plan
        plan_data = {
            "actions": analysis["actions"],
            "priority": 75,
            "source": payload.get("worker_name", "analyzer"),
            "source_type": "worker",
            "rationale": analysis.get("rationale", "Auto-analysis"),
            "confidence": confidence,
        }
        try:
            req = urllib.request.Request(
                f"{base}/api/plan/submit",
                data=json.dumps(plan_data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            submit_result = json.loads(resp.read())
        except Exception:
            log.warning("Failed to submit plan", exc_info=True)
            submit_result = {"status": "error"}
        return {"status": "ok", "action": "plan_submitted", "result": submit_result}
    else:
        # Lower confidence — submit directive
        directive_text = analysis.get("directive", analysis.get("rationale", "No guidance"))
        directive_data = {
            "text": directive_text,
            "priority": 75,
            "source": payload.get("worker_name", "analyzer"),
            "sticky": False,
            "max_plans": 3,
        }
        try:
            req = urllib.request.Request(
                f"{base}/api/directive/submit",
                data=json.dumps(directive_data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            submit_result = json.loads(resp.read())
        except Exception:
            log.warning("Failed to submit directive", exc_info=True)
            submit_result = {"status": "error"}
        return {"status": "ok", "action": "directive_submitted", "result": submit_result}


def _build_analysis_prompt(state, plan_info):
    """Build analysis prompt from game state and plan queue info."""
    import json
    lines = ["Current Factorio game state:"]
    lines.append(json.dumps(state, indent=2, default=str)[:3000])
    if plan_info.get("current"):
        lines.append(f"\nCurrent plan: step {plan_info['current'].get('index', 0)}"
                     f"/{plan_info['current'].get('total', 0)}")
    if plan_info.get("queued"):
        lines.append(f"\n{len(plan_info['queued'])} plans queued")
    lines.append("\nAnalyze the state and recommend next actions.")
    return "\n".join(lines)
