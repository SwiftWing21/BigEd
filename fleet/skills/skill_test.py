"""
Skill test — runs a drafted skill in a sandbox with a test payload,
validates the return schema, and reports pass/fail.

Payload:
  draft_path    str   path to draft file (relative to fleet root or absolute)
                      if omitted, picks the most recent untested draft
  test_payload  dict  payload to pass to the skill's run() (optional, uses defaults)
  timeout       int   max seconds to run (default 30)

Output: knowledge/code_drafts/<name>_test_<date>.md
Returns: {passed, errors, output_preview, tested_file, saved_to}
"""
import importlib.util
import json
import traceback
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
DRAFTS_DIR = FLEET_DIR / "knowledge" / "code_drafts"


def _find_draft(requested: str) -> Path | None:
    """Find a draft file to test."""
    if requested:
        p = Path(requested)
        if p.is_absolute() and p.exists():
            return p
        rel = FLEET_DIR / requested
        if rel.exists():
            return rel
        rel2 = DRAFTS_DIR / requested
        if rel2.exists():
            return rel2
        return None
    # Pick most recent untested draft
    if not DRAFTS_DIR.exists():
        return None
    drafts = sorted(DRAFTS_DIR.glob("*_draft_*.py"), key=lambda f: f.stat().st_mtime, reverse=True)
    for draft in drafts:
        test_marker = draft.stem.replace("_draft_", "_test_")
        if not list(DRAFTS_DIR.glob(f"{test_marker}*")):
            return draft
    return drafts[0] if drafts else None


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


def run(payload, config):
    requested = payload.get("draft_path", "")
    test_payload = payload.get("test_payload", {})
    timeout = payload.get("timeout", 30)

    draft = _find_draft(requested)
    if not draft:
        return {"error": "No draft file found to test", "passed": False}

    draft_name = draft.stem
    errors = []
    output = None
    passed = False

    # Phase 1: Syntax check
    try:
        with open(draft, encoding="utf-8") as f:
            compile(f.read(), str(draft), "exec")
    except SyntaxError as e:
        errors.append(f"Syntax error: {e}")
        return _save_report(draft_name, draft, False, errors, None)

    # Phase 2: Import check
    try:
        mod = _load_module(draft)
    except Exception as e:
        errors.append(f"Import error: {e}")
        return _save_report(draft_name, draft, False, errors, None)

    # Phase 3: Interface check
    if not hasattr(mod, "run"):
        errors.append("Missing run() function — required by fleet skill interface")
        return _save_report(draft_name, draft, False, errors, None)

    run_fn = mod.run
    import inspect
    sig = inspect.signature(run_fn)
    params = list(sig.parameters.keys())
    if len(params) < 2:
        errors.append(f"run() has {len(params)} params, needs 2 (payload, config)")
        return _save_report(draft_name, draft, False, errors, None)

    # Phase 4: Execution test (cross-platform timeout via threading)
    try:
        import threading

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
        validation_errors = _validate_result(output)
        errors.extend(validation_errors)

    passed = len(errors) == 0
    return _save_report(draft_name, draft, passed, errors, output)


def _save_report(draft_name, draft_path, passed, errors, output):
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

    report_file.write_text("\n".join(lines))

    return {
        "passed": passed,
        "errors": errors,
        "output_preview": str(output)[:500] if output else None,
        "tested_file": str(draft_path),
        "saved_to": str(report_file),
    }
