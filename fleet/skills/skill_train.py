"""
Skill trainer — autoresearch-style iterative improvement loop for fleet skills.

Adapts Karpathy's autoresearch pattern (modify → run → evaluate → keep/revert)
to fleet skill code instead of neural network weights.

The key insight: skills need mechanical metrics to enable automated iteration.
This skill defines evaluation harnesses per skill type, then uses an LLM to
propose code changes and measures whether they improve the metric.

Payload:
  skill:       str  — skill module name (e.g. "summarize", "web_search")
  iterations:  int  — max improvement attempts (default 5)
  dry_run:     bool — if true, report plan without executing

Returns:
  {"skill": str, "iterations_run": int, "improved": bool,
   "before_score": float, "after_score": float, "saved_to": str}
"""
import copy
import importlib
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
RESULTS_DIR = FLEET_DIR / "knowledge" / "skill_training"

# ── Mechanical Metrics ────────────────────────────────────────────────────────
# Each skill type needs a deterministic, repeatable evaluation function.
# Score is 0.0 (worst) to 1.0 (best). Higher is better.

def _eval_summarize(skill_module, config):
    """Evaluate summarize skill: does it produce a non-empty summary with bullet points?"""
    test_cases = [
        {"text": "The quick brown fox jumps over the lazy dog. " * 20,
         "description": "test passage about foxes"},
        {"description": "Explain how neural networks learn through backpropagation"},
    ]
    scores = []
    for tc in test_cases:
        try:
            result = skill_module.run(tc, config)
            summary = result.get("summary", "")
            if not summary:
                scores.append(0.0)
                continue
            score = 0.0
            score += 0.3 if len(summary) > 50 else 0.0       # non-trivial length
            score += 0.2 if len(summary) < 2000 else 0.0      # concise
            score += 0.2 if "- " in summary or "• " in summary else 0.0  # has bullets
            score += 0.15 if "\n" in summary else 0.0          # multi-line
            score += 0.15 if not result.get("error") else 0.0  # no error
            scores.append(score)
        except Exception:
            scores.append(0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _eval_web_search(skill_module, config):
    """Evaluate web_search: does it return structured results with titles and URLs?"""
    test_cases = [
        {"query": "Python programming language"},
        {"query": "local AI deployment small business"},
    ]
    scores = []
    for tc in test_cases:
        try:
            result = skill_module.run(tc, config)
            results_list = result.get("results", [])
            if not results_list:
                scores.append(0.0)
                continue
            score = 0.0
            score += 0.3 if len(results_list) >= 3 else 0.1   # enough results
            has_titles = all(r.get("title") for r in results_list[:3])
            has_urls = all(r.get("url") for r in results_list[:3])
            score += 0.3 if has_titles else 0.0
            score += 0.3 if has_urls else 0.0
            score += 0.1 if not result.get("error") else 0.0
            scores.append(score)
        except Exception:
            scores.append(0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _eval_generic(skill_module, config):
    """Fallback: just check the skill doesn't crash on empty/minimal input."""
    try:
        result = skill_module.run({}, config)
        if isinstance(result, dict):
            if result.get("error"):
                return 0.3  # returned error gracefully (not a crash)
            return 0.7      # returned successfully
        return 0.5           # returned something
    except Exception:
        return 0.0           # crashed


EVAL_REGISTRY = {
    "summarize": _eval_summarize,
    "web_search": _eval_web_search,
}


def _get_evaluator(skill_name):
    return EVAL_REGISTRY.get(skill_name, _eval_generic)


def _propose_improvement(skill_name, skill_code, score, config):
    """Use LLM to propose a code improvement for the skill."""
    from skills._models import call_complex

    system = """\
You are a skill optimizer for an AI agent fleet. You receive a Python skill module
and its current evaluation score (0.0-1.0). Propose a SMALL, targeted code change
to improve the score.

Rules:
- Keep the same run(payload, config) interface
- Don't add new dependencies
- Focus on robustness, output quality, and error handling
- Return the COMPLETE modified skill file (not a diff)
- Wrap the code in ```python ... ```
"""
    user = (
        f"Skill: {skill_name}\n"
        f"Current score: {score:.3f}\n\n"
        f"Current code:\n```python\n{skill_code}\n```\n\n"
        f"Propose an improvement. Return the complete modified file."
    )
    return call_complex(system, user, config, max_tokens=4096, cache_system=True)


def _extract_code(response):
    """Extract Python code block from LLM response."""
    import re
    m = re.search(r'```python\s*\n(.*?)```', response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def run(payload, config):
    skill_name = payload.get("skill", "")
    iterations = max(1, min(20, int(payload.get("iterations", 5))))
    dry_run = bool(payload.get("dry_run", False))

    if not skill_name:
        return {"error": "skill name required"}

    skill_path = SKILLS_DIR / f"{skill_name}.py"
    if not skill_path.exists():
        return {"error": f"skill '{skill_name}' not found"}

    evaluator = _get_evaluator(skill_name)

    # Baseline evaluation
    try:
        module = importlib.import_module(f"skills.{skill_name}")
        importlib.reload(module)
        baseline_score = evaluator(module, config)
    except Exception as e:
        return {"error": f"baseline eval failed: {e}"}

    if dry_run:
        return {
            "skill": skill_name,
            "baseline_score": baseline_score,
            "evaluator": evaluator.__name__,
            "iterations_planned": iterations,
            "dry_run": True,
        }

    # Save original
    original_code = skill_path.read_text(encoding="utf-8")
    best_score = baseline_score
    best_code = original_code
    log_entries = []

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for i in range(iterations):
        # Propose improvement
        try:
            response = _propose_improvement(skill_name, best_code, best_score, config)
            new_code = _extract_code(response)
            if not new_code:
                log_entries.append({"iteration": i + 1, "status": "no_code", "score": best_score})
                continue
        except Exception as e:
            log_entries.append({"iteration": i + 1, "status": f"proposal_error: {e}", "score": best_score})
            continue

        # Write proposed code
        backup_path = skill_path.with_suffix(".py.bak")
        shutil.copy2(skill_path, backup_path)
        skill_path.write_text(new_code, encoding="utf-8")

        # Evaluate
        try:
            module = importlib.import_module(f"skills.{skill_name}")
            importlib.reload(module)
            new_score = evaluator(module, config)
        except Exception as e:
            # Revert on crash
            shutil.copy2(backup_path, skill_path)
            backup_path.unlink(missing_ok=True)
            log_entries.append({"iteration": i + 1, "status": f"eval_crash: {e}", "score": best_score})
            continue

        if new_score > best_score:
            # Keep improvement
            best_score = new_score
            best_code = new_code
            log_entries.append({"iteration": i + 1, "status": "keep", "score": new_score})
            backup_path.unlink(missing_ok=True)
        else:
            # Revert
            shutil.copy2(backup_path, skill_path)
            backup_path.unlink(missing_ok=True)
            log_entries.append({"iteration": i + 1, "status": "revert", "score": new_score})

    # Ensure best code is written
    if best_score > baseline_score:
        skill_path.write_text(best_code, encoding="utf-8")

    # Save training log
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = RESULTS_DIR / f"{skill_name}_train_{ts}.json"
    log_file.write_text(json.dumps({
        "skill": skill_name,
        "timestamp": ts,
        "baseline_score": baseline_score,
        "final_score": best_score,
        "improved": best_score > baseline_score,
        "iterations": log_entries,
    }, indent=2))

    return {
        "skill": skill_name,
        "iterations_run": len(log_entries),
        "improved": best_score > baseline_score,
        "before_score": baseline_score,
        "after_score": best_score,
        "saved_to": str(log_file),
    }
