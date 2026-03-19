"""
Skill promote — moves a tested+reviewed draft from code_drafts/ into skills/.

Gate checks before promotion:
  1. Draft must exist in knowledge/code_drafts/
  2. Must have a passing test report (_test_*PASS* or _test_*.md with "PASS")
  3. Must have at least one review (code_write_review or manual)

Payload:
  draft_name    str   draft filename (e.g. "email_outreach_draft_20260318_coder_1.py")
                      or skill name (e.g. "email_outreach") — finds most recent
  force         bool  skip gate checks (default false, use with caution)
  skill_name    str   override the deployed skill filename (default: inferred from draft)

Returns: {promoted, skill_path, gates_passed, warnings}
"""
import re
import shutil
from datetime import datetime
from pathlib import Path

SKILL_NAME = "skill_promote"
DESCRIPTION = "Skill promote — moves a tested+reviewed draft from code_drafts/ into skills/."

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
DRAFTS_DIR = FLEET_DIR / "knowledge" / "code_drafts"
REVIEWS_DIR = FLEET_DIR / "knowledge" / "code_writes" / "reviews"


def _find_draft(name: str) -> Path | None:
    """Find the draft file by exact name or skill name pattern."""
    if not DRAFTS_DIR.exists():
        return None
    # Exact match
    exact = DRAFTS_DIR / name
    if exact.exists():
        return exact
    # Pattern match — find most recent draft for this skill name
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower().replace(".py", ""))
    candidates = sorted(
        DRAFTS_DIR.glob(f"{safe}_draft_*.py"),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    return candidates[0] if candidates else None


def _check_test_passed(draft_stem: str) -> tuple[bool, str]:
    """Check if there's a passing test report for this draft."""
    test_stem = draft_stem.replace("_draft_", "_test_")
    test_files = list(DRAFTS_DIR.glob(f"{test_stem}*.md"))
    if not test_files:
        # Also check with just the skill name prefix
        parts = draft_stem.split("_draft_")
        if parts:
            test_files = list(DRAFTS_DIR.glob(f"{parts[0]}_test_*.md"))
    if not test_files:
        return False, "No test report found — run skill_test first"
    # Check most recent test
    latest = sorted(test_files, key=lambda f: f.stat().st_mtime, reverse=True)[0]
    content = latest.read_text(errors="ignore")
    if "PASS" in content and "FAIL" not in content.split("PASS")[0]:
        return True, f"Test passed: {latest.name}"
    return False, f"Test failed: {latest.name}"


def _check_reviewed(draft_stem: str) -> tuple[bool, str]:
    """Check if there's at least one review for this draft's skill."""
    skill_name = draft_stem.split("_draft_")[0]
    # Check code_write reviews
    if REVIEWS_DIR.exists():
        reviews = list(REVIEWS_DIR.glob(f"*{skill_name}*"))
        if reviews:
            return True, f"Found {len(reviews)} review(s)"
    # Check code_drafts for review notes
    review_notes = list(DRAFTS_DIR.glob(f"{skill_name}_review_*.md"))
    if review_notes:
        return True, f"Found {len(review_notes)} review note(s)"
    return False, "No review found — run code_write_review first"


def _infer_skill_name(draft_path: Path, override: str) -> str:
    """Derive the deployed skill filename."""
    if override:
        name = override.replace(".py", "")
        return f"{name}.py"
    # Extract from draft name: foo_draft_20260318_coder_1.py -> foo.py
    stem = draft_path.stem
    match = re.match(r"^(.+?)_draft_", stem)
    if match:
        return f"{match.group(1)}.py"
    return draft_path.name


def run(payload, config):
    draft_name = payload.get("draft_name", "")
    force = payload.get("force", False)
    skill_override = payload.get("skill_name", "")

    if not draft_name:
        return {"error": "No draft_name provided", "promoted": False}

    draft = _find_draft(draft_name)
    if not draft:
        return {"error": f"Draft not found: {draft_name}", "promoted": False}

    gates = {}
    warnings = []

    # Gate 1: Test passed
    test_ok, test_msg = _check_test_passed(draft.stem)
    gates["test_passed"] = test_ok
    if not test_ok:
        warnings.append(test_msg)

    # Gate 2: Reviewed
    review_ok, review_msg = _check_reviewed(draft.stem)
    gates["reviewed"] = review_ok
    if not review_ok:
        warnings.append(review_msg)

    # Gate 3: No existing skill with same name (unless force)
    target_name = _infer_skill_name(draft, skill_override)
    target_path = SKILLS_DIR / target_name
    if target_path.exists():
        gates["no_conflict"] = False
        warnings.append(f"Existing skill will be overwritten: {target_name}")
    else:
        gates["no_conflict"] = True

    # Check gates
    all_passed = all(gates.values())
    if not all_passed and not force:
        return {
            "promoted": False,
            "gates": gates,
            "warnings": warnings,
            "draft": str(draft),
            "message": "Gates failed — use force=true to override",
        }

    # Promote: copy draft to skills/
    shutil.copy2(draft, target_path)

    # Log the promotion
    log_file = DRAFTS_DIR / f"{draft.stem}_promoted_{datetime.now().strftime('%Y%m%d')}.md"
    log_file.write_text(
        f"# Skill Promoted\n"
        f"**Draft:** {draft.name}\n"
        f"**Deployed as:** {target_name}\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Force:** {force}\n"
        f"**Gates:** {gates}\n"
        f"**Warnings:** {warnings}\n"
    )

    return {
        "promoted": True,
        "skill_path": str(target_path),
        "skill_name": target_name,
        "gates": gates,
        "warnings": warnings,
        "saved_to": str(log_file),
    }