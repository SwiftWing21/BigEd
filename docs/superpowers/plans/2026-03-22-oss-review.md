# OSS Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two fleet skills (`oss_review` + `oss_review_swarm`) and one Claude Code plugin for discovering, rating, and reviewing open-source projects.

**Architecture:** Shared core module (`_oss_core.py`) handles GitHub API, registry queries, and pre-rating. `oss_review.py` wraps it with single-agent review. `oss_review_swarm.py` wraps it with 4+1 agent swarm consensus + regression tracking. Claude Code plugin (`.claude/skills/oss-review.md`) provides interactive UX.

**Tech Stack:** Python stdlib (urllib, json), fleet skill contract, fleet.db (oss_watchlist table), existing skills (web_search, evaluate, swarm_consensus)

**Spec:** `docs/superpowers/specs/2026-03-22-oss-review-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `fleet/skills/_oss_core.py` | Shared: GitHub API, registry queries, pre-rating, report formatting |
| `fleet/skills/oss_review.py` | Lightweight skill: discover, pre_rate, review (single agent) |
| `fleet/skills/oss_review_swarm.py` | Heavy skill: swarm review, watchlist, compare, regression |
| `fleet/db.py` | Add oss_watchlist table to init_db() |
| `.claude/skills/oss-review.md` | Claude Code plugin for interactive review |
| `knowledge/oss_reviews/` | Output directory (auto-created) |

---

### Task 1: Shared Core — GitHub API + Registry Queries

**Files:**
- Create: `fleet/skills/_oss_core.py`

- [ ] **Step 1: Create _oss_core.py with GitHub API client**

```python
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
```

- [ ] **Step 2: Add registry query functions (PyPI, npm, OSV)**

```python
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
```

- [ ] **Step 3: Add pre-rating (traffic light) function**

```python
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
```

- [ ] **Step 4: Add report formatting function**

```python
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
```

- [ ] **Step 5: Verify module compiles**

Run: `python -m py_compile fleet/skills/_oss_core.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add fleet/skills/_oss_core.py
git commit -m "feat: _oss_core.py — GitHub API, registry queries, pre-rating, report formatting"
```

---

### Task 2: Lightweight Skill — oss_review

**Files:**
- Create: `fleet/skills/oss_review.py`

- [ ] **Step 1: Create oss_review.py with skill contract + discover action**

```python
"""OSS Review — discover, pre-rate, and review open-source projects (single agent)."""
import json
import logging
import os
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
```

- [ ] **Step 2: Implement discover action**

```python
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
```

- [ ] **Step 3: Implement pre_rate action**

```python
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
```

- [ ] **Step 4: Implement review action (single-agent LLM review)**

```python
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
        readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
        req = __import__("urllib.request", fromlist=["urlopen"]).Request(
            readme_url, headers={"User-Agent": "BigEd-CC"})
        with __import__("urllib.request", fromlist=["urlopen"]).urlopen(req, timeout=10) as r:
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
    import time
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
```

- [ ] **Step 5: Implement report action (grades only, minimal LLM)**

```python
def _report(payload, config, log):
    """Quick report card — grades only, no deep findings."""
    result = _review(payload, config, log)
    # Strip detailed findings for lighter output
    result.pop("findings_count", None)
    return result
```

- [ ] **Step 6: Verify skill compiles and imports**

Run: `cd fleet && python -c "from skills.oss_review import SKILL_NAME; print(SKILL_NAME)"`
Expected: `oss_review`

- [ ] **Step 7: Commit**

```bash
git add fleet/skills/oss_review.py
git commit -m "feat: oss_review skill — discover, pre-rate, single-agent review"
```

---

### Task 3: Swarm Skill — oss_review_swarm

**Files:**
- Create: `fleet/skills/oss_review_swarm.py`
- Modify: `fleet/db.py` (add oss_watchlist table)

- [ ] **Step 1: Add oss_watchlist table to db.py init_db()**

Add after the output_feedback table creation in `init_db()`:

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS oss_watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_url TEXT NOT NULL UNIQUE,
        project_name TEXT,
        last_review_at TEXT,
        last_grade TEXT,
        review_frequency TEXT DEFAULT 'weekly',
        baseline_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
""")
```

- [ ] **Step 2: Create oss_review_swarm.py with skill contract + swarm review**

```python
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
```

- [ ] **Step 3: Implement swarm review (4 lens agents + synthesis)**

```python
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
```

- [ ] **Step 4: Implement watchlist + compare actions**

```python
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
```

- [ ] **Step 5: Verify both files compile**

Run: `cd fleet && python -c "from skills.oss_review_swarm import SKILL_NAME; print(SKILL_NAME)"`
Expected: `oss_review_swarm`

- [ ] **Step 6: Commit**

```bash
git add fleet/skills/oss_review_swarm.py fleet/db.py
git commit -m "feat: oss_review_swarm — 4-lens swarm audit, watchlist, regression compare"
```

---

### Task 4: Claude Code Plugin

**Files:**
- Create: `.claude/skills/oss-review.md`

- [ ] **Step 1: Create the plugin file**

```markdown
---
name: oss-review
description: Review open-source projects for quality, security, and maintainability
---

# OSS Project Reviewer

When the user asks to review an open-source project, search for projects, or evaluate a GitHub repository:

## If a GitHub URL is provided:

1. Fetch repo metadata from the GitHub API (`https://api.github.com/repos/{owner}/{repo}`)
2. Pre-rate: stars, forks, open issues, last commit age, license
3. Check for known vulnerabilities via OSV.dev (`https://api.osv.dev/v1/query`)
4. Show the traffic light rating:
   - GREEN: >1000 stars, 0 critical CVEs, recent commits
   - YELLOW: 100-1000 stars, minor CVEs, moderately active
   - RED: low activity, critical CVEs, or abandoned
5. Ask: "Pre-rating is [GREEN/YELLOW/RED]. Proceed with full review?"
6. If yes, fetch README and file tree, then analyze:
   - Security: dependency risks, auth patterns, input validation
   - Performance: complexity, resource usage, async patterns
   - Architecture: modularity, test coverage, API design, docs
   - Compliance: license, data handling, supply chain
7. Output a report card with letter grades (A-F) per dimension + key findings

## If a search query is provided:

1. Search GitHub for repositories matching the query
2. Pre-rate the top 5 results
3. Present ranked candidates with traffic light ratings
4. Ask which one to review in detail

## Output format:

Use this report card format:

| Dimension | Grade | Key Finding |
|-----------|-------|-------------|
| Security | B+ | No critical CVEs, 2 medium dependency issues |
| Performance | A- | Clean async patterns |
| Architecture | B | Good modularity, 72% test coverage |
| Compliance | A | MIT license, clean SBOM |
| **Overall** | **B+** | |

## Important:
- Always show the pre-rating before doing a full review
- Include known issues from the project's issue tracker when available
- Flag any critical CVEs prominently
- Note if the project appears abandoned (no commits >90 days)
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/oss-review.md
git commit -m "feat: oss-review Claude Code plugin — interactive project reviewer"
```

---

### Task 5: Smoke Test + Integration Verification

**Files:**
- Modify: `fleet/smoke_test.py` (add oss_review import test)

- [ ] **Step 1: Verify all skills import**

Run:
```bash
cd fleet
python -c "from skills.oss_review import SKILL_NAME, COMPLEXITY; print(f'{SKILL_NAME}: {COMPLEXITY}')"
python -c "from skills.oss_review_swarm import SKILL_NAME, COMPLEXITY; print(f'{SKILL_NAME}: {COMPLEXITY}')"
python -c "from skills._oss_core import pre_rate, parse_github_url; print('core OK')"
```

- [ ] **Step 2: Run full smoke test**

Run: `python smoke_test.py --fast`
Expected: 22/22 passed

- [ ] **Step 3: Verify output directory created**

Run: `python -c "from skills._oss_core import REVIEWS_DIR; REVIEWS_DIR.mkdir(parents=True, exist_ok=True); print(REVIEWS_DIR)"`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: OSS Review complete — oss_review + oss_review_swarm + Claude plugin"
```
