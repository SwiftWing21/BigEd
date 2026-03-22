"""Shared core for OSS review skills — GitHub API, registry queries, pre-rating."""
import json
import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

FLEET_DIR = Path(__file__).parent.parent
REVIEWS_DIR = FLEET_DIR / "knowledge" / "oss_reviews"

# GitHub API (60 req/hr unauthenticated, 5000 with token)
def fetch_github_repo(owner: str, repo: str, token: str = "") -> dict:
    """Fetch repo metadata from GitHub API."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "BigEd-CC"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def fetch_github_issues(owner: str, repo: str, token: str = "", state: str = "open") -> list:
    """Fetch recent issues for bug count / response time analysis."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues?state={state}&per_page=30"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "BigEd-CC"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return []

def fetch_github_tree(owner: str, repo: str, token: str = "") -> list:
    """Fetch file tree (top-level) for structure analysis."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "BigEd-CC"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"][:500]
    except Exception:
        return []

def fetch_pypi_stats(package: str) -> dict:
    """Fetch download stats from PyPI."""
    try:
        url = f"https://pypistats.org/api/packages/{package}/recent"
        req = urllib.request.Request(url, headers={"User-Agent": "BigEd-CC"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return {"downloads_last_month": data.get("data", {}).get("last_month", 0)}
    except Exception:
        return {}

def fetch_npm_stats(package: str) -> dict:
    """Fetch download stats from npm."""
    try:
        url = f"https://api.npmjs.org/downloads/point/last-month/{package}"
        req = urllib.request.Request(url, headers={"User-Agent": "BigEd-CC"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return {"downloads_last_month": data.get("downloads", 0)}
    except Exception:
        return {}

def fetch_osv_vulns(package: str, ecosystem: str = "PyPI") -> list:
    """Query OSV.dev for known vulnerabilities."""
    try:
        body = json.dumps({"package": {"name": package, "ecosystem": ecosystem}}).encode()
        req = urllib.request.Request(
            "https://api.osv.dev/v1/query", data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "BigEd-CC"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("vulns", [])
    except Exception:
        return []

def pre_rate(repo_data: dict, registry_data: dict = None, vulns: list = None) -> dict:
    """Traffic light pre-rating from metadata. No LLM needed."""
    registry_data = registry_data or {}
    vulns = vulns or []

    stars = repo_data.get("stargazers_count", 0)
    forks = repo_data.get("forks_count", 0)
    open_issues = repo_data.get("open_issues_count", 0)
    license_name = (repo_data.get("license") or {}).get("spdx_id", "NONE")
    downloads = registry_data.get("downloads_last_month", 0)

    # Last commit age
    pushed_at = repo_data.get("pushed_at", "")
    days_since_push = 999
    if pushed_at:
        try:
            from datetime import datetime, timezone
            pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            days_since_push = (datetime.now(timezone.utc) - pushed).days
        except Exception:
            pass

    # CVE analysis
    critical_cves = sum(1 for v in vulns
                        for s in v.get("severity", [])
                        if s.get("score", 0) >= 9.0)
    total_cves = len(vulns)

    # Traffic light
    if critical_cves > 0 or (stars < 100 and downloads < 1000 and days_since_push > 180):
        light = "RED"
    elif stars > 1000 or downloads > 10000:
        if critical_cves == 0 and days_since_push < 30:
            light = "GREEN"
        else:
            light = "YELLOW"
    elif days_since_push < 90 and total_cves <= 2:
        light = "YELLOW"
    else:
        light = "RED"

    return {
        "light": light,
        "stars": stars,
        "forks": forks,
        "open_issues": open_issues,
        "license": license_name,
        "downloads_last_month": downloads,
        "days_since_push": days_since_push,
        "cve_count": total_cves,
        "critical_cves": critical_cves,
        "description": repo_data.get("description", ""),
        "language": repo_data.get("language", ""),
    }

def parse_github_url(url: str) -> tuple:
    """Extract owner/repo from GitHub URL. Returns (owner, repo) or (None, None)."""
    import re
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2)
    return None, None

def format_report_card(project_name: str, url: str, pre_rating: dict,
                       grades: dict, findings: list) -> str:
    """Generate markdown report card."""
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d")

    overall_scores = [v for v in grades.values() if isinstance(v, (int, float))]
    overall = sum(overall_scores) / max(1, len(overall_scores))
    overall_grade = _score_to_grade(overall)

    report = f"# OSS Review: {project_name}\n"
    report += f"**URL:** {url}\n"
    report += f"**Pre-Rating:** {pre_rating['light']} | Date: {ts}\n\n"
    report += "## Report Card\n"
    report += "| Dimension | Grade | Score |\n|-----------|-------|-------|\n"
    for dim, score in grades.items():
        report += f"| {dim} | {_score_to_grade(score)} | {score}/100 |\n"
    report += f"| **Overall** | **{overall_grade}** | **{overall:.0f}/100** |\n\n"

    report += "## Pre-Rating Metrics\n"
    report += f"- Stars: {pre_rating['stars']:,} | Forks: {pre_rating['forks']:,}\n"
    report += f"- Downloads: {pre_rating['downloads_last_month']:,}/month\n"
    report += f"- Open Issues: {pre_rating['open_issues']} | CVEs: {pre_rating['cve_count']}"
    report += f" ({pre_rating['critical_cves']} critical)\n"
    report += f"- Last Commit: {pre_rating['days_since_push']} days ago\n"
    report += f"- License: {pre_rating['license']} | Language: {pre_rating['language']}\n\n"

    if findings:
        report += "## Key Findings\n"
        for i, f in enumerate(findings, 1):
            sev = f.get("severity", "NOTE")
            report += f"{i}. [{sev}] {f.get('description', '')}\n"

    return report

def _score_to_grade(score):
    if score >= 90: return "A"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    if score >= 65: return "C+"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"
