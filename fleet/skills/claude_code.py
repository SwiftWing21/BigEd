"""
Claude Code integration — headless mode for deep code review, refactoring, and analysis.
Uses `claude -p` (print mode) which runs non-interactively and exits.
Falls back to call_complex() if Claude Code CLI is not installed.
"""
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

SKILL_NAME = "claude_code"
DESCRIPTION = "Deep code analysis via Claude Code CLI — review, refactor, document, test generation"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
PROJECT_DIR = FLEET_DIR.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge" / "claude_code"

_CLAUDE_CLI = shutil.which("claude")


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "review")

    if not _CLAUDE_CLI:
        return _fallback(payload, config)

    if action == "review":
        return _review(payload)
    elif action == "refactor":
        return _refactor(payload)
    elif action == "document":
        return _document(payload)
    elif action == "test":
        return _generate_tests(payload)
    elif action == "custom":
        return _custom_prompt(payload)
    elif action == "status":
        return _check_status()
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _run_claude(prompt: str, cwd: str = None, timeout: int = 300) -> dict:
    """Execute claude -p and return structured result."""
    if not cwd:
        cwd = str(PROJECT_DIR)
    try:
        result = subprocess.run(
            [_CLAUDE_CLI, "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd,
        )
        output = result.stdout.strip()
        error = result.stderr.strip()

        # Save output to knowledge
        _save_output(prompt[:50], output)

        return {
            "status": "ok" if result.returncode == 0 else "error",
            "output": output,
            "error": error if error else None,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Claude Code timed out after {timeout}s"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _review(payload):
    """Deep code review of a file or directory."""
    target = payload.get("file") or payload.get("path", "")
    focus = payload.get("focus", "bugs, security issues, and improvements")

    if target:
        prompt = f"Review {target} for {focus}. Be specific with line numbers and concrete fixes."
    else:
        prompt = f"Review the codebase for {focus}. Focus on the most critical issues."

    result = _run_claude(prompt)
    return json.dumps(result)


def _refactor(payload):
    """Suggest or apply refactoring to a file."""
    target = payload.get("file", "")
    principles = payload.get("principles", "DRY, readability, performance")

    if not target:
        return json.dumps({"error": "file required for refactoring"})

    prompt = (f"Refactor {target} applying these principles: {principles}. "
              f"Show the specific changes as a diff. Do NOT change external behavior.")

    result = _run_claude(prompt)
    return json.dumps(result)


def _document(payload):
    """Generate documentation for a file or module."""
    target = payload.get("file") or payload.get("module", "")
    style = payload.get("style", "concise docstrings + module overview")

    if not target:
        return json.dumps({"error": "file or module required"})

    prompt = f"Document {target} with {style}. Include function signatures, parameters, return types, and usage examples."

    result = _run_claude(prompt)
    return json.dumps(result)


def _generate_tests(payload):
    """Generate test cases for a file."""
    target = payload.get("file", "")
    framework = payload.get("framework", "pytest-style assertions")

    if not target:
        return json.dumps({"error": "file required for test generation"})

    prompt = (f"Generate comprehensive tests for {target} using {framework}. "
              f"Cover edge cases, error paths, and main functionality. "
              f"Output runnable test code.")

    result = _run_claude(prompt, timeout=180)
    return json.dumps(result)


def _custom_prompt(payload):
    """Run a custom prompt through Claude Code."""
    prompt = payload.get("prompt", "")
    cwd = payload.get("cwd", str(PROJECT_DIR))
    timeout = payload.get("timeout", 300)

    if not prompt:
        return json.dumps({"error": "prompt required"})

    result = _run_claude(prompt, cwd=cwd, timeout=timeout)
    return json.dumps(result)


def _check_status():
    """Check Claude Code CLI availability and version."""
    status = {
        "cli_found": bool(_CLAUDE_CLI),
        "cli_path": _CLAUDE_CLI,
    }
    if _CLAUDE_CLI:
        try:
            result = subprocess.run(
                [_CLAUDE_CLI, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            status["version"] = result.stdout.strip()
        except Exception:
            status["version"] = "unknown"

    # Check VS Code
    code = shutil.which("code")
    status["vscode_found"] = bool(code)
    status["vscode_path"] = code

    return json.dumps(status)


def _save_output(label, output):
    """Save Claude Code output to knowledge directory."""
    try:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_label}.md"
        (KNOWLEDGE_DIR / filename).write_text(output, encoding="utf-8")
    except Exception:
        pass


def _fallback(payload, config):
    """Fallback to call_complex() when Claude Code CLI is not available."""
    try:
        from skills._models import call_complex
        action = payload.get("action", "review")
        target = payload.get("file", payload.get("prompt", ""))

        system = "You are a senior code reviewer. Provide specific, actionable feedback."
        user = f"Action: {action}. Target: {target}"

        result = call_complex(system, user, config, skill_name="claude_code")
        return json.dumps({"status": "fallback", "output": result, "note": "Claude Code CLI not found — used API fallback"})
    except Exception as e:
        return json.dumps({"error": f"Fallback failed: {e}"})
