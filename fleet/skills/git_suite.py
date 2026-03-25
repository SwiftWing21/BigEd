"""
git_suite.py — Consolidated Git + GitHub skill suite.

Merges git_manager, github_sync, and github_interact into a single skill.
Canonical helpers: _run_git(), _github_headers(), _github_api().
"""
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

# ── Skill metadata ────────────────────────────────────────────────────────────

SKILL_NAME = "git_suite"
DESCRIPTION = "Git and GitHub operations: status, diff, commit, issues, PRs, sync"
VERSION = "1.0.0"
REQUIRES_NETWORK = True   # GitHub sub-actions need network; git-only actions still work offline
COMPLEXITY = "medium"
SUITE = "git"

# ── Constants ─────────────────────────────────────────────────────────────────

FLEET_DIR = Path(__file__).parent.parent
GITHUB_API = "https://api.github.com"
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")

# Branches requiring explicit force for destructive ops
PROTECTED_BRANCHES = {"main", "master", "production", "release"}

# Commands that are never allowed regardless of payload
BANNED_COMMANDS = {"push --force", "reset --hard", "clean -f", "clean -fd"}

# ── Canonical shared helpers ──────────────────────────────────────────────────


def _run_git(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command with safety timeout."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _github_headers() -> dict:
    """Build GitHub auth + accept headers from GITHUB_TOKEN env var."""
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "BigEd-Fleet"}
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _github_api(method: str, path: str, body: dict | None = None) -> dict:
    """Generic GitHub API call with rate-limit awareness.

    Returns {"data": ..., "rate_remaining": int} on success,
    {"error": str} on failure.
    """
    import urllib.request
    import urllib.error

    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode() if body else None
    headers = _github_headers()
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


def _require_token() -> dict | None:
    """Return an error dict if GITHUB_TOKEN is missing, else None."""
    if not os.environ.get("GITHUB_TOKEN"):
        return {
            "status": "error",
            "error": "GITHUB_TOKEN not set. Add: export GITHUB_TOKEN='ghp_...' to ~/.secrets",
        }
    return None


# ── Git local actions ─────────────────────────────────────────────────────────


def _git_status(payload: dict, _config: dict) -> dict:
    """Return modified, untracked, and staged files."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        result = _run_git(["status", "--porcelain"], repo_path)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip()}

        staged, modified, untracked = [], [], []
        for line in result.stdout.splitlines():
            if len(line) < 3:
                continue
            index_status = line[0]
            worktree_status = line[1]
            filepath = line[3:]
            if index_status in "MADRC":
                staged.append(filepath)
            if worktree_status in "MD":
                modified.append(filepath)
            if index_status == "?" and worktree_status == "?":
                untracked.append(filepath)

        branch_result = _run_git(["branch", "--show-current"], repo_path)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

        return {
            "status": "ok",
            "branch": branch,
            "staged": staged,
            "modified": modified,
            "untracked": untracked,
            "clean": not (staged or modified or untracked),
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git status timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _git_diff(payload: dict, _config: dict) -> dict:
    """Return diff output. --stat by default, full diff with payload.get('full')."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        args = ["diff"]
        if payload.get("staged"):
            args.append("--cached")
        files = payload.get("files")
        if isinstance(files, list) and files:
            args.append("--")
            args.extend(files)
        if not payload.get("full"):
            stat_args = args.copy()
            stat_args.insert(1, "--stat")
            result = _run_git(stat_args, repo_path)
        else:
            result = _run_git(args, repo_path)

        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip()}

        output = result.stdout.strip()
        if not output:
            return {"status": "ok", "diff": "", "message": "No differences found"}

        max_lines = payload.get("max_lines", 500)
        lines = output.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            output = "\n".join(lines[:max_lines])

        return {
            "status": "ok",
            "diff": output,
            "truncated": truncated,
            "total_lines": len(lines),
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git diff timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _git_log(payload: dict, _config: dict) -> dict:
    """Return last N commits (default 10)."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        count = min(payload.get("count", 10), 100)
        fmt = payload.get("format", "%H|%an|%ai|%s")
        args = ["log", f"-{count}", f"--format={fmt}"]
        path_filter = payload.get("file")
        if path_filter:
            args.extend(["--", path_filter])

        result = _run_git(args, repo_path)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip()}

        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
            else:
                commits.append({"raw": line})

        return {"status": "ok", "commits": commits, "count": len(commits)}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git log timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _git_branch(payload: dict, _config: dict) -> dict:
    """List branches, create, or delete a branch."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        create = payload.get("create")
        if create:
            args = ["checkout", "-b", create] if payload.get("switch") else ["branch", create]
            result = _run_git(args, repo_path)
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            return {"status": "ok", "created": create, "switched": bool(payload.get("switch"))}

        delete = payload.get("delete")
        if delete:
            if delete in PROTECTED_BRANCHES:
                return {"status": "error", "error": f"Refusing to delete protected branch: {delete}"}
            result = _run_git(["branch", "-d", delete], repo_path)
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            return {"status": "ok", "deleted": delete}

        result = _run_git(
            ["branch", "-a", "--format=%(refname:short)|%(objectname:short)|%(upstream:short)"],
            repo_path,
        )
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip()}

        current_result = _run_git(["branch", "--show-current"], repo_path)
        current = current_result.stdout.strip() if current_result.returncode == 0 else ""

        branches = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 2)
            branches.append({
                "name": parts[0],
                "hash": parts[1] if len(parts) > 1 else "",
                "upstream": parts[2] if len(parts) > 2 else "",
                "current": parts[0] == current,
            })

        return {"status": "ok", "branches": branches, "current": current}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git branch timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _git_commit(payload: dict, _config: dict) -> dict:
    """Stage specified files and commit. NEVER auto-adds all files."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        files = payload.get("files")
        message = payload.get("message")

        if not message:
            return {"status": "error", "error": "Commit message required (payload.message)"}
        if not files:
            return {
                "status": "error",
                "error": (
                    "Explicit file list required (payload.files). "
                    "This skill never auto-stages all files. "
                    "Use action='status' to see what's changed."
                ),
            }
        if not isinstance(files, list):
            return {"status": "error", "error": "payload.files must be a list of file paths"}

        stage_errors = []
        staged = []
        for f in files:
            result = _run_git(["add", "--", f], repo_path)
            if result.returncode != 0:
                stage_errors.append({"file": f, "error": result.stderr.strip()})
            else:
                staged.append(f)

        if not staged:
            return {"status": "error", "error": "No files staged successfully", "details": stage_errors}

        result = _run_git(["commit", "-m", message], repo_path)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip(), "stage_errors": stage_errors}

        hash_result = _run_git(["rev-parse", "--short", "HEAD"], repo_path)
        commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else "unknown"

        resp = {
            "status": "committed",
            "hash": commit_hash,
            "message": message,
            "files_staged": staged,
            "timestamp": datetime.now().isoformat(),
        }
        if stage_errors:
            resp["stage_errors"] = stage_errors
        return resp
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git commit timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _git_stash(payload: dict, _config: dict) -> dict:
    """Stash, pop, drop, or list stashed changes."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        sub = payload.get("sub", "list")

        if sub in ("push", "save"):
            args = ["stash", "push"]
            stash_message = payload.get("message")
            if stash_message:
                args.extend(["-m", stash_message])
            stash_files = payload.get("files")
            if isinstance(stash_files, list) and stash_files:
                args.append("--")
                args.extend(stash_files)
            result = _run_git(args, repo_path)
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            return {"status": "stashed", "output": result.stdout.strip()}

        elif sub == "pop":
            index = payload.get("index", 0)
            result = _run_git(["stash", "pop", f"stash@{{{index}}}"], repo_path)
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            return {"status": "popped", "index": index, "output": result.stdout.strip()}

        elif sub == "drop":
            index = payload.get("index", 0)
            if not payload.get("confirm"):
                return {"status": "error", "error": "Destructive op: set payload.confirm=true to drop stash"}
            result = _run_git(["stash", "drop", f"stash@{{{index}}}"], repo_path)
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            return {"status": "dropped", "index": index}

        else:  # list
            result = _run_git(["stash", "list"], repo_path)
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            entries = result.stdout.strip().splitlines() if result.stdout.strip() else []
            return {"status": "ok", "stashes": entries, "count": len(entries)}

    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git stash timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _git_checkout(payload: dict, _config: dict) -> dict:
    """Switch branches. Refuses protected branches without explicit force flag."""
    repo_path = payload.get("path", str(FLEET_DIR.parent))
    if not Path(repo_path).is_dir():
        return {"status": "error", "error": f"Not a directory: {repo_path}"}
    try:
        target = payload.get("target")
        if not target:
            return {"status": "error", "error": "Checkout target required (payload.target)"}

        if target in PROTECTED_BRANCHES and not payload.get("force"):
            return {
                "status": "error",
                "error": (
                    f"Refusing to checkout protected branch '{target}' without force flag. "
                    "Set payload.force=true to override."
                ),
                "protected_branches": list(PROTECTED_BRANCHES),
            }

        status_result = _run_git(["status", "--porcelain"], repo_path)
        if status_result.returncode == 0 and status_result.stdout.strip():
            if not payload.get("force"):
                return {
                    "status": "error",
                    "error": (
                        "Uncommitted changes detected. Commit or stash first, "
                        "or set payload.force=true to discard."
                    ),
                    "hint": "Use action='stash' sub='push' to save changes",
                }

        result = _run_git(["checkout", target], repo_path)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip()}

        return {
            "status": "switched",
            "branch": target,
            "output": result.stderr.strip() or result.stdout.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git checkout timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── GitHub sync actions ───────────────────────────────────────────────────────


def _github_auth_status(payload: dict, _config: dict) -> dict:
    """Check if GITHUB_TOKEN is configured and valid."""
    import urllib.request

    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        try:
            req = urllib.request.Request(
                "https://api.github.com/user",
                headers=_github_headers(),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                user = json.loads(resp.read())
            return {
                "status": "ok",
                "authenticated": True,
                "user": user.get("login"),
                "name": user.get("name"),
            }
        except Exception:
            return {"status": "error", "authenticated": False, "reason": "Token invalid or expired"}
    return {"status": "error", "authenticated": False, "reason": "GITHUB_TOKEN not set"}


def _github_device_auth(payload: dict, _config: dict) -> dict:
    """Initiate GitHub Device Authorization Flow (OAuth).
    Returns a user_code for the operator to enter at github.com/login/device."""
    import urllib.request

    if not GITHUB_CLIENT_ID:
        return {
            "status": "error",
            "error": "GITHUB_CLIENT_ID not set. Add to ~/.secrets.",
            "setup": (
                "1. Create OAuth App at github.com/settings/developers\n"
                "2. Set Device Flow enabled\n"
                "3. Add: export GITHUB_CLIENT_ID='your_client_id' to ~/.secrets"
            ),
        }
    try:
        data = json.dumps({"client_id": GITHUB_CLIENT_ID, "scope": "repo"}).encode()
        req = urllib.request.Request(
            "https://github.com/login/device/code",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        return {
            "status": "awaiting_user",
            "user_code": result.get("user_code"),
            "verification_uri": result.get("verification_uri"),
            "instructions": (
                f"Go to {result.get('verification_uri')} and enter code: {result.get('user_code')}"
            ),
            "expires_in": result.get("expires_in", 900),
            "device_code": result.get("device_code"),
            "interval": result.get("interval", 5),
        }
    except Exception as e:
        return {"status": "error", "error": f"Device auth failed: {e}"}


def _github_clone(payload: dict, _config: dict) -> dict:
    """Clone a GitHub repo."""
    repo = payload.get("repo")
    path = payload.get("path")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'user/repo')"}
    target = Path(path) if path else FLEET_DIR.parent / "repos" / repo.split("/")[-1]
    target.parent.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
    try:
        result = subprocess.run(
            ["git", "clone", url, str(target)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return {"status": "cloned", "path": str(target)}
        return {"status": "error", "error": result.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _github_push(payload: dict, _config: dict) -> dict:
    """Stage, commit, and push changes in a repo."""
    path = payload.get("path")
    message = payload.get("message", "Fleet auto-commit")
    if not path:
        return {"status": "error", "error": "path required"}
    repo_path = Path(path)
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(repo_path), capture_output=True, timeout=30)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(repo_path), capture_output=True, text=True, timeout=30,
        )
        if "nothing to commit" in result.stdout:
            return {"status": "nothing_to_commit"}
        result = subprocess.run(
            ["git", "push"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return {"status": "pushed", "message": message}
        return {"status": "error", "error": result.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _github_backup(payload: dict, config: dict) -> dict:
    """Backup fleet knowledge + config to a GitHub repo."""
    import shutil

    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required for backup target"}

    backup_dir = FLEET_DIR.parent / "repos" / "fleet-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for src in ["fleet.toml", "knowledge", "data"]:
        src_path = FLEET_DIR / src
        dst_path = backup_dir / src
        if src_path.is_dir():
            if dst_path.exists():
                shutil.rmtree(dst_path)
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        elif src_path.exists():
            shutil.copy2(src_path, dst_path)

    return _github_push(
        {"path": str(backup_dir), "message": f"Fleet backup {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
        config,
    )


# ── GitHub interaction actions ────────────────────────────────────────────────


def _list_issues(payload: dict, _config: dict) -> dict:
    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'owner/repo')"}
    err = _require_token()
    if err:
        return err
    state = payload.get("state", "open")
    labels = payload.get("labels", "")
    per_page = min(int(payload.get("per_page", 30)), 100)
    page = int(payload.get("page", 1))
    path = f"/repos/{repo}/issues?state={state}&per_page={per_page}&page={page}"
    if labels:
        path += f"&labels={labels}"
    result = _github_api("GET", path)
    if "error" in result:
        return {"status": "error", **result}
    issues = [
        {
            "number": i["number"],
            "title": i["title"],
            "state": i["state"],
            "labels": [lb["name"] for lb in i.get("labels", [])],
            "author": i.get("user", {}).get("login"),
            "created_at": i.get("created_at"),
            "comments": i.get("comments", 0),
            "is_pr": "pull_request" in i,
        }
        for i in result["data"]
    ]
    out = {"status": "ok", "issues": issues, "count": len(issues), "rate_remaining": result.get("rate_remaining")}
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return out


def _create_issue(payload: dict, _config: dict) -> dict:
    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'owner/repo')"}
    err = _require_token()
    if err:
        return err
    title = payload.get("title")
    if not title:
        return {"status": "error", "error": "title required"}
    body = {"title": title, "body": payload.get("body", "")}
    if payload.get("labels"):
        body["labels"] = payload["labels"] if isinstance(payload["labels"], list) else [payload["labels"]]
    if payload.get("assignees"):
        body["assignees"] = payload["assignees"] if isinstance(payload["assignees"], list) else [payload["assignees"]]
    if payload.get("milestone"):
        body["milestone"] = int(payload["milestone"])
    result = _github_api("POST", f"/repos/{repo}/issues", body)
    if "error" in result:
        return {"status": "error", **result}
    d = result["data"]
    out = {
        "status": "ok",
        "created": True,
        "number": d["number"],
        "url": d["html_url"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return out


def _comment_issue(payload: dict, _config: dict) -> dict:
    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'owner/repo')"}
    err = _require_token()
    if err:
        return err
    number = payload.get("number")
    comment_body = payload.get("body") or payload.get("comment")
    if not number:
        return {"status": "error", "error": "number required (issue/PR number)"}
    if not comment_body:
        return {"status": "error", "error": "body required (comment text)"}
    result = _github_api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": comment_body})
    if "error" in result:
        return {"status": "error", **result}
    d = result["data"]
    out = {
        "status": "ok",
        "commented": True,
        "comment_id": d["id"],
        "url": d["html_url"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return out


def _close_issue(payload: dict, _config: dict) -> dict:
    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'owner/repo')"}
    err = _require_token()
    if err:
        return err
    number = payload.get("number")
    if not number:
        return {"status": "error", "error": "number required (issue/PR number)"}
    reason = payload.get("state_reason", "completed")
    result = _github_api("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed", "state_reason": reason})
    if "error" in result:
        return {"status": "error", **result}
    d = result["data"]
    out = {
        "status": "ok",
        "closed": True,
        "number": d["number"],
        "title": d["title"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return out


def _list_prs(payload: dict, _config: dict) -> dict:
    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'owner/repo')"}
    err = _require_token()
    if err:
        return err
    state = payload.get("state", "open")
    per_page = min(int(payload.get("per_page", 30)), 100)
    page = int(payload.get("page", 1))
    path = f"/repos/{repo}/pulls?state={state}&per_page={per_page}&page={page}"
    result = _github_api("GET", path)
    if "error" in result:
        return {"status": "error", **result}
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
    out = {"status": "ok", "pull_requests": prs, "count": len(prs), "rate_remaining": result.get("rate_remaining")}
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return out


def _create_pr(payload: dict, _config: dict) -> dict:
    repo = payload.get("repo")
    if not repo:
        return {"status": "error", "error": "repo required (e.g., 'owner/repo')"}
    err = _require_token()
    if err:
        return err
    title = payload.get("title")
    head = payload.get("head")
    base = payload.get("base", "main")
    if not title:
        return {"status": "error", "error": "title required"}
    if not head:
        return {"status": "error", "error": "head branch required"}
    body = {
        "title": title,
        "head": head,
        "base": base,
        "body": payload.get("body", ""),
        "draft": payload.get("draft", False),
    }
    result = _github_api("POST", f"/repos/{repo}/pulls", body)
    if "error" in result:
        return {"status": "error", **result}
    d = result["data"]
    out = {
        "status": "ok",
        "created": True,
        "number": d["number"],
        "url": d["html_url"],
        "head": d["head"]["ref"],
        "base": d["base"]["ref"],
        "rate_remaining": result.get("rate_remaining"),
    }
    if result.get("rate_warning"):
        out["rate_warning"] = result["rate_warning"]
    return out


# ── Entry point ───────────────────────────────────────────────────────────────

_ACTIONS = {
    # git local
    "status":      _git_status,
    "diff":        _git_diff,
    "log":         _git_log,
    "branch":      _git_branch,
    "commit":      _git_commit,
    "stash":       _git_stash,
    "checkout":    _git_checkout,
    # github sync
    "auth":        _github_device_auth,
    "auth_status": _github_auth_status,
    "clone":       _github_clone,
    "push":        _github_push,
    "backup":      _github_backup,
    # github interaction
    "list_issues":   _list_issues,
    "create_issue":  _create_issue,
    "comment_issue": _comment_issue,
    "close_issue":   _close_issue,
    "list_prs":      _list_prs,
    "create_pr":     _create_pr,
}


def run(payload: dict, config: dict) -> dict:
    """Dispatch to git/GitHub sub-action handlers."""
    from skills._dispatch import dispatch_action
    return dispatch_action(payload, config, _ACTIONS, default="status")
