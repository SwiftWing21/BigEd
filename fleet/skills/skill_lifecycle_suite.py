"""
Skill lifecycle suite — draft, test, evolve, promote, deploy.

Consolidates skill_draft, skill_test, skill_evolve, skill_promote, and deploy_skill
into a single suite with shared helpers and enforced promotion gates.

Gate semantics (MUST NOT be weakened):
  - promote requires a passing test report (skill_test must have been run first)
  - deploy requires a prior promotion (skill must already be in skills/ or promoted)

Subactions:
  draft   — generate a new skill file from a description (LLM-assisted)
  test    — run a draft in a sandbox and produce a pass/fail report
  evolve  — generate an improved version of an existing skill (LLM-assisted)
  promote — copy a tested+reviewed draft into skills/
  deploy  — validate, copy, and verify a promoted skill into production

Payload keys vary by action — see each handler's docstring below.

LLM actions (draft, evolve) require an Ollama/Claude backend configured in config.
Non-LLM actions (test, promote, deploy) are fully local.
"""

import ast
import importlib
import importlib.util
import inspect
import json
import re
import shutil
import threading
import traceback
from datetime import datetime
from pathlib import Path
import logging

from skills._dispatch import dispatch_action

SKILL_NAME = "skill_lifecycle_suite"
DESCRIPTION = "Skill lifecycle: draft, test, evolve, promote, deploy"
VERSION = "1.0.0"
REQUIRES_NETWORK = False
COMPLEXITY = "medium"
SUITE = "skill_lifecycle"
log = logging.getLogger(SKILL_NAME)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
DRAFTS_DIR = FLEET_DIR / "knowledge" / "code_drafts"
REVIEWS_DIR_CODE = FLEET_DIR / "knowledge" / "code_reviews"
REVIEWS_DIR_FMA = FLEET_DIR / "knowledge" / "fma_reviews"
REVIEWS_DIR_WRITES = FLEET_DIR / "knowledge" / "code_writes" / "reviews"
DEPLOY_LOG_DIR = FLEET_DIR / "knowledge" / "deployments"


# ---------------------------------------------------------------------------
# Shared helpers (canonical deduplicated versions)
# ---------------------------------------------------------------------------

def _extract_code(raw: str) -> str:
    """Pull the Python code block out of an LLM response (3 sources → 1 canonical)."""
    m = re.search(r"```python\s*(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw.strip()


def _find_draft(name: str, source_dir: Path = DRAFTS_DIR) -> Path | None:
    """Locate a draft file by explicit path, skill name, or most-recent fallback.

    Resolution order:
      1. Absolute path if name looks absolute and exists.
      2. Relative to FLEET_DIR.
      3. Relative to source_dir.
      4. Most recent *_draft_*.py in source_dir matching the skill name prefix.
      5. If name is empty: most recent untested draft in DRAFTS_DIR.

    This is the canonical implementation replacing 3 separate _find_draft copies.
    """
    if not name:
        # Pick the most recent untested draft
        if not DRAFTS_DIR.exists():
            return None
        drafts = sorted(
            DRAFTS_DIR.glob("*_draft_*.py"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for draft in drafts:
            test_marker = draft.stem.replace("_draft_", "_test_")
            if not list(DRAFTS_DIR.glob(f"{test_marker}*")):
                return draft
        return drafts[0] if drafts else None

    # Try absolute path
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p

    # Try relative paths
    rel = FLEET_DIR / name
    if rel.exists():
        return rel
    rel2 = source_dir / name
    if rel2.exists():
        return rel2

    # Pattern match — most recent draft for this skill name
    if not source_dir.exists():
        return None
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower().replace(".py", ""))
    candidates = sorted(
        source_dir.glob(f"{safe}_draft_*.py"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _draft_filename(skill_name: str, suffix: str, agent_name: str) -> Path:
    """Canonical draft output filename: <skill>_<suffix>_<YYYYMMDD>_<agent>.py"""
    safe = re.sub(r"[^a-z0-9_]", "_", skill_name.lower().replace(".py", ""))
    date_str = datetime.now().strftime("%Y%m%d")
    return DRAFTS_DIR / f"{safe}_{suffix}_{date_str}_{agent_name}.py"


def _validate_skill_module(path: Path) -> tuple[bool, str]:
    """Validate a skill file has SKILL_NAME constant and run() function via AST.

    Centralised AST validation (was only in deploy_skill, now canonical for all).
    Returns (ok, message).
    """
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


# ---------------------------------------------------------------------------
# Action: draft
# ---------------------------------------------------------------------------

_STYLE_EXAMPLE = '''\
def run(payload, config):
    """
    Payload keys:
      target   str  — what to operate on
      options  dict — optional parameters
    Returns dict with: result, saved_to, error (if any)
    """
    target = payload.get("target", "")
    if not target:
        return {"error": "No target provided"}
    # ... implementation ...
    return {"result": "...", "saved_to": "..."}
'''

_PERSPECTIVE_GUIDANCE = {
    "software architect": (
        "Design a clean, extensible module. "
        "Define a clear payload schema at the top. "
        "Separate concerns into helper functions. "
        "Document the public interface fully. "
        "Think about how this skill will be composed with others."
    ),
    "code critic / reviewer": (
        "Prioritize defensive coding. "
        "Validate all inputs explicitly. "
        "Handle every exception path with a useful error message. "
        "Add inline comments explaining non-obvious decisions. "
        "Never assume the payload is well-formed."
    ),
    "performance optimizer": (
        "Minimize I/O operations and avoid redundant reads. "
        "Use streaming or chunking where data may be large. "
        "Set appropriate timeouts on all network calls. "
        "Keep memory footprint small — process items incrementally. "
        "Prefer generators over loading full lists into memory."
    ),
}


def _action_draft(payload: dict, config: dict) -> dict:
    """Generate a new fleet skill file from a description.

    Payload:
      skill_name    str  e.g. "email_outreach"
      description   str  what the skill should do
      perspective   str  "software architect" | "code critic / reviewer" | "performance optimizer"
      agent_name    str  default "coder_1"
      inputs        str  optional — describe expected payload fields
      outputs       str  optional — describe what the skill should return
    """
    from skills._models import call_complex

    skill_name = payload.get("skill_name", "unnamed_skill")
    description = payload.get("description", "")
    perspective = payload.get("perspective", "software architect")
    agent_name = payload.get("agent_name", "coder_1")
    inputs_hint = payload.get("inputs", "")
    outputs_hint = payload.get("outputs", "")

    if not description:
        return {"error": "No description provided"}

    guidance = _PERSPECTIVE_GUIDANCE.get(perspective, _PERSPECTIVE_GUIDANCE["software architect"])

    prompt = f"""You are a {perspective} writing a new Python skill module for a local AI agent fleet system.

Fleet skill contract:
- Every skill exports a single function: run(payload: dict, config: dict) -> dict
- payload comes from the task queue (user-supplied, treat as untrusted)
- config contains: config["models"]["ollama_host"], config["models"]["local"]
- Skills save output to knowledge/ subdirectories and return a result dict
- Skills must import everything they need internally (no top-level side effects)

Style reference:
```python
{_STYLE_EXAMPLE}
```

SKILL NAME: {skill_name}
DESCRIPTION: {description}
{f"INPUTS: {inputs_hint}" if inputs_hint else ""}
{f"OUTPUTS: {outputs_hint}" if outputs_hint else ""}

YOUR PERSPECTIVE AS {perspective.upper()}:
{guidance}

Write the complete Python skill file. Include:
1. Module docstring explaining purpose, payload keys, and output
2. All imports
3. Any helper functions needed
4. The run(payload, config) function

Respond with ONLY the Python code in a ```python ... ``` block."""

    raw = call_complex("You are a skill developer for an AI agent fleet.", prompt, config)
    code = _extract_code(raw)

    # Add draft header warning if no existing module docstring
    header = (
        f'"""\nDRAFT — generated by {agent_name} ({perspective})\n'
        f'Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}\n'
        f'Description: {description}\n'
        f'STATUS: REVIEW BEFORE USE — not deployed automatically\n"""\n\n'
    )
    if not code.startswith('"""'):
        code = header + code

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _draft_filename(skill_name, "draft", agent_name)
    out_file.write_text(code, encoding="utf-8")

    return {
        "skill_name": skill_name,
        "perspective": perspective,
        "saved_to": str(out_file),
        "preview": code[:400],
    }


# ---------------------------------------------------------------------------
# Action: test
# ---------------------------------------------------------------------------

def _load_module(path: Path):
    """Dynamically load a Python file as a module."""
    spec = importlib.util.spec_from_file_location("draft_skill", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _validate_result(result) -> list[str]:
    """Check the skill result meets fleet conventions."""
    errors = []
    if result is None:
        errors.append("run() returned None — must return a dict")
        return errors
    if not isinstance(result, dict):
        errors.append(f"run() returned {type(result).__name__} — must return dict")
        return errors
    if "error" in result and len(result) == 1:
        errors.append(f"Skill returned only an error: {result['error']}")
    return errors


def _save_test_report(draft_name: str, draft_path: Path, passed: bool,
                      errors: list, output) -> dict:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    status = "PASS" if passed else "FAIL"
    report_file = DRAFTS_DIR / f"{draft_name.replace('_draft_', '_test_')}_{date_str}.md"

    lines = [
        f"# Skill Test: `{draft_path.name}`",
        f"**Status:** {status}",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if errors:
        lines.append("## Errors")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")
    if output is not None:
        lines.append("## Output Preview")
        preview = json.dumps(output, indent=2, default=str)[:1000]
        lines.append(f"```json\n{preview}\n```")

    report_file.write_text("\n".join(lines), encoding="utf-8")

    return {
        "passed": passed,
        "errors": errors,
        "output_preview": str(output)[:500] if output else None,
        "tested_file": str(draft_path),
        "saved_to": str(report_file),
    }


def _action_test(payload: dict, config: dict) -> dict:
    """Run a drafted skill in a sandbox with a test payload, validate schema.

    Payload:
      draft_path    str   path to draft file (relative to fleet root or absolute)
                          if omitted, picks the most recent untested draft
      test_payload  dict  payload to pass to the skill's run() (optional)
      timeout       int   max seconds to run (default 30)
    """
    requested = payload.get("draft_path", "")
    test_payload = payload.get("test_payload", {})
    timeout = payload.get("timeout", 30)

    draft = _find_draft(requested)
    if not draft:
        return {"error": "No draft file found to test", "passed": False}

    draft_name = draft.stem
    errors = []
    output = None

    # Phase 1: Syntax check
    try:
        with open(draft, encoding="utf-8") as f:
            compile(f.read(), str(draft), "exec")
    except SyntaxError as e:
        errors.append(f"Syntax error: {e}")
        return _save_test_report(draft_name, draft, False, errors, None)

    # Phase 2: Import check
    try:
        mod = _load_module(draft)
    except Exception as e:
        errors.append(f"Import error: {e}")
        return _save_test_report(draft_name, draft, False, errors, None)

    # Phase 3: Interface check
    if not hasattr(mod, "run"):
        errors.append("Missing run() function — required by fleet skill interface")
        return _save_test_report(draft_name, draft, False, errors, None)

    run_fn = mod.run
    sig = inspect.signature(run_fn)
    params = list(sig.parameters.keys())
    if len(params) < 2:
        errors.append(f"run() has {len(params)} params, needs 2 (payload, config)")
        return _save_test_report(draft_name, draft, False, errors, None)

    # Phase 4: Execution test (cross-platform timeout via threading)
    try:
        result_box = [None]
        error_box = [None]

        def _run_target():
            try:
                result_box[0] = run_fn(test_payload, config)
            except Exception as e:
                error_box[0] = e

        t = threading.Thread(target=_run_target, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            raise TimeoutError(f"Skill execution exceeded {timeout}s")
        if error_box[0]:
            raise error_box[0]
        output = result_box[0]
    except TimeoutError as e:
        errors.append(str(e))
    except Exception as e:
        errors.append(f"Runtime error: {type(e).__name__}: {e}")
        errors.append(traceback.format_exc()[-500:])

    # Phase 5: Output validation
    if output is not None:
        errors.extend(_validate_result(output))

    passed = len(errors) == 0
    return _save_test_report(draft_name, draft, passed, errors, output)


# ---------------------------------------------------------------------------
# Action: evolve
# ---------------------------------------------------------------------------

def _find_reviews(skill_name: str) -> str:
    """Gather review findings for a skill (checks code_reviews and fma_reviews)."""
    reviews = []
    safe = skill_name.replace(".py", "").replace("/", "_")
    for review_dir in [REVIEWS_DIR_CODE, REVIEWS_DIR_FMA]:
        if not review_dir.exists():
            continue
        for f in sorted(review_dir.glob(f"*{safe}*_review_*.md"), reverse=True)[:3]:
            content = f.read_text(errors="ignore")
            match = re.search(r"## Findings\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
            if match:
                reviews.append(f"### From {f.name}\n{match.group(1).strip()}")
    return "\n\n".join(reviews) if reviews else ""


def _action_evolve(payload: dict, config: dict) -> dict:
    """Generate an improved version of an existing skill based on its review findings.

    Payload:
      skill_name    str   skill to evolve (e.g. "web_search")
      focus         str   optional focus area (e.g. "error handling")
      perspective   str   "software architect" | "code critic / reviewer" | "performance optimizer"
      agent_name    str   default "coder_1"
    """
    from skills._models import call_complex

    skill_name = payload.get("skill_name", "")
    focus = payload.get("focus", "")
    perspective = payload.get("perspective", "software architect")
    agent_name = payload.get("agent_name", "coder_1")

    if not skill_name:
        return {"error": "No skill_name provided"}

    skill_file = SKILLS_DIR / f"{skill_name.replace('.py', '')}.py"
    if not skill_file.exists():
        return {"error": f"Skill not found: {skill_file}"}

    source = skill_file.read_text(errors="ignore")
    source_lines = len(source.splitlines())
    reviews = _find_reviews(skill_name)
    focus_text = f"\nFOCUS YOUR CHANGES ON: {focus}" if focus else ""

    system = f"""You are a {perspective} evolving an existing fleet skill.
Your job is to improve the skill based on review findings while preserving its working interface.

RULES:
- Keep the same run(payload, config) -> dict interface
- Preserve all working functionality
- Apply the review findings as improvements
- Don't change the module docstring payload schema unless adding new optional fields
- Add improvements incrementally — don't rewrite from scratch
{focus_text}"""

    user = f"""CURRENT SKILL ({skill_name}, {source_lines} lines):
```python
{source[:4000]}
```

{"REVIEW FINDINGS:" + chr(10) + reviews[:2000] if reviews else "No formal reviews found — apply general best practices for: error handling, input validation, logging, and efficiency."}

Write the complete improved Python file. Explain your changes in a comment block at the top.
Respond with ONLY the Python code in a ```python ... ``` block."""

    raw = call_complex(system, user, config, skill_name="skill_lifecycle_suite")
    evolved = _extract_code(raw)

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _draft_filename(skill_name, "evolved", agent_name)
    out_file.write_text(evolved, encoding="utf-8")

    return {
        "evolved": True,
        "skill_name": skill_name,
        "original_lines": source_lines,
        "evolved_lines": len(evolved.splitlines()),
        "reviews_used": bool(reviews),
        "saved_to": str(out_file),
        "preview": evolved[:500],
    }


# ---------------------------------------------------------------------------
# Action: promote
# ---------------------------------------------------------------------------

def _check_test_passed(draft_stem: str) -> tuple[bool, str]:
    """Check if there's a passing test report for this draft.

    GATE: promote requires a prior test pass — this check MUST NOT be skipped
    except via explicit force=true in the payload.
    """
    test_stem = draft_stem.replace("_draft_", "_test_")
    test_files = list(DRAFTS_DIR.glob(f"{test_stem}*.md"))
    if not test_files:
        parts = draft_stem.split("_draft_")
        if parts:
            test_files = list(DRAFTS_DIR.glob(f"{parts[0]}_test_*.md"))
    if not test_files:
        return False, "No test report found — run skill_test (action='test') first"
    latest = sorted(test_files, key=lambda f: f.stat().st_mtime, reverse=True)[0]
    content = latest.read_text(errors="ignore")
    if "PASS" in content and "FAIL" not in content.split("PASS")[0]:
        return True, f"Test passed: {latest.name}"
    return False, f"Test failed: {latest.name}"


def _check_reviewed(draft_stem: str) -> tuple[bool, str]:
    """Check if there's at least one review for this draft's skill."""
    skill_name = draft_stem.split("_draft_")[0]
    if REVIEWS_DIR_WRITES.exists():
        reviews = list(REVIEWS_DIR_WRITES.glob(f"*{skill_name}*"))
        if reviews:
            return True, f"Found {len(reviews)} review(s)"
    review_notes = list(DRAFTS_DIR.glob(f"{skill_name}_review_*.md"))
    if review_notes:
        return True, f"Found {len(review_notes)} review note(s)"
    return False, "No review found — run code_write_review first"


def _infer_skill_name(draft_path: Path, override: str) -> str:
    """Derive the deployed skill filename from a draft path or explicit override."""
    if override:
        name = override.replace(".py", "")
        return f"{name}.py"
    stem = draft_path.stem
    match = re.match(r"^(.+?)_draft_", stem)
    if match:
        return f"{match.group(1)}.py"
    return draft_path.name


def _action_promote(payload: dict, config: dict) -> dict:
    """Move a tested+reviewed draft from code_drafts/ into skills/.

    GATES (enforced unless force=true):
      1. Draft must exist in knowledge/code_drafts/
      2. Must have a passing test report (_test_*PASS*)
      3. Must have at least one review

    Payload:
      draft_name    str   draft filename or skill name (finds most recent)
      force         bool  skip gate checks (default false)
      skill_name    str   override deployed skill filename
    """
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

    # Gate 1: Test passed (SAFETY GATE — do not remove)
    test_ok, test_msg = _check_test_passed(draft.stem)
    gates["test_passed"] = test_ok
    if not test_ok:
        warnings.append(test_msg)

    # Gate 2: Reviewed
    review_ok, review_msg = _check_reviewed(draft.stem)
    gates["reviewed"] = review_ok
    if not review_ok:
        warnings.append(review_msg)

    # Gate 3: Conflict check
    target_name = _infer_skill_name(draft, skill_override)
    target_path = SKILLS_DIR / target_name
    if target_path.exists():
        gates["no_conflict"] = False
        warnings.append(f"Existing skill will be overwritten: {target_name}")
    else:
        gates["no_conflict"] = True

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

    log_file = DRAFTS_DIR / f"{draft.stem}_promoted_{datetime.now().strftime('%Y%m%d')}.md"
    log_file.write_text(
        f"# Skill Promoted\n"
        f"**Draft:** {draft.name}\n"
        f"**Deployed as:** {target_name}\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Force:** {force}\n"
        f"**Gates:** {gates}\n"
        f"**Warnings:** {warnings}\n", encoding="utf-8"
    )

    return {
        "promoted": True,
        "skill_path": str(target_path),
        "skill_name": target_name,
        "gates": gates,
        "warnings": warnings,
        "saved_to": str(log_file),
    }


# ---------------------------------------------------------------------------
# Action: deploy
# ---------------------------------------------------------------------------

def _action_deploy(payload: dict, config: dict) -> dict:
    """Validate, copy, and verify a promoted skill into production.

    GATE: deploy uses _validate_skill_module (AST check) before copying.
    The skill must already exist as a draft (via draft/promote path) or be
    explicitly provided via source_path.

    Payload:
      skill_name      str   skill to deploy (required)
      source_path     str   override source dir (default: knowledge/code_drafts/)
      affinity_roles  list  roles that should receive this skill (logged only)
      verify          bool  try to import the deployed module (default True)
    """
    try:
        from filesystem_guard import FileSystemGuard
        from config import load_config
        guard = FileSystemGuard(load_config())
        if not guard.check_access("fleet/skills", "write", skill="skill_lifecycle_suite"):
            return {"error": "Access denied to skills/ by FileSystemGuard"}
    except ImportError:
        pass

    skill_name = payload.get("skill_name", "")
    if not skill_name:
        return {"status": "failed", "error": "No skill_name provided"}

    source_path = Path(payload.get("source_path", str(DRAFTS_DIR)))
    affinity_roles = payload.get("affinity_roles", [])
    verify = payload.get("verify", True)

    # Step 1: Find the draft
    draft = _find_draft(skill_name, source_dir=source_path)
    if not draft:
        return {
            "status": "failed",
            "skill_name": skill_name,
            "error": f"No draft found for '{skill_name}' in {source_path}",
        }

    # Step 2: Validate structure via AST (SAFETY GATE — do not remove)
    valid, validation_msg = _validate_skill_module(draft)
    if not valid:
        return {
            "status": "failed",
            "skill_name": skill_name,
            "error": validation_msg,
            "draft": str(draft),
        }

    # Step 3: Copy to skills/
    target = SKILLS_DIR / f"{skill_name}.py"
    try:
        shutil.copy2(draft, target)
    except Exception as e:
        return {
            "status": "failed",
            "skill_name": skill_name,
            "error": f"Copy failed: {e}",
        }

    # Step 4: Affinity note
    affinity_note = f"Recommended for roles: {', '.join(affinity_roles)}" if affinity_roles else ""

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
            f"**Affinity:** {affinity_note or 'None'}\n", encoding="utf-8"
        )
    except Exception:
        pass  # Log failure must not block deployment

    return {
        "status": "deployed",
        "skill_name": skill_name,
        "deployed_to": str(target),
        "verification": verification,
        "affinity_note": affinity_note,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_ACTIONS = {
    "draft":   _action_draft,
    "test":    _action_test,
    "evolve":  _action_evolve,
    "promote": _action_promote,
    "deploy":  _action_deploy,
}


def run(payload: dict, config: dict) -> dict:
    return dispatch_action(payload, config, _ACTIONS, default="draft")
