"""
Branch manager — creates and manages product branches on BigEds_Agents repo.

Each "ed" variant gets its own branch with a curated subset of skills + config.

Product branches:
  product/edd        — lightweight single-agent variant
  product/eddie      — customer-facing chatbot package
  product/small-ed   — minimal fleet (3 agents, no GPU)
  product/ed-security — security-focused fleet variant
  (custom)           — any product/<name> branch

Payload:
  action        str   "create" | "list" | "sync" | "package"
  product_name  str   e.g. "edd", "eddie", "small-ed" (required for create/sync/package)
  description   str   product description (for create)
  skills        list  skill names to include (for create, default: core set)
  base_branch   str   branch to fork from (default: "main")

Returns: {action, branch, status, details}
"""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from config import GITHUB_OWNER, GITHUB_REPO

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
WORKSPACE = KNOWLEDGE_DIR / "code_writes" / "workspace"

AGENTS_REPO = f"git@github.com:{GITHUB_OWNER}/{GITHUB_REPO}.git"
SKILL_NAME = "branch_manager"
DESCRIPTION = "Branch manager — creates and manages product branches on BigEds_Agents repo."

REQUIRES_NETWORK = True

# Core skills every product variant needs
CORE_SKILLS = [
    "_models.py",
    "summarize.py",
    "rag_query.py",
    "rag_index.py",
]

# Product presets
PRODUCT_PRESETS = {
    "edd": {
        "description": "Lightweight single-agent — research + summarize + RAG",
        "skills": CORE_SKILLS + ["web_search.py", "arxiv_fetch.py", "flashcard.py"],
        "config": {"workers": {"coder_count": 0}, "fleet": {"max_workers": 1}},
    },
    "eddie": {
        "description": "Customer-facing chatbot — Discord + RAG + knowledge base",
        "skills": CORE_SKILLS + ["web_search.py", "discuss.py"],
        "config": {"fleet": {"max_workers": 2, "discord_bot_enabled": True}},
    },
    "small-ed": {
        "description": "Minimal fleet — 3 agents, no GPU, eco mode only",
        "skills": CORE_SKILLS + ["web_search.py", "code_review.py", "flashcard.py", "lead_research.py"],
        "config": {"fleet": {"max_workers": 3, "eco_mode": True}, "workers": {"coder_count": 1}},
    },
    "ed-security": {
        "description": "Security-focused — audits, pen testing, advisory pipeline",
        "skills": CORE_SKILLS + ["security_audit.py", "security_apply.py", "pen_test.py",
                                  "web_search.py", "web_crawl.py", "code_review.py"],
        "config": {"fleet": {"max_workers": 4}, "workers": {"coder_count": 2}},
    },
}


def _git(args: list, cwd: str = None) -> tuple[int, str]:
    """Run a git command, return (returncode, output)."""
    env = os.environ.copy()
    pat = os.environ.get("GITHUB_PAT", "")
    if pat:
        env["GIT_ASKPASS"] = "/bin/true"
        env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd or str(WORKSPACE),
            capture_output=True, text=True, timeout=30, env=env,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _ensure_agents_repo():
    """Clone BigEds_Agents if not already present."""
    agents_dir = KNOWLEDGE_DIR / "biged_agents"
    if not (agents_dir / ".git").exists():
        agents_dir.mkdir(parents=True, exist_ok=True)
        pat = os.environ.get("GITHUB_PAT", "")
        if pat:
            remote = f"https://{pat}@github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
        else:
            remote = AGENTS_REPO
        code, out = _git(["clone", remote, str(agents_dir)], cwd=str(KNOWLEDGE_DIR))
        if code != 0:
            # Repo might be empty — init it
            agents_dir.mkdir(parents=True, exist_ok=True)
            _git(["init", "-b", "main"], cwd=str(agents_dir))
            _git(["remote", "add", "origin", remote], cwd=str(agents_dir))
            _git(["commit", "--allow-empty", "-m", "init BigEds_Agents"], cwd=str(agents_dir))
    return agents_dir


def _action_create(product_name: str, description: str, skills: list, base: str) -> dict:
    agents_dir = _ensure_agents_repo()
    branch = f"product/{product_name}"

    # Fetch latest
    _git(["fetch", "origin"], cwd=str(agents_dir))

    # Create branch from base
    _git(["checkout", "-B", branch, f"origin/{base}"], cwd=str(agents_dir))
    # If that fails (remote branch doesn't exist), branch from local
    code, _ = _git(["rev-parse", "--verify", branch], cwd=str(agents_dir))
    if code != 0:
        _git(["checkout", "-b", branch], cwd=str(agents_dir))

    # Create product manifest
    manifest = {
        "product": product_name,
        "description": description,
        "created": datetime.now().isoformat(),
        "skills": skills,
        "base_branch": base,
    }
    manifest_path = agents_dir / "product.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Create README
    readme = agents_dir / "README.md"
    readme.write_text(
        f"# BigEd: {product_name}\n\n"
        f"{description}\n\n"
        f"## Included Skills\n"
        + "\n".join(f"- `{s}`" for s in skills)
        + f"\n\n---\n*Generated by fleet branch_manager on {datetime.now().strftime('%Y-%m-%d')}*\n"
    )

    # Copy skills
    skills_dest = agents_dir / "skills"
    skills_dest.mkdir(exist_ok=True)
    src_skills = FLEET_DIR / "skills"
    for skill_file in skills:
        src = src_skills / skill_file
        if src.exists():
            (skills_dest / skill_file).write_text(src.read_text())

    # Copy core fleet files
    for core_file in ["db.py", "config.py", "worker.py", "rag.py"]:
        src = FLEET_DIR / core_file
        if src.exists():
            (agents_dir / core_file).write_text(src.read_text())

    # Commit
    _git(["add", "-A"], cwd=str(agents_dir))
    _git(["commit", "-m", f"init product/{product_name}: {description[:60]}"], cwd=str(agents_dir))

    # Push
    code, out = _git(["push", "-u", "origin", branch], cwd=str(agents_dir))

    return {
        "action": "create",
        "branch": branch,
        "status": "created" if code == 0 else "created_local",
        "push_output": out[:300],
        "skills_included": skills,
        "manifest": str(manifest_path),
    }


def _action_list() -> dict:
    agents_dir = _ensure_agents_repo()
    _git(["fetch", "origin"], cwd=str(agents_dir))
    code, out = _git(["branch", "-r", "--list", "origin/product/*"], cwd=str(agents_dir))
    branches = [b.strip() for b in out.splitlines() if b.strip()]
    # Also check local branches
    code2, out2 = _git(["branch", "--list", "product/*"], cwd=str(agents_dir))
    local = [b.strip() for b in out2.splitlines() if b.strip()]
    return {
        "action": "list",
        "remote_branches": branches,
        "local_branches": local,
        "status": "ok",
    }


def _action_sync(product_name: str) -> dict:
    """Sync skills from main fleet into a product branch."""
    agents_dir = _ensure_agents_repo()
    branch = f"product/{product_name}"

    _git(["checkout", branch], cwd=str(agents_dir))

    # Read manifest to know which skills belong
    manifest_path = agents_dir / "product.json"
    if not manifest_path.exists():
        return {"error": f"No product.json on branch {branch}", "action": "sync"}

    manifest = json.loads(manifest_path.read_text())
    skills = manifest.get("skills", [])

    # Copy updated skills
    updated = []
    src_skills = FLEET_DIR / "skills"
    skills_dest = agents_dir / "skills"
    for skill_file in skills:
        src = src_skills / skill_file
        if src.exists():
            (skills_dest / skill_file).write_text(src.read_text())
            updated.append(skill_file)

    # Update core files
    for core_file in ["db.py", "config.py", "worker.py", "rag.py"]:
        src = FLEET_DIR / core_file
        if src.exists():
            (agents_dir / core_file).write_text(src.read_text())

    _git(["add", "-A"], cwd=str(agents_dir))
    code, _ = _git(["diff", "--cached", "--quiet"], cwd=str(agents_dir))
    if code != 0:
        _git(["commit", "-m", f"sync: update {len(updated)} skills from main fleet"], cwd=str(agents_dir))
        _git(["push", "origin", branch], cwd=str(agents_dir))

    return {
        "action": "sync",
        "branch": branch,
        "skills_synced": updated,
        "status": "synced",
    }


def run(payload, config):
    action = payload.get("action", "list")
    product_name = payload.get("product_name", "")
    description = payload.get("description", "")
    skills = payload.get("skills", [])
    base = payload.get("base_branch", "main")

    if action == "list":
        return _action_list()

    if not product_name:
        return {"error": "product_name required", "action": action}

    # Apply preset if available
    preset = PRODUCT_PRESETS.get(product_name, {})
    if not description:
        description = preset.get("description", f"BigEd variant: {product_name}")
    if not skills:
        skills = preset.get("skills", CORE_SKILLS)

    if action == "create":
        return _action_create(product_name, description, skills, base)
    elif action == "sync":
        return _action_sync(product_name)
    else:
        return {"error": f"Unknown action: {action}", "action": action}