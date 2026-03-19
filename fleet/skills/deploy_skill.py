"""
Deploy skill — deploys a promoted skill into production fleet.

Copies the most recent draft from knowledge/code_drafts/ to skills/, validates
the module structure, optionally verifies the import, and logs the deployment.

Payload:
  skill_name      str   skill to deploy (required)
  source_path     str   override source dir (default: knowledge/code_drafts/)
  affinity_roles  list  roles that should receive this skill (optional, logged only)
  verify          bool  try to import the deployed module (default True)

Output: knowledge/deployments/{skill_name}_deployed_{date}.md
Returns: {status, skill_name, deployed_to, verification, affinity_note}
"""
import ast
import importlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

SKILL_NAME = "deploy_skill"
DESCRIPTION = "Deploy a promoted skill into production fleet — copies to skills/, updates affinity, runs verification"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
DRAFTS_DIR = FLEET_DIR / "knowledge" / "code_drafts"
DEPLOY_LOG_DIR = FLEET_DIR / "knowledge" / "deployments"


def _find_draft(skill_name: str, source_path: Path) -> Path | None:
    """Find the most recent draft file matching skill_name."""
    if not source_path.exists():
        return None
    safe = re.sub(r"[^a-z0-9_]", "_", skill_name.lower().replace(".py", ""))
    candidates = sorted(
        source_path.glob(f"{safe}_draft_*.py"),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    if candidates:
        return candidates[0]
    # Also try exact match
    exact = source_path / f"{safe}.py"
    if exact.exists():
        return exact
    return None


def _validate_skill(path: Path) -> tuple[bool, str]:
    """Validate the draft has SKILL_NAME constant and a run() function via AST."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Cannot read/parse file: {e}"

    has_skill_name = False
    has_run = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SKILL_NAME":
                    has_skill_name = True
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True

    issues = []
    if not has_skill_name:
        issues.append("missing SKILL_NAME constant")
    if not has_run:
        issues.append("missing run() function")

    if issues:
        return False, f"Validation failed: {', '.join(issues)}"
    return True, "Validation passed"


def run(payload, config):
    skill_name = payload.get("skill_name", "")
    if not skill_name:
        return json.dumps({"status": "failed", "error": "No skill_name provided"})

    source_path = Path(payload.get("source_path", str(DRAFTS_DIR)))
    affinity_roles = payload.get("affinity_roles", [])
    verify = payload.get("verify", True)

    # Step 1: Find the draft
    draft = _find_draft(skill_name, source_path)
    if not draft:
        return json.dumps({
            "status": "failed",
            "skill_name": skill_name,
            "error": f"No draft found for '{skill_name}' in {source_path}",
        })

    # Step 2: Validate structure
    valid, validation_msg = _validate_skill(draft)
    if not valid:
        return json.dumps({
            "status": "failed",
            "skill_name": skill_name,
            "error": validation_msg,
            "draft": str(draft),
        })

    # Step 3: Copy to skills/
    target = SKILLS_DIR / f"{skill_name}.py"
    try:
        shutil.copy2(draft, target)
    except Exception as e:
        return json.dumps({
            "status": "failed",
            "skill_name": skill_name,
            "error": f"Copy failed: {e}",
        })

    # Step 4: Affinity note
    affinity_note = ""
    if affinity_roles:
        affinity_note = f"Recommended for roles: {', '.join(affinity_roles)}"

    # Step 5: Verify import
    verification = "skipped"
    if verify:
        try:
            mod = importlib.import_module(f"skills.{skill_name}")
            importlib.reload(mod)
            verification = "import OK"
        except Exception as e:
            verification = f"import failed: {e}"

    # Step 6: Save deployment log
    try:
        DEPLOY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = DEPLOY_LOG_DIR / f"{skill_name}_deployed_{date_str}.md"
        log_file.write_text(
            f"# Deployment: `{skill_name}`\n\n"
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Source:** {draft}\n"
            f"**Deployed to:** {target}\n"
            f"**Validation:** {validation_msg}\n"
            f"**Verification:** {verification}\n"
            f"**Affinity:** {affinity_note or 'None'}\n"
        )
    except Exception:
        pass  # Log failure must not break deployment

    return json.dumps({
        "status": "deployed",
        "skill_name": skill_name,
        "deployed_to": str(target),
        "verification": verification,
        "affinity_note": affinity_note,
    })
