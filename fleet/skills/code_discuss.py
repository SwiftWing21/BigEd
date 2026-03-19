"""
Code discussion skill — coder agents read accumulated code context and prior
discussion, contribute their perspective, and post findings to the messages table.

Each coder_N has a distinct perspective driven by payload role_perspective:
  coder_1 → "software architect"
  coder_2 → "code critic / reviewer"
  coder_3 → "performance optimizer"
  coder_N → "generalist coder"

Payload:
  agent_name:       "coder_1"  (defaults to "coder")
  topic:            what to discuss, e.g. "fleet supervisor restart logic"
  role_perspective: "software architect" | "code critic" | "performance optimizer"
  round:            int (default 1)
  code_context:     optional code snippet or file path to focus on
"""
import json
from datetime import datetime
from pathlib import Path

from skills._models import call_complex

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

PERSPECTIVE_MAP = {
    "coder_1": "software architect",
    "coder_2": "code critic / reviewer",
    "coder_3": "performance optimizer",
}

CODE_KEYWORDS = [
    "code", "function", "class", "module", "import", "def ", "return",
    "error", "bug", "refactor", "performance", "algorithm", "sql", "api",
    "skill", "worker", "fleet", "supervisor", "db.", "payload", "config",
]


def _load_code_context(topic, code_context_hint):
    """Load relevant code context: explicit snippet, or scan knowledge files."""
    # If a direct code snippet was passed in, use it
    if code_context_hint:
        return code_context_hint[:3000]

    # Otherwise look for code-relevant knowledge files
    texts = []

    # Check if topic looks like a file path hint
    for skill_file in sorted((FLEET_DIR / "skills").glob("*.py"))[:5]:
        name = skill_file.stem
        if name in topic.lower() or topic.lower() in name:
            texts.append(f"# {skill_file.name}\n{skill_file.read_text()[:1200]}")
            break

    # Load recent code-related summaries
    summaries_dir = KNOWLEDGE_DIR / "summaries"
    if summaries_dir.exists():
        for f in sorted(summaries_dir.glob("*.md"), reverse=True)[:20]:
            content = f.read_text()
            if any(kw in content.lower() for kw in CODE_KEYWORDS):
                texts.append(content[:600])
                if len(texts) >= 4:
                    break

    return "\n\n---\n\n".join(texts[:4]) if texts else ""


def _load_discussion_so_far(topic):
    """Load prior contributions on this topic from messages table."""
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT from_agent, body_json FROM messages
            WHERE json_extract(body_json, '$.topic') = ?
              AND channel IN ('agent', 'fleet')
            ORDER BY created_at ASC
        """, (topic,)).fetchall()
    contributions = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
            contributions.append(f"[{row['from_agent']}]: {body.get('contribution', '')}")
        except Exception:
            pass
    return "\n\n".join(contributions)


def run(payload, config):
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    agent_name = payload.get("agent_name", "coder")
    topic = payload.get("topic", "fleet code quality")
    role_perspective = payload.get(
        "role_perspective",
        PERSPECTIVE_MAP.get(agent_name, "generalist coder")
    )
    round_num = payload.get("round", 1)
    code_context_hint = payload.get("code_context", "")

    code_context = _load_code_context(topic, code_context_hint)
    prior_discussion = _load_discussion_so_far(topic)

    system_prompt = f"""You are the {role_perspective} in a technical code review session.
As the {role_perspective}, provide your analysis of the topic.
Be specific and technical. Reference actual code patterns or line-level details where relevant.
Build on prior contributions if any exist — don't repeat what was already said.
4-6 bullet points max."""

    user_prompt = f"""TOPIC: {topic}
ROUND: {round_num}

{f"CODE CONTEXT:{chr(10)}{code_context[:3000]}" if code_context else ""}

{"PRIOR DISCUSSION:" + chr(10) + prior_discussion[:2000] if prior_discussion else "You are opening the technical discussion."}"""

    contribution = call_complex(system_prompt, user_prompt, config, skill_name="code_discuss")

    # Post to messages table
    db.post_message(
        from_agent=agent_name,
        to_agent="all",
        body_json=json.dumps({
            "topic": topic,
            "round": round_num,
            "role_perspective": role_perspective,
            "contribution": contribution,
            "timestamp": datetime.now().isoformat(),
        }),
        channel="agent",
    )

    # Save to discussion log
    log_dir = KNOWLEDGE_DIR / "code_discussion"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{topic[:40].replace(' ', '_')}_round{round_num}.md"
    with open(log_file, "a") as f:
        f.write(f"\n## [{agent_name}] — {role_perspective}\n{contribution}\n")

    return {"contribution": contribution, "topic": topic, "round": round_num}
