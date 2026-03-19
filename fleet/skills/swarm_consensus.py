"""0.10.00: Swarm Consensus — multi-agent debate before complex task execution."""
import json
import time
from datetime import datetime
from pathlib import Path

SKILL_NAME = "swarm_consensus"
DESCRIPTION = "Force multiple agents to debate and reach consensus before executing complex tasks"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent

def run(payload: dict, config: dict) -> str:
    """Orchestrate a multi-agent debate on a topic before dispatching execution."""
    topic = payload.get("topic", "")
    participants = payload.get("participants", ["coder_1", "security", "researcher"])
    max_rounds = payload.get("max_rounds", 3)
    execution_skill = payload.get("execution_skill")  # skill to dispatch after consensus
    execution_payload = payload.get("execution_payload", {})

    if not topic:
        return json.dumps({"error": "topic required"})

    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    # Phase 1: Post discussion topic to agent channel
    discussion_id = f"consensus_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    for agent in participants:
        db.post_message(
            "planner", agent,
            json.dumps({
                "type": "consensus_request",
                "discussion_id": discussion_id,
                "topic": topic,
                "participants": participants,
                "instruction": f"Share your perspective on: {topic}. Consider risks, benefits, and implementation approach. Be specific and concise.",
            }),
            channel="agent"
        )

    # Phase 2: Collect responses (poll for up to 60s)
    responses = {}
    deadline = time.time() + 60
    while time.time() < deadline and len(responses) < len(participants):
        for agent in participants:
            if agent in responses:
                continue
            msgs = db.get_messages(agent, unread_only=True, limit=5, channels=["agent"])
            for m in msgs:
                try:
                    body = json.loads(m.get("body_json", "{}"))
                    if body.get("discussion_id") == discussion_id:
                        responses[agent] = body.get("response", body.get("message", ""))
                except Exception:
                    continue
        if len(responses) < len(participants):
            time.sleep(2)

    # Phase 3: Synthesize consensus
    from skills._models import call_complex

    debate_text = "\n\n".join(
        f"**{agent}:** {resp}" for agent, resp in responses.items()
    )

    system = "You are a project manager synthesizing team input into a consensus decision. Output JSON: {\"consensus\": true/false, \"decision\": \"...\", \"key_points\": [...], \"risks\": [...], \"recommendation\": \"proceed/revise/abort\"}"
    user = f"Topic: {topic}\n\nTeam perspectives:\n{debate_text}\n\nSynthesize a consensus decision."

    try:
        synthesis = call_complex(system, user, config, max_tokens=512, skill_name="swarm_consensus")
    except Exception as e:
        synthesis = json.dumps({"consensus": False, "decision": f"Synthesis failed: {e}"})

    # Phase 4: Post consensus note
    db.post_note("agent", "planner", json.dumps({
        "type": "consensus_result",
        "discussion_id": discussion_id,
        "topic": topic,
        "participants": participants,
        "responses_received": len(responses),
        "synthesis": synthesis,
    }))

    # Phase 5: Dispatch execution task if consensus reached and skill specified
    task_id = None
    if execution_skill:
        try:
            parsed = json.loads(synthesis) if isinstance(synthesis, str) else synthesis
            if parsed.get("recommendation") == "proceed":
                task_id = db.post_task(
                    execution_skill,
                    json.dumps({**execution_payload, "_consensus": synthesis}),
                    priority=7,
                )
        except Exception:
            pass

    # Save debate record
    knowledge_dir = FLEET_DIR / "knowledge" / "consensus"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    record = knowledge_dir / f"{discussion_id}.md"
    record.write_text(
        f"# Consensus: {topic}\n\n"
        f"**Participants:** {', '.join(participants)}\n"
        f"**Responses:** {len(responses)}/{len(participants)}\n\n"
        f"## Perspectives\n\n{debate_text}\n\n"
        f"## Synthesis\n\n{synthesis}\n",
        encoding="utf-8"
    )

    return json.dumps({
        "status": "ok",
        "discussion_id": discussion_id,
        "participants": participants,
        "responses_received": len(responses),
        "synthesis": synthesis,
        "execution_task_id": task_id,
        "record": str(record),
    })
