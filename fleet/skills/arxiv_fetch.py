"""Fetch and summarize arxiv papers by ID or keyword query."""
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import httpx

NS = {"atom": "http://www.w3.org/2005/Atom"}


def run(payload, config):
    arxiv_id = payload.get("arxiv_id", "").replace("arxiv:", "").strip()
    query = payload.get("query", "")

    if arxiv_id:
        url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    elif query:
        url = f"https://export.arxiv.org/api/query?search_query=all:{query}&max_results=3&sortBy=lastUpdatedDate"
    else:
        return {"error": "Provide arxiv_id or query"}

    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", NS)
    if not entries:
        return {"error": "No papers found"}

    papers = []
    for entry in entries[:3]:
        papers.append({
            "id": entry.findtext("atom:id", "", NS).strip(),
            "title": entry.findtext("atom:title", "", NS).strip().replace("\n", " "),
            "abstract": entry.findtext("atom:summary", "", NS).strip().replace("\n", " ")[:1000],
        })

    # Summarize the first result
    from skills.summarize import _ollama
    p = papers[0]
    prompt = f"Paper: {p['title']}\n\nAbstract: {p['abstract']}\n\nKey takeaways in 3 bullets:"
    papers[0]["summary"] = _ollama(prompt, config)

    slug = re.sub(r"[^a-z0-9]+", "_", p["title"][:40].lower()).strip("_")
    out_dir = Path(__file__).parent.parent / "knowledge" / "summaries"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{date.today()}_{slug}.md"
    out_file.write_text(f"# {p['title']}\n\n{p['id']}\n\n{papers[0]['summary']}\n")

    return {"papers": papers, "saved_to": str(out_file)}
