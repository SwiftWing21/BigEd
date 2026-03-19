"""
Local business lead research — finds potential clients in target zip codes.
Searches for healthcare, accounting/tax, and legal businesses near 95076 (Watsonville CA).
"""
import json
from datetime import date
from pathlib import Path

TARGET_ZIPS = ["95076", "95003", "95010", "95019", "95060", "95062", "95065", "95066", "95073"]
INDUSTRIES = {
    "healthcare": [
        "medical practice", "family doctor", "urgent care", "dental office",
        "chiropractor", "physical therapy", "mental health clinic", "pediatrician",
        "optometrist", "dermatologist",
    ],
    "accounting": [
        "accountant", "CPA", "tax preparer", "bookkeeper", "accounting firm",
        "tax services", "payroll services", "financial advisor",
    ],
    "legal": [
        "law firm", "attorney", "lawyer", "legal services", "notary",
        "immigration attorney", "family law", "personal injury attorney",
    ],
}

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
SKILL_NAME = "lead_research"
DESCRIPTION = "Local business lead research — finds potential clients in target zip codes."

REQUIRES_NETWORK = True


def _search(query, config):
    """Use the web_search skill's waterfall."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import web_search
    result = web_search.run({"query": query}, config)
    return result.get("results", [])


def run(payload, config):
    industry = payload.get("industry", "all")
    zip_code = payload.get("zip_code", "95076")
    city = payload.get("city", "Watsonville CA")

    industries_to_search = (
        {industry: INDUSTRIES[industry]}
        if industry in INDUSTRIES
        else INDUSTRIES
    )

    all_leads = []

    for sector, terms in industries_to_search.items():
        for term in terms[:3]:  # top 3 terms per sector per call
            query = f"{term} {city} {zip_code}"
            results = _search(query, config)
            for r in results:
                if r.get("title") or r.get("snippet"):
                    all_leads.append({
                        "sector": sector,
                        "search_term": term,
                        "zip": zip_code,
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("snippet", "")[:200],
                    })

    # Save to leads file
    out_dir = KNOWLEDGE_DIR / "leads"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"{date.today()}_{zip_code}_{industry}.jsonl"
    with open(out_file, "a") as f:
        for lead in all_leads:
            f.write(json.dumps(lead) + "\n")

    # Also write a readable markdown summary
    md_file = out_dir / f"{date.today()}_leads_summary.md"
    with open(md_file, "a") as f:
        f.write(f"\n## {city} {zip_code} — {industry.title()}\n")
        for lead in all_leads:
            f.write(f"- **{lead['title']}** ({lead['sector']})\n")
            if lead["snippet"]:
                f.write(f"  {lead['snippet']}\n")
            if lead["url"]:
                f.write(f"  {lead['url']}\n")

    return {
        "leads_found": len(all_leads),
        "zip_code": zip_code,
        "industry": industry,
        "saved_to": str(out_file),
    }