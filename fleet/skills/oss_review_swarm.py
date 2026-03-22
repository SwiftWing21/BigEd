"""OSS Review Swarm — multi-agent swarm audit with regression tracking."""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

SKILL_NAME = "oss_review_swarm"
DESCRIPTION = "Multi-agent swarm audit of open-source projects with regression tracking"
COMPLEXITY = "complex"
REQUIRES_NETWORK = True

LENSES = {
    "security": "You are a security auditor. Focus on: CVEs, dependency vulnerabilities, "
                "injection risks, authentication patterns, secrets in code, SSRF vectors.",
    "performance": "You are a performance engineer. Focus on: algorithmic complexity, "
                   "memory patterns, I/O blocking, caching strategies, resource cleanup.",
    "architecture": "You are a software architect. Focus on: module coupling, test coverage, "
                    "API surface area, error handling patterns, documentation quality.",
    "compliance": "You are a compliance auditor. Focus on: license compatibility, SBOM, "
                  "data handling practices, supply chain integrity, dependency hygiene.",
}

def run(payload: dict, config: dict, log=None) -> dict:
    if log is None:
        log = logging.getLogger(__name__)
    action = payload.get("action", "review")

    if action == "review":
        return _swarm_review(payload, config, log)
    elif action == "watchlist_add":
        return _watchlist_add(payload, config, log)
    elif action == "watchlist_remove":
        return _watchlist_remove(payload, config, log)
    elif action == "compare":
        return _compare(payload, config, log)
    elif action in ("discover", "pre_rate", "report"):
        from skills.oss_review import run as light_run
        return light_run(payload, config, log)
    else:
        return {"error": f"Unknown action: {action}"}

def _swarm_review(payload, config, log):
    """Run 4 specialized review agents + synthesis."""
    url = payload.get("url", "")
    if not url:
        return {"error": "url required"}

    # Pre-rate first
    from skills.oss_review import _pre_rate
    rating = _pre_rate(payload, config, log)
    if "error" in rating:
        return rating

    from skills._oss_core import parse_github_url, fetch_github_tree, REVIEWS_DIR
    token = os.environ.get("GITHUB_TOKEN", "")
    owner, repo = parse_github_url(url)

    # Fetch README + tree
    readme = ""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md",
            headers={"User-Agent": "BigEd-CC"})
        with urllib.request.urlopen(req, timeout=10) as r:
            readme = r.read().decode("utf-8", errors="ignore")[:5000]
    except Exception:
        pass
    tree = fetch_github_tree(owner, repo, token)

    context = (
        f"Project: {owner}/{repo}\nURL: {url}\n"
        f"Stars: {rating['stars']}, Language: {rating['language']}\n"
        f"CVEs: {rating['cve_count']}\n\n"
        f"README:\n{readme[:2000]}\n\nFile tree:\n" + "\n".join(tree[:80])
    )

    # Run 4 lens agents
    from skills._models import call_complex
    lens_results = {}
    for lens_name, system_prompt in LENSES.items():
        prompt = (
            f"Review this project from your specialized perspective.\n\n"
            f"{context}\n\n"
            f"Provide:\n1. Score (0-100)\n2. Top 5 findings with severity (CRITICAL/HIGH/MEDIUM/LOW)\n"
            f"Format as JSON: {{\"score\": N, \"findings\": [{{\"severity\": \"...\", \"description\": \"...\"}}]}}"
        )
        try:
            resp = call_complex(
                system=system_prompt, user=prompt, config=config,
                max_tokens=1024, skill_name="oss_review_swarm",
                agent_name=payload.get("agent_name"))
            import re
            m = re.search(r'\{.*\}', resp, re.DOTALL)
            if m:
                lens_results[lens_name] = json.loads(m.group())
            else:
                lens_results[lens_name] = {"score": 60, "findings": [{"severity": "NOTE", "description": resp[:300]}]}
        except Exception as e:
            lens_results[lens_name] = {"score": 50, "findings": [{"severity": "NOTE", "description": str(e)}]}

    # Synthesis: merge findings, score confidence
    all_findings = []
    grades = {}
    for lens_name, result in lens_results.items():
        grades[lens_name.title()] = result.get("score", 50)
        for f in result.get("findings", []):
            f["lens"] = lens_name
            # Check if duplicate across lenses
            f["confidence"] = "single"
            for other_lens, other_result in lens_results.items():
                if other_lens != lens_name:
                    for of in other_result.get("findings", []):
                        if _findings_similar(f, of):
                            f["confidence"] = "cross-validated"
                            break
            all_findings.append(f)

    # Deduplicate cross-validated findings
    seen = set()
    deduped = []
    for f in all_findings:
        key = f["description"][:50].lower()
        if key not in seen:
            deduped.append(f)
            seen.add(key)

    # Generate report
    from skills._oss_core import format_report_card
    report = format_report_card(f"{owner}/{repo}", url, rating, grades, deduped)
    report += f"\n\n## Swarm Details\n"
    report += f"- Agents: {len(LENSES)} specialized + synthesis\n"
    report += f"- Cross-validated findings: {sum(1 for f in deduped if f.get('confidence') == 'cross-validated')}\n"
    report += f"- Single-lens findings: {sum(1 for f in deduped if f.get('confidence') == 'single')}\n"

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REVIEWS_DIR / f"{owner}_{repo}_swarm_{time.strftime('%Y%m%d')}.md"
    report_path.write_text(report, encoding="utf-8")

    # Store baseline for regression tracking
    baseline = {"grades": grades, "findings_count": len(deduped), "date": time.strftime("%Y-%m-%d")}
    _update_watchlist_baseline(url, f"{owner}/{repo}", baseline)

    return {
        "project": f"{owner}/{repo}",
        "url": url,
        "light": rating["light"],
        "grades": grades,
        "findings_count": len(deduped),
        "cross_validated": sum(1 for f in deduped if f.get("confidence") == "cross-validated"),
        "saved_to": str(report_path),
    }

def _findings_similar(f1, f2):
    """Check if two findings are about the same issue."""
    d1 = f1.get("description", "").lower()[:60]
    d2 = f2.get("description", "").lower()[:60]
    # Simple word overlap check
    words1 = set(d1.split())
    words2 = set(d2.split())
    if len(words1) < 3 or len(words2) < 3:
        return False
    overlap = len(words1 & words2) / max(1, min(len(words1), len(words2)))
    return overlap > 0.5

def _watchlist_add(payload, config, log):
    """Add project to regression tracking watchlist."""
    url = payload.get("url", "")
    frequency = payload.get("frequency", "weekly")
    if not url:
        return {"error": "url required"}
    from skills._oss_core import parse_github_url
    owner, repo = parse_github_url(url)
    if not owner:
        return {"error": f"Could not parse: {url}"}
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oss_watchlist (project_url, project_name, review_frequency) VALUES (?,?,?)",
                (url, f"{owner}/{repo}", frequency))
    db._retry_write(_do)
    return {"added": f"{owner}/{repo}", "frequency": frequency}

def _watchlist_remove(payload, config, log):
    """Remove project from watchlist."""
    url = payload.get("url", "")
    if not url:
        return {"error": "url required"}
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute("DELETE FROM oss_watchlist WHERE project_url=?", (url,))
    db._retry_write(_do)
    return {"removed": url}

def _compare(payload, config, log):
    """Compare current review against stored baseline."""
    url = payload.get("url", "")
    if not url:
        return {"error": "url required"}
    import db
    with db.get_conn() as conn:
        row = conn.execute("SELECT baseline_json, last_grade FROM oss_watchlist WHERE project_url=?", (url,)).fetchone()
    if not row or not row["baseline_json"]:
        return {"error": "No baseline found — run a review first"}
    baseline = json.loads(row["baseline_json"])

    # Run fresh review
    current = _swarm_review(payload, config, log)
    if "error" in current:
        return current

    # Diff grades
    grade_changes = {}
    for dim, old_score in baseline.get("grades", {}).items():
        new_score = current.get("grades", {}).get(dim, 0)
        delta = new_score - old_score
        grade_changes[dim] = {"previous": old_score, "current": new_score, "delta": delta}

    return {
        "project": current.get("project"),
        "previous_date": baseline.get("date"),
        "current_date": time.strftime("%Y-%m-%d"),
        "grade_changes": grade_changes,
        "previous_findings": baseline.get("findings_count", 0),
        "current_findings": current.get("findings_count", 0),
    }

def _update_watchlist_baseline(url, name, baseline):
    """Update or insert watchlist baseline."""
    try:
        import db
        def _do():
            with db.get_conn() as conn:
                existing = conn.execute("SELECT id FROM oss_watchlist WHERE project_url=?", (url,)).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE oss_watchlist SET baseline_json=?, last_grade=?, last_review_at=datetime('now') WHERE project_url=?",
                        (json.dumps(baseline), str(baseline.get("grades", {})), url))
                else:
                    conn.execute(
                        "INSERT INTO oss_watchlist (project_url, project_name, baseline_json, last_review_at) VALUES (?,?,?,datetime('now'))",
                        (url, name, json.dumps(baseline)))
        db._retry_write(_do)
    except Exception:
        pass
