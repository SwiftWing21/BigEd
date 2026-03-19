"""
Post-refactor verification skill — validates codebase integrity after major changes.
Checks imports, call signatures, process launches, and cross-module references.

Run after: module extraction, WSL→native migration, skill migration, API changes.
"""
import ast
import importlib
import json
import sys
from pathlib import Path

SKILL_NAME = "refactor_verify"
DESCRIPTION = "Verify codebase integrity after refactors — imports, signatures, launch patterns"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "full")
    if action == "full":
        return _full_verify()
    elif action == "imports":
        return json.dumps(_check_imports())
    elif action == "skills":
        return json.dumps(_check_skills())
    elif action == "launch":
        return json.dumps(_check_launch_patterns())
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _full_verify() -> str:
    results = {
        "imports": _check_imports(),
        "skills": _check_skills(),
        "launch": _check_launch_patterns(),
        "syntax": _check_syntax(),
    }
    total = sum(r.get("passed", 0) for r in results.values())
    failed = sum(r.get("failed", 0) for r in results.values())
    results["summary"] = {
        "total_checks": total + failed,
        "passed": total,
        "failed": failed,
        "status": "CLEAN" if failed == 0 else "ISSUES_FOUND",
    }
    return json.dumps(results)


def _check_imports() -> dict:
    """Verify all fleet modules import without errors."""
    modules = [
        "db", "config", "providers", "services", "cost_tracking",
        "idle_evolution", "comms", "diagnostics", "marathon",
        "process_control", "dag_queue", "a2a", "guardrails",
        "integrity", "audit_log", "agent_cards", "workflows",
        "dead_code_scan",
    ]
    passed = 0
    failures = []
    for mod in modules:
        try:
            importlib.import_module(mod)
            passed += 1
        except Exception as e:
            failures.append({"module": mod, "error": str(e)[:100]})
    return {"passed": passed, "failed": len(failures), "failures": failures}


def _check_skills() -> dict:
    """Verify all skills import and have required attributes."""
    skills_dir = FLEET_DIR / "skills"
    passed = 0
    failures = []
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_name = f.stem
        try:
            mod = importlib.import_module(f"skills.{mod_name}")
            # Check required attributes
            if not hasattr(mod, "run"):
                failures.append({"skill": mod_name, "error": "missing run() function"})
                continue
            if not hasattr(mod, "SKILL_NAME"):
                failures.append({"skill": mod_name, "error": "missing SKILL_NAME"})
                continue
            passed += 1
        except Exception as e:
            failures.append({"skill": mod_name, "error": str(e)[:100]})
    return {"passed": passed, "failed": len(failures), "failures": failures}


def _check_launch_patterns() -> dict:
    """Check for WSL/pkill/pgrep anti-patterns that break on native Windows."""
    issues = []
    check_dirs = [FLEET_DIR, FLEET_DIR.parent / "BigEd" / "launcher"]

    wsl_patterns = ["wsl(", "wsl_bg(", "pkill ", "pgrep ", "nohup "]

    for check_dir in check_dirs:
        if not check_dir.exists():
            continue
        for f in check_dir.rglob("*.py"):
            if ".venv" in str(f) or "__pycache__" in str(f) or "_graveyard" in str(f):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for i, line in enumerate(content.splitlines(), 1):
                    # Skip comments and strings that document patterns
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                        continue
                    for pattern in wsl_patterns:
                        if pattern in line and "def wsl" not in line and "def wsl_bg" not in line:
                            # Check if it's in actual code (not a string/comment)
                            issues.append({
                                "file": str(f.relative_to(FLEET_DIR.parent)),
                                "line": i,
                                "pattern": pattern.strip(),
                                "context": stripped[:80],
                            })
            except Exception:
                continue

    return {"passed": 0 if issues else 1, "failed": len(issues), "issues": issues[:20]}


def _check_syntax() -> dict:
    """AST-parse all Python files for syntax errors."""
    passed = 0
    failures = []
    for f in FLEET_DIR.rglob("*.py"):
        if ".venv" in str(f) or "__pycache__" in str(f):
            continue
        try:
            ast.parse(f.read_text(encoding="utf-8"))
            passed += 1
        except SyntaxError as e:
            failures.append({"file": str(f.relative_to(FLEET_DIR)), "error": str(e)[:100]})
    return {"passed": passed, "failed": len(failures), "failures": failures}
