"""v0.49: Git repository management — agents can stage, commit, branch, diff."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

SKILL_NAME = "git_manager"
DESCRIPTION = "Manage git repositories — stage, commit, branch, diff, log"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent

# Branches that require explicit force flag for destructive ops
PROTECTED_BRANCHES = {"main", "master", "production", "release"}

# Commands that are never allowed
BANNED_COMMANDS = {"push --force", "reset --hard", "clean -f", "clean -fd"}


def _run_git(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command with safety timeout."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run(payload: dict, config: dict) -> str:
    """Git management operations: status, diff, log, branch, commit, stash, checkout."""
    action = payload.get("action", "status")
    repo_path = payload.get("path", str(FLEET_DIR.parent))

    # Verify the path is actually a git repo
    if not Path(repo_path).is_dir():
        return json.dumps({"error": f"Not a directory: {repo_path}"})

    actions = {
        "status": _git_status,
        "diff": _git_diff,
        "log": _git_log,
        "branch": _git_branch,
        "commit": _git_commit,
        "stash": _git_stash,
        "checkout": _git_checkout,
    }

    fn = actions.get(action)
    if not fn:
        return json.dumps({"error": f"Unknown action: {action}", "available": list(actions.keys())})
    return fn(repo_path, payload)


def _git_status(repo_path: str, payload: dict) -> str:
    """Return modified, untracked, and staged files."""
    try:
        result = _run_git(["status", "--porcelain"], repo_path)
        if result.returncode != 0:
            return json.dumps({"error": result.stderr.strip()})

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

        # Get current branch
        branch_result = _run_git(["branch", "--show-current"], repo_path)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

        return json.dumps({
            "branch": branch,
            "staged": staged,
            "modified": modified,
            "untracked": untracked,
            "clean": not (staged or modified or untracked),
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git status timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git_diff(repo_path: str, payload: dict) -> str:
    """Return diff output. --stat by default, full diff with payload.get('full')."""
    try:
        args = ["diff"]

        # Diff staged changes if requested
        if payload.get("staged"):
            args.append("--cached")

        # Specific files
        files = payload.get("files")
        if isinstance(files, list) and files:
            args.append("--")
            args.extend(files)

        # Full diff or stat summary
        if not payload.get("full"):
            stat_args = args.copy()
            stat_args.insert(1, "--stat")
            result = _run_git(stat_args, repo_path)
        else:
            result = _run_git(args, repo_path)

        if result.returncode != 0:
            return json.dumps({"error": result.stderr.strip()})

        output = result.stdout.strip()
        if not output:
            return json.dumps({"diff": "", "message": "No differences found"})

        # Truncate very large diffs to avoid blowing up context
        max_lines = payload.get("max_lines", 500)
        lines = output.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            output = "\n".join(lines[:max_lines])

        return json.dumps({
            "diff": output,
            "truncated": truncated,
            "total_lines": len(lines),
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git diff timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git_log(repo_path: str, payload: dict) -> str:
    """Return last N commits (default 10)."""
    try:
        count = min(payload.get("count", 10), 100)  # Cap at 100
        fmt = payload.get("format", "%H|%an|%ai|%s")
        args = ["log", f"-{count}", f"--format={fmt}"]

        # Optional path filter
        path_filter = payload.get("file")
        if path_filter:
            args.extend(["--", path_filter])

        result = _run_git(args, repo_path)
        if result.returncode != 0:
            return json.dumps({"error": result.stderr.strip()})

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

        return json.dumps({"commits": commits, "count": len(commits)})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git log timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git_branch(repo_path: str, payload: dict) -> str:
    """List branches or create a new one."""
    try:
        create = payload.get("create")
        if create:
            # Create and optionally switch to the new branch
            args = ["checkout", "-b", create] if payload.get("switch") else ["branch", create]
            result = _run_git(args, repo_path)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"created": create, "switched": bool(payload.get("switch"))})

        delete = payload.get("delete")
        if delete:
            if delete in PROTECTED_BRANCHES:
                return json.dumps({"error": f"Refusing to delete protected branch: {delete}"})
            # Safe delete only (not -D)
            result = _run_git(["branch", "-d", delete], repo_path)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"deleted": delete})

        # List branches
        result = _run_git(["branch", "-a", "--format=%(refname:short)|%(objectname:short)|%(upstream:short)"], repo_path)
        if result.returncode != 0:
            return json.dumps({"error": result.stderr.strip()})

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

        return json.dumps({"branches": branches, "current": current})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git branch timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git_commit(repo_path: str, payload: dict) -> str:
    """Stage specified files and commit. NEVER auto-adds all files."""
    try:
        files = payload.get("files")
        message = payload.get("message")

        if not message:
            return json.dumps({"error": "Commit message required (payload.message)"})

        if not files:
            return json.dumps({
                "error": "Explicit file list required (payload.files). "
                         "This skill never auto-stages all files. "
                         "Use action='status' to see what's changed."
            })

        if not isinstance(files, list):
            return json.dumps({"error": "payload.files must be a list of file paths"})

        # Stage each file individually
        stage_errors = []
        staged = []
        for f in files:
            result = _run_git(["add", "--", f], repo_path)
            if result.returncode != 0:
                stage_errors.append({"file": f, "error": result.stderr.strip()})
            else:
                staged.append(f)

        if not staged:
            return json.dumps({"error": "No files staged successfully", "details": stage_errors})

        # Commit
        result = _run_git(["commit", "-m", message], repo_path)
        if result.returncode != 0:
            return json.dumps({"error": result.stderr.strip(), "stage_errors": stage_errors})

        # Get the new commit hash
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
        return json.dumps(resp)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git commit timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git_stash(repo_path: str, payload: dict) -> str:
    """Stash, pop, or list stashed changes."""
    try:
        sub = payload.get("sub", "list")

        if sub == "push" or sub == "save":
            args = ["stash", "push"]
            stash_message = payload.get("message")
            if stash_message:
                args.extend(["-m", stash_message])
            # Optionally stash specific files
            stash_files = payload.get("files")
            if isinstance(stash_files, list) and stash_files:
                args.append("--")
                args.extend(stash_files)
            result = _run_git(args, repo_path)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"status": "stashed", "output": result.stdout.strip()})

        elif sub == "pop":
            index = payload.get("index", 0)
            result = _run_git(["stash", "pop", f"stash@{{{index}}}"], repo_path)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"status": "popped", "index": index, "output": result.stdout.strip()})

        elif sub == "drop":
            index = payload.get("index", 0)
            if not payload.get("confirm"):
                return json.dumps({"error": "Destructive op: set payload.confirm=true to drop stash"})
            result = _run_git(["stash", "drop", f"stash@{{{index}}}"], repo_path)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"status": "dropped", "index": index})

        else:  # list
            result = _run_git(["stash", "list"], repo_path)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            entries = result.stdout.strip().splitlines() if result.stdout.strip() else []
            return json.dumps({"stashes": entries, "count": len(entries)})

    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git stash timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git_checkout(repo_path: str, payload: dict) -> str:
    """Switch branches. Refuses main/master without explicit force flag."""
    try:
        target = payload.get("target")
        if not target:
            return json.dumps({"error": "Checkout target required (payload.target)"})

        # Safety: refuse protected branches unless force flag is set
        if target in PROTECTED_BRANCHES and not payload.get("force"):
            return json.dumps({
                "error": f"Refusing to checkout protected branch '{target}' without force flag. "
                         f"Set payload.force=true to override.",
                "protected_branches": list(PROTECTED_BRANCHES),
            })

        # Check for uncommitted changes that would be lost
        status_result = _run_git(["status", "--porcelain"], repo_path)
        if status_result.returncode == 0 and status_result.stdout.strip():
            if not payload.get("force"):
                return json.dumps({
                    "error": "Uncommitted changes detected. Commit or stash first, "
                             "or set payload.force=true to discard.",
                    "hint": "Use action='stash' sub='push' to save changes",
                })

        result = _run_git(["checkout", target], repo_path)
        if result.returncode != 0:
            return json.dumps({"error": result.stderr.strip()})

        return json.dumps({
            "status": "switched",
            "branch": target,
            "output": result.stderr.strip() or result.stdout.strip(),
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "git checkout timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})
