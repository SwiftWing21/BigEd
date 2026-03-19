"""
Structured discussion skill — agent reads accumulated research/messages,
contributes its perspective on a topic, and posts findings to the messages table.
Used to facilitate multi-agent "discussion" rounds before synthesis.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import httpx

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def _ollama(prompt, config):
    resp = httpx.post(
        f"{config['models']['ollama_host']}/api/generate",
        json={"model": config["models"]["local"], "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


BUSINESS_KEYWORDS = [
    "market", "healthcare", "medical", "accounting", "bookkeeping", "tax",
    "legal", "law", "attorney", "AI service", "managed service", "SMB",
    "small business", "revenue", "pricing", "implementation", "onboarding",
    "local LLM", "HIPAA", "compliance", "watsonville", "santa cruz",
    "opportunity", "pain point", "software gap", "client", "billing",
]

def _load_research_context():
    """Load business/market research summaries only — filter out arxiv ML papers."""
    summaries_dir = KNOWLEDGE_DIR / "summaries"
    if not summaries_dir.exists():
        return ""
    texts = []
    for f in sorted(summaries_dir.glob("*.md")):
        content = f.read_text()
        lower = content.lower()
        # Only include if it contains business-relevant keywords
        if any(kw in lower for kw in BUSINESS_KEYWORDS):
            texts.append(content[:800])
    # Also load reports
    reports_dir = KNOWLEDGE_DIR / "reports"
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.md"))[-5:]:
            texts.append(f.read_text()[:600])
    return "\n\n---\n\n".join(texts[-15:])


def _load_discussion_so_far(topic):
    """Load prior contributions on this topic from messages table."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
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
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import db

    agent_name = payload.get("agent_name", "unknown")
    topic = payload.get("topic", "business opportunities")
    role_perspective = payload.get("role_perspective", "general analyst")
    round_num = payload.get("round", 1)

    research_context = _load_research_context()
    prior_discussion = _load_discussion_so_far(topic)

    prompt = f"""You are the {role_perspective} in a strategic business planning session.

TOPIC: {topic}
ROUND: {round_num}

RESEARCH CONTEXT:
{research_context[:4000]}

{"PRIOR DISCUSSION:" + chr(10) + prior_discussion[:2000] if prior_discussion else "You are starting the discussion."}

Based on the above, provide your perspective as the {role_perspective}. Be specific, concise, and build on prior contributions if any exist. Focus on actionable insights relevant to your role. 4-6 bullet points max."""

    contribution = _ollama(prompt, config)

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

    # Also save to a discussion log file
    log_dir = KNOWLEDGE_DIR / "discussion"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{topic[:40].replace(' ', '_')}_round{round_num}.md"
    with open(log_file, "a") as f:
        f.write(f"\n## [{agent_name}] — {role_perspective}\n{contribution}\n")

    return {"contribution": contribution, "topic": topic, "round": round_num}
