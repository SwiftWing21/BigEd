"""v0.49: GitHub Issues & PR interaction — list, create, comment, close."""
import json
import os
import urllib.request
from pathlib import Path

SKILL_NAME = "github_interact"
DESCRIPTION = "Interact with GitHub Issues and Pull Requests via API"
REQUIRES_NETWORK = True

GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    """Build auth + accept headers using GITHUB_TOKEN from env."""
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "BigEd-Fleet"}
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _api(method: str, path: str, body: dict | None = None) -> dict:
    """Generic GitHub API call with rate-limit awareness.

    Returns {"data": ..., "rate_remaining": int} on success,
    {"error": str} on failure.
    """
    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode() if body else None
    headers = _headers()
    if body:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rate_remaining = int(resp.headers.get("X-RateLimit-Remaining", -1))
            raw = resp.read()
            payload = json.loads(raw) if raw else {}
            result = {"data": payload, "rate_remaining": rate_remaining}
            if rate_remaining != -1 and rate_remaining < 50:
                result["rate_warning"] = (
                    f"Low rate limit: {rate_remaining} requests remaining"
                )
            return result
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode()
        except Exception:
            pass
        rate_remaining = int(exc.headers.get("X-RateLimit-Remaining", -1))
        if exc.code == 403 and rate_remaining == 0:
            reset = exc.headers.get("X-RateLimit-Reset", "unknown")
            return {"error": f"Rate limited. Resets at epoch {reset}."}
        return {"error": f"HTTP {exc.code}: {error_body or exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


# ---- actions ---------------------------------------------------------------

def _list_issues(repo: str, payload: dict) -> str:
    state = payload.get("state", "open")
    labels = payload.get("labels", "")
    per_page = min(int(payload.get("per_page", 30)), 100)
    page = int(payload.get("page", 1))
    path = f"/repos/{repo}/issues?state={state}&per_page={per_page}&page={page}"
    if labels:
        path += f"&labels={labels}"
    result = _api("GET", path)
    if "error" in result:
        return json.dumps(result)
    issues = [
        {
            "number": i["number"],
            "title": i["title"],
            "state": i["state"],
            "labels": [l["name"] for l in i.get("labels", [])],
            "author": i.get("user", {}).get("login"),
            "created_at": i.get("created_at"),
            "comments": i.get("comments", 0),
            "is_pr": "pull_request" in i,
        }
        for i in result["data"]
    ]
    out = {"issues": issues, "count": len(issues), "rate_remaining": result.get("rate_remaining")}
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return json.dumps(out)


def _create_issue(repo: str, payload: dict) -> str:
    title = payload.get("title")
    if not title:
        return json.dumps({"error": "title required"})
    body = {
        "title": title,
        "body": payload.get("body", ""),
    }
    if payload.get("labels"):
        body["labels"] = payload["labels"] if isinstance(payload["labels"], list) else [payload["labels"]]
    if payload.get("assignees"):
        body["assignees"] = payload["assignees"] if isinstance(payload["assignees"], list) else [payload["assignees"]]
    if payload.get("milestone"):
        body["milestone"] = int(payload["milestone"])
    result = _api("POST", f"/repos/{repo}/issues", body)
    if "error" in result:
        return json.dumps(result)
    d = result["data"]
    out = {
        "created": True,
        "number": d["number"],
        "url": d["html_url"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return json.dumps(out)


def _comment_issue(repo: str, payload: dict) -> str:
    number = payload.get("number")
    comment_body = payload.get("body") or payload.get("comment")
    if not number:
        return json.dumps({"error": "number required (issue/PR number)"})
    if not comment_body:
        return json.dumps({"error": "body required (comment text)"})
    result = _api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": comment_body})
    if "error" in result:
        return json.dumps(result)
    d = result["data"]
    out = {
        "commented": True,
        "comment_id": d["id"],
        "url": d["html_url"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return json.dumps(out)


def _close_issue(repo: str, payload: dict) -> str:
    number = payload.get("number")
    if not number:
        return json.dumps({"error": "number required (issue/PR number)"})
    reason = payload.get("state_reason", "completed")  # "completed" or "not_planned"
    result = _api("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed", "state_reason": reason})
    if "error" in result:
        return json.dumps(result)
    d = result["data"]
    out = {
        "closed": True,
        "number": d["number"],
        "title": d["title"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return json.dumps(out)


def _list_prs(repo: str, payload: dict) -> str:
    state = payload.get("state", "open")
    per_page = min(int(payload.get("per_page", 30)), 100)
    page = int(payload.get("page", 1))
    path = f"/repos/{repo}/pulls?state={state}&per_page={per_page}&page={page}"
    result = _api("GET", path)
    if "error" in result:
        return json.dumps(result)
    prs = [
        {
            "number": p["number"],
            "title": p["title"],
            "state": p["state"],
            "author": p.get("user", {}).get("login"),
            "head": p.get("head", {}).get("ref"),
            "base": p.get("base", {}).get("ref"),
            "created_at": p.get("created_at"),
            "draft": p.get("draft", False),
            "mergeable_state": p.get("mergeable_state"),
        }
        for p in result["data"]
    ]
    out = {"pull_requests": prs, "count": len(prs), "rate_remaining": result.get("rate_remaining")}
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return json.dumps(out)


def _create_pr(repo: str, payload: dict) -> str:
    title = payload.get("title")
    head = payload.get("head")
    base = payload.get("base", "main")
    if not title:
        return json.dumps({"error": "title required"})
    if not head:
        return json.dumps({"error": "head branch required"})
    body = {
        "title": title,
        "head": head,
        "base": base,
        "body": payload.get("body", ""),
        "draft": payload.get("draft", False),
    }
    result = _api("POST", f"/repos/{repo}/pulls", body)
    if "error" in result:
        return json.dumps(result)
    d = result["data"]
    out = {
        "created": True,
        "number": d["number"],
        "url": d["html_url"],
        "head": d["head"]["ref"],
        "base": d["base"]["ref"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return json.dumps(out)


# ---- entry point -----------------------------------------------------------

def run(payload: dict, config: dict) -> str:
    """Dispatch GitHub interaction actions."""
    action = payload.get("action", "list_issues")
    repo = payload.get("repo")  # "owner/repo" format
    if not repo:
        return json.dumps({"error": "repo required (e.g., 'owner/repo')"})

    if not os.environ.get("GITHUB_TOKEN"):
        return json.dumps({
            "error": "GITHUB_TOKEN not set. Add: export GITHUB_TOKEN='ghp_...' to ~/.secrets",
        })

    actions = {
        "list_issues": _list_issues,
        "create_issue": _create_issue,
        "comment_issue": _comment_issue,
        "close_issue": _close_issue,
        "list_prs": _list_prs,
        "create_pr": _create_pr,
    }
    fn = actions.get(action)
    if not fn:
        return json.dumps({"error": f"Unknown action: {action}. Valid: {', '.join(actions)}"})
    return fn(repo, payload)
