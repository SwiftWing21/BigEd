"""
Account review skill — analyzes all tracked service accounts, usage vs free tier limits,
monthly costs, and generates a prioritized upgrade recommendation report.

Reads accounts from the launcher's tools.db (shared source of truth).
Saves report to knowledge/reports/account_review_<date>.md

Payload:
  focus       str   "upgrades" | "cost" | "all" (default: "all")
  threshold   int   usage_pct threshold to flag for upgrade (default: 70)
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from skills._models import call_complex

FLEET_DIR     = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
REPORTS_DIR   = KNOWLEDGE_DIR / "reports"

# Launcher's tools.db — accessible from WSL via the /mnt mount
LAUNCHER_DB = Path("/mnt/c/Users/max/Projects/Education/BigEd/launcher/data/tools.db")

# Estimated paid tier costs for upgrade cost-benefit analysis
_UPGRADE_COSTS = {
    "Anthropic API":   "pay-as-you-go: ~$3-15/M tokens",
    "Google Gemini":   "paid: $0.075-0.15/M tokens (Gemini 1.5 Flash/Pro)",
    "Stability AI":    "pay-as-you-go: $0.003-0.065/image",
    "Replicate":       "pay-as-you-go: ~$0.0004-0.01/second",
    "Brave Search":    "Pro: $3/month for 20k queries",
    "Tavily Search":   "Basic: $20/month for 10k searches",
    "HuggingFace":     "Pro: $9/month for private models + more compute",
    "GitHub":          "Team: $4/user/month for more Actions minutes",
}


def _read_accounts():
    """Read accounts table from launcher tools.db."""
    if not LAUNCHER_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(LAUNCHER_DB))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM accounts ORDER BY upgrade_priority DESC, category, service"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]



def run(payload, config):
    focus     = payload.get("focus", "all")
    threshold = int(payload.get("threshold", 70))

    accounts = _read_accounts()
    if not accounts or (len(accounts) == 1 and "error" in accounts[0]):
        return {"error": "Could not read accounts from tools.db", "detail": str(accounts)}

    # Build summary data for the prompt
    total_cost = sum(a.get("monthly_cost", 0) or 0 for a in accounts)
    free_accts = [a for a in accounts if a.get("tier") == "free"]
    near_limit = [a for a in free_accts if (a.get("usage_pct") or 0) >= threshold]
    paid_accts = [a for a in accounts if a.get("tier") == "paid"]

    accounts_summary = []
    for a in accounts:
        upgrade_cost = _UPGRADE_COSTS.get(a.get("service", ""), "unknown")
        accounts_summary.append(
            f"- {a.get('service','?')} [{a.get('category','?')}] "
            f"tier={a.get('tier','?')} "
            f"usage={a.get('usage_pct',0)}% "
            f"free_limit={a.get('free_limit','—')} "
            f"monthly_cost=${a.get('monthly_cost',0):.2f} "
            f"priority={a.get('upgrade_priority',0)} "
            f"paid_tier_cost={upgrade_cost}"
        )

    system = "You are a business operations analyst reviewing a small AI services company's SaaS account portfolio."

    user = f"""CURRENT DATE: {datetime.now().strftime('%Y-%m-%d')}
TOTAL MONTHLY SPEND: ${total_cost:.2f}
FREE ACCOUNTS: {len(free_accts)} | NEAR LIMIT (>{threshold}% usage): {len(near_limit)} | PAID: {len(paid_accts)}

ACCOUNTS:
{chr(10).join(accounts_summary)}

ANALYSIS FOCUS: {focus}

Write a concise account review report with these sections:

## Executive Summary
2-3 sentences on overall account health and spend.

## Accounts Needing Immediate Attention
List accounts at or near free tier limits. For each: current usage, what breaks at the limit, and urgency.

## Upgrade Recommendations (Priority Order)
For each recommended upgrade:
- Service name and why it matters to the business
- What the paid tier unlocks (capacity, features, reliability)
- Estimated monthly cost and ROI justification
- When to upgrade (now / when revenue hits $X / when usage hits Y)

## Free Accounts to Keep
Which free tiers are sufficient and why — don't upgrade these yet.

## New Services to Consider
Any services not yet in the portfolio that would benefit the AI implementation business.
Focus on: client delivery quality, sales pipeline, compliance, reliability."""

    report_text = call_complex(system, user, config)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    out_file = REPORTS_DIR / f"account_review_{date_str}.md"

    header = (
        f"# Account Portfolio Review\n"
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Monthly Spend:** ${total_cost:.2f} | "
        f"**Free Accounts:** {len(free_accts)} | "
        f"**Near Limit:** {len(near_limit)}\n\n---\n\n"
    )
    out_file.write_text(header + report_text)

    return {
        "accounts_reviewed": len(accounts),
        "near_limit":        len(near_limit),
        "total_monthly_cost": total_cost,
        "saved_to":          str(out_file),
        "summary":           report_text[:400],
    }
