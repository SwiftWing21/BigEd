"""OSS Review — discover, pre-rate, and review open-source projects (single agent)."""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

SKILL_NAME = "oss_review"
DESCRIPTION = "Discover, pre-rate, and review open-source projects (single agent)"
COMPLEXITY = "medium"
REQUIRES_NETWORK = True

def run(payload: dict, config: dict, log=None) -> dict:
    if log is None:
        log = logging.getLogger(__name__)
    action = payload.get("action", "review")

    if action == "discover":
        return _discover(payload, config, log)
    elif action == "pre_rate":
        return _pre_rate(payload, config, log)
    elif action == "review":
        return _review(payload, config, log)
    elif action == "report":
        return _report(payload, config, log)
    else:
        return {"error": f"Unknown action: {action}"}

def _discover(payload, config, log):
    """Search for projects by topic, return pre-rated candidates."""
    query = payload.get("query", "")
    limit = payload.get("limit", 5)
    if not query:
        return {"error": "query required for discover action"}

    # Use web_search skill for discovery
    from skills.web_search import run as ws_run
    search_result = ws_run({"query": f"{query} site:github.com"}, config, log)
    results = search_result.get("results", [])

    from skills._oss_core import parse_github_url, fetch_github_repo, pre_rate
    token = os.environ.get("GITHUB_TOKEN", "")

    candidates = []
    for r in results[:limit * 2]:  # fetch extra in case some fail
        url = r.get("url", "")
        owner, repo = parse_github_url(url)
        if not owner:
            continue
        repo_data = fetch_github_repo(owner, repo, token)
        if "error" in repo_data:
            continue
        rating = pre_rate(repo_data)
        candidates.append({
            "name": f"{owner}/{repo}",
            "url": url,
            "description": rating["description"],
            "light": rating["light"],
            "stars": rating["stars"],
            "downloads": rating["downloads_last_month"],
            "cves": rating["cve_count"],
            "last_push_days": rating["days_since_push"],
        })
        if len(candidates) >= limit:
            break

    candidates.sort(key=lambda x: x["stars"], reverse=True)
    return {"query": query, "candidates": candidates, "count": len(candidates)}

def _pre_rate(payload, config, log):
    """Quick traffic-light rating of a specific project."""
    url = payload.get("url", "")
    if not url:
        return {"error": "url required"}

    from skills._oss_core import parse_github_url, fetch_github_repo, pre_rate
    from skills._oss_core import fetch_pypi_stats, fetch_osv_vulns
    token = os.environ.get("GITHUB_TOKEN", "")

    owner, repo = parse_github_url(url)
    if not owner:
        return {"error": f"Could not parse GitHub URL: {url}"}

    repo_data = fetch_github_repo(owner, repo, token)
    if "error" in repo_data:
        return {"error": f"GitHub API failed: {repo_data['error']}"}

    # Try registry stats
    language = (repo_data.get("language") or "").lower()
    registry = {}
    if language == "python":
        registry = fetch_pypi_stats(repo)
    vulns = fetch_osv_vulns(repo, "PyPI" if language == "python" else "npm")

    rating = pre_rate(repo_data, registry, vulns)
    rating["project"] = f"{owner}/{repo}"
    rating["url"] = url
    return rating

def _review(payload, config, log):
    """Full single-agent review of a project."""
    url = payload.get("url", "")
    focus = payload.get("focus", "")
    if not url:
        return {"error": "url required"}

    # Pre-rate first
    rating = _pre_rate(payload, config, log)
    if "error" in rating:
        return rating

    from skills._oss_core import (parse_github_url, fetch_github_repo,
                                   fetch_github_tree, format_report_card, REVIEWS_DIR)
    token = os.environ.get("GITHUB_TOKEN", "")
    owner, repo = parse_github_url(url)

    # Fetch README
    readme = ""
    try:
        import urllib.request
        readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
        req = urllib.request.Request(
            readme_url, headers={"User-Agent": "BigEd-CC"})
        with urllib.request.urlopen(req, timeout=10) as r:
            readme = r.read().decode("utf-8", errors="ignore")[:5000]
    except Exception:
        pass

    # Fetch file tree for structure analysis
    tree = fetch_github_tree(owner, repo, token)
    tree_summary = "\n".join(tree[:100])

    # LLM review
    focus_line = f"\nFocus especially on: {focus}" if focus else ""
    prompt = (
        f"Review this open-source project:\n"
        f"Project: {owner}/{repo}\n"
        f"URL: {url}\n"
        f"Stars: {rating['stars']}, Forks: {rating['forks']}\n"
        f"Language: {rating['language']}, License: {rating['license']}\n"
        f"CVEs: {rating['cve_count']} ({rating['critical_cves']} critical)\n"
        f"{focus_line}\n\n"
        f"README:\n{readme[:3000]}\n\n"
        f"File structure:\n{tree_summary}\n\n"
        f"Grade this project on these dimensions (0-100 each):\n"
        f"1. Security\n2. Performance\n3. Architecture\n4. Compliance\n\n"
        f"For each dimension, provide the score and 1-3 key findings.\n"
        f"Format as JSON: {{\"grades\": {{\"Security\": 85, ...}}, "
        f"\"findings\": [{{\"severity\": \"HIGH\", \"dimension\": \"Security\", "
        f"\"description\": \"...\"}}]}}"
    )

    from skills._models import call_complex
    response = call_complex(
        system="You are a senior software architect reviewing open-source projects. "
               "Provide structured, actionable reviews with severity-tagged findings.",
        user=prompt, config=config, max_tokens=2048,
        skill_name="oss_review", agent_name=payload.get("agent_name"))

    # Parse LLM response
    grades = {"Security": 70, "Performance": 70, "Architecture": 70, "Compliance": 70}
    findings = []
    try:
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            grades = parsed.get("grades", grades)
            findings = parsed.get("findings", [])
    except Exception:
        findings = [{"severity": "NOTE", "description": response[:500]}]

    # Generate report
    report = format_report_card(f"{owner}/{repo}", url, rating, grades, findings)
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REVIEWS_DIR / f"{owner}_{repo}_review_{time.strftime('%Y%m%d')}.md"
    report_path.write_text(report, encoding="utf-8")

    return {
        "project": f"{owner}/{repo}",
        "url": url,
        "light": rating["light"],
        "grades": grades,
        "findings_count": len(findings),
        "saved_to": str(report_path),
    }

def _report(payload, config, log):
    """Quick report card — grades only, no deep findings."""
    result = _review(payload, config, log)
    # Strip detailed findings for lighter output
    result.pop("findings_count", None)
    return result
