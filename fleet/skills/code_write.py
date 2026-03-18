"""
Code write skill — invokes aider-chat to create or edit files using local Ollama.

All output goes to knowledge/code_writes/ as a staged workspace with git tracking.
Generated code is NEVER auto-deployed — coder agents review via code_review skill.

Payload:
  instructions  str   what to build or change (required)
  project_dir   str   working directory (default: knowledge/code_writes/workspace)
  files         list  specific files to edit (optional — aider auto-detects if omitted)
  create_files  list  new files to create before running aider (optional)
  read_only     list  files aider can see but not edit (optional, for context)
  model         str   override model (default: from fleet.toml local)

Returns:
  {"summary": str, "diff": str, "files_changed": list, "commit": str, "saved_to": str}
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
WRITES_DIR = KNOWLEDGE_DIR / "code_writes"
WORKSPACE = WRITES_DIR / "workspace"

AGENT_REPO = "git@github.com:SwiftWing21/biged-agent-vm.git"


def _git_env() -> dict:
    """Build env with GitHub PAT for HTTPS auth if SSH isn't available."""
    env = os.environ.copy()
    pat = os.environ.get("GITHUB_PAT", "")
    if pat:
        # Enable git credential via PAT for HTTPS remotes
        env["GIT_ASKPASS"] = "/bin/true"
        env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _ensure_workspace(project_dir: Path):
    """Ensure the project dir exists and has a git repo (aider requires one)."""
    project_dir.mkdir(parents=True, exist_ok=True)
    git_dir = project_dir / ".git"
    if not git_dir.exists():
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=str(project_dir),
            capture_output=True, timeout=10,
        )
        # Initial commit so aider has a baseline
        subprocess.run(
            ["git", "add", "-A"], cwd=str(project_dir),
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init workspace"],
            cwd=str(project_dir), capture_output=True, timeout=10,
        )
    # Ensure remote is set
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=str(project_dir),
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0:
        # Use HTTPS with PAT if available, otherwise SSH
        pat = os.environ.get("GITHUB_PAT", "")
        if pat:
            remote = f"https://{pat}@github.com/SwiftWing21/biged-agent-vm.git"
        else:
            remote = AGENT_REPO
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(project_dir), capture_output=True, timeout=5,
        )


def _create_files(project_dir: Path, create_files: list):
    """Create stub files before invoking aider."""
    for f in create_files:
        p = project_dir / f
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(f"# {f}\n# Auto-created for aider code generation\n")


def _get_diff(project_dir: Path) -> str:
    """Get the diff of uncommitted + last commit changes."""
    r = subprocess.run(
        ["git", "diff", "HEAD~1", "--stat"],
        cwd=str(project_dir), capture_output=True, text=True, timeout=10,
    )
    stat = r.stdout.strip()
    r2 = subprocess.run(
        ["git", "diff", "HEAD~1"],
        cwd=str(project_dir), capture_output=True, text=True, timeout=10,
    )
    full = r2.stdout.strip()
    # Cap diff size for the result payload
    if len(full) > 4000:
        full = full[:4000] + "\n... (truncated)"
    return stat, full


def _get_changed_files(project_dir: Path) -> list:
    """List files changed in the last commit."""
    r = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only"],
        cwd=str(project_dir), capture_output=True, text=True, timeout=10,
    )
    return [f for f in r.stdout.strip().splitlines() if f]


def run(payload, config):
    instructions = payload.get("instructions", "")
    if not instructions:
        return {"error": "No instructions provided"}

    project_dir = Path(payload.get("project_dir", str(WORKSPACE)))
    files = payload.get("files", [])
    create_files = payload.get("create_files", [])
    read_only = payload.get("read_only", [])
    model_override = payload.get("model", "")

    ollama_host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    model = model_override or config.get("models", {}).get("local", "qwen3:8b")

    # Ensure workspace
    _ensure_workspace(project_dir)

    # Create any new files
    if create_files:
        _create_files(project_dir, create_files)

    # Build aider command
    cmd = [
        sys.executable, "-m", "aider",
        "--model", f"ollama_chat/{model}",
        "--no-auto-commits",
        "--yes-always",
        "--no-suggest-shell-commands",
        "--no-pretty",
        "--no-stream",
        "--message", instructions,
    ]

    # Add files to edit
    for f in files:
        cmd.extend(["--file", f])

    # Add read-only context files
    for f in read_only:
        cmd.extend(["--read", f])

    env = os.environ.copy()
    env["OLLAMA_API_BASE"] = ollama_host

    # Run aider
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
            env=env,
        )
        aider_output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return {"error": "aider timed out after 10 minutes"}
    except Exception as e:
        return {"error": f"aider failed to run: {e}"}

    # Commit aider's changes
    subprocess.run(
        ["git", "add", "-A"], cwd=str(project_dir),
        capture_output=True, timeout=10,
    )

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"code_write: {instructions[:80]}"
    commit_result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=str(project_dir), capture_output=True, text=True, timeout=10,
    )

    if "nothing to commit" in commit_result.stdout:
        return {
            "summary": "aider ran but made no changes",
            "aider_output": aider_output[:2000],
            "files_changed": [],
            "saved_to": str(project_dir),
        }

    # Gather results
    changed = _get_changed_files(project_dir)
    stat, diff = _get_diff(project_dir)

    # Save a log of what happened
    log_dir = WRITES_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"code_write_{ts}.md"
    log_file.write_text(
        f"# Code Write — {timestamp}\n\n"
        f"## Instructions\n{instructions}\n\n"
        f"## Files Changed\n" + "\n".join(f"- {f}" for f in changed) + "\n\n"
        f"## Diff\n```\n{diff}\n```\n\n"
        f"## Aider Output\n```\n{aider_output[:3000]}\n```\n"
    )

    return {
        "summary": f"Changed {len(changed)} file(s): {', '.join(changed[:5])}",
        "diff_stat": stat,
        "diff": diff[:2000],
        "files_changed": changed,
        "commit": commit_msg,
        "saved_to": str(project_dir),
        "log": str(log_file),
    }
