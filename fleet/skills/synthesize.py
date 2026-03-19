"""
Synthesize all agent discussion contributions + research into a final document.
Used to produce business pitch, agent prep docs, or strategic reports.
Uses Sonnet for synthesis quality — this is a high-value, infrequent call.
"""
import json
from datetime import date
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def _sonnet(system, user, config=None):
    from skills._models import call_complex
    return call_complex(system, user, config or {}, max_tokens=4096, cache_system=True)


def _load_all_discussion(topic=None):
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import db
    with db.get_conn() as conn:
        if topic:
            rows = conn.execute("""
                SELECT from_agent, body_json, created_at FROM messages
                WHERE json_extract(body_json, '$.topic') = ?
                ORDER BY created_at ASC
            """, (topic,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT from_agent, body_json, created_at FROM messages
                ORDER BY created_at ASC
            """).fetchall()
    contributions = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
            contributions.append(
                f"[{row['from_agent']} - {body.get('role_perspective','?')} - Round {body.get('round','?')}]\n"
                f"{body.get('contribution', '')}"
            )
        except Exception:
            pass
    return "\n\n".join(contributions)


def _load_research_summaries():
    summaries_dir = KNOWLEDGE_DIR / "summaries"
    if not summaries_dir.exists():
        return ""
    texts = []
    for f in sorted(summaries_dir.glob("*.md")):
        texts.append(f.read_text()[:600])
    return "\n\n---\n\n".join(texts)


def run(payload, config):
    doc_type = payload.get("doc_type", "business_pitch")
    topic = payload.get("topic")
    output_name = payload.get("output_name", f"{doc_type}_{date.today()}")

    discussion = _load_all_discussion(topic)
    research = _load_research_summaries()

    if doc_type == "business_pitch":
        system = """You are a senior business strategist creating a compelling business pitch.
Structure the output as a complete pitch document with:
1. Executive Summary
2. The Problem (market pain points)
3. Our Solution (local AI implementation partner)
4. Market Opportunity (size, segments, geography)
5. Service Offerings & Pricing Tiers
6. Competitive Advantage (privacy-first, local deployment)
7. Target Customer Profiles (healthcare, accounting/tax, legal)
8. Revenue Model & Projections
9. Go-to-Market Strategy
10. Hardware & Infrastructure (RTX 3080 Ti primary, GTX 1070 secondary)
11. Immediate Next Steps

Be specific, use data from the research where available, and make it actionable."""

        user = f"""Create a complete business pitch based on this research and agent discussion.

AGENT DISCUSSION:
{discussion[:6000]}

MARKET RESEARCH:
{research[:4000]}

Location: Watsonville, CA 95076 and surrounding area (Santa Cruz County)
Hardware available: RTX 3080 Ti 12GB (primary), GTX 1070 8GB (secondary/Ryzen 1700)
Target industries: Healthcare, Accounting/Tax/Bookkeeping, Legal"""

    elif doc_type == "agent_prep":
        agent_role = payload.get("agent_role", "sales")
        system = f"""You are creating a comprehensive preparation document for an AI {agent_role} agent.
Include: role definition, knowledge base, scripts, objection handling, workflows, and decision trees.
Make it specific to selling/implementing local AI for healthcare, accounting/tax, and legal SMBs."""

        user = f"""Create a complete {agent_role} agent preparation document.

RESEARCH & DISCUSSION:
{discussion[:4000]}

{research[:3000]}"""

    else:
        system = "You are a business analyst creating a strategic document."
        user = f"Create a {doc_type} document based on:\n\n{discussion[:5000]}\n\n{research[:3000]}"

    result = _sonnet(system, user, config)

    out_dir = KNOWLEDGE_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{output_name}.md"
    out_file.write_text(f"# {output_name.replace('_', ' ').title()}\n\n{result}\n")

    return {"doc_type": doc_type, "saved_to": str(out_file), "length": len(result)}
