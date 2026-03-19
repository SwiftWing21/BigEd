"""
v0.46: GitHub Device Authorization Flow + repo sync.
Enables OAuth device flow for GitHub authentication without PATs.
Agents can autonomously provision repos, push code, and back up fleet state.
"""
import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SKILL_NAME = "github_sync"
DESCRIPTION = "GitHub OAuth device flow authentication and repository sync"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")  # Set in ~/.secrets


def run(payload: dict, config: dict) -> str:
    """GitHub sync operations: auth, clone, push, backup."""
    action = payload.get("action", "status")

    if action == "auth":
        return _device_auth_flow()
    elif action == "clone":
        return _clone_repo(payload.get("repo"), payload.get("path"))
    elif action == "push":
        return _push_changes(payload.get("path"), payload.get("message", "Fleet auto-commit"))
    elif action == "backup":
        return _backup_fleet_state(payload.get("repo"))
    elif action == "status":
        return _check_auth_status()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _device_auth_flow() -> str:
    """Initiate GitHub Device Authorization Flow (OAuth).
    Returns a user_code for the operator to enter at github.com/login/device."""
    if not GITHUB_CLIENT_ID:
        return json.dumps({
            "error": "GITHUB_CLIENT_ID not set. Add to ~/.secrets.",
            "setup": "1. Create OAuth App at github.com/settings/developers\n"
                     "2. Set Device Flow enabled\n"
                     "3. Add: export GITHUB_CLIENT_ID='your_client_id' to ~/.secrets"
        })

    try:
        # Step 1: Request device code
        data = json.dumps({
            "client_id": GITHUB_CLIENT_ID,
            "scope": "repo"
        }).encode()
        req = urllib.request.Request(
            "https://github.com/login/device/code",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        device_code = result.get("device_code")
        user_code = result.get("user_code")
        verification_uri = result.get("verification_uri")
        expires_in = result.get("expires_in", 900)
        interval = result.get("interval", 5)

        return json.dumps({
            "status": "awaiting_user",
            "user_code": user_code,
            "verification_uri": verification_uri,
            "instructions": f"Go to {verification_uri} and enter code: {user_code}",
            "expires_in": expires_in,
            "device_code": device_code,
            "interval": interval,
        })
    except Exception as e:
        return json.dumps({"error": f"Device auth failed: {e}"})


def _poll_for_token(device_code: str, interval: int = 5, timeout: int = 300) -> str | None:
    """Poll GitHub for access token after user authorizes."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            data = json.dumps({
                "client_id": GITHUB_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            }).encode()
            req = urllib.request.Request(
                "https://github.com/login/oauth/access_token",
                data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())

            if "access_token" in result:
                # Store token securely
                _store_token(result["access_token"])
                return result["access_token"]

            error = result.get("error")
            if error == "authorization_pending":
                time.sleep(interval)
                continue
            elif error == "slow_down":
                interval += 5
                time.sleep(interval)
                continue
            else:
                return None
        except Exception:
            time.sleep(interval)
    return None


def _store_token(token: str):
    """Store GitHub token in ~/.secrets."""
    secrets_file = Path.home() / ".secrets"
    lines = []
    if secrets_file.exists():
        lines = secrets_file.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not l.startswith("export GITHUB_TOKEN=")]
    lines.append(f"export GITHUB_TOKEN='{token}'")
    tmp = secrets_file.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(secrets_file)


def _check_auth_status() -> str:
    """Check if GitHub token is configured."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        # Verify token works
        try:
            req = urllib.request.Request(
                "https://api.github.com/user",
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                user = json.loads(resp.read())
            return json.dumps({
                "authenticated": True,
                "user": user.get("login"),
                "name": user.get("name"),
            })
        except Exception:
            return json.dumps({"authenticated": False, "reason": "Token invalid or expired"})
    return json.dumps({"authenticated": False, "reason": "GITHUB_TOKEN not set"})


def _clone_repo(repo: str, path: str = None) -> str:
    """Clone a GitHub repo."""
    import subprocess
    if not repo:
        return json.dumps({"error": "repo required (e.g., 'user/repo')"})
    target = Path(path) if path else FLEET_DIR.parent / "repos" / repo.split("/")[-1]
    target.parent.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
    try:
        result = subprocess.run(
            ["git", "clone", url, str(target)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return json.dumps({"status": "cloned", "path": str(target)})
        return json.dumps({"error": result.stderr.strip()})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _push_changes(path: str, message: str) -> str:
    """Stage, commit, and push changes in a repo."""
    import subprocess
    if not path:
        return json.dumps({"error": "path required"})
    repo_path = Path(path)
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(repo_path), capture_output=True, timeout=30)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(repo_path), capture_output=True, text=True, timeout=30
        )
        if "nothing to commit" in result.stdout:
            return json.dumps({"status": "nothing_to_commit"})
        result = subprocess.run(
            ["git", "push"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return json.dumps({"status": "pushed", "message": message})
        return json.dumps({"error": result.stderr.strip()})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _backup_fleet_state(repo: str = None) -> str:
    """Backup fleet knowledge + config to a GitHub repo."""
    import subprocess
    if not repo:
        return json.dumps({"error": "repo required for backup target"})

    backup_dir = FLEET_DIR.parent / "repos" / "fleet-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Copy key files
    import shutil
    for src in ["fleet.toml", "knowledge", "data"]:
        src_path = FLEET_DIR / src
        dst_path = backup_dir / src
        if src_path.is_dir():
            if dst_path.exists():
                shutil.rmtree(dst_path)
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        elif src_path.exists():
            shutil.copy2(src_path, dst_path)

    # Commit and push
    return _push_changes(str(backup_dir), f"Fleet backup {datetime.now().strftime('%Y-%m-%d %H:%M')}")
