"""
Skill trainer v2 — autoresearch-style iterative improvement loop for fleet skills.

v0.10: Basic modify→evaluate→keep/revert loop
v0.28: Discovery logging, training profiles, cross-skill learning

Adapts Karpathy's autoresearch pattern to fleet skill code.
Improvements include:
- Configuration discoveries (not just score improvements)
- New methods/solves that simplify existing approaches
- Markdown discovery logs for every iteration (negative results have value)
- Training profiles: aggressive, conservative, exploratory

Payload:
  skill:       str  — skill module name (e.g. "summarize", "web_search")
  iterations:  int  — max improvement attempts (default 5)
  profile:     str  — training profile: aggressive|conservative|exploratory (default conservative)
  dry_run:     bool — if true, report plan without executing

Returns:
  {"skill": str, "iterations_run": int, "improved": bool,
   "before_score": float, "after_score": float, "discoveries": list, "saved_to": str}
"""
import importlib
import json
import shutil
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
RESULTS_DIR = FLEET_DIR / "knowledge" / "skill_training"
DISCOVERIES_DIR = FLEET_DIR / "knowledge" / "skill_training" / "discoveries"

# Training profiles: control iteration count, LLM temperature, approach
TRAINING_PROFILES = {
    "conservative": {
        "max_iterations": 3,
        "temperature": 0.3,
        "approach": "Make minimal, targeted changes. Focus on robustness and error handling.",
    },
    "aggressive": {
        "max_iterations": 10,
        "temperature": 0.7,
        "approach": "Try significant restructuring. Optimize for speed and output quality. "
                    "Consider caching, parallel processing, and algorithmic improvements.",
    },
    "exploratory": {
        "max_iterations": 5,
        "temperature": 0.9,
        "approach": "Try fundamentally different approaches. Consider entirely new methods, "
                    "different APIs, alternative data structures. Innovation over incremental gains.",
    },
}


# ── Mechanical Metrics ────────────────────────────────────────────────────────

def _eval_summarize(skill_module, config):
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
            score += 0.3 if len(summary) > 50 else 0.0
            score += 0.2 if len(summary) < 2000 else 0.0
            score += 0.2 if "- " in summary or "* " in summary else 0.0
            score += 0.15 if "\n" in summary else 0.0
            score += 0.15 if not result.get("error") else 0.0
            scores.append(score)
        except Exception:
            scores.append(0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _eval_web_search(skill_module, config):
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
            score += 0.3 if len(results_list) >= 3 else 0.1
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
    try:
        result = skill_module.run({}, config)
        if isinstance(result, dict):
            if result.get("error"):
                return 0.3
            return 0.7
        return 0.5
    except Exception:
        return 0.0


EVAL_REGISTRY = {
    "summarize": _eval_summarize,
    "web_search": _eval_web_search,
}


def _get_evaluator(skill_name):
    return EVAL_REGISTRY.get(skill_name, _eval_generic)


# ── Discovery Logging ────────────────────────────────────────────────────────

def _log_discovery(skill_name, iteration, discovery_type, description,
                   score_before, score_after, code_diff_summary=""):
    """Log a training discovery as markdown for human review."""
    DISCOVERIES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{skill_name}_{ts}_iter{iteration}.md"
    filepath = DISCOVERIES_DIR / filename

    delta = score_after - score_before
    delta_str = f"+{delta:.3f}" if delta >= 0 else f"{delta:.3f}"
    icon = "+" if delta > 0 else ("=" if delta == 0 else "-")

    content = f"""# Discovery: {skill_name} (iteration {iteration})

**Date:** {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Type:** {discovery_type}
**Score:** {score_before:.3f} -> {score_after:.3f} ({delta_str})
**Outcome:** {icon} {"Improvement kept" if delta > 0 else "Reverted (negative result)"}

## What was tried

{description}

## Code changes

{code_diff_summary or "See training log for full diff."}

## Takeaway

{"This change improved the skill and was kept." if delta > 0 else
 "This change did not improve the metric. The negative result is still valuable "
 "as it narrows the search space for future iterations."}
"""
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)


# ── Cross-Skill Learning ────────────────────────────────────────────────────

def _get_cross_skill_context():
    """Read recent discoveries from other skills to inform the current training."""
    if not DISCOVERIES_DIR.exists():
        return ""

    discoveries = sorted(DISCOVERIES_DIR.glob("*.md"), reverse=True)[:10]
    if not discoveries:
        return ""

    context_parts = []
    for d in discoveries:
        try:
            content = d.read_text(encoding="utf-8")
            # Extract just the header and takeaway
            lines = content.splitlines()
            header = "\n".join(l for l in lines[:8] if l.strip())
            context_parts.append(header)
        except Exception:
            pass

    if not context_parts:
        return ""

    return (
        "\n\n## Cross-Skill Learning (recent discoveries from other skills):\n\n"
        + "\n---\n".join(context_parts[:5])
    )


# ── LLM Proposal ────────────────────────────────────────────────────────────

def _propose_improvement(skill_name, skill_code, score, config, profile):
    """Use LLM to propose a code improvement for the skill."""
    from skills._models import call_complex

    profile_cfg = TRAINING_PROFILES.get(profile, TRAINING_PROFILES["conservative"])
    cross_skill = _get_cross_skill_context()

    system = f"""\
You are a skill optimizer for an AI agent fleet. You receive a Python skill module
and its current evaluation score (0.0-1.0). Propose a code change to improve the score.

Training profile: {profile} — {profile_cfg['approach']}

Rules:
- Keep the same run(payload, config) interface
- Don't add new dependencies
- Focus on robustness, output quality, and error handling
- Return the COMPLETE modified skill file (not a diff)
- Wrap the code in ```python ... ```
- Explain your reasoning before the code block
{cross_skill}
"""
    user = (
        f"Skill: {skill_name}\n"
        f"Current score: {score:.3f}\n\n"
        f"Current code:\n```python\n{skill_code}\n```\n\n"
        f"Propose an improvement. Return the complete modified file."
    )
    return call_complex(system, user, config, max_tokens=4096, cache_system=True)


def _extract_code(response):
    import re
    m = re.search(r'```python\s*\n(.*?)```', response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _extract_reasoning(response):
    """Extract the reasoning/explanation before the code block."""
    import re
    m = re.search(r'```python', response)
    if m:
        return response[:m.start()].strip()
    return response[:500]


# ── Main Entry ───────────────────────────────────────────────────────────────

def run(payload, config):
    skill_name = payload.get("skill", "")
    profile = payload.get("profile", "conservative")
    dry_run = bool(payload.get("dry_run", False))

    if profile not in TRAINING_PROFILES:
        return {"error": f"unknown profile '{profile}'. Use: {list(TRAINING_PROFILES.keys())}"}

    profile_cfg = TRAINING_PROFILES[profile]
    iterations = max(1, min(20, int(payload.get("iterations", profile_cfg["max_iterations"]))))

    if not skill_name:
        return {"error": "skill name required"}

    skill_path = SKILLS_DIR / f"{skill_name}.py"
    if not skill_path.exists():
        return {"error": f"skill '{skill_name}' not found"}

    # Acquire exclusive training lock
    import sys as _sys
    _sys.path.insert(0, str(FLEET_DIR))
    import db as _db
    _db.init_db()
    lock_holder = f"skill_train:{skill_name}"
    if not _db.acquire_lock("training", lock_holder):
        current = _db.check_lock("training")
        return {"error": f"training lock held by '{current}' -- try later"}

    try:
        return _run_training(skill_name, skill_path, iterations, dry_run, config,
                             lock_holder, profile)
    finally:
        _db.release_lock("training", lock_holder)


def _run_training(skill_name, skill_path, iterations, dry_run, config,
                  lock_holder, profile):
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
            "profile": profile,
            "profile_config": TRAINING_PROFILES.get(profile, {}),
            "dry_run": True,
        }

    original_code = skill_path.read_text(encoding="utf-8")
    best_score = baseline_score
    best_code = original_code
    log_entries = []
    discoveries = []

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for i in range(iterations):
        # Propose improvement
        try:
            response = _propose_improvement(skill_name, best_code, best_score,
                                            config, profile)
            new_code = _extract_code(response)
            reasoning = _extract_reasoning(response)
            if not new_code:
                log_entries.append({"iteration": i + 1, "status": "no_code",
                                    "score": best_score})
                continue
        except Exception as e:
            log_entries.append({"iteration": i + 1, "status": f"proposal_error: {e}",
                                "score": best_score})
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
            shutil.copy2(backup_path, skill_path)
            backup_path.unlink(missing_ok=True)
            log_entries.append({"iteration": i + 1, "status": f"eval_crash: {e}",
                                "score": best_score})
            # Log negative discovery
            discovery_path = _log_discovery(
                skill_name, i + 1, "crash",
                f"Proposed change crashed during evaluation: {e}\n\nReasoning: {reasoning}",
                best_score, 0.0)
            discoveries.append({"type": "crash", "path": discovery_path})
            continue

        # Determine discovery type
        if new_score > best_score:
            discovery_type = "improvement"
            if new_score - best_score > 0.2:
                discovery_type = "breakthrough"
        elif new_score == best_score:
            discovery_type = "neutral"
        else:
            discovery_type = "regression"

        # Log discovery (always — negative results have value)
        discovery_path = _log_discovery(
            skill_name, i + 1, discovery_type,
            reasoning, best_score, new_score,
            f"Lines changed: ~{abs(len(new_code.splitlines()) - len(best_code.splitlines()))}")
        discoveries.append({"type": discovery_type, "path": discovery_path,
                            "delta": new_score - best_score})

        if new_score > best_score:
            best_score = new_score
            best_code = new_code
            log_entries.append({"iteration": i + 1, "status": "keep",
                                "score": new_score})
            backup_path.unlink(missing_ok=True)
        else:
            shutil.copy2(backup_path, skill_path)
            backup_path.unlink(missing_ok=True)
            log_entries.append({"iteration": i + 1, "status": "revert",
                                "score": new_score})

    # Ensure best code is written
    if best_score > baseline_score:
        skill_path.write_text(best_code, encoding="utf-8")

    # Save training log
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = RESULTS_DIR / f"{skill_name}_train_{ts}.json"
    log_file.write_text(json.dumps({
        "skill": skill_name,
        "timestamp": ts,
        "profile": profile,
        "baseline_score": baseline_score,
        "final_score": best_score,
        "improved": best_score > baseline_score,
        "iterations_run": len(log_entries),
        "iterations": log_entries,
        "discoveries": [{"type": d["type"], "delta": d.get("delta", 0)}
                        for d in discoveries],
    }, indent=2))

    return {
        "skill": skill_name,
        "profile": profile,
        "iterations_run": len(log_entries),
        "improved": best_score > baseline_score,
        "before_score": baseline_score,
        "after_score": best_score,
        "discoveries": len(discoveries),
        "breakthroughs": sum(1 for d in discoveries if d["type"] == "breakthrough"),
        "saved_to": str(log_file),
    }
