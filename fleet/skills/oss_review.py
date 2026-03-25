"""OSS Review — discover, pre-rate, and review open-source projects.

Single-agent by default; pass ``swarm=true`` in the payload for the
multi-agent swarm review flow (4 specialized lenses + synthesis +
regression-tracking watchlist).
"""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

SKILL_NAME = "oss_review"
DESCRIPTION = "Discover, pre-rate, and review open-source projects (single or swarm agent)"
VERSION = "2.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = True

# ── Swarm constants ────────────────────────────────────────────────────────────
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


def run(payload: dict, config: dict) -> dict:
    # Route to swarm flow when requested
    if payload.get("swarm", False):
        action = payload.get("action", "review")
        if action == "review":
            return _swarm_review(payload, config)
        elif action == "watchlist_add":
            return _watchlist_add(payload, config)
        elif action == "watchlist_remove":
            return _watchlist_remove(payload, config)
        elif action == "compare":
            return _compare(payload, config)
        # fall through to single-agent for discover/pre_rate/report

    action = payload.get("action", "review")
    if action == "discover":
        return _discover(payload, config)
    elif action == "pre_rate":
        return _pre_rate(payload, config)
    elif action == "review":
        return _review(payload, config)
    elif action == "report":
        return _report(payload, config)
    else:
        return {"error": f"Unknown action: {action}"}

def _discover(payload, config):
    """Search for projects by topic, return pre-rated candidates."""
    query = payload.get("query", "")
    limit = payload.get("limit", 5)
    if not query:
        return {"error": "query required for discover action"}

    # Use web_search skill for discovery
    from skills.web_search import run as ws_run
    search_result = ws_run({"query": f"{query} site:github.com"}, config)
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

def _pre_rate(payload, config):
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

def _review(payload, config):
    """Full single-agent review of a project."""
    url = payload.get("url", "")
    focus = payload.get("focus", "")
    if not url:
        return {"error": "url required"}

    # Pre-rate first
    rating = _pre_rate(payload, config)
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

def _report(payload, config):
    """Quick report card — grades only, no deep findings."""
    result = _review(payload, config)
    # Strip detailed findings for lighter output
    result.pop("findings_count", None)
    return result


# ── Swarm helpers ──────────────────────────────────────────────────────────────

def _swarm_review(payload, config):
    """Run 4 specialized review agents + synthesis."""
    url = payload.get("url", "")
    if not url:
        return {"error": "url required"}

    rating = _pre_rate(payload, config)
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
            import re
            resp = call_complex(
                system=system_prompt, user=prompt, config=config,
                max_tokens=1024, skill_name="oss_review",
                agent_name=payload.get("agent_name"))
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

    from skills._oss_core import format_report_card
    report = format_report_card(f"{owner}/{repo}", url, rating, grades, deduped)
    report += f"\n\n## Swarm Details\n"
    report += f"- Agents: {len(LENSES)} specialized + synthesis\n"
    report += f"- Cross-validated findings: {sum(1 for f in deduped if f.get('confidence') == 'cross-validated')}\n"
    report += f"- Single-lens findings: {sum(1 for f in deduped if f.get('confidence') == 'single')}\n"

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REVIEWS_DIR / f"{owner}_{repo}_swarm_{time.strftime('%Y%m%d')}.md"
    report_path.write_text(report, encoding="utf-8")

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
    words1 = set(d1.split())
    words2 = set(d2.split())
    if len(words1) < 3 or len(words2) < 3:
        return False
    overlap = len(words1 & words2) / max(1, min(len(words1), len(words2)))
    return overlap > 0.5


def _watchlist_add(payload, config):
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


def _watchlist_remove(payload, config):
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


def _compare(payload, config):
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

    current = _swarm_review(payload, config)
    if "error" in current:
        return current

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
